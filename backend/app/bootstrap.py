"""Opening and closing the app's resources, in one place.

This exists because the same bug has now happened twice. The eval harness drives
the agent core directly — no HTTP, no server — which is a genuinely useful
property, but it means the harness does not run the FastAPI lifespan. So every
resource the app opens at startup has to be opened by the harness too, and twice
it was not:

  * `OPENAI_API_KEY` (ENGINEERING_LOG 16): the eval called load_dotenv() and so
    found a key in the environment; uvicorn did not, and the first real turn
    failed with "Missing credentials".
  * `open_vectors()` (ENGINEERING_LOG 19): the app lifespan opened the vector
    store, the eval did not, and every retrieval case failed with the model
    politely reporting that the literature was unavailable.

Both were invisible in the failing output — the second one looked exactly like an
empty corpus. Two call sites listing resources by hand is one list too many, so
there is now one function and both callers use it.
"""

from __future__ import annotations

import logging

from .bot import db
from .bot.audit import audit_logger
from .bot.checkpointer import close_checkpointer, open_checkpointer
from .integrations.google.client import close_http
from .services.vectors import close_vectors, open_vectors
from .services.vectors import count as corpus_size

log = logging.getLogger(__name__)


async def open_resources(*, audit: bool = True) -> dict:
    """Opens every backing store. Returns a summary for the startup log.

    `audit=False` for the eval harness, which has no requests to attribute and
    no reason to append to the production audit log.
    """
    db.open_pools()
    if audit:
        audit_logger.start()
    await open_checkpointer()
    # Creates the collection if absent, so a fresh checkout serves an empty
    # corpus rather than erroring on the first question.
    open_vectors()
    return {"corpus_chunks": corpus_size()}


async def close_resources(*, audit: bool = True) -> None:
    if audit:
        await audit_logger.stop()
    await close_checkpointer()
    await close_http()
    close_vectors()
    db.close_pools()
