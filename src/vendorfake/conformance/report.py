"""What a run of the suite said, and how it is rendered.

FOR: turning a list of per-check outcomes into a verdict a build can act on,
and into text a person can read at three in the morning.

INVARIANT: **a check that could not run is SKIPPED, and a skip is never a
pass; a check that was never ASKED is an ERROR, and an error is not a
failure.** The second half is a reporting rule, not a leniency: an error is
red exactly as a failure is, but it says the unit never started rather than
naming a contract that was violated, and those are different things to go and
fix.

The reference implementation's floor was ``passed >= 9`` against ten
checks, which is green for a run in which one contract was never asked -- and
"never asked" is exactly the state a check silently gated out of every profile
lands in. That floor is deliberately not reproduced. This report is ``ok``
only when nothing failed and **every check passed on at least one profile**;
under ``strict=True`` (the CI posture -- ``--strict`` on the CLI, off by
default) it additionally requires that nothing skipped that was not declared
to skip and that every declared skip actually happened. The anti-vacuity rule
is the one that holds in every mode, and it is strictly stronger than any
count: a check that skipped everywhere proved nothing, however many others
passed.

WHY THE EXPECTED-SKIP MATRIX IS DATA. Three of the shipped profiles genuinely
lack the capability some contract needs, permanently. Failing those under
``--strict`` would make strict mode unusable, and exempting them in code would
put a second, silent description of a profile next to the profile. Keeping the
pairs in ``manifest.json`` fails two ways instead, under ``--strict``: an
undeclared skip is a failure, and a declared skip that stops happening is a
failure too, because it means a profile changed and the record did not.

A second vendor brings a second matrix (``ConformanceTarget.expected_skips``)
and one more kind of record: a contract its API can never be asked -- Clover
documents no idempotency key, so the replay contract has nothing to replay.
That is ``ConformanceTarget.inapplicable``: a check id with a reason, printed
by name, dropped from the anti-vacuity rule, and guarded from the other side
-- a check declared inapplicable that runs is a stale declaration and fails.
A skip matrix alone never grants this; a contract skipped on every profile
without a stated reason is still a contract nobody tested.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from vendorfake.conformance.types import Outcome

__all__ = ["CheckResult", "ConformanceReport", "format_report"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One contract, asked once, on one profile over one transport."""

    check_id: str
    name: str
    profile: str
    transport: str
    outcome: Outcome
    #: Evidence on a pass, the fix to make on a failure, the reason on a skip.
    detail: str
    duration_ms: int

    @property
    def case_id(self) -> str:
        """``C03-oauth-only-inprocess`` -- the name a red run should print."""
        return f"{self.check_id}-{self.profile}-{self.transport}"


