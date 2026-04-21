"""Exponential-backoff retry wrapper for PydanticAI Agent.run() calls.

Targets transient overload conditions (HTTP 529, rate-limit errors) and
re-raises anything else immediately.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

MAX_API_RETRIES = 5
API_RETRY_BASE_DELAY = 10.0  # seconds; doubles each retry


async def run_agent_with_retry(agent, prompt, retries: int = MAX_API_RETRIES, base_delay: float = API_RETRY_BASE_DELAY):
    """Run agent.run() with exponential backoff on overloaded/rate-limit errors."""
    for attempt in range(retries + 1):
        try:
            return await agent.run(prompt)
        except Exception as e:
            err_str = str(e)
            is_retryable = (
                "529" in err_str
                or "overloaded" in err_str.lower()
                or "rate" in err_str.lower()
            )
            if not is_retryable or attempt == retries:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning(
                f"API overloaded (attempt {attempt + 1}/{retries + 1}), "
                f"retrying in {delay:.0f}s: {err_str[:120]}"
            )
            await asyncio.sleep(delay)
