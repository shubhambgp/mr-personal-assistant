"""One shared AsyncOpenAI client.

Built lazily and cached: constructing it per request would throw away the
connection pool and the HTTP/2 session, which matters when a turn makes several
round-trips.
"""

from __future__ import annotations

from functools import lru_cache

from openai import AsyncOpenAI

from ..config import settings


@lru_cache(maxsize=1)
def get_client() -> AsyncOpenAI:
    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set — the chat endpoint cannot run without it."
        )
    return AsyncOpenAI(api_key=settings.openai_api_key)
