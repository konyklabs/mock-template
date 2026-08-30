"""The suite, tested the way any other predicate is tested: from both sides.

FOR: turning "the conformance suite defines correctness" from a claim into a
measurement. A suite that has only ever been run against a correct unit has
shown that it terminates. These tests show that it discriminates: every
contract in ``conformance/manifest.json`` is answered by at least one unit that
violates it, and each of those units turns the named check red and leaves the
rest alone.

FIVE PROPERTIES, and each of them has caught something real:

1. **The control is green.** ``M00`` mutates nothing. Without it, a red check
   under some other mutant would carry an unexamined second explanation --
   that the mutant harness builds units differently from ``create_unit``.
2. **Every mutant trips what it names.** A check nobody has ever seen fail is
   a check nobody has shown to work.
3. **No mutant trips what it does not name, and no mutant silences a check.**
   A mutant that reddened everything would prove nothing about the contract it
   targets, and a mutant that turned a contract *off* would look like a pass.
   Both are failures here.
4. **Every registered check has a mutant.** This is the one that bites later:
   it fails the moment a twenty-third contract is added with no evidence it can
   fail, which is exactly when nobody is looking.
5. **A unit that will not start ERRORS, and does not "fail" anything.** The
   mutant that removes a core-gated capability declaration cannot be
   constructed at all. Every contract must report ERROR and none may report
   FAIL, because a report that says ``[FAIL] C11`` about a unit that never ran
   is a report that says nothing about C11.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conformance.mutants import MUTANTS, NULL_MUTANT, Mutant, Provenance, mutant_target
from tests.conformance.mutants.model import register
from tests.conformance.mutants.seams import AuthAdapterOverlay, VendorOverlay
from vendorfake.conformance import CHECKS, Outcome, expected_skips, format_report, run_conformance
from vendorfake.conformance.report import ConformanceReport
from vendorfake.core.kernel.types import UnitError, UnitErrorKind

_CHECK_IDS = frozenset(spec.id for spec in CHECKS)
_ALL = [NULL_MUTANT, *MUTANTS]
_CONSTRUCTABLE = [mutant for mutant in MUTANTS if not mutant.fails_to_construct]


def _run(mutant: Mutant) -> ConformanceReport:
    """The whole registry against one mutant, in process, over its profiles.

    Always the in-process transport, whatever the mutant declares. ``M10``
    declares both because C10's precondition is "this target *offers* a second
    binding" -- but running every other contract twice would double the cost
    and, for a transport mutant, would redden checks through the wrapped
    binding that the mutant is not aimed at.
    """
    return run_conformance(mutant_target(mutant), transports=("inprocess",), strict=False)


def _outcomes(report: ConformanceReport, outcome: Outcome) -> frozenset[str]:
    return frozenset(result.check_id for result in report.results if result.outcome is outcome)


def _tolerated_skips(mutant: Mutant) -> frozenset[str]:
    """Skips a mutant is allowed to produce, derived rather than listed.

    Five sources, all of them data: the contracts that need a second binding
    (none of these runs offers one), the contracts that need a second OS
    process (which only the mutant whose defect is per-process pays for), the
    committed expected-skip matrix restricted to the profiles this mutant
    covers, the skips the mutant declares with a reason (``also_skips``), and
    whatever the mutant itself declares it will silence.
    """
    both_transports = frozenset(spec.id for spec in CHECKS if spec.requires.both_transports)
    out_of_process = (
        frozenset() if mutant.out_of_process else frozenset(spec.id for spec in CHECKS if spec.requires.out_of_process)
    )
    declared = frozenset(
        check_id
        for check_id, profiles in expected_skips().items()
        if any(profile in profiles for profile in mutant.profiles)
    )
    return both_transports | out_of_process | declared | mutant.also_skips | mutant.skips_everywhere


@pytest.mark.conformance
def test_the_control_mutates_nothing_and_is_green() -> None:
    """M00: the harness itself builds a conformant unit."""
    report = _run(NULL_MUTANT)
    red = _outcomes(report, Outcome.FAIL)
    assert not red, (
        "the unmutated unit failed a contract, so no other mutant result is attributable to its "
        f"mutation. tests/conformance/mutants/model.py::build_unit has drifted from "
        f"registry.create_unit.\n{format_report(report)}"
    )


@pytest.mark.conformance
@pytest.mark.parametrize("mutant", _CONSTRUCTABLE, ids=[mutant.label for mutant in _CONSTRUCTABLE])
def test_a_mutant_trips_the_checks_it_names_and_no_others(mutant: Mutant) -> None:
    report = _run(mutant)
    red = _outcomes(report, Outcome.FAIL)
    skipped = _outcomes(report, Outcome.SKIP)

    missed = sorted(mutant.trips - red)
    # Fix the check in src/vendorfake/conformance/checks/, or, if the mutant no
    # longer reproduces the defect, the mutant in tests/conformance/mutants/.
    assert not missed, f"{mutant.label} did not trip {missed} (defect: {mutant.defect})\n{format_report(report)}"

    collateral = sorted(red - mutant.expected_red)
    # Narrow the mutation, or declare genuine collateral in `also_trips` with a
    # written `cascade` reason.
    assert not collateral, (
        f"{mutant.label} tripped undeclared {collateral}, beyond {sorted(mutant.expected_red)}\n{format_report(report)}"
    )

    silenced = sorted(skipped - _tolerated_skips(mutant))
    # A mutation that removes a contract's precondition hides it. A legitimate
    # skip is declared in the mutant's `skips_everywhere`.
    assert not silenced, (
        f"{mutant.label} made {silenced} SKIP; tolerated: {sorted(_tolerated_skips(mutant))}\n{format_report(report)}"
    )


@pytest.mark.conformance
def test_a_unit_that_cannot_be_constructed_errors_rather_than_failing() -> None:
    """The FAIL/ERROR split, held down by the one mutant that cannot start.

    Before the split, removing a core-gated capability declaration printed
    ``[FAIL] C11`` -- which is exactly what C11 prints when it *has* run and
    found the declaration missing, and exactly what every other contract
    printed at the same moment. A reader could not tell "this contract was
    violated" from "this unit does not start", and the second says nothing
    about any contract at all.

    So: every case ERROR, no case FAIL, and the report red with a problem line
    that names the construction failure rather than a list of contracts.
    """
    mutant = next(m for m in MUTANTS if m.fails_to_construct)
    report = _run(mutant)

    assert not _outcomes(report, Outcome.FAIL), (
        f"{mutant.label} reported a FAILING CONTRACT for a unit that never constructed. A failure "
        f"names a contract to go and fix; this unit answered nothing at all.\n{format_report(report)}"
    )
    errored = _outcomes(report, Outcome.ERROR)
    assert errored == _CHECK_IDS, (
        f"{mutant.label} errored on {sorted(errored)}, expected every registered contract "
        f"{sorted(_CHECK_IDS)}: the unit could not be built, so no contract could be asked.\n"
        f"{format_report(report)}"
    )
    assert not report.ok, (
        "a unit that could not be constructed was reported as a clean run. An ERROR is red exactly "
        f"as a FAILURE is; only the reason differs.\n{format_report(report)}"
    )
    assert any("ERRORED" in problem and "never asked" in problem for problem in report.problems), (
        f"the report does not say that the contracts were never asked: {list(report.problems)}"
    )


@pytest.mark.conformance
def test_every_registered_check_has_a_mutant() -> None:
    """The rule that bites later, when a check is added and nobody is looking."""
    covered = frozenset(check_id for mutant in MUTANTS for check_id in mutant.trips)
    unproven = sorted(_CHECK_IDS - covered)
    # A check that has never been seen red is a check nobody has shown to work:
    # add a violating unit to tests/conformance/mutants/catalog.py.
    assert not unproven, f"checks with no mutant declaring them in trips: {unproven}"


@pytest.mark.conformance
def test_no_mutant_names_a_check_that_does_not_exist() -> None:
    """A mutant aimed at a deleted contract would silently stop proving anything."""
    named = frozenset(
        check_id for mutant in MUTANTS for check_id in (mutant.trips | mutant.also_trips | mutant.skips_everywhere)
    )
    stale = sorted(named - _CHECK_IDS)
    assert not stale, f"mutants name {stale}, which no check registers. Registered: {sorted(_CHECK_IDS)}."


@pytest.mark.conformance
def test_a_contract_skipped_on_every_profile_is_a_suite_level_failure() -> None:
    """The skip path, which no failing check can catch.

    ``M20`` removes the vendor's state machines, so C13's precondition is unmet
    on all six profiles and the contract is never asked. Nothing goes red --
    that is the whole difficulty -- and a suite whose verdict was "no failures"
    would report the emptiest matrix as its cleanest. The anti-vacuity rule in
    ``report.ok`` is what refuses it, and this is the test that holds that rule
    down.
    """
    mutant = next(m for m in MUTANTS if m.skips_everywhere)
    report = _run(mutant)

    # A universally skipped contract is invisible to every check.
    assert not _outcomes(report, Outcome.FAIL), (
        f"{mutant.label} failed {sorted(_outcomes(report, Outcome.FAIL))}, expected none\n{format_report(report)}"
    )
    assert frozenset(report.never_ran) == mutant.skips_everywhere, (
        f"{mutant.label} should leave exactly {sorted(mutant.skips_everywhere)} having passed on no "
        f"profile; report.never_ran is {list(report.never_ran)}.\n{format_report(report)}"
    )
    # ConformanceReport.ok must be False when any check passed on no profile:
    # that rule is what stands between a gated-out contract and a green build.
    assert not report.ok, f"report.ok is True with never_ran={list(report.never_ran)}\n{format_report(report)}"
    assert any("NEVER RAN C13" in problem for problem in report.problems), (
        f"the report does not name the never-run contract in its problems: {list(report.problems)}"
    )


@pytest.mark.conformance
def test_the_mutant_registry_is_internally_consistent() -> None:
    """Ids unique and ordered, every defect stated, every cascade justified."""
    ids = [mutant.id for mutant in _ALL]
    assert ids == sorted(set(ids)), f"mutant ids must be unique and id-ordered: {ids}"
    silent = [mutant.id for mutant in _ALL if not mutant.defect.strip()]
    assert not silent, f"{silent} do not say what is broken about them"
    unjustified = [mutant.id for mutant in MUTANTS if mutant.also_trips and not mutant.cascade.strip()]
    assert not unjustified, f"{unjustified} tolerate collateral with no written reason"
    unexplained = [mutant.id for mutant in MUTANTS if mutant.also_skips and not mutant.skip_reason.strip()]
    assert not unexplained, f"{unexplained} tolerate a skip with no written reason"


@pytest.mark.conformance
def test_tolerated_collateral_must_be_justified() -> None:
    """The guard on ``also_trips``, exercised rather than assumed.

    No mutant currently needs it -- every one of them trips exactly the
    contract it names -- which is precisely why the rule has to be tested
    directly: an unused guard is indistinguishable from a broken one, and the
    first person to reach for ``also_trips`` will be reaching for it under
    pressure, to make a red meta-test go green.
    """
    with pytest.raises(RuntimeError, match="written down"):
        register(
            Mutant(
                id="M99",
                name="unjustified-collateral",
                defect="Declares collateral damage without saying why it is legitimate.",
                provenance=Provenance.HYPOTHETICAL,
                trips=frozenset({"C01"}),
                also_trips=frozenset({"C02"}),
            )
        )
    with pytest.raises(RuntimeError, match="written down"):
        register(
            Mutant(
                id="M99",
                name="unexplained-skip",
                defect="Declares a tolerated skip without saying why the contract became unaskable.",
                provenance=Provenance.HYPOTHETICAL,
                trips=frozenset({"C01"}),
                also_skips=frozenset({"C02"}),
            )
        )
    # A refused mutant must not reach the registry.
    assert "M99" not in {mutant.id for mutant in MUTANTS}, f"registry: {sorted(m.id for m in MUTANTS)}"


def test_c17_observes_auth_through_a_preloaded_chaos_rule() -> None:
    """C17 must not let a profile's own chaos rule answer for authentication.

    On chaos-demo the preloaded rate-limit rule fires on every third
    ``POST /v2/orders`` -- deterministically C17's third, *accepted* probe --
    and pre-auth faults run before ``AuthAdapter.resolve``, so before C17
    reset chaos its acceptance clause was satisfied by a ``rate_limited``
    answer that never reached auth (the gate's blocking finding on
    konyklabs/vendorfake#17, 2026-08-28). This mutant refuses exactly the
    credentials that cover ``ORDERS_WRITE``: with the reset in place the
    accepted probe reaches auth and C17 goes red; without it the 429 answers
    first and the refusal is certified conformant. Deliberately not
    ``register()``-ed -- refusing every covering credential would cascade
    through the order-driving checks in the registry-wide properties above,
    and this defect is only expressible on a profile that preloads a rule.
    """

    def denies_the_covered(inner: Any, args: Any, mode: str) -> Any:
        result = inner.resolve(args, mode)
        if "ORDERS_WRITE" in result.scopes:
            raise UnitError(UnitErrorKind.UNAUTHORIZED, detail="mutant: covering credentials are refused")
        return result

    mutant = Mutant(
        id="M98",
        name="auth-denies-the-covered-credential",
        defect="Any credential that covers ORDERS_WRITE is refused as unauthorized.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C17"}),
        profiles=("chaos-demo",),
        vendor=lambda inner: VendorOverlay(inner, auth=AuthAdapterOverlay(inner.auth, resolve=denies_the_covered)),
    )
    report = run_conformance(mutant_target(mutant), transports=("inprocess",), check_ids=("C17",), strict=False)
    assert "C17" in _outcomes(report, Outcome.FAIL), format_report(report)
