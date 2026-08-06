"""
Per-IP sliding-window rate limiter implemented with an in-memory store.

Usage as a FastAPI dependency::

    @router.get("/heavy", dependencies=[Depends(rate_limit(10, 60))])
    async def heavy_endpoint(): ...
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Callable

from fastapi import Depends, HTTPException, Request, status


class RateLimiter:
    """Thread-safe sliding-window counter keyed by client IP."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str, max_requests: int, window_seconds: float) -> bool:
        now = time.monotonic()
        timestamps = self._hits[key]
        cutoff = now - window_seconds
        self._hits[key] = [t for t in timestamps if t > cutoff]
        if len(self._hits[key]) >= max_requests:
            return False
        self._hits[key].append(now)
        return True


_limiter = RateLimiter()


def rate_limit(max_requests: int = 60, window_seconds: float = 60.0) -> Callable:
    """Return a FastAPI dependency that enforces the given rate limit."""

    async def _check(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        if not _limiter.is_allowed(client_ip, max_requests, window_seconds):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Try again later.",
            )

    return Depends(_check)
