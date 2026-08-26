"""What the clock guarantees, in both modes.

The load-bearing test in this file is the re-scan one. A webhook retry
schedules the next retry from inside its own timer callback, so an
``advance()`` that snapshots the due list once and fires that batch reports
four delivery attempts where the contract says twelve -- and reports it without
failing, which is the failure mode this project can least afford.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time

import pytest

from vendorfake.core.time.clock import Clock

RFC3339_MS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
RFC3339_SECONDS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def virtual(start: str = "2026-01-01T00:00:00.000Z") -> Clock:
    return Clock("virtual", start)


class TestTimestamps:
    def test_iso_ms_is_rfc3339_with_milliseconds(self) -> None:
        assert virtual().iso_ms() == "2026-01-01T00:00:00.000Z"
        assert RFC3339_MS.match(Clock("real").iso_ms())

    def test_iso_seconds_is_rfc3339_truncated_to_seconds(self) -> None:
        assert virtual().iso_seconds() == "2026-01-01T00:00:00Z"
        assert RFC3339_SECONDS.match(Clock("real").iso_seconds())

    def test_iso_seconds_truncates_rather_than_rounds(self) -> None:
        clock = virtual("2026-01-01T00:00:00.999Z")
        assert clock.iso_ms() == "2026-01-01T00:00:00.999Z"
        assert clock.iso_seconds() == "2026-01-01T00:00:00Z"

    def test_offsets_are_applied_in_milliseconds(self) -> None:
        clock = virtual()
        assert clock.iso_ms(1500) == "2026-01-01T00:00:01.500Z"
        assert clock.iso_seconds(30 * 24 * 60 * 60 * 1000) == "2026-01-31T00:00:00Z"

    def test_a_start_without_an_offset_is_read_as_utc(self) -> None:
        assert Clock("virtual", "2026-01-01T00:00:00").iso_ms() == "2026-01-01T00:00:00.000Z"


class TestVirtualAdvance:
    def test_advance_is_refused_on_a_real_clock(self) -> None:
        with pytest.raises(ValueError, match=re.escape('clock.mode="virtual"')):
            Clock("real").advance(10)

    def test_advance_refuses_negative_and_non_finite(self) -> None:
        clock = virtual()
        with pytest.raises(ValueError):
            clock.advance(-1)
        with pytest.raises(ValueError):
            clock.advance(float("inf"))

    def test_a_timer_that_is_not_yet_due_does_not_fire(self) -> None:
        clock = virtual()
        fired: list[str] = []
        clock.after(100, "webhook:retry", lambda: fired.append("a"))
        assert clock.advance(99) == 0
        assert fired == []
        assert clock.advance(1) == 1
        assert fired == ["a"]

    def test_a_zero_delay_timer_is_due_at_the_next_advance(self) -> None:
        clock = virtual()
        fired: list[str] = []
        clock.after(0, "webhook:retry", lambda: fired.append("a"))
        assert clock.advance(0) == 1
        assert fired == ["a"]

    def test_advance_rescans_so_a_timer_scheduled_from_a_timer_fires_in_the_same_call(self) -> None:
        # The exhaustion case: one initial attempt plus eleven retries, each
        # scheduled from inside the previous one, must collapse into a single
        # advance(). A batch-snapshot implementation reports 1 here.
        clock = virtual()
        attempts: list[int] = []

        def attempt(n: int) -> None:
            attempts.append(n)
            if n < 12:
                clock.after(0, "webhook:retry", lambda: attempt(n + 1))

        clock.after(0, "webhook:attempt", lambda: attempt(1))
        fired = clock.advance(0)

        assert attempts == list(range(1, 13))
        assert fired == 12
        assert clock.pending() == []

    def test_due_timers_fire_earliest_first_then_in_scheduling_order(self) -> None:
        clock = virtual()
        order: list[str] = []
        clock.after(30, "c", lambda: order.append("c"))
        clock.after(10, "a", lambda: order.append("a"))
        clock.after(10, "b", lambda: order.append("b"))
        assert clock.advance(30) == 3
        assert order == ["a", "b", "c"]

    def test_settle_runs_before_every_rescan_including_the_last(self) -> None:
        # This is how a background delivery worker and advance() coexist: the
        # worker registers the next retry during settle(), so the re-scan sees
        # a timer that did not exist when the callback returned.
        clock = virtual()
        settles: list[int] = []
        fired: list[str] = []
        pending_work: list[int] = []

        def settle() -> None:
            settles.append(len(fired))
            while pending_work:
                pending_work.pop()
                clock.after(0, "webhook:retry", lambda: fired.append("retry"))

        def enqueue() -> None:
            fired.append("attempt")
            pending_work.append(1)

        clock.after(0, "webhook:attempt", enqueue)
        assert clock.advance(0, settle=settle) == 2
        assert fired == ["attempt", "retry"]
        # Before the first scan, after the attempt, and after the retry.
        assert settles == [0, 1, 2]

    def test_advance_returns_the_number_fired(self) -> None:
        clock = virtual()
        for _ in range(3):
            clock.after(5, "t", lambda: None)
        assert clock.advance(5) == 3
        assert clock.advance(5) == 0


class TestPendingAndCancel:
    def test_pending_reports_id_label_and_time_remaining(self) -> None:
        clock = virtual()
        timer_id = clock.after(250, "webhook:retry:dlv_00001", lambda: None)
        (entry,) = clock.pending()
        assert entry.id == timer_id
        assert entry.label == "webhook:retry:dlv_00001"
        assert entry.due_in_ms == 250

    def test_pending_time_remaining_shrinks_as_the_clock_advances(self) -> None:
        clock = virtual()
        clock.after(250, "webhook:retry", lambda: None)
        clock.advance(100)
        assert clock.pending()[0].due_in_ms == 150

    def test_a_virtual_timer_is_recorded_even_though_nothing_arms_it(self) -> None:
        assert len(virtual().pending()) == 0
        clock = virtual()
        clock.after(1, "t", lambda: None)
        assert len(clock.pending()) == 1

    def test_a_real_timer_is_recorded_too_so_pending_tells_the_truth_in_both_modes(self) -> None:
        clock = Clock("real")
        clock.after(60_000, "webhook:retry", lambda: None)
        assert [t.label for t in clock.pending()] == ["webhook:retry"]
        clock.clear_all()

    def test_cancel_removes_the_timer_and_it_never_fires(self) -> None:
        clock = virtual()
        fired: list[str] = []
        timer_id = clock.after(10, "t", lambda: fired.append("a"))
        clock.cancel(timer_id)
        assert clock.pending() == []
        assert clock.advance(100) == 0
        assert fired == []

    def test_cancelling_an_unknown_id_is_a_no_op(self) -> None:
        virtual().cancel(9999)

    def test_clear_all_drops_every_timer(self) -> None:
        clock = virtual()
        for _ in range(3):
            clock.after(10, "t", lambda: None)
        clock.clear_all()
        assert clock.pending() == []


class TestRealMode:
    def test_a_real_timer_fires_on_a_background_thread(self) -> None:
        clock = Clock("real")
        done = threading.Event()
        threads: list[str] = []

        def callback() -> None:
            threads.append(threading.current_thread().name)
            done.set()

        clock.after(1, "t", callback)
        assert done.wait(2.0), "the real-mode timer never fired"
        assert threads[0] != threading.current_thread().name
        assert clock.pending() == []

    def test_a_pending_real_timer_does_not_hold_the_process_open(self) -> None:
        # The reference's `handle.unref()`: "Do not hold the process open for a
        # pending webhook retry." Asserted the only way that means anything --
        # a real interpreter with a thirty-second timer outstanding, which must
        # still exit at once.
        script = (
            "from vendorfake.core.time.clock import Clock\n"
            'Clock("real").after(30_000, "w", lambda: None)\n'
            'print("scheduled")\n'
        )
        started = time.monotonic()
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "scheduled"
        assert time.monotonic() - started < 15

    def test_real_now_tracks_the_wall_clock(self) -> None:
        clock = Clock("real")
        before = clock.now()
        time.sleep(0.01)
        assert clock.now() > before
