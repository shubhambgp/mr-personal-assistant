"""Retrieval eval: recall@k and MRR over evals/rag_golden.yaml.

    python -m evals.run_rag_eval                # scored, offline
    python -m evals.run_rag_eval --refresh      # re-embed the questions (needs a key)
    python -m evals.run_rag_eval --verbose      # show what came back for failures

WHY THIS EXISTS SEPARATELY FROM run_eval. This measures retrieval alone — no
model, no generation. That isolates the half of a RAG system that fails
silently: if a chunking change fragments a section, the end-to-end eval just
produces a slightly worse answer that nobody notices, while recall@5 drops
visibly here.

WHY IT NEEDS NO API KEY. The dense leg needs an embedding of each question, and
the questions are fixed — so they are embedded once and the vectors committed to
evals/rag_query_vectors.npz, keyed by model name. That makes this runnable on
every pull request with no key and no cost, while still exercising the real
hybrid path rather than a sparse-only approximation. `--refresh` regenerates
them, and a model change is caught because the cache records which model wrote it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

import yaml  # noqa: E402

from app.bot.context import RepContext  # noqa: E402
from app.services import vectors  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "rag_golden.yaml"
CACHE = Path(__file__).resolve().parent / "rag_query_vectors.npz"

#: Retrieval is scored at 5 because that is what the tool returns by default.
K = 5
#: Absence is asserted by CONTENT, not by score.
#:
#: A retriever cannot say "I don't know": it always returns its nearest
#: neighbours. And RRF scores are rank-based and relative — 1.0 means "top of
#: both legs", not "confidently relevant" — so thresholding them to detect
#: absence measures nothing. Asking "half-life" of this corpus returns Cardevia's
#: composition section at 0.64 simply because it is the closest thing there is.
#:
#: What IS a meaningful retrieval property is whether the retrieved passages
#: contain the concept at all. If they do not, the model has what it needs to
#: refuse — and that refusal is then asserted end-to-end in evals/golden.yaml,
#: which is where a judgement about wording belongs.

CTX = RepContext(chair_id=7100001, rep_code=7800001, rep_name="Eval Rep")


def load_cases() -> list[dict]:
    return yaml.safe_load(GOLDEN.read_text())["cases"]


async def refresh_cache(cases: list[dict]) -> None:
    import os

    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("--refresh needs OPENAI_API_KEY to embed the questions.")
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    questions = [c["question"] for c in cases]
    response = await client.embeddings.create(
        model=vectors.EMBEDDING_MODEL, input=questions
    )
    import numpy as np

    # float16 and npz, the same format as the corpus cache: 80 KB instead of the
    # 820 KB the equivalent JSON took, and verified not to change top-10 ranking.
    ids = [c["id"] for c in cases]
    np.savez_compressed(
        CACHE,
        model=np.array(vectors.EMBEDDING_MODEL),
        ids=np.array(ids),
        vectors=np.array([d.embedding for d in response.data], dtype="float16"),
    )
    size_kb = CACHE.stat().st_size / 1024
    print(f"wrote {len(questions)} query vectors to {CACHE.name} ({size_kb:.0f} KB)")


def load_cache(cases: list[dict]) -> dict[str, list[float]]:
    if not CACHE.exists():
        sys.exit(f"{CACHE.name} missing — run: python -m evals.run_rag_eval --refresh")
    import numpy as np

    with np.load(CACHE, allow_pickle=False) as blob:
        model = str(blob["model"])
        ids = [str(i) for i in blob["ids"]]
        matrix = blob["vectors"].astype("float32")
    if model != vectors.EMBEDDING_MODEL:
        sys.exit(
            f"cached query vectors were made with {model!r} but the app now "
            f"uses {vectors.EMBEDDING_MODEL!r}. Re-run with --refresh."
        )
    cached = {i: v.tolist() for i, v in zip(ids, matrix, strict=True)}
    missing = [c["id"] for c in cases if c["id"] not in cached]
    if missing:
        sys.exit(f"no cached vector for {missing} — re-run with --refresh.")
    return cached


def matches(case: dict, hit: dict) -> bool:
    """Substring matching, so the set survives cosmetic heading edits."""
    title = (hit.get("title") or "").lower()
    section = (hit.get("section") or "").lower()
    want_title = (case.get("title_contains") or "").lower()
    want_section = (case.get("section_contains") or "").lower()
    return want_title in title and want_section in section


def evaluate(cases: list[dict], cached: dict, *, verbose: bool) -> int:
    vectors.open_vectors()

    ranks: list[int | None] = []
    none_ok = none_total = 0
    failures: list[tuple[dict, list[dict]]] = []

    for case in cases:
        hits = vectors.search(
            CTX, query=case["question"], dense_query=cached[case["id"]], limit=K
        )
        if terms := case.get("expect_absent_terms"):
            none_total += 1
            blob = " ".join((h.get("text") or "") for h in hits).lower()
            present = [t for t in terms if t.lower() in blob]
            if present:
                failures.append((case, hits))
            else:
                none_ok += 1
            continue

        rank = next((i + 1 for i, h in enumerate(hits) if matches(case, h)), None)
        ranks.append(rank)
        if rank is None:
            failures.append((case, hits))

    found = [r for r in ranks if r is not None]
    recall_at_1 = sum(1 for r in found if r == 1) / len(ranks) if ranks else 0.0
    recall_at_k = len(found) / len(ranks) if ranks else 0.0
    mrr = sum(1 / r for r in found) / len(ranks) if ranks else 0.0

    print("RETRIEVAL EVAL")
    print("=" * 60)
    print(f"  corpus points        {vectors.count()}")
    print(f"  answerable cases     {len(ranks)}")
    print(f"  recall@1             {recall_at_1:.1%}")
    print(f"  recall@{K}             {recall_at_k:.1%}")
    print(f"  MRR                  {mrr:.3f}")
    print(f"  absence cases        {none_ok}/{none_total} "
          f"(top-{K} genuinely lacks the concept)")

    if failures:
        print(f"\n  {len(failures)} failing case(s):")
        for case, hits in failures:
            kind = (
                f"retrieved text contains {case['expect_absent_terms']}"
                if case.get("expect_absent_terms")
                else "not in top 5"
            )
            print(f"    {case['id']}: {kind}")
            print(f"      q: {case['question']}")
            if verbose:
                for h in hits:
                    print(f"        {h['score']:.3f} {(h.get('title') or '')[:40]:40} "
                          f"§{h.get('section') or '-'}")

    vectors.close_vectors()
    # A retrieval regression should fail the build, not be noted in passing.
    return 0 if not failures else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="re-embed the questions")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    cases = load_cases()
    if args.refresh:
        asyncio.run(refresh_cache(cases))
        return 0
    return evaluate(cases, load_cache(cases), verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
