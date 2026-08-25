"""McpToolProvider — placeholder for tools served by external MCP servers.

NOT IMPLEMENTED, same rationale as rag_tools.py: the seam exists and is
exercised by the registry tests, the implementation does not.

The first target is email. The intended shape: read mcp_servers.yaml, connect
to each configured server, list its tools, and adapt each one into a ToolSpec
so the agent loop consumes it identically to a SQL tool. Then get_daily_plan
gains an email contributor and can say "Dr Sharma replied about the RCPA
figures this morning" alongside "2 visits short this month".

TWO RULES THIS MUST FOLLOW (see CLAUDE.md):

1. Scope. A remote server will happily read whatever mailbox it is asked for.
   The mailbox comes from ctx.email_account — which is populated from the
   verified JWT — never from a tool parameter the model can set.
2. Name collisions. Remote tool names are outside our control. ToolRegistry
   already raises on a duplicate, so a server exposing `get_daily_plan` fails
   at startup rather than silently shadowing ours. Do not "fix" that by
   last-one-wins.

Untrusted input: an MCP server's tool descriptions and results are third-party
text that reaches the model. Treat them as data, not instructions.
"""

from __future__ import annotations

from pathlib import Path

from ..bot.context import RepContext
from .base import ToolSpec

CONFIG_PATH = Path(__file__).resolve().parents[2] / "etl" / "mcp_servers.yaml"


class McpToolProvider:
    name = "mcp"

    def get_tools(self, ctx: RepContext, db) -> list[ToolSpec]:
        del ctx, db  # nothing yet; signature is the contract
        return []