@dataclass(frozen=True, slots=True)
class ConformanceReport:
    """Every result from one invocation, plus the rules for reading them."""

    results: tuple[CheckResult, ...]
    strict: bool = False
    #: Check id -> profiles on which a skip is expected and permanent.
    expected_skips: Mapping[str, frozenset[str]] = field(default_factory=dict)
    #: Check id -> why the target's vendor can never be asked it. See
    #: ``ConformanceTarget.inapplicable``.
    inapplicable: Mapping[str, str] = field(default_factory=dict)
    #: Whether this run covered every profile the target declares.
    #:
    #: The anti-vacuity rule -- a check that passed nowhere is a failure -- is a
    #: statement about the whole matrix and does not exist inside a run narrowed
    #: to one profile, where a contract legitimately skips. The runner sets this
    #: from what was actually asked for, so ``--profile oauth-only`` stays
    #: usable and the aggregation stays strict.
    cross_profile: bool = True

    # -- counts -------------------------------------------------------------

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.outcome is Outcome.PASS)

    @property
    def failed(self) -> int:
        return sum(1 for result in self.results if result.outcome is Outcome.FAIL)

    @property
    def skipped(self) -> int:
        return sum(1 for result in self.results if result.outcome is Outcome.SKIP)

    @property
    def errored(self) -> int:
        return sum(1 for result in self.results if result.outcome is Outcome.ERROR)

    @property
    def errors(self) -> tuple[CheckResult, ...]:
        """Cases where the unit never got far enough to be asked anything."""
        return tuple(result for result in self.results if result.outcome is Outcome.ERROR)

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(result for result in self.results if result.outcome is Outcome.FAIL)

    # -- the three rules ----------------------------------------------------

    @property
    def never_ran(self) -> tuple[str, ...]:
        """Checks that passed on no profile at all. They proved nothing.

        A check the target declares inapplicable -- by name, with a reason --
        is not counted: it was never a contract this vendor could be asked.
        A check that merely skipped everywhere still is.
        """
        seen = {result.check_id for result in self.results}
        passed = {result.check_id for result in self.results if result.outcome is Outcome.PASS}
        return tuple(sorted(seen - passed - set(self.inapplicable)))

    @property
    def stale_inapplicable(self) -> tuple[str, ...]:
        """Checks declared inapplicable that ran anyway (passed or failed).

        The declaration outlived the gap it described -- the vendor grew the
        surface -- and the record must move in the same commit.
        """
        ran = {result.check_id for result in self.results if result.outcome in (Outcome.PASS, Outcome.FAIL)}
        return tuple(sorted(check_id for check_id in self.inapplicable if check_id in ran))

    @property
    def undeclared_skips(self) -> tuple[str, ...]:
        """Skips the manifest does not account for, as ``C08-no-faults``."""
        out: list[str] = []
        for result in self.results:
            if result.outcome is not Outcome.SKIP:
                continue
            if result.profile in self.expected_skips.get(result.check_id, frozenset()):
                continue
            if result.check_id in self.inapplicable:
                continue
            out.append(f"{result.check_id}-{result.profile}")
        return tuple(sorted(set(out)))

    @property
    def stale_expected_skips(self) -> tuple[str, ...]:
        """Declared skips that did not happen on a profile this run covered.

        A profile gained the capability a contract needs and nobody updated the
        record. Harmless to behaviour, fatal to the claim that the matrix
        describes the profiles.
        """
        ran = {(result.check_id, result.profile): result.outcome for result in self.results}
        out: list[str] = []
        for check_id, profiles in self.expected_skips.items():
            for profile in profiles:
                outcome = ran.get((check_id, profile))
                if outcome is not None and outcome is not Outcome.SKIP:
                    out.append(f"{check_id}-{profile}")
        return tuple(sorted(out))

    @property
    def problems(self) -> tuple[str, ...]:
        """Every reason this report is not ``ok``, in the order to read them."""
        out: list[str] = []
        # Errors first, and deliberately: when a unit will not construct every
        # contract errors at once, and the reader needs to see "this unit did
        # not start" before a wall of contract names that were never asked.
        for result in self.errors:
            out.append(
                f"ERRORED {result.case_id}: the unit could not be reached, so {result.check_id} was "
                f"never asked and this run proves nothing about it."
            )
        for result in self.failures:
            out.append(f"FAILED {result.case_id}: {result.name}")
        for check_id in self.never_ran if self.cross_profile else ():
            out.append(
                f"NEVER RAN {check_id}: it passed on no profile in this run, so it proved nothing. "
                f"A universally skipped check is a contract nobody is testing -- give it a profile "
                f"that meets its preconditions, or delete it from conformance/manifest.json."
            )
        for check_id in self.stale_inapplicable:
            out.append(
                f"DECLARED INAPPLICABLE BUT RAN {check_id}: the target says its vendor cannot be asked this "
                f"({self.inapplicable[check_id]}), and it ran. The gap closed; delete the declaration in the same "
                f"commit."
            )
        if self.strict:
            for case in self.undeclared_skips:
                out.append(
                    f"UNDECLARED SKIP {case}: strict mode refuses a skip that conformance/manifest.json "
                    f"does not list under expected_skips. Either the profile lost a capability, or the "
                    f"skip is legitimate and belongs in the manifest with the rest."
                )
            for case in self.stale_expected_skips:
                out.append(
                    f"STALE EXPECTED SKIP {case}: conformance/manifest.json says this pair always skips, "
                    f"and it did not. The profile changed; update expected_skips in the same commit."
                )
        return tuple(out)

    @property
    def ok(self) -> bool:
        return not self.problems


_MARK = {Outcome.PASS: "PASS", Outcome.FAIL: "FAIL", Outcome.SKIP: "SKIP", Outcome.ERROR: "ERR!"}


def format_report(report: ConformanceReport) -> str:
    """The whole run as text, grouped by (profile, transport).

    Every line carries the evidence or the reason, because a report that only
    says which checks passed is a report nobody can use to tell whether the
    run examined anything.
    """
    lines: list[str] = []
    seen: list[tuple[str, str]] = []
    for result in report.results:
        pair = (result.profile, result.transport)
        if pair not in seen:
            seen.append(pair)
    for profile, transport in seen:
        lines.append(f"== {profile} / {transport} ==")
        for result in report.results:
            if (result.profile, result.transport) != (profile, transport):
                continue
            lines.append(f"[{_MARK[result.outcome]}] {result.check_id} {result.name} ({result.duration_ms}ms)")
            for line in result.detail.splitlines():
                lines.append(f"        {line}")
        lines.append("")
    lines.append(f"{report.passed} passed, {report.failed} failed, {report.skipped} skipped, {report.errored} errored")
    for check_id, reason in sorted(report.inapplicable.items()):
        lines.append(f"inapplicable to this target: {check_id} -- {reason}")
    if report.never_ran:
        note = "" if report.cross_profile else " (informational: this run covered only part of the profile matrix)"
        lines.append(f"never ran on any profile in this run: {', '.join(report.never_ran)}{note}")
    if report.strict and report.undeclared_skips:
        lines.append(f"undeclared skips: {', '.join(report.undeclared_skips)}")
    if report.strict and report.stale_expected_skips:
        lines.append(f"stale expected skips: {', '.join(report.stale_expected_skips)}")
    lines.append("OK" if report.ok else "NOT OK")
    return "\n".join(lines)
