"""The retry shape: no vendor default, JS rounding, and the off-by-one.

Three semantics a reviewer could disagree about, and each one is money or a
missing delivery: whether the core ships a schedule, which way a halfway
millisecond rounds, and whether ``len(schedule)`` intervals mean that many
attempts or one more.
"""

from __future__ import annotations

import pytest

from vendorfake.core.config.models import RetryPolicy as ConfigRetryPolicy
from vendorfake.core.webhooks.retry import (
    DEFAULT_TIMEOUT_MS,
    MutableRetryPolicy,
    retry_delay_ms,
    schedule_exhausted,
)


def test_the_core_ships_no_schedule() -> None:
    """The whole reason this module exists.

    A schedule is one vendor's published documentation. The reference imports
    its vendor's eleven intervals into vendor-neutral core and uses them as the
    config default, which makes a core that cannot be compiled without that
    vendor. Here the default is empty, and ``Unit`` refuses to start when a
    vendor declares ``webhooks`` and the merge left it that way -- so the
    absence is a startup error rather than instant exhaustion.
    """
    assert MutableRetryPolicy().schedule_ms == ()
    assert ConfigRetryPolicy().schedule_ms == ()


def test_the_timeout_is_the_one_defensible_default() -> None:
    """Ten seconds. Defensible where a schedule is not: every vendor has some
    timeout, and defaulting it to zero would report every delivery as timed
    out."""
    assert DEFAULT_TIMEOUT_MS == 10_000
    assert MutableRetryPolicy().timeout_ms == 10_000
    assert MutableRetryPolicy().time_scale == 1.0


def test_a_halfway_millisecond_rounds_the_way_javascript_rounds() -> None:
    """``Math.round(2.5) == 3`` and Python's ``round(2.5) == 2``.

    A user-supplied ``time_scale`` lands on a halfway case the first time
    somebody picks a round number, and the reference's own scaled schedule is
    asserted to the millisecond. Rounding down here would silently shorten
    every scaled interval by one millisecond on half the schedule.
    """
    policy = MutableRetryPolicy(schedule_ms=(5, 15), time_scale=0.5)
    assert retry_delay_ms(policy, 0) == 3
    assert retry_delay_ms(policy, 1) == 8
    assert round(2.5) == 2  # the trap, stated so the assertion above is not mistaken for a tautology


def test_scaling_keeps_the_shape_of_the_documented_schedule() -> None:
    """One minute then two minutes, scaled to ten and twenty milliseconds --
    the compression the reference's own retry test observes."""
    policy = MutableRetryPolicy(schedule_ms=(60_000, 120_000), time_scale=0.000167)
    assert [retry_delay_ms(policy, n) for n in (0, 1)] == [10, 20]


def test_eleven_intervals_permit_twelve_attempts() -> None:
    """The off-by-one that decides whether a test asserts twelve records or one.

    ``retry_number`` is 0 for the first send, so the schedule is exhausted only
    once ``retry_number`` reaches its length: attempts 1..12 for eleven
    intervals, of which eleven fail and the twelfth is exhausted.
    """
    policy = MutableRetryPolicy(schedule_ms=tuple(range(11)))
    assert [schedule_exhausted(policy, n) for n in range(13)] == [False] * 11 + [True, True]


def test_an_empty_schedule_exhausts_on_the_first_attempt() -> None:
    """Which is exactly why an unmerged vendor default must be a startup error:
    the symptom is indistinguishable from an unreachable subscriber."""
    assert schedule_exhausted(MutableRetryPolicy(), 0) is True


def test_a_patch_coerces_rather_than_indexes() -> None:
    """The patch is a parsed JSON body from the control plane.

    ``{"time_scale": "0.5"}`` is a request a consumer is entitled to send, and
    ``"0.5" * 60000`` is a sixty-thousand-character string rather than a
    ``TypeError`` -- which would surface as an unhandled exception on the
    delivery worker rather than as a bad request.
    """
    patched = MutableRetryPolicy(schedule_ms=(1,), time_scale=1.0, timeout_ms=100).patched(
        {"time_scale": "0.5", "timeout_ms": "250", "schedule_ms": ["10", 20.9]}
    )
    assert patched.time_scale == 0.5
    assert patched.timeout_ms == 250
    assert patched.schedule_ms == (10, 20)


def test_a_patch_touches_only_the_keys_it_names() -> None:
    policy = MutableRetryPolicy(schedule_ms=(1, 2), time_scale=2.0, timeout_ms=99)
    patched = policy.patched({"time_scale": 0.5})
    assert (patched.schedule_ms, patched.time_scale, patched.timeout_ms) == ((1, 2), 0.5, 99)


def test_a_patch_leaves_the_policy_it_was_given_untouched() -> None:
    """The dispatcher swaps the whole object precisely so that an attempt which has
    already read the policy keeps the schedule it read. A patch that mutated in place
    would put a shortened schedule under an attempt mid-flight, which is the race
    ``_run_attempt``'s single read exists to close."""
    policy = MutableRetryPolicy(schedule_ms=(1, 2), time_scale=2.0, timeout_ms=99)
    patched = policy.patched({"schedule_ms": [5], "time_scale": 0.5, "timeout_ms": 1})
    assert patched is not policy
    assert (policy.schedule_ms, policy.time_scale, policy.timeout_ms) == ((1, 2), 2.0, 99)


def test_an_unparseable_value_leaves_the_field_alone() -> None:
    """Rejection is the control-plane schema's job. Doing it here as well would
    mean doing it differently in one of the two places eventually."""
    assert MutableRetryPolicy(time_scale=0.5).patched({"time_scale": "not a number"}).time_scale == 0.5


def test_a_string_schedule_is_not_iterated_character_by_character() -> None:
    """``"10"`` is a ``Sequence``; iterating it would give ``("1", "0")`` and a
    schedule of ``(1, 0)``, which is a plausible-looking wrong answer."""
    assert MutableRetryPolicy(schedule_ms=(7,)).patched({"schedule_ms": "10"}).schedule_ms == (7,)


def test_the_live_policy_is_a_copy_of_the_resolved_configuration() -> None:
    """``/__unit/info`` reports what the unit was started with.

    A runtime patch must not rewrite that retroactively, or a report of a
    scenario would describe the scenario as it ended rather than as it began.
    """
    resolved = ConfigRetryPolicy(schedule_ms=(1, 2), time_scale=1.0, timeout_ms=500)
    live = MutableRetryPolicy.of(resolved).patched({"time_scale": 0.25})
    assert resolved.time_scale == 1.0
    assert live.time_scale == 0.25


def test_the_published_shape_is_snake_case_with_all_three_keys() -> None:
    assert MutableRetryPolicy(schedule_ms=(1,), time_scale=0.5, timeout_ms=10).as_json() == {
        "schedule_ms": [1],
        "time_scale": 0.5,
        "timeout_ms": 10,
    }


def test_asking_for_a_delay_past_the_schedule_is_a_programming_error() -> None:
    """Loud rather than plausible: a silent zero would look like an instant
    retry and turn exhaustion into an infinite loop."""
    with pytest.raises(IndexError):
        retry_delay_ms(MutableRetryPolicy(schedule_ms=(1,)), 1)
