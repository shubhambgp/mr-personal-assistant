"""LLM eval gate: drives the real agent against evals/golden.yaml.

This is the only test that spends money and the only one that can fail for
reasons outside the code (model drift), so it is a separate step from
`pytest tests`, and CI runs it only when an API key is present.

It drives the agent core directly — no HTTP, no server. That is possible
because app/bot/ is transport-agnostic, and it means a failure here is a
failure in the agent, not in the plumbing.

    .venv/bin/python -m evals.run_eval
    .venv/bin/python -m evals.run_eval --case cross_chair_doctor_not_found
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import yaml  # noqa: E402

from app.bootstrap import close_resources, open_resources  # noqa: E402
from app.bot import db, graph  # noqa: E402
from app.bot.checkpointer import checkpointer  # noqa: E402
from app.bot.context import RepContext  # noqa: E402
from app.registry import registry  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "golden.yaml"
#: Hand-authored retrieval cases. Kept separate because golden.yaml is generated
#: from SQL and these expectations come from the committed literature corpus.
GOLDEN_RAG = Path(__file__).resolve().parent / "golden_rag.yaml"


def _normalise(text: str) -> str:
    """Lowercase, and fold typographic punctuation to ASCII.

    Not cosmetic: three cases once failed purely because the model wrote a
    curly apostrophe where the expectation had a straight one. That was a broken
    test, not a broken bot.
    """
    folded = (
        text.replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("–", "-")
        .replace("—", "-")
        .replace(" ", " ")
    )
    return re.sub(r"\s+", " ", folded).lower()


def check(answer: str, case: dict) -> list[str]:
    text = _normalise(answer)
    failures = []

    for needle in case.get("expect_contains_all") or []:
        if _normalise(str(needle)) not in text:
            failures.append(f"missing required: {needle!r}")

    any_of = case.get("expect_contains_any") or []
    if any_of and not any(_normalise(str(n)) in text for n in any_of):
        failures.append(f"none of the accepted phrasings appeared: {any_of}")

    for needle in case.get("expect_not_contains") or []:
        if _normalise(str(needle)) in text:
            failures.append(f"must NOT appear but did: {needle!r}")

    return failures


async def run_case(case: dict) -> tuple[bool, str, list[dict]]:
    ctx_row = None
    with db.ro_pool().connection() as conn:
        ctx_row = conn.execute(
            "SELECT chair_id, rep_code, rep_name FROM reps WHERE chair_id = %s",
            (case["chair_id"],),
        ).fetchone()
    if ctx_row is None:
        return False, f"chair_id {case['chair_id']} not in reps — regenerate golden.yaml", []

    ctx = RepContext(chair_id=ctx_row[0], rep_code=ctx_row[1], rep_name=ctx_row[2] or "Rep")
    trace: list[dict] = []

    async def on_text_delta(_delta: str) -> None:
        return None

    async def on_tool_start(_call_id: str, name: str, args: dict) -> None:
        trace.append({"tool": name, "input": args})

    async def on_tool_end(
        _call_id: str, name: str, _args: dict, _output: str, is_error: bool, ms: float
    ) -> None:
        trace.append({"tool": name, "done_ms": round(ms, 1), "error": is_error})

    vintage = ", ".join(sorted({v for _t, v, _n in db.data_vintage()})) or "unknown"

    with db.ro_pool().connection() as conn:
        result = await graph.run_turn(
            ctx=ctx,
            tool_specs=registry.build(ctx, conn),
            user_message=case["question"],
            # A fresh thread per case: the eval measures single-turn behaviour,
            # so leaking state between cases would make results order-dependent.
            thread_id=str(uuid.uuid4()),
            vintage_summary=vintage,
            on_text_delta=on_text_delta,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
            checkpointer=checkpointer(),
        )

    failures = check(result.final_text, case)
    return not failures, result.final_text if failures else "", trace if failures else []


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="run only this case id")
    args = parser.parse_args()

    cases = yaml.safe_load(GOLDEN.read_text())["cases"]
    if GOLDEN_RAG.exists():
        cases += yaml.safe_load(GOLDEN_RAG.read_text())["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case] or sys.exit(f"no case {args.case!r}")

    # Exactly what the app opens at startup. Listing them here by hand is how
    # the vector store came to be missing — see app/bootstrap.py.
    await open_resources(audit=False)

    passed = 0
    failed: list[str] = []

    for case in cases:
        ok, answer, trace = await run_case(case)
        print(f"  {'PASS' if ok else 'FAIL'}  {case['id']}")
        if ok:
            passed += 1
        else:
            failed.append(case["id"])
            for line in check(answer, case):
                print(f"          {line}")
            print(f"          answer: {answer[:400]}")
            if trace:
                print(f"          tools: {[t.get('tool') for t in trace]}")
            if case.get("note"):
                print(f"          note: {case['note']}")

    await close_resources(audit=False)
    print(f"\n{passed}/{len(cases)} passed")
    if failed:
        print(f"failed: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
