"""Sliding-window async rate limiter for LLM API calls."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)


class AsyncRateLimiter:
    """Sliding-window rate limiter that paces by both requests and tokens.

    Anthropic's primary rate limit is input-tokens-per-minute (ITPM). A
    request-only cap can still burst past that when prompts are large, so
    callers that know the prompt size should pass `tokens=` to `acquire()`.

    Enforced in the same rolling `window_seconds`:
      - at most `max_requests` calls, and
      - (if set) at most `max_tokens` total input tokens.

    If either cap would be exceeded, `acquire()` sleeps until the oldest
    entry falls out of the window and re-checks.
    """

    def __init__(
        self,
        max_requests: int = 4,
        window_seconds: float = 60.0,
        max_tokens: Optional[int] = None,
    ):
        self._max_requests = max_requests
        self._max_tokens = max_tokens
        self._window = window_seconds
        self._lock = asyncio.Lock()
        self._entries: deque = deque()  # (timestamp, tokens)

    async def acquire(self, tokens: int = 0):
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._entries and (now - self._entries[0][0]) >= self._window:
                    self._entries.popleft()

                total_reqs = len(self._entries)
                total_tokens = sum(t for _, t in self._entries)

                reqs_ok = total_reqs < self._max_requests
                tokens_ok = (
                    self._max_tokens is None
                    or total_tokens + tokens <= self._max_tokens
                )

                if reqs_ok and tokens_ok:
                    self._entries.append((now, tokens))
                    return

                sleep_for = self._window - (now - self._entries[0][0])
                sleep_for = max(sleep_for, 0.1)
                reasons = []
                if not reqs_ok:
                    reasons.append(
                        f"requests {total_reqs}/{self._max_requests}"
                    )
                if not tokens_ok:
                    reasons.append(
                        f"tokens {total_tokens + tokens}/{self._max_tokens}"
                    )
                logger.info(
                    f"Rate limiter sleeping {sleep_for:.1f}s "
                    f"(incoming={tokens} tok, {'; '.join(reasons)})"
                )
                await asyncio.sleep(sleep_for)
