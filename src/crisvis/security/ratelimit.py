"""Límites de frecuencia por cliente y globales, con ventana deslizante."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class SlidingWindow:
    def __init__(
        self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = max(1, int(limit))
        self.window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


class RateLimits:
    """Un límite por cliente y otro global para cada clase de petición."""

    def __init__(
        self, limits: dict[str, tuple[int, int, float]], clock: Callable[[], float] = time.monotonic
    ) -> None:
        # nombre -> (por cliente, global, ventana en segundos)
        self._per: dict[str, tuple[SlidingWindow, SlidingWindow]] = {
            name: (SlidingWindow(per, window, clock), SlidingWindow(total, window, clock))
            for name, (per, total, window) in limits.items()
        }

    def allow(self, name: str, client: str) -> bool:
        per, total = self._per[name]
        return per.allow(client) and total.allow("*")
