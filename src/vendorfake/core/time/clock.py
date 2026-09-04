"""Clock and timer scheduler, in real and virtual modes: lets behaviour a vendor measures in
hours be observed in a millisecond.

``advance()`` re-scans after *every* firing, never once per call, since a retry can schedule
itself again from inside its own timer callback. Real mode arms a daemon ``threading.Timer`` per
timer; virtual mode only fires timers on the calling thread, via ``advance()``. Callbacks run
with the internal lock released, so a blocking callback cannot deadlock a worker.
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
    """One scheduled timer as ``/__unit/info`` and ``drain()`` see it; ``label``
    is load-bearing since the webhook dispatcher matches timers by label prefix.
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
    """Parse an RFC 3339 instant into epoch milliseconds; no offset means UTC, not local time."""
    parsed = datetime.fromisoformat(start)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (parsed - _EPOCH).total_seconds() * 1000.0


def _iso(epoch_ms: float, timespec: str) -> str:
    moment = _EPOCH + timedelta(milliseconds=math.floor(epoch_ms))
    return moment.isoformat(timespec=timespec).replace("+00:00", "Z")


class Clock:
    def __init__(self, mode: ClockMode = "real", start: str | None = None) -> None:
        self.mode: ClockMode = mode
        self._lock = threading.RLock()
        self._virtual_now: float = _parse_start(start) if start else _wall_now_ms()
        self._next_id = 1
        self._timers: dict[int, _Timer] = {}
        self._handles: dict[int, threading.Timer] = {}

    def now(self) -> float:
        if self.mode == "virtual":
            with self._lock:
                return self._virtual_now
        return _wall_now_ms()

    def iso_ms(self, offset_ms: float = 0.0) -> str:
        """RFC 3339 with milliseconds -- the format Square uses for ``created_at``."""
        return _iso(self.now() + offset_ms, "milliseconds")

    def iso_seconds(self, offset_ms: float = 0.0) -> str:
        """RFC 3339 truncated, not rounded, to seconds -- the format Square uses for ``expires_at``."""
        return _iso(self.now() + offset_ms, "seconds")

    def after(self, delay_ms: float, label: str, fn: Callable[[], None]) -> int:
        """Schedule ``fn`` and return the timer id, recorded in both modes; only real mode arms a thread to fire it."""
        delay = max(0.0, float(delay_ms))
        with self._lock:
            timer_id = self._next_id
            self._next_id += 1
            self._timers[timer_id] = _Timer(id=timer_id, due_at=self.now() + delay, fn=fn, label=label)
            if self.mode == "real":
                handle = threading.Timer(delay / 1000.0, self._fire_real, args=(timer_id,))
                # A pending webhook retry must not keep the process alive.
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
        """Virtual mode only: advance time, fire everything now due, and return
        the count fired; ``settle``, if given, runs before each re-scan.
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
        with self._lock:
            now = self.now()
            return [PendingTimer(id=t.id, label=t.label, due_in_ms=t.due_at - now) for t in self._timers.values()]

    def clear_all(self) -> None:
        """Drop every timer. Called on shutdown; a fake must not outlive its test."""
        with self._lock:
            ids = list(self._timers)
        for timer_id in ids:
            self.cancel(timer_id)
