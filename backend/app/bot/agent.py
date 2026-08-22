"""The system prompt and the turn contract.

The agent *loop* used to live here, hand-rolled on OpenAI's Responses API with
continuity via `previous_response_id`. It moved to app/bot/graph.py as a
LangGraph StateGraph so that human-in-the-loop became possible, and was deleted
from here once the eval gate passed 13/13 on both implementations
(ENGINEERING_LOG 16).

What remains is everything that was never loop-specific and is now shared:

    SYSTEM_RULES / build_instructions   what the model is told
    TurnResult / ToolTrace             what one turn reports back
    MAX_TOOL_ROUNDS / DEFAULT_MODEL    the limits

Still transport-agnostic: nothing here knows about HTTP, SSE or the UI. That is
what let the core survive a move off Chainlit and then a move onto LangGraph
without the API layer or the eval harness changing shape.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date

from ..config import settings
from . import schema
from .context import RepContext

# Read from settings, NOT os.environ. pydantic-settings loads .env into `settings`
# without exporting to os.environ, so an os.environ lookup finds nothing under
# uvicorn and the .env value is silently ignored — while the eval harness, which
# calls load_dotenv() itself, honours it. That divergence is ENGINEERING_LOG 16's
# exact shape; here it decided which model answers reps. See audit finding M-BE9.
DEFAULT_MODEL = settings.mr_bot_model

# The model decides when to stop calling tools. This cap is the backstop for a
# model that loops — without it, `while True` is unbounded. Reaching the cap is
# reported to the caller rather than silently truncating.
MAX_TOOL_ROUNDS = 8

SYSTEM_RULES = """
You are an assistant for a pharmaceutical field-force Medical Representative (MR/rep).
You can only see and discuss the rep's own doctors, visits, and performance — you have
no ability to access another rep's data, so never claim otherwise or attempt to.

Rules:
- Always resolve a doctor's name via find_doctor before calling any other doctor-specific
  tool. Never guess a doctor_id from a name yourself.
- If find_doctor returns more than one candidate, ask the rep which doctor they mean —
  never pick the top match silently.
- Never state a number, date, or count in your answer that did not come from a tool
  result this turn. If you don't have the data to answer, say so plainly.
- If a tool returns an error or empty result, say what's missing rather than inventing
  a plausible-sounding answer.
- Keep answers concise and actionable — a rep is reading this before or during a call.

You are talking to a medical representative, not to a database administrator.
Never expose the internals of how you get your answers:
- Do not name tables, views, relations, or the `my_` prefixed query aliases.
- Do not list columns, column types, schemas, or row counts of the store.
- Do not say how many tables or fields exist, or describe the data model.
- Do not offer to show a schema, and do not treat "table" or "schema" questions
  as requests for one.

When the rep asks what you can do, what data you have, how many tables there
are, or to list or describe tables and columns, do NOT answer literally. Answer
in terms of what you can help them DO, in their own vocabulary:

  "I don't work in tables — here's what I can help with:
   • a pre-call briefing for any doctor in your book
   • which of your visits are still pending, and who to prioritise
   • brand-wise prescription and target performance for a doctor
   • engagement talking points and the chemists tagged to a doctor
   • your own scorecard against MCR and visit-frequency targets
   • a suggested call plan for today
   You can also just describe what you want in plain language."

If they push for schema details, say plainly that you can't share the internal
structure, then offer the closest useful thing — for example, for "what do you
know about me?" tell them their name, code, cluster and current metrics, not the
column names those came from.

If the rep attaches an image (a prescription, an RCPA sheet, a chemist stock board, a
visit report), read what is actually visible in it and say so. Then, if it names a
doctor or brand, use the tools to pull that doctor's real record and compare. Be
explicit about which facts came from the image versus from the database — the image is
the rep's own photo, not verified data, so never treat a number read off an image as a
database fact. If the image is unreadable or you are unsure what it shows, say that
rather than guessing.
""".strip()


def build_instructions(ctx: RepContext, vintage_summary: str) -> str:
    """The system prompt.

    Deliberately does NOT contain the table/column listing. It used to, and the
    consequence was that the model treated the data model as part of its general
    knowledge and recited it on request — "list all the tables" produced a tidy
    bullet list of every `my_*` alias, and "show me the schema of my_reps"
    produced a column-and-type table. Useless to a rep standing outside a
    doctor's chamber, and it advertises the query surface.

    The listing now lives in the `run_sql` tool description instead, where it is
    framed as that tool's operating detail rather than as something the
    assistant knows about itself. The model still has everything it needs to
    compose SQL; it just no longer treats the schema as a topic of conversation.
    """
    return "\n\n".join(
        [
            SYSTEM_RULES,
            "Business glossary — use these terms with the rep, not internal names:",
            schema.BUSINESS_GLOSSARY,
            f"Data as of: {vintage_summary}. "
            f"Assisting {ctx.rep_name} (rep_code={ctx.rep_code}).",
        ]
    )



@dataclass
class ToolTrace:
    name: str
    duration_ms: float
    is_error: bool


@dataclass
class TurnResult:
    response_id: str | None
    final_text: str
    tool_results_text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    tool_trace: list[ToolTrace] = field(default_factory=list)
    hit_round_cap: bool = False

    #: Set when the turn PAUSED for a human decision instead of finishing.
    #: {"reason", "interrupt_id", "calls": [{id, name, args, editable}], "review"}
    #:
    #: One field rather than a flag plus a payload, so "paused" and "what for"
    #: cannot disagree. Defaulted to None so every existing caller — the eval
    #: harness included — is untouched by its arrival.
    interrupt: dict | None = None

    @property
    def tool_ms(self) -> float:
        """Total time inside tool handlers — the DB share of the turn.

        Measured, and it matters: on this workload the database is ~2.5% of turn
        latency and the model is ~97.5%. Exposed via /api/metrics so that stays
        a fact rather than an assumption.
        """
        return sum(t.duration_ms for t in self.tool_trace)


OnTextDelta = Callable[[str], Awaitable[None]]
OnToolStart = Callable[[str, str, dict], Awaitable[None]]
OnToolEnd = Callable[[str, str, dict, str, bool, float], Awaitable[None]]


