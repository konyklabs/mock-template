"""Which phase a fault belongs to, and what its parameters mean."""

from __future__ import annotations

import time

import pytest

from vendorfake.core.chaos.engine import ChaosDecision
from vendorfake.core.chaos.faults import (
    AUTH_PHASE_FAULTS,
    FAULT_PARAM_KEYS,
    apply_request_fault,
)
from vendorfake.core.chaos.rules import BUILTIN_FAULTS
from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.core.logging import SilentLogger
from vendorfake.core.time.clock import Clock

LOG = SilentLogger()


def _decision(fault: str, **params: object) -> ChaosDecision:
    return ChaosDecision(rule_id="r1", fault=fault, params=dict(params), occurrence=1)


def _apply(fault: str, phase: str = "pre", *, clock: Clock | None = None, **params: object) -> None:
    apply_request_fault(
        _decision(fault, **params),
        phase,  # type: ignore[arg-type]
        clock=clock or Clock("real"),
        log=LOG,
    )


@pytest.mark.parametrize(
    ("fault", "kind"),
    [
        ("rate_limit", UnitErrorKind.RATE_LIMITED),
        ("server_error", UnitErrorKind.INTERNAL),
        ("unavailable", UnitErrorKind.UNAVAILABLE),
    ],
)
def test_each_pre_auth_fault_raises_its_own_kind(fault: str, kind: UnitErrorKind) -> None:
    with pytest.raises(UnitError) as caught:
        _apply(fault)
    assert caught.value.kind is kind
    assert caught.value.info is not None
    assert caught.value.info["chaos_rule"] == "r1"


def test_a_pre_auth_fault_does_nothing_in_the_post_auth_phase() -> None:
    _apply("server_error", "post_auth")


def test_token_expiry_does_nothing_in_the_pre_phase_and_fires_in_the_post_auth_one() -> None:
    _apply("token_expiry", "pre")
    with pytest.raises(UnitError) as caught:
        _apply("token_expiry", "post_auth")
    assert caught.value.kind is UnitErrorKind.TOKEN_EXPIRED


def test_the_phase_split_is_data_and_not_a_hard_coded_comparison() -> None:
    assert set(AUTH_PHASE_FAULTS) == {"token_expiry"}


def test_retry_after_seconds_is_coerced_from_a_string() -> None:
    """It arrives as text on the in-band path -- ``chaos:rate_limit:
    retry_after_seconds=3`` is split textually -- and as arbitrary JSON on the
    rule path."""
    with pytest.raises(UnitError) as caught:
        _apply("rate_limit", retry_after_seconds="3")
    assert caught.value.info is not None
    assert caught.value.info["retry_after_seconds"] == 3


def test_a_junk_retry_after_falls_back_to_the_documented_default() -> None:
    with pytest.raises(UnitError) as caught:
        _apply("rate_limit", retry_after_seconds="soon")
    assert caught.value.info is not None
    assert caught.value.info["retry_after_seconds"] == 1


def test_the_timeout_delay_is_reported_as_an_integer_not_a_float() -> None:
    """``{"delay_ms": 100.0}`` versus ``{"delay_ms": 100}`` is a byte
    difference in a body a consumer may be diffing against the oracle's."""
    with pytest.raises(UnitError) as caught:
        _apply("timeout", delay_ms="0")
    assert caught.value.info is not None
    assert caught.value.info["delay_ms"] == 0
    assert isinstance(caught.value.info["delay_ms"], int)
    assert caught.value.detail == "Injected timeout after 0ms."


def test_a_real_clock_timeout_really_waits() -> None:
    """The reference's own assertion -- elapsed >= 20ms for a 25ms delay -- runs
    on a profile whose clock mode is real, so this is the branch it pins."""
    clock = Clock("real")
    started = time.monotonic()
    with pytest.raises(UnitError) as caught:
        _apply("timeout", clock=clock, delay_ms=25)
    assert (time.monotonic() - started) * 1000 >= 20
    assert caught.value.kind is UnitErrorKind.TIMEOUT


def test_a_virtual_clock_timeout_moves_time_and_returns_at_once() -> None:
    """A request that parked on a virtual timer would hold the pipeline while
    the only call that can fire that timer -- another request -- waits for the
    same lock. So virtual mode advances inline and never waits."""
    clock = Clock("virtual", "2024-01-01T00:00:00.000Z")
    before = clock.now()
    started = time.monotonic()
    with pytest.raises(UnitError) as caught:
        _apply("timeout", clock=clock, delay_ms=5000)
    elapsed_real_ms = (time.monotonic() - started) * 1000
    assert caught.value.kind is UnitErrorKind.TIMEOUT
    assert clock.now() - before == 5000
    assert elapsed_real_ms < 500


def test_a_virtual_clock_timeout_fires_the_timers_that_became_due() -> None:
    clock = Clock("virtual", "2024-01-01T00:00:00.000Z")
    fired: list[str] = []
    clock.after(50, "retry", lambda: fired.append("retry"))
    with pytest.raises(UnitError):
        _apply("timeout", clock=clock, delay_ms=100)
    assert fired == ["retry"]


def test_a_zero_or_negative_delay_does_not_touch_the_clock() -> None:
    clock = Clock("virtual", "2024-01-01T00:00:00.000Z")
    before = clock.now()
    with pytest.raises(UnitError):
        _apply("timeout", clock=clock, delay_ms=-5)
    assert clock.now() == before


def test_an_unknown_fault_is_a_warning_and_not_an_error() -> None:
    """The fault vocabulary is open by design: a fork adds a name without
    editing the core, and a unit that refused one would make that impossible."""
    warned: list[tuple[str, object]] = []

    class Recording(SilentLogger):
        def warn(self, msg, fields=None):  # type: ignore[no-untyped-def]
            warned.append((msg, fields))

    apply_request_fault(_decision("teleport"), "pre", clock=Clock("real"), log=Recording())
    assert warned[0][0] == "unknown request-scope fault ignored"
    assert warned[0][1] == {"fault": "teleport", "rule": "r1"}


def test_a_webhook_scope_fault_named_by_a_request_rule_does_nothing_loudly() -> None:
    warned: list[str] = []

    class Recording(SilentLogger):
        def warn(self, msg, fields=None):  # type: ignore[no-untyped-def]
            warned.append(msg)

    apply_request_fault(_decision("webhook.drop"), "pre", clock=Clock("real"), log=Recording())
    assert warned == ["unknown request-scope fault ignored"]


def test_the_parameter_key_table_covers_every_catalogued_fault() -> None:
    assert set(FAULT_PARAM_KEYS) == {spec.name for spec in BUILTIN_FAULTS}


def test_the_three_parameter_names_are_pinned_verbatim() -> None:
    """snake_case, and these exact strings: a profile document and a magic
    value both spell them, and neither is validated against the catalogue."""
    assert FAULT_PARAM_KEYS["rate_limit"] == ("retry_after_seconds",)
    assert FAULT_PARAM_KEYS["timeout"] == ("delay_ms",)
    assert FAULT_PARAM_KEYS["webhook.duplicate"] == ("copies",)


def test_every_declared_parameter_appears_in_the_catalogue_prose() -> None:
    """The prose in ``BUILTIN_FAULTS`` is a promise; this is what keeps it one."""
    prose = {spec.name: (spec.params or "") for spec in BUILTIN_FAULTS}
    for name, keys in FAULT_PARAM_KEYS.items():
        for key in keys:
            assert key in prose[name], f"{name}.{key} is implemented but undocumented"
