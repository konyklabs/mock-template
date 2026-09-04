"""What a run of the suite said, and how it is rendered.

A check that could not run is SKIPPED, never a pass; a check never ASKED is an ERROR, distinct from a FAIL that names a violated contract -- both are red, but only FAIL points at something to fix.

A report is ``ok`` only when nothing failed and every check passed on at least one profile (the anti-vacuity rule, which holds in every mode and is stronger than any count). Under ``strict=True`` it additionally requires that nothing skipped undeclared and every declared skip actually happened.

The expected-skip matrix (``manifest.json``, or ``ConformanceTarget.expected_skips`` for a second vendor) records skips that are permanent and legitimate, so ``--strict`` fails two ways instead of exempting them silently: an undeclared skip, and a declared skip that stops happening. ``ConformanceTarget.inapplicable`` covers the further case where a vendor's API has no such contract at all (e.g. no idempotency key) -- printed by name, dropped from the anti-vacuity rule, and flagged stale if it ever runs.
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
    #: Check id -> why strict mode refuses this run for never having observed it, computed by the runner. Distinct from ``never_ran``, which is about the matrix and is off for a one-profile container run (konyklabs/roadmap#15).
    unobserved: Mapping[str, str] = field(default_factory=dict)
    #: Whether this run covered every profile the target declares. The anti-vacuity rule doesn't hold inside a run narrowed to one profile; the runner sets this from what was actually asked for.
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
        """Checks that passed on no profile at all -- they proved nothing. A check declared inapplicable is excluded; one that merely skipped everywhere is not."""
        seen = {result.check_id for result in self.results}
        passed = {result.check_id for result in self.results if result.outcome is Outcome.PASS}
        return tuple(sorted(seen - passed - set(self.inapplicable)))

    @property
    def stale_inapplicable(self) -> tuple[str, ...]:
        """Checks declared inapplicable that ran anyway (passed or failed) -- the declaration outlived the gap it described and must move in the same commit."""
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
        """Declared skips that did not happen on a profile this run covered -- the profile gained the capability and nobody updated the record."""
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
        # Errors first: "this unit did not start" belongs before a wall of never-asked contract names.
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
            for check_id, reason in sorted(self.unobserved.items()):
                out.append(
                    f"NEVER OBSERVED {check_id}: {reason} Strict mode refuses to certify a unit on a contract "
                    f"no profile in the run could ask, whether or not each skip was declared."
                )
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
    """The whole run as text, grouped by (profile, transport); every line carries the evidence or the reason, not just a pass/fail mark."""
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
    for check_id in report.stale_inapplicable:
        lines.append(
            f"DECLARED INAPPLICABLE BUT RAN {check_id}: the target says its vendor cannot be asked this "
            f"({report.inapplicable[check_id]}), and it ran -- delete the declaration in the same commit"
        )
    if report.never_ran:
        note = "" if report.cross_profile else " (informational: this run covered only part of the profile matrix)"
        lines.append(f"never ran on any profile in this run: {', '.join(report.never_ran)}{note}")
    for check_id, reason in sorted(report.unobserved.items()) if report.strict else ():
        lines.append(f"never observed in this run: {check_id} -- {reason}")
    if report.strict and report.undeclared_skips:
        lines.append(f"undeclared skips: {', '.join(report.undeclared_skips)}")
    if report.strict and report.stale_expected_skips:
        lines.append(f"stale expected skips: {', '.join(report.stale_expected_skips)}")
    lines.append("OK" if report.ok else "NOT OK")
    return "\n".join(lines)
