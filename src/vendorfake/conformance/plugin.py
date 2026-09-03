"""The registry, rendered as one pytest test per (contract x profile x transport).

FOR: giving a vendor outside this distribution the contracts for free. It
installs the wheel, publishes one
:class:`~vendorfake.conformance.types.ConformanceTarget`, and runs::

    pytest --pyargs vendorfake.conformance -p vendorfake.conformance.plugin --conformance-target my_pkg.testing:target

Every registered contract is then applied to *its* unit on every profile *it*
declares, and a red run names ``test_contract[C13-full-inprocess]`` rather than
"the conformance suite failed".

WHY THIS FILE IMPORTS NO PYTEST. ``tools/boundary.toml`` allows this package
exactly one third-party dependency, ``httpx``. That is not an accident to be
worked around: the whole claim of the suite is that a check talks only to a
control plane, so the package must stay runnable by a consumer that has no
test runner at all. pytest's plugin protocol is duck-typed -- hooks are found
by name and handed the objects they need -- so a plugin can be written without
the import, and a skip is raised as :class:`unittest.SkipTest`, which pytest
honours and the standard library owns.

WHY THE COLLECTED MODULE IS A SIBLING. ``pytest --pyargs <package>`` collects
the test files *inside* that package; ``test_contracts.py`` is that file and
holds one function, because everything it could hold instead is already in the
registry. The parametrisation arrives through :func:`pytest_generate_tests`,
so there is no fixture to define and no second description of the matrix.

WHAT THIS LAYER ADDS OVER ``run_conformance``: exactly one thing, and it is
the reason the ledger below exists. Per-test rendering loses the cross-profile
verdict -- 96 green lines are indistinguishable from 96 green lines in which
one contract skipped on every single profile. :func:`pytest_sessionfinish`
restores it: when a run covered the whole matrix, a contract that passed
nowhere fails the session. That is the same anti-vacuity rule
:class:`~vendorfake.conformance.report.ConformanceReport` applies, asserted in
the only place a per-test rendering can assert it.
"""

from __future__ import annotations

import os
import unittest
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from vendorfake.conformance.runner import (
    TARGET_ENV_VAR,
    resolve_target,
    run_check,
    select_checks,
    skip_is_declared,
)
from vendorfake.conformance.types import (
    CheckSpec,
    ConformanceError,
    ConformanceFailure,
    ConformanceTarget,
    Outcome,
)

__all__ = ["ARGNAME", "Case", "run_case"]

ARGNAME = "conformance_case"
"""The parameter ``test_contracts.py`` takes. Also the switch this plugin
reads before doing anything, so a pytest run that has nothing to do with this
package pays one set membership and returns."""

_UNCONFIGURED = (
    f"no conformance target: pass --conformance-target module:attribute, or set {TARGET_ENV_VAR}. "
    f"This package never guesses a vendor -- see vendorfake.conformance.types.ConformanceTarget."
)


# ---------------------------------------------------------------------------
# One case.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Case:
    """One contract, aimed at one (profile, transport). What a test id names."""

    spec: CheckSpec
    target: ConformanceTarget
    profile: str
    transport: str
    strict: bool

    @property
    def case_id(self) -> str:
        return f"{self.spec.id}-{self.profile}-{self.transport}"

    def __str__(self) -> str:
        return self.case_id


def run_case(case: Case | None, ledger: _Ledger | None = None) -> None:
    """Execute one case, translating its outcome into pytest's vocabulary.

    ``None`` is the unconfigured run: one skip that says how to configure it,
    rather than N skips that each say it again.
    """
    if case is None:
        raise unittest.SkipTest(_UNCONFIGURED)
    result = run_check(case.spec, case.target, case.profile, case.transport)
    (ledger if ledger is not None else LEDGER).record(result.check_id, result.outcome)
    if result.outcome is Outcome.SKIP:
        if case.strict and not skip_is_declared(case.target, case.spec.id, case.profile):
            raise ConformanceFailure(
                f"{result.case_id} skipped and --conformance-strict refuses a skip that neither the "
                f"target's skip matrix nor conformance/manifest.json declares: {result.detail}"
            )
        raise unittest.SkipTest(f"{result.case_id}: {result.detail}")
    if result.outcome is Outcome.ERROR:
        # Reported apart from a failure, because it is a different fact: the
        # contract was never asked. A run where every case says this is a unit
        # that would not start, not a suite that found sixteen violations.
        raise ConformanceError(f"{result.case_id}: the unit could not be reached\n{result.detail}")
    if result.outcome is Outcome.FAIL:
        raise ConformanceFailure(f"{result.check_id} {result.name}\n{result.detail}")


