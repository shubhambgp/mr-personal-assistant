"""The single tool registry the app uses.

Adding a tool family means adding a provider here — not touching agent.py, not
touching the API layer. The two stubs are wired in deliberately: composing with
them today is what proves the seam works before there is anything behind it.
"""

from __future__ import annotations

from .tools.agenda_tools import AgendaToolProvider
from .tools.base import ToolRegistry
from .tools.mcp_tools import McpToolProvider
from .tools.rag_tools import RagToolProvider
from .tools.sql_tools import SqlToolProvider

registry = ToolRegistry(
    [
        SqlToolProvider(),
        RagToolProvider(),
        # Contributes only the task tools when Google is unconfigured, and one
        # extra explain-yourself tool when configured but not connected — so a
        # fresh checkout and CI compose exactly as they did before.
        AgendaToolProvider(),
        McpToolProvider(),  # still the empty seam — see the module docstring
    ]
)
