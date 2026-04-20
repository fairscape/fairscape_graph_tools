"""Sliding-window async rate limiter for LLM API calls."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

logger = logging.getLogger(__name__)


class AsyncRateLimiter:
    """Sliding-window rate limiter for async contexts.

    Allows at most `max_requests` within any rolling `window_seconds` period.
    Callers await `acquire()` before making an API call.
    """

    def __init__(self, max_requests: int = 2, window_seconds: float = 10.0):
        self._max = max_requests
        self._window = window_seconds
        self._lock = asyncio.Lock()
        self._timestamps: deque = deque()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            # Evict timestamps outside the window
            while self._timestamps and (now - self._timestamps[0]) >= self._window:
                self._timestamps.popleft()
            # If at capacity, sleep until the oldest timestamp exits the window
            if len(self._timestamps) >= self._max:
                sleep_for = self._window - (now - self._timestamps[0])
                if sleep_for > 0:
                    logger.info(f"Rate limiter: sleeping {sleep_for:.1f}s")
                    await asyncio.sleep(sleep_for)
                self._timestamps.popleft()
            self._timestamps.append(time.monotonic())
