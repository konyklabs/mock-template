"""Clock and timer scheduler, in real and virtual modes.

FOR: letting behaviour that a vendor measures in hours be observed in a
millisecond. A subscriber that is down gets retried on Square's documented
twenty-four-hour schedule; a test that wants to see all twelve attempts cannot
wait a day, so it advances a virtual clock instead and every timer that became
due fires at once.

INVARIANT: ``advance()`` re-scans after *every* firing, never once per call.
A webhook retry schedules the next retry from inside its own timer callback,
so a port that snapshots the due list and fires that batch would report four
delivery attempts where the contract says twelve -- and would report it
silently, which is worse than hanging. The reference states the same rule in
one line at ``packages/core/src/time/clock.ts``: "Re-scan after each firing: a
timer may schedule another due timer."

How the two modes drive timers, because they drive them differently
--------------------------------------------------------------------
Both modes record every timer in the same map, so ``pending()`` is accurate in
either. What differs is who fires them.

**Real mode.** ``after()`` additionally arms a ``threading.Timer``, whose
thread invokes the callback. The callback therefore runs on a background
thread, off the request path. The timer thread is a daemon: this is the
reference's ``handle.unref()`` -- "Do not hold the process open for a pending
webhook retry" -- and without it a process with one scheduled retry would
refuse to exit.

**Virtual mode.** No real timer is ever armed; nothing fires until somebody
calls ``advance()``, which fires due timers *on the calling thread*.

That difference is the whole of the tension between ``advance()`` and a
background delivery worker, and ``settle`` is how it is resolved. When a
delivery runs on a worker thread, the timer callback only *enqueues* the
attempt; the next retry's timer is registered by the worker after the attempt
completes, which is after ``advance()`` would otherwise have re-scanned, found
nothing and returned. So ``advance(ms, settle=...)`` calls ``settle`` before
each re-scan, and the dispatcher passes its own quiesce function: worker idle
and queue empty. The re-scan then sees the retry the worker just scheduled and
the cascade collapses into one call, exactly as it does in the reference's
single-threaded loop. A caller with no worker passes nothing and the loop is
the reference's loop unchanged.

Callbacks are always invoked with the internal lock released. A delivery
callback blocks on a queue; holding the clock's lock across it would let a
worker that wants to schedule a retry deadlock against the ``advance()`` that
is waiting for it.
"""

from __future__ import annotations

import math
import threading
import time as _wall
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

__all__ = ["Clock", "ClockMode", "PendingTimer"]

ClockMode = Literal["real", "virtual"]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class PendingTimer:
    """One scheduled timer as ``/__unit/info`` and ``drain()`` see it.

    ``label`` is load-bearing, not decoration: the webhook dispatcher finds its
    own timers by label prefix when it decides whether the unit has settled.
    """

    id: int
    label: str
    due_in_ms: float


@dataclass(frozen=True, slots=True)
class _Timer:
    id: int
    due_at: float
    fn: Callable[[], None]
    label: str


def _wall_now_ms() -> float:
    return _wall.time() * 1000.0


def _parse_start(start: str) -> float:
    """Parse an RFC 3339 instant into epoch milliseconds.

    A value with no offset is read as UTC rather than as local time: a profile
    that pins a start instant is pinning a moment, and reading it as local time
    would make the same profile produce different timestamps on two machines.
    """
    parsed = datetime.fromisoformat(start)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (parsed - _EPOCH).total_seconds() * 1000.0


def _iso(epoch_ms: float, timespec: str) -> str:
    moment = _EPOCH + timedelta(milliseconds=math.floor(epoch_ms))
    return moment.isoformat(timespec=timespec).replace("+00:00", "Z")


