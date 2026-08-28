"""The three rules that decide whether a run is ok.

The reference's floor was `passed >= 9` against ten checks, which is green for
a run in which one contract was never asked. These tests pin the replacement.
"""

from __future__ import annotations

from vendorfake.conformance import CheckResult, ConformanceReport, Outcome, format_report


def _result(check_id: str, profile: str, outcome: Outcome) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        name=f"{check_id} contract",
        profile=profile,
        transport="inprocess",
        outcome=outcome,
        detail="",
        duration_ms=1,
    )


def test_a_clean_run_is_ok() -> None:
    report = ConformanceReport((_result("C01", "full", Outcome.PASS),))
    assert report.ok
    assert (report.passed, report.failed, report.skipped) == (1, 0, 0)


def test_a_failure_is_not_ok() -> None:
    report = ConformanceReport((_result("C01", "full", Outcome.FAIL),))
    assert not report.ok
    assert "FAILED C01-full-inprocess" in report.problems[0]


def test_a_check_that_skipped_everywhere_is_not_ok_even_without_strict() -> None:
    """The anti-vacuity rule. `passed >= N` would have called this green."""
    report = ConformanceReport(
        (
            _result("C01", "full", Outcome.PASS),
            _result("C02", "full", Outcome.SKIP),
            _result("C02", "lean", Outcome.SKIP),
        ),
        expected_skips={"C02": frozenset({"full", "lean"})},
    )
    assert report.never_ran == ("C02",)
    assert not report.ok


def test_the_anti_vacuity_rule_is_off_for_a_narrowed_run() -> None:
    """`--profile oauth-only` must stay usable.

    A contract that legitimately skips on one profile has not proved nothing;
    it has not been asked. The rule is a statement about the whole matrix and
    the runner says whether this run was one.
    """
    report = ConformanceReport(
        (
            _result("C01", "full", Outcome.PASS),
            _result("C02", "full", Outcome.SKIP),
        ),
        expected_skips={"C02": frozenset({"full"})},
        cross_profile=False,
    )
    assert report.never_ran == ("C02",)
    assert report.ok


def test_a_declared_skip_that_happens_elsewhere_is_ok_under_strict() -> None:
    report = ConformanceReport(
        (
            _result("C01", "full", Outcome.PASS),
            _result("C01", "lean", Outcome.SKIP),
        ),
        strict=True,
        expected_skips={"C01": frozenset({"lean"})},
    )
    assert report.ok
    assert report.undeclared_skips == ()


def test_an_undeclared_skip_fails_only_under_strict() -> None:
    results = (
        _result("C01", "full", Outcome.PASS),
        _result("C01", "lean", Outcome.SKIP),
    )
    assert ConformanceReport(results).ok
    strict = ConformanceReport(results, strict=True)
    assert not strict.ok
    assert strict.undeclared_skips == ("C01-lean",)


def test_a_declared_skip_that_stopped_happening_fails_under_strict() -> None:
    """A profile gained a capability and the record did not move with it."""
    report = ConformanceReport(
        (_result("C01", "lean", Outcome.PASS),),
        strict=True,
        expected_skips={"C01": frozenset({"lean"})},
    )
    assert report.stale_expected_skips == ("C01-lean",)
    assert not report.ok


def test_the_formatted_report_says_what_happened() -> None:
    text = format_report(ConformanceReport((_result("C01", "full", Outcome.PASS),)))
    assert "== full / inprocess ==" in text
    assert "[PASS] C01" in text
    assert text.endswith("OK")