# ---------------------------------------------------------------------------
# The cross-profile verdict a per-test rendering would otherwise lose.
# ---------------------------------------------------------------------------


@dataclass
class _Ledger:
    """What this session asked, and what came back, per contract id.

    Module-level state, deliberately: the hook that arms it and the hook that
    reads it are handed different objects and share nothing else. It is armed
    only by :func:`pytest_generate_tests`, so every pytest run that does not
    collect this package leaves it empty and the session hooks return at once.
    """

    armed: bool = False
    target_name: str = ""
    profiles: tuple[str, ...] = ()
    transports: tuple[str, ...] = ()
    whole_matrix: bool = False
    contracts: int = 0
    inapplicable: dict[str, str] = field(default_factory=dict)
    outcomes: dict[str, set[Outcome]] = field(default_factory=dict)

    def arm(
        self,
        *,
        target: ConformanceTarget,
        profiles: Sequence[str],
        transports: Sequence[str],
        specs: Sequence[CheckSpec],
        whole_matrix: bool,
    ) -> None:
        self.armed = True
        self.target_name = target.name
        self.profiles = tuple(profiles)
        self.transports = tuple(transports)
        self.whole_matrix = whole_matrix
        self.contracts = len(specs)
        self.inapplicable = dict(target.inapplicable)
        self.outcomes = {}

    def record(self, check_id: str, outcome: Outcome) -> None:
        self.outcomes.setdefault(check_id, set()).add(outcome)

    @property
    def never_passed(self) -> tuple[str, ...]:
        """Contracts that passed nowhere, less the ones the target declared
        its vendor can never be asked -- the same carve-out the report makes."""
        return tuple(
            sorted(
                cid
                for cid, outcomes in self.outcomes.items()
                if Outcome.PASS not in outcomes and cid not in self.inapplicable
            )
        )

    @property
    def stale_inapplicable(self) -> tuple[str, ...]:
        """Declared inapplicable, yet ran: the declaration is stale."""
        return tuple(
            sorted(
                cid
                for cid, outcomes in self.outcomes.items()
                if cid in self.inapplicable and outcomes != {Outcome.SKIP}
            )
        )

    @property
    def complete(self) -> bool:
        """Whether every contract this session generated actually reported.

        ``-k``, ``-x``, ``--lf`` and a distributed run all execute a subset,
        and "this contract passed nowhere" is unanswerable over a subset --
        the profile that would have passed it may simply not have run. So the
        rule applies only when every generated contract reported at least
        once, and stays silent rather than guessing otherwise.
        """
        return len(self.outcomes) == self.contracts

    def problems(self) -> tuple[str, ...]:
        if not (self.armed and self.whole_matrix and self.complete):
            return ()
        never_ran = tuple(
            f"NEVER RAN {check_id}: it passed on none of "
            f"{', '.join(self.profiles)} in this run, so it proved nothing. A universally skipped "
            f"check is a contract nobody is testing -- give it a profile that meets its "
            f"preconditions, or delete it from conformance/manifest.json."
            for check_id in self.never_passed
        )
        stale = tuple(
            f"DECLARED INAPPLICABLE BUT RAN {check_id}: the target says its vendor cannot be asked this "
            f"({self.inapplicable[check_id]}), and it ran. The gap closed; delete the declaration in the "
            f"same commit."
            for check_id in self.stale_inapplicable
        )
        return never_ran + stale


LEDGER = _Ledger()


