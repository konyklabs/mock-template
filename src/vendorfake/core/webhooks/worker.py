"""One thread, one queue, and the handshake that makes ``advance()`` honest.

**A job is atomic with respect to :meth:`quiesce`**, which returns only when the queue is
empty *and* no job is mid-flight -- a delivery job's last act is to register the next
retry, so a ``quiesce`` returning before that would report a settled unit whose retry is
unscheduled. ``Clock.advance(ms, settle=worker.quiesce)`` is the other half, calling it
before each re-scan. Deliveries are strictly ordered because there is exactly **one**
worker thread and one FIFO queue, which is what gives the delivery log one writer. A job
runs with this module's condition **released**, so the only lock nesting is worker
condition then clock; one that raises is captured, not fatal.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

__all__ = ["DeliveryWorker"]

Job = Callable[[], None]

_JOIN_TIMEOUT_SECONDS = 5.0
"""How long :meth:`DeliveryWorker.stop` waits, so shutdown is not hostage to a
subscriber; the thread is a daemon."""


class DeliveryWorker:
    """A single-threaded serial executor with a quiescence handshake, knowing about jobs
    rather than deliveries."""

    __slots__ = ("_busy", "_cond", "_failures", "_generation", "_name", "_queue", "_stopped", "_thread")

    def __init__(self, *, name: str = "vendorfake-delivery") -> None:
        self._cond = threading.Condition(threading.Lock())
        self._queue: deque[Job] = deque()
        self._busy = False
        self._stopped = False
        self._generation = 0
        self._failures: list[str] = []
        self._name = name
        self._thread: threading.Thread | None = None

    def submit(self, job: Job) -> None:
        """Append a job; the thread starts lazily, so a unit that never delivers spawns none."""
        with self._cond:
            if self._stopped:
                raise RuntimeError("the delivery worker has been stopped and cannot accept new work")
            self._queue.append(job)
            self._generation += 1
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
                self._thread.start()
            self._cond.notify_all()

    @property
    def generation(self) -> int:
        with self._cond:
            return self._generation

    @property
    def pending(self) -> int:
        with self._cond:
            return len(self._queue)

    @property
    def busy(self) -> bool:
        with self._cond:
            return self._busy

    def failures(self) -> tuple[str, ...]:
        with self._cond:
            return tuple(self._failures)

    def quiesce(self, timeout: float | None = None) -> bool:
        """Block until the queue is empty and no job is running; False when ``timeout`` seconds
        elapsed first. From the worker thread it would deadlock, so it raises instead."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            if self._thread is not None and threading.current_thread() is self._thread:
                raise RuntimeError("quiesce() called from the delivery worker; it would wait for itself")
            while self._queue or self._busy:
                if deadline is None:
                    self._cond.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True

    def stop(self) -> None:
        """Finish the queue, then let the thread exit; idempotent, refusing only *new* work."""
        with self._cond:
            if self._stopped:
                thread = self._thread
            else:
                self._stopped = True
                thread = self._thread
                self._cond.notify_all()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=_JOIN_TIMEOUT_SECONDS)

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._stopped:
                    self._cond.wait()
                if not self._queue:
                    # `_thread` still points here on purpose: `quiesce`'s guard compares identity.
                    self._cond.notify_all()
                    return
                job = self._queue.popleft()
                self._busy = True
            try:
                job()
            except Exception as exc:
                with self._cond:
                    self._failures.append(f"{type(exc).__name__}: {exc}")
            finally:
                # The job has returned, so the next retry's timer is registered
                # before `busy` clears and a waiting `quiesce()` sees a settled worker.
                with self._cond:
                    self._busy = False
                    self._generation += 1
                    self._cond.notify_all()
