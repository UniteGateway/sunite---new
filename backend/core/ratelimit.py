"""In-process token-bucket limiter (single instance). Replace with Redis for multi-instance deployments."""
from __future__ import annotations
import threading, time


class TokenBucket:
    def __init__(self, capacity: int, refill_per_sec: float, clock=time.monotonic):
        self.capacity, self.rate, self.clock = capacity, refill_per_sec, clock
        self._b: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        with self._lock:
            now = self.clock()
            tokens, last = self._b.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            ok = tokens >= cost
            self._b[key] = (tokens - cost if ok else tokens, now)
            if len(self._b) > 50_000:      # bound memory
                self._b.pop(next(iter(self._b)))
            return ok
