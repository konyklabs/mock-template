"""One thread, one queue, and the handshake that makes ``advance()`` honest.

FOR: running delivery attempts off the request path, in an order that is
*determined* rather than merely likely, and giving a caller a way to ask "is
everything you were going to do done?" that cannot answer yes too early.

INVARIANT: **a job is atomic with respect to :meth:`quiesce`.** ``quiesce()``
returns only when the queue is empty *and* no job is mid-flight. It is not
enough for the queue to be empty: a delivery job's last act is to register the
next retry on the clock, and a ``quiesce`` that returned between the send
completing and that registration would report a settled unit whose next retry
has not yet been scheduled.

WHY THAT MATTERS ENOUGH TO BUILD A WORKER AROUND IT -- and the tension the
critique named. ``Clock.advance()`` fires a due timer, then re-scans, which is
what lets a retry schedule the next retry and have the whole cascade collapse
into one call. In the reference that works because the timer callback *is* the
delivery: JavaScript's ``await`` inside ``advance()``'s loop runs the attempt to
completion, and the next timer exists before the re-scan. Move deliveries onto
a worker thread and the timer callback merely enqueues; the next retry's timer
is registered by the worker *after* ``advance()`` has already re-scanned, found
nothing and returned. A twelve-attempt cascade then reports four, silently and
intermittently, which is worse than a hang because a hang is noticed.

The resolution is a handshake and not a sleep: ``Clock.advance(ms,
settle=worker.quiesce)`` calls :meth:`quiesce` before each re-scan, so the
re-scan sees the timer the worker just registered. Both halves are needed --
without the worker's atomicity ``quiesce`` returns too early, and without
``advance``'s ``settle`` hook nobody calls it. ``Clock`` already takes the
argument; the dispatcher passes it, and this module is the other end.

Deliveries are strictly ordered because there is exactly **one** worker thread
and one FIFO queue. That is also what makes the delivery log have exactly one
writer: chaos outcomes that never touch the sink -- ``skipped`` and ``dropped``
-- are submitted as terminal jobs to this same queue rather than recorded from
the request thread. Two writers would renumber ``dlv_NNNNN`` and reorder
``deliveries()``, and the reference's own chaos tests assert a delivery order.

LOCK ORDER, stated because there are four locks in play. A job runs with this
module's condition **released**, and the dispatcher's job then takes the
condition again to record and reschedule, acquiring the clock's lock inside it.
So the only nesting that ever occurs is ``worker condition -> clock lock``, and
``advance()`` invokes both ``settle`` and every timer callback with the clock
lock released. There is no path that takes them in the other order.

A job that raises does not kill the worker: the exception is captured and the
queue keeps moving, because a dispatcher whose delivery thread died would
answer ``drain()`` forever and look like a hung subscriber. :meth:`failures`
publishes what was caught so a test can assert nothing was.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

__all__ = ["DeliveryWorker"]

Job = Callable[[], None]

_JOIN_TIMEOUT_SECONDS = 5.0
"""How long :meth:`DeliveryWorker.stop` waits for the thread to finish its
current job. Bounded rather than indefinite: shutdown must not be hostage to a
subscriber that is answering slowly, and the thread is a daemon so a leftover
one cannot hold the process open."""


class DeliveryWorker:
    """A single-threaded serial executor with a quiescence handshake.

    Deliberately generic: it knows about jobs, not about deliveries. That is
    what lets the dispatcher submit an HTTP attempt and a "record this as
    dropped" terminal job to the same queue and get one writer for free.
    """

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

    # -- submission ---------------------------------------------------------

    def submit(self, job: Job) -> None:
        """Append a job and make sure a thread exists to run it.

        The thread is started lazily, on the first submission, so a unit whose
        vendor has no webhooks -- or a test that never delivers -- never spawns
        one. Starting it while holding the condition is safe and deliberate:
        the new thread's first act is to acquire the same condition, so it
        cannot observe a half-built queue.
        """
        with self._cond:
            if self._stopped:
                raise RuntimeError("the delivery worker has been stopped and cannot accept new work")
            self._queue.append(job)
            self._generation += 1
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
                self._thread.start()
            self._cond.notify_all()

    # -- observation --------------------------------------------------------

    @property
    def generation(self) -> int:
        """Bumped on every submission and on every completed job.

        A caller that samples this, does some work, and samples it again knows
        whether the worker moved underneath it. ``drain()`` does not need it --
        :meth:`quiesce` is a stronger statement -- but a diagnostic that wants
        to say "the worker was busy the whole time" does.
        """
        with self._cond:
            return self._generation

    @property
    def pending(self) -> int:
        """Jobs queued but not started. Excludes one in flight; see :attr:`busy`."""
        with self._cond:
            return len(self._queue)

    @property
    def busy(self) -> bool:
        """True while a job is running."""
        with self._cond:
            return self._busy

    def failures(self) -> tuple[str, ...]:
        """Every job that raised, described, oldest first."""
        with self._cond:
            return tuple(self._failures)

    # -- the handshake ------------------------------------------------------

    def quiesce(self, timeout: float | None = None) -> bool:
        """Block until the queue is empty and no job is running.

        Returns True when that state was reached and False when ``timeout``
        seconds elapsed first. ``None`` waits indefinitely, which is correct
        for the virtual clock -- there is no wall-clock work to wait on, only a
        job that either completes or does not.

        Calling this from the worker thread itself would deadlock immediately
        and permanently, so it raises instead. That is not a hypothetical: a
        delivery job that called ``drain()`` would do exactly it.
        """
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

    # -- lifecycle ----------------------------------------------------------

    def stop(self) -> None:
        """Finish the queue, then let the thread exit. Idempotent.

        Everything already submitted still runs: a ``stop`` that discarded
        queued deliveries would make ``Unit.stop``'s drain conditional on
        timing. What it refuses is *new* work.
        """
        with self._cond:
            if self._stopped:
                thread = self._thread
            else:
                self._stopped = True
                thread = self._thread
                self._cond.notify_all()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=_JOIN_TIMEOUT_SECONDS)

    # -- the thread ---------------------------------------------------------

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._stopped:
                    self._cond.wait()
                if not self._queue:
                    # `_thread` is deliberately left pointing at this finished
                    # thread: `quiesce`'s "am I the worker?" guard compares
                    # identity, and clearing it would let a job that called
                    # `quiesce` after the queue drained wait on itself.
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
                # The job has returned, so anything it scheduled -- notably the
                # next retry's timer -- is registered before `busy` clears and
                # a waiting `quiesce()` is allowed to see a settled worker.
                with self._cond:
                    self._busy = False
                    self._generation += 1
                    self._cond.notify_all()
