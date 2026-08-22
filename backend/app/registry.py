"""The single tool registry the app uses.

Adding a tool family means adding a provider here — not touching agent.py, not
touching the API layer. The stub is wired in deliberately: composing with
it today is what proves the seam works before there is anything behind it.
"""

from __future__ import annotations

from .tools.base import ToolRegistry
from .tools.mcp_tools import McpToolProvider
from .tools.sql_tools import SqlToolProvider

registry = ToolRegistry(
    [
        SqlToolProvider(),
        McpToolProvider(),  # still the empty seam — see the module docstring
    ]
)
