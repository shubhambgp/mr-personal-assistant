"""RepContext — the identity every tool is scoped to.

This is the single carrier of "who is asking". It is built ONLY from a verified
JWT (see app/core/security.py) and is never assembled from a request body,
query string or header. Every tool closes over it; no tool accepts a chair_id
or rep_id as a parameter.

That invariant is the whole security model. It is stated here, in CLAUDE.md,
and in app/tools/sql_tools.py, because a future provider (RAG retrieval, an
email MCP server) will need scoping too, and a single audited context object is
what stops each new provider from inventing its own weaker rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RepContext:
    """Immutable. Frozen so a tool handler cannot mutate the scope it runs under."""

    chair_id: int
    rep_code: int
    rep_name: str

    # Reserved for the next milestones. Both will be populated from the same
    # verified token, never from client input:
    #   email_account -> which mailbox the MCP email provider may read
    #   doc_scope     -> which document collections RAG retrieval may search
    email_account: str | None = None
    doc_scope: tuple[str, ...] = field(default_factory=tuple)

    def cache_key(self) -> str:
        """Prompt-cache partition. Per-rep, so one rep's cached prefix can never
        be served to another."""
        return f"qorvexa-chair-{self.chair_id}"
