"""Request guards: per-client rate limiting and bounded inference concurrency."""
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
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


class InferenceRunner:
    """Bounded worker pool with a bounded queue and a per-request timeout (C24).

    At most `workers` calls run at once and `workers + max_queue` are admitted in total; a caller
    that cannot be admitted within `wait_seconds` gets 503, and one whose call outlasts
    `timeout_seconds` gets 504. Python cannot cancel a running call, so a timed-out call keeps its
    admission slot until it finishes: stuck inference cannot pile up unbounded work.
    """

    def __init__(self, workers: int, max_queue: int, wait_seconds: float, timeout_seconds: float) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="inference")
        self._admission = threading.BoundedSemaphore(max(1, workers) + max(0, max_queue))
        self.wait_seconds = wait_seconds
        self.timeout_seconds = timeout_seconds

    def run(self, fn, *args, **kwargs):
        if not self._admission.acquire(timeout=self.wait_seconds):
            raise HTTPException(status_code=503, detail="inference busy, retry shortly", headers={"Retry-After": "2"})
        try:
            future = self._executor.submit(fn, *args, **kwargs)
        except BaseException:
            self._admission.release()
            raise
        future.add_done_callback(lambda _: self._admission.release())
        try:
            return future.result(timeout=self.timeout_seconds)
        except FutureTimeout as error:
            raise HTTPException(status_code=504, detail="inference timed out") from error