class Clock:
    """The only source of time in the core, and the only timer scheduler."""

    def __init__(self, mode: ClockMode = "real", start: str | None = None) -> None:
        self.mode: ClockMode = mode
        self._lock = threading.RLock()
        self._virtual_now: float = _parse_start(start) if start else _wall_now_ms()
        self._next_id = 1
        self._timers: dict[int, _Timer] = {}
        self._handles: dict[int, threading.Timer] = {}

    # -- reading time -------------------------------------------------------

    def now(self) -> float:
        """Epoch milliseconds. Milliseconds throughout: the retry schedule and
        its time scale are expressed in them and a unit change here would move
        every documented interval."""
        if self.mode == "virtual":
            with self._lock:
                return self._virtual_now
        return _wall_now_ms()

    def iso_ms(self, offset_ms: float = 0.0) -> str:
        """RFC 3339 with milliseconds -- the format Square uses for ``created_at``."""
        return _iso(self.now() + offset_ms, "milliseconds")

    def iso_seconds(self, offset_ms: float = 0.0) -> str:
        """RFC 3339 truncated to seconds -- the format Square uses for ``expires_at``.

        Truncated, not rounded: the reference strips the milliseconds with a
        regex, and a consumer asserting
        ``^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$`` must never be handed a
        value that rounded up into the next second.
        """
        return _iso(self.now() + offset_ms, "seconds")

    # -- scheduling ---------------------------------------------------------

    def after(self, delay_ms: float, label: str, fn: Callable[[], None]) -> int:
        """Schedule ``fn`` and return the timer id.

        The timer is recorded in both modes so ``pending()`` tells the truth in
        both; only real mode additionally arms a thread to fire it.
        """
        delay = max(0.0, float(delay_ms))
        with self._lock:
            timer_id = self._next_id
            self._next_id += 1
            self._timers[timer_id] = _Timer(id=timer_id, due_at=self.now() + delay, fn=fn, label=label)
            if self.mode == "real":
                handle = threading.Timer(delay / 1000.0, self._fire_real, args=(timer_id,))
                # The reference's `unref()`: a pending webhook retry must not
                # keep the process alive.
                handle.daemon = True
                self._handles[timer_id] = handle
                handle.start()
        return timer_id

    def cancel(self, timer_id: int) -> None:
        """Forget a timer. Cancelling an unknown or already-fired id is a no-op."""
        with self._lock:
            self._timers.pop(timer_id, None)
            handle = self._handles.pop(timer_id, None)
        if handle is not None:
            handle.cancel()

    def _fire_real(self, timer_id: int) -> None:
        with self._lock:
            self._handles.pop(timer_id, None)
            timer = self._timers.pop(timer_id, None)
        if timer is None:
            return
        # Outside the lock, deliberately: see the module docstring.
        timer.fn()

    def advance(self, ms: float, *, settle: Callable[[], None] | None = None) -> int:
        """Virtual mode only: move time forward and fire everything that came due.

        Returns the number of timers fired. The earliest-due timer fires first,
        ties broken by scheduling order, and the due set is recomputed after
        every firing -- which is what lets a timer schedule another already-due
        timer and still be fired inside the same call.

        ``settle`` runs before each re-scan. Pass the dispatcher's quiesce
        function when deliveries run on a worker thread, so that a retry the
        worker is about to schedule is registered before the loop looks again.
        """
        if self.mode != "virtual":
            raise ValueError('clock.advance requires clock.mode="virtual"')
        if not math.isfinite(ms) or ms < 0:
            raise ValueError(f"clock.advance requires a finite, non-negative number of milliseconds, got {ms!r}")
        with self._lock:
            self._virtual_now += float(ms)
        fired = 0
        while True:
            if settle is not None:
                settle()
            with self._lock:
                due = sorted(
                    (t for t in self._timers.values() if t.due_at <= self._virtual_now),
                    key=lambda t: (t.due_at, t.id),
                )
                if not due:
                    return fired
                timer = due[0]
                del self._timers[timer.id]
            timer.fn()
            fired += 1

    def pending(self) -> list[PendingTimer]:
        """Every timer still scheduled, in scheduling order."""
        with self._lock:
            now = self.now()
            return [PendingTimer(id=t.id, label=t.label, due_in_ms=t.due_at - now) for t in self._timers.values()]

    def clear_all(self) -> None:
        """Drop every timer. Called on shutdown; a fake must not outlive its test."""
        with self._lock:
            ids = list(self._timers)
        for timer_id in ids:
            self.cancel(timer_id)
