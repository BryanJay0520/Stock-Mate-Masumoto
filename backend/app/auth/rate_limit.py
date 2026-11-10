"""Login rate limiting (MVP, per-process).

A sliding window of failed-attempt timestamps per ``username|client`` key.
This is intentionally in-memory: the MVP runs a single process (D17 applies
the same thinking to the scheduler). If we ever run multiple workers, this
moves to a shared store (documented in DECISIONS.md).
"""

import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self) -> None:
        self._attempts: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> bool:
        """Record one attempt. Return False when the limit is exceeded."""
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts.setdefault(key, deque())
            while attempts and attempts[0] <= now - window_seconds:
                attempts.popleft()
            if len(attempts) >= limit:
                return False
            attempts.append(now)
            return True

    def reset(self, key: str) -> None:
        """Clear the counter (called after a successful login)."""
        with self._lock:
            self._attempts.pop(key, None)
