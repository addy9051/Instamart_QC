"""Request guards: per-client rate limiting and bounded inference concurrency."""
import threading
import time
from collections import deque
from contextlib import contextmanager

from fastapi import HTTPException


class RateLimiter:
    """Sliding-window limiter keyed by client id (in-process; use a shared store when scaled out)."""

    def __init__(self, per_minute: int, max_clients: int = 10_000) -> None:
        self.per_minute = per_minute
        self.max_clients = max_clients
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, client: str) -> None:
        if self.per_minute <= 0:
            return
        now = time.monotonic()
        with self._lock:
            if len(self._hits) >= self.max_clients and client not in self._hits:
                cutoff = now - 60
                for key in [k for k, v in self._hits.items() if not v or v[-1] < cutoff]:
                    del self._hits[key]
            hits = self._hits.setdefault(client, deque())
            while hits and hits[0] <= now - 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                retry = max(1, int(60 - (now - hits[0])) + 1)
                raise HTTPException(status_code=429, detail="rate limit exceeded", headers={"Retry-After": str(retry)})
            hits.append(now)


class InferenceGate:
    """Caps concurrent model inference so image uploads cannot exhaust CPU/GPU."""

    def __init__(self, max_concurrent: int, wait_seconds: float) -> None:
        self._semaphore = threading.BoundedSemaphore(max(1, max_concurrent))
        self.wait_seconds = wait_seconds

    @contextmanager
    def slot(self):
        if not self._semaphore.acquire(timeout=self.wait_seconds):
            raise HTTPException(status_code=503, detail="inference busy, retry shortly", headers={"Retry-After": "2"})
        try:
            yield
        finally:
            self._semaphore.release()

