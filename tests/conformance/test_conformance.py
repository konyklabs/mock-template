"""The conformance suite, rendered as one pytest test per (check, profile, transport).

A red run names ``test_conformance[C03-oauth-only-inprocess]`` and prints the
contract's own prose. That is the property a single "the suite failed" test
cannot give, and it is why this layer exists on top of the registry rather
than instead of it.
"""

from __future__ import annotations

import pytest

from tests.conformance.harness import PROFILES, target
from vendorfake.conformance import (
    CHECKS,
    CheckSpec,
    ConformanceTarget,
    Outcome,
    expected_skips,
    format_report,
    run_check,
    run_conformance,
)

_CASES = [(spec, profile) for profile in PROFILES for spec in CHECKS]
_IDS = [f"{spec.id}-{profile}-inprocess" for spec, profile in _CASES]


@pytest.mark.conformance
@pytest.mark.parametrize(("spec", "profile"), _CASES, ids=_IDS)
def test_conformance(spec: CheckSpec, profile: str, conformance_target: ConformanceTarget) -> None:
    result = run_check(spec, conformance_target, profile, "inprocess")
    if result.outcome is Outcome.SKIP:
        assert profile in expected_skips().get(spec.id, frozenset()), (
            f"{result.case_id} skipped for a reason the manifest does not declare: {result.detail}"
        )
        pytest.skip(result.detail)
    assert result.outcome is Outcome.PASS, f"{result.check_id} {result.name}\n{result.detail}"


@pytest.mark.conformance
@pytest.mark.parametrize("spec", CHECKS, ids=[spec.id for spec in CHECKS])
def test_conformance_over_http(spec: CheckSpec, conformance_target: ConformanceTarget) -> None:
    """The whole suite through a real socket, on the profile that enables everything."""
    result = run_check(spec, conformance_target, "full", "http")
    if result.outcome is Outcome.SKIP:
        pytest.skip(result.detail)
    assert result.outcome is Outcome.PASS, f"{result.check_id} {result.name}\n{result.detail}"


@pytest.mark.conformance
def test_the_aggregate_run_is_strict_clean() -> None:
    """The cross-profile rules only exist in an all-profile run.

    ``report.ok`` is False when any contract passed on no profile at all, which
    is what makes a universally skipped check a failure rather than a green
    line. It cannot be asserted from inside a single-profile run.
    """
    report = run_conformance(target(), transports=("inprocess",), strict=True)
    assert report.ok, format_report(report)
    assert not report.never_ran, format_report(report)
