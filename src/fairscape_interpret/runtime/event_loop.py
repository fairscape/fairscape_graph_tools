"""Persistent per-process event loop for running async coroutines from sync code.

Needed by the Celery worker: PydanticAI caches an HTTP client against the
event loop it was first used on, so creating a fresh loop per task raises
'bound to a different event loop'. This module gives the worker a single,
reused loop. The CLI should prefer `asyncio.run(...)` directly.
"""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine

_worker_loop: asyncio.AbstractEventLoop | None = None


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run an async coroutine using a single, persistent event loop per worker process.

    This prevents 'RuntimeError: bound to a different event loop' caused by
    PydanticAI's globally cached HTTP client.
    """
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
    return _worker_loop.run_until_complete(coro)
