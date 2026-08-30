"""The pytest rendering: does it really expand the registry, and can it go red?

Two layers, deliberately. The fast tests drive :func:`run_case` and the ledger
directly. The slow ones run pytest in a subprocess against
``--pyargs vendorfake.conformance``, because that string is the whole promise
made to a downstream vendor: nothing about the plugin is verified by a test
that imports it and never asks pytest to load it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

import pytest

from tests.conformance.harness import FAULTLESS_PROFILE, PROFILES, target
from vendorfake.conformance import (
    CHECKS,
    TARGET_ENV_VAR,
    ConformanceFailure,
    Outcome,
    expected_skips,
    find_check,
)
from vendorfake.conformance.plugin import ARGNAME, Case, _Ledger, run_case

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = "tests.conformance.harness"


def _pytest_pyargs(*extra: str) -> subprocess.CompletedProcess[str]:
    """``pytest --pyargs vendorfake.conformance`` as a downstream vendor runs it."""
    # `-m pytest` and not the console script: it puts the repository root on
    # sys.path, which is how the subprocess reaches the harness target. The
    # environment variable is cleared so that a shell which has exported a
    # target cannot make the unconfigured-run test pass by accident.
    env = dict(os.environ)
    env.pop(TARGET_ENV_VAR, None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--pyargs",
            "vendorfake.conformance",
            "-q",
            "-p",
            "no:cacheprovider",
            *extra,
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# One case, translated into pytest's vocabulary.
# ---------------------------------------------------------------------------


def _case(check_id: str, profile: str, *, strict: bool = False) -> Case:
    return Case(
        spec=find_check(check_id),
        target=target(profiles=PROFILES, transports=("inprocess",)),
        profile=profile,
        transport="inprocess",
        strict=strict,
    )


def test_a_passing_contract_returns_and_is_recorded() -> None:
    ledger = _Ledger()
    run_case(_case("C01", "full"), ledger)
    assert ledger.outcomes == {"C01": {Outcome.PASS}}


def test_a_declared_skip_becomes_a_stdlib_skip_naming_the_reason() -> None:
    """``unittest.SkipTest`` and not ``pytest.skip``: this package imports no pytest."""
    ledger = _Ledger()
    with pytest.raises(unittest.SkipTest) as raised:
        run_case(_case("C08", FAULTLESS_PROFILE), ledger)
    assert "C08-no-faults-inprocess" in str(raised.value)
    assert ledger.outcomes == {"C08": {Outcome.SKIP}}


def test_strict_turns_an_undeclared_skip_into_a_failure() -> None:
    """C10 needs a second binding; a single-transport target cannot give one."""
    case = Case(
        spec=find_check("C10"),
        target=target(profiles=("full",), transports=("inprocess",)),
        profile="full",
        transport="inprocess",
        strict=True,
    )
    with pytest.raises(ConformanceFailure) as raised:
        run_case(case, _Ledger())
    assert "nor conformance/manifest.json declares" in str(raised.value)


def test_strict_still_permits_a_declared_skip() -> None:
    with pytest.raises(unittest.SkipTest):
        run_case(_case("C08", FAULTLESS_PROFILE, strict=True), _Ledger())


def test_strict_permits_the_skips_a_target_declares_itself() -> None:
    """The second vendor names its own matrix and the contracts it cannot be
    asked; the plugin resolves both from the target, not the manifest --
    which is what let ``plugin (clover)`` fail on C19 until it did."""
    from tests.conformance.harness import clover_target

    clover = clover_target(profiles=PROFILES, transports=("inprocess",))
    ledger = _Ledger()
    inapplicable = Case(spec=find_check("C19"), target=clover, profile="full", transport="inprocess", strict=True)
    with pytest.raises(unittest.SkipTest):
        run_case(inapplicable, ledger)
    own_matrix = Case(spec=find_check("C07"), target=clover, profile="oauth-only", transport="inprocess", strict=True)
    with pytest.raises(unittest.SkipTest):
        run_case(own_matrix, ledger)
    assert ledger.outcomes == {"C19": {Outcome.SKIP}, "C07": {Outcome.SKIP}}


def test_the_ledger_carves_out_inapplicable_contracts_and_flags_a_stale_declaration() -> None:
    from tests.conformance.harness import clover_target

    ledger = _Ledger()
    ledger.arm(
        target=clover_target(),
        profiles=("full", "no-chaos"),
        transports=("inprocess",),
        specs=(find_check("C01"), find_check("C19")),
        whole_matrix=True,
    )
    ledger.record("C01", Outcome.PASS)
    ledger.record("C19", Outcome.SKIP)
    assert ledger.never_passed == ()
    assert ledger.problems() == ()
    ledger.record("C19", Outcome.PASS)
    assert ledger.stale_inapplicable == ("C19",)
    assert len(ledger.problems()) == 1
    assert ledger.problems()[0].startswith("DECLARED INAPPLICABLE BUT RAN C19")


def test_an_unconfigured_run_skips_once_and_says_how_to_configure_it() -> None:
    with pytest.raises(unittest.SkipTest) as raised:
        run_case(None, _Ledger())
    assert "--conformance-target" in str(raised.value)


# ---------------------------------------------------------------------------
# The ledger: the cross-profile verdict a per-test rendering would lose.
# ---------------------------------------------------------------------------


def _armed(*, profiles: tuple[str, ...], whole_matrix: bool = True) -> _Ledger:
    ledger = _Ledger()
    ledger.arm(
        target=target(),
        profiles=profiles,
        transports=("inprocess",),
        specs=CHECKS[:2],
        whole_matrix=whole_matrix,
    )
    return ledger


def test_a_contract_that_passed_nowhere_is_a_problem() -> None:
    ledger = _armed(profiles=("full", "no-chaos"))
    ledger.record("C01", Outcome.PASS)
    ledger.record("C02", Outcome.SKIP)
    problems = ledger.problems()
    assert len(problems) == 1
    assert problems[0].startswith("NEVER RAN C02")


def test_a_contract_that_passed_on_one_profile_is_not_a_problem() -> None:
    ledger = _armed(profiles=("full", "no-chaos"))
    ledger.record("C01", Outcome.PASS)
    ledger.record("C02", Outcome.SKIP)
    ledger.record("C02", Outcome.PASS)
    assert ledger.problems() == ()


def test_a_partial_run_states_nothing_about_the_matrix() -> None:
    """``-k``, ``-x`` and ``--lf`` all execute a subset, where the rule is unanswerable."""
    ledger = _armed(profiles=("full", "no-chaos"))
    ledger.record("C02", Outcome.SKIP)
    assert not ledger.complete
    assert ledger.problems() == ()


def test_a_narrowed_matrix_states_nothing_either() -> None:
    ledger = _armed(profiles=("full",), whole_matrix=False)
    ledger.record("C01", Outcome.SKIP)
    ledger.record("C02", Outcome.SKIP)
    assert ledger.problems() == ()


def test_an_unarmed_ledger_is_silent() -> None:
    """Every pytest run on the machine loads this plugin. Most collect nothing here."""
    assert _Ledger().problems() == ()


# ---------------------------------------------------------------------------
# The promise itself: pytest --pyargs vendorfake.conformance.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_pyargs_expands_the_registry_over_every_profile() -> None:
    done = _pytest_pyargs("--conformance-target", f"{HARNESS}:target", "--conformance-strict")
    assert done.returncode == 0, done.stdout + done.stderr
    expected = len(CHECKS) * len(PROFILES)
    # Every skip in this matrix is declared in conformance/manifest.json, which
    # is what --conformance-strict is asserting: a profile that genuinely lacks
    # a capability skips forever, and an UNdeclared skip is a failure.
    skipped = sum(len(profiles) for profiles in expected_skips().values())
    assert f"{expected - skipped} passed, {skipped} skipped" in done.stdout, done.stdout
    assert "conformance: every contract passed on at least one profile" in done.stdout, done.stdout


@pytest.mark.integration
def test_pyargs_names_the_contract_and_the_profile_in_a_test_id() -> None:
    done = _pytest_pyargs(
        "--conformance-target",
        f"{HARNESS}:target",
        "--conformance-check",
        "C13",
        "--conformance-profile",
        "full",
        "--collect-only",
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "test_contract[C13-full-inprocess]" in done.stdout, done.stdout


@pytest.mark.integration
def test_the_session_goes_red_when_a_contract_passes_on_no_profile() -> None:
    """The falsifiability of the anti-vacuity rule, end to end.

    Every test in this run is green -- the three contracts that cannot run on
    this target skip, and a skip is not a failure. The session must still exit
    non-zero, because a contract nobody could ask proved nothing.

    The three are named rather than counted: C08 and C12 need fault injection,
    which this profile switches off, and C21 needs a virtual clock, which it
    does not run. Naming them means a fourth contract quietly joining the list
    changes this test rather than sliding under a number.
    """
    silent = ("C08", "C12", "C21")
    done = _pytest_pyargs("--conformance-target", f"{HARNESS}:one_profile_target")
    assert done.returncode == 1, done.stdout + done.stderr
    assert f"{len(CHECKS) - len(silent)} passed, {len(silent)} skipped" in done.stdout, done.stdout
    for check_id in silent:
        assert f"NEVER RAN {check_id}" in done.stdout, done.stdout
    assert "NEVER RAN C01" not in done.stdout, done.stdout


@pytest.mark.integration
def test_without_a_target_the_run_skips_rather_than_guessing_a_vendor() -> None:
    done = _pytest_pyargs("-rs")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 skipped" in done.stdout, done.stdout
    assert "no conformance target" in done.stdout, done.stdout


def test_the_argname_is_the_one_the_shipped_test_module_takes() -> None:
    """A rename on one side and not the other is a fixture error at collection."""
    from vendorfake.conformance import test_contracts

    assert ARGNAME in test_contracts.test_contract.__code__.co_varnames
