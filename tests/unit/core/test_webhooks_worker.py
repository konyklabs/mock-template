"""The one thread, and the handshake ``Clock.advance`` depends on.

The property under test is not "jobs run" -- that is trivially true of any
executor -- but that :meth:`quiesce` cannot answer yes while a job is still
running, and that FIFO order is a guarantee rather than a coincidence. The
first is what makes a retry cascade collapse into one ``advance()``; the second
is what gives the delivery log one writer and stable ``dlv_NNNNN`` ids.
"""

from __future__ import annotations

import threading

import pytest

from vendorfake.core.webhooks.worker import DeliveryWorker


def test_no_thread_exists_until_the_first_submission() -> None:
    """Every unit builds a dispatcher, and most units never deliver anything.

    A worker that spawned its thread eagerly would cost one thread per unit
    across a suite that builds hundreds, and a leaked daemon thread from one
    test shows up as a mystery in the next.
    """
    before = threading.active_count()
    worker = DeliveryWorker()
    assert threading.active_count() == before
    worker.submit(lambda: None)
    worker.quiesce()
    worker.stop()


def test_jobs_run_in_submission_order() -> None:
    """FIFO is the guarantee, not a likelihood.

    The delivery log is numbered in write order and ``deliveries()`` publishes
    that order; the reference's chaos tests assert a delivery *sequence*. A
    pool of two threads would satisfy every other test in this file and break
    those.
    """
    worker = DeliveryWorker()
    seen: list[int] = []
    for n in range(50):
        worker.submit(lambda n=n: seen.append(n))  # type: ignore[misc]
    worker.quiesce()
    worker.stop()
    assert seen == list(range(50))


def test_order_survives_submissions_from_several_threads() -> None:
    """Each submitter's own jobs stay in order relative to each other.

    Between threads there is no order to guarantee, and none is claimed. What
    is claimed -- and what the delivery path relies on -- is that the queue is
    a queue: nothing overtakes anything submitted before it from the same
    caller.
    """
    worker = DeliveryWorker()
    seen: list[str] = []
    lock = threading.Lock()

    def submitter(tag: str) -> None:
        for n in range(20):
            worker.submit(lambda tag=tag, n=n: _append(lock, seen, f"{tag}{n}"))  # type: ignore[misc]

    threads = [threading.Thread(target=submitter, args=(tag,)) for tag in "ab"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    worker.quiesce()
    worker.stop()

    for tag in "ab":
        assert [s for s in seen if s.startswith(tag)] == [f"{tag}{n}" for n in range(20)]


def _append(lock: threading.Lock, target: list[str], value: str) -> None:
    with lock:
        target.append(value)


def test_quiesce_cannot_return_while_a_job_is_mid_flight() -> None:
    """THE invariant. Without it the retry cascade silently under-reports.

    The job here mimics a delivery: it takes time, and its *last* act is the
    thing a caller cares about having happened -- for a real delivery,
    registering the next retry on the clock. A ``quiesce`` that only waited for
    an empty queue would return after the job started and before it finished,
    and ``Clock.advance``'s re-scan would find no timer and stop.
    """
    worker = DeliveryWorker()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def job() -> None:
        started.set()
        release.wait(timeout=5)
        finished.set()

    worker.submit(job)
    assert started.wait(timeout=5)
    # The queue is now empty -- the job has been dequeued -- but the job is not
    # done. This is the exact window the naive implementation returns in.
    assert worker.pending == 0
    assert worker.busy is True
    assert worker.quiesce(timeout=0.05) is False
    assert finished.is_set() is False

    release.set()
    assert worker.quiesce(timeout=5) is True
    assert finished.is_set() is True
    worker.stop()


def test_a_job_that_submits_more_work_keeps_quiesce_waiting() -> None:
    """ "Schedule a timer from inside a timer", in worker terms.

    A delivery job's follow-up arrives while ``quiesce`` is already waiting, so
    the wait must be re-evaluated rather than decided once. Three generations
    deep, because a two-deep chain passes under an implementation that checks
    the queue exactly twice.
    """
    worker = DeliveryWorker()
    seen: list[str] = []

    def third() -> None:
        seen.append("third")

    def second() -> None:
        seen.append("second")
        worker.submit(third)

    def first() -> None:
        seen.append("first")
        worker.submit(second)

    worker.submit(first)
    assert worker.quiesce(timeout=5) is True
    assert seen == ["first", "second", "third"]
    worker.stop()


def test_quiesce_from_inside_the_worker_raises_rather_than_hanging() -> None:
    """A job that called ``drain()`` would wait for itself, forever.

    Raising turns a permanent hang -- the worst failure mode a test suite can
    have, because it has no output -- into a captured failure with a name.
    """
    worker = DeliveryWorker()
    caught: list[BaseException] = []

    def job() -> None:
        try:
            worker.quiesce(timeout=0.1)
        except RuntimeError as exc:
            caught.append(exc)

    worker.submit(job)
    assert worker.quiesce(timeout=5) is True
    worker.stop()
    assert len(caught) == 1
    assert "would wait for itself" in str(caught[0])


def test_a_raising_job_is_recorded_and_the_queue_keeps_moving() -> None:
    """A dead delivery thread would answer ``drain()`` forever.

    Swallowing is the lesser evil, and :meth:`failures` is why it is not a
    silent one: a test asserts the tuple is empty, so a swallowed exception is
    still a red test rather than a missing delivery record.
    """
    worker = DeliveryWorker()
    seen: list[str] = []

    def boom() -> None:
        raise ValueError("subscriber exploded")

    worker.submit(boom)
    worker.submit(lambda: seen.append("after"))
    assert worker.quiesce(timeout=5) is True
    worker.stop()

    assert seen == ["after"]
    assert worker.failures() == ("ValueError: subscriber exploded",)


def test_quiesce_on_an_idle_worker_returns_at_once() -> None:
    worker = DeliveryWorker()
    assert worker.quiesce(timeout=0) is True
    worker.stop()


def test_stop_refuses_new_work_but_finishes_what_was_queued() -> None:
    """``Unit.stop`` drains first and then stops.

    A ``stop`` that discarded queued deliveries would make that drain
    conditional on timing -- the record would exist or not depending on how
    fast the worker was -- which is exactly the nondeterminism the single
    worker exists to remove.
    """
    worker = DeliveryWorker()
    seen: list[str] = []
    worker.submit(lambda: seen.append("queued"))
    worker.stop()
    assert seen == ["queued"]
    with pytest.raises(RuntimeError, match="stopped"):
        worker.submit(lambda: seen.append("late"))
    assert seen == ["queued"]


def test_stop_is_idempotent() -> None:
    worker = DeliveryWorker()
    worker.submit(lambda: None)
    worker.stop()
    worker.stop()


def test_the_generation_counter_moves_on_submission_and_on_completion() -> None:
    """Two bumps per job: one when it arrives, one when it is done.

    A sampler that saw only submissions could not tell "nothing was submitted"
    from "everything submitted has finished", which is the question a
    diagnostic asks.
    """
    worker = DeliveryWorker()
    start = worker.generation
    worker.submit(lambda: None)
    worker.quiesce(timeout=5)
    worker.stop()
    assert worker.generation == start + 2
