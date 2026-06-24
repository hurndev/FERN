from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class _Window:
    timestamps: deque[float] = field(default_factory=deque)


class RateLimiter:
    """Sliding-window per-key rate limiter. Thread-safe (used from async context on one loop)."""

    def __init__(self) -> None:
        self._windows: dict[tuple[str, str], _Window] = {}
        self._lock = Lock()

    def allow(self, action: str, key: str, max_count: int, window_seconds: int) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        wk = (action, key)
        with self._lock:
            window = self._windows.get(wk)
            if window is None:
                window = _Window()
                self._windows[wk] = window

            while window.timestamps and window.timestamps[0] <= cutoff:
                window.timestamps.popleft()

            if len(window.timestamps) >= max_count:
                return False

            window.timestamps.append(now)
            return True

    def reset(self, action: str, key: str) -> None:
        with self._lock:
            self._windows.pop((action, key), None)