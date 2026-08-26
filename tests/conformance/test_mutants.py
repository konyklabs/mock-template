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

import pytest

from tests.conformance.mutants import MUTANTS, NULL_MUTANT, Mutant, Provenance, mutant_target
from tests.conformance.mutants.model import register
from vendorfake.conformance import CHECKS, Outcome, expected_skips, format_report, run_conformance
from vendorfake.conformance.report import ConformanceReport

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

    Four sources, all of them data: the contracts that need a second binding
    (none of these runs offers one), the contracts that need a second OS
    process (which only the mutant whose defect is per-process pays for), the
    committed expected-skip matrix restricted to the profiles this mutant
    covers, and whatever the mutant itself declares it will silence.
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
    return both_transports | out_of_process | declared | mutant.skips_everywhere


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
    assert not missed, (
        f"{mutant.label} did not trip {missed}, which it declares it must.\n"
        f"  defect: {mutant.defect}\n"
        f"A contract that stays green against a unit built to violate it is a contract that is not "
        f"being enforced -- fix the check in src/vendorfake/conformance/checks/, or, if the mutant "
        f"no longer reproduces the defect, fix the mutant in "
        f"tests/conformance/mutants/catalog.py.\n{format_report(report)}"
    )

    collateral = sorted(red - mutant.expected_red)
    assert not collateral, (
        f"{mutant.label} also tripped {collateral}, which it does not declare.\n"
        f"  defect: {mutant.defect}\n"
        f"A mutant that reddens contracts it was not aimed at proves nothing about the one it "
        f"names. Either narrow the mutation, or -- if the defect genuinely violates those "
        f"contracts too -- add them to `also_trips` with a written `cascade` "
        f"reason.\n{format_report(report)}"
    )

    silenced = sorted(skipped - _tolerated_skips(mutant))
    assert not silenced, (
        f"{mutant.label} made {silenced} SKIP rather than fail.\n"
        f"  defect: {mutant.defect}\n"
        f"A mutation that removes a contract's precondition hides it: the suite reports green for "
        f"a unit it never examined. If the skip is legitimate, declare it in the mutant's "
        f"`skips_everywhere`.\n{format_report(report)}"
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
    assert not unproven, (
        f"{unproven} have no mutant, so nothing shows they can fail. A check that has never been "
        f"seen red is a check nobody has shown to work: add a unit to "
        f"tests/conformance/mutants/catalog.py that violates it and declare the id in `trips`."
    )


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

    assert not _outcomes(report, Outcome.FAIL), (
        f"{mutant.label} was expected to produce no failure at all -- the point is that a "
        f"universally skipped contract is invisible to every check.\n{format_report(report)}"
    )
    assert frozenset(report.never_ran) == mutant.skips_everywhere, (
        f"{mutant.label} should leave exactly {sorted(mutant.skips_everywhere)} having passed on no "
        f"profile; report.never_ran is {list(report.never_ran)}.\n{format_report(report)}"
    )
    assert not report.ok, (
        "a contract that skipped on every profile was reported as a clean run. "
        "ConformanceReport.ok must be False when any check passed on no profile at all -- that "
        "rule is the only thing standing between a gated-out contract and a green build.\n" + format_report(report)
    )
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
    assert "M99" not in {mutant.id for mutant in MUTANTS}, "a refused mutant must not reach the registry"