# ---------------------------------------------------------------------------
# Hooks. Every one returns immediately unless this package was collected.
# ---------------------------------------------------------------------------


def pytest_addoption(parser: Any) -> None:
    group = parser.getgroup("conformance", "vendorfake conformance suite")
    group.addoption(
        "--conformance-target",
        dest="conformance_target",
        default=None,
        metavar="MODULE:ATTR",
        help=f"module:attribute publishing a ConformanceTarget (or set {TARGET_ENV_VAR})",
    )
    group.addoption(
        "--conformance-profile",
        dest="conformance_profiles",
        action="append",
        default=[],
        metavar="NAME",
        help="repeatable; default is every profile the target declares",
    )
    group.addoption(
        "--conformance-transport",
        dest="conformance_transports",
        action="append",
        default=[],
        metavar="NAME",
        help="repeatable; default is the first transport the target declares",
    )
    group.addoption(
        "--conformance-check",
        dest="conformance_checks",
        action="append",
        default=[],
        metavar="ID",
        help="repeatable; default is every registered contract",
    )
    group.addoption(
        "--conformance-strict",
        dest="conformance_strict",
        action="store_true",
        default=False,
        help="a skip conformance/manifest.json does not declare is a failure",
    )


def pytest_configure(config: Any) -> None:
    config.addinivalue_line("markers", "conformance: one contract from the vendorfake conformance registry")


def pytest_generate_tests(metafunc: Any) -> None:
    """Expand the registry into cases, or leave every other test alone."""
    if ARGNAME not in metafunc.fixturenames:
        return
    config = metafunc.config
    spec_text = config.getoption("conformance_target", None) or os.environ.get(TARGET_ENV_VAR)
    if not spec_text:
        metafunc.parametrize(ARGNAME, [None], ids=["target-not-configured"])
        return

    target = resolve_target(str(spec_text))
    asked_profiles: list[str] = list(config.getoption("conformance_profiles", []) or [])
    asked_transports: list[str] = list(config.getoption("conformance_transports", []) or [])
    asked_checks: list[str] = list(config.getoption("conformance_checks", []) or [])
    strict = bool(config.getoption("conformance_strict", False))

    profiles = tuple(asked_profiles) or tuple(target.profiles)
    # One transport by default: the matrix this layer exists to render is
    # contract x profile, and running every binding by default would triple a
    # downstream vendor's suite without being asked.
    transports = tuple(asked_transports) or tuple(target.transports)[:1]
    specs = select_checks(asked_checks or None)

    cases = [
        Case(spec=spec, target=target, profile=profile, transport=transport, strict=strict)
        for transport in transports
        for profile in profiles
        for spec in specs
    ]
    LEDGER.arm(
        target=target,
        profiles=profiles,
        transports=transports,
        specs=specs,
        whole_matrix=set(profiles) >= set(target.profiles) and not asked_checks,
    )
    metafunc.parametrize(ARGNAME, cases, ids=[case.case_id for case in cases])


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: Any) -> None:
    """Say what the matrix proved, not merely that the tests finished."""
    if not LEDGER.armed:
        return
    write = terminalreporter.write_line
    write("")
    write(
        f"conformance: target {LEDGER.target_name!r}, "
        f"{len(LEDGER.outcomes)} contract(s) x {len(LEDGER.profiles)} profile(s) "
        f"x {len(LEDGER.transports)} transport(s) [{', '.join(LEDGER.profiles)}]"
    )
    problems = LEDGER.problems()
    for line in problems:
        write(line, red=True)
    if problems:
        return
    if LEDGER.whole_matrix and LEDGER.complete:
        write("conformance: every contract passed on at least one profile")
    else:
        write("conformance: partial run -- the cross-profile rule was not applied")


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    """Fail the session for a contract that passed on no profile at all.

    The only assertion this layer adds, and it cannot live in a test: it is a
    statement about the whole matrix, and by the time it is answerable every
    test has already reported green.
    """
    if LEDGER.problems() and session.exitstatus == 0:
        session.exitstatus = 1
