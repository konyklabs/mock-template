"""The corpus, rendered as one pytest test per case.

FOR: giving a vendor the behaviour leg for free inside its own test run::

    pytest -p vendorfake.fidelity.plugin --fidelity-target my_pkg.testing:fidelity_target

Any test function taking ``fidelity_case`` is parametrised with every case
of the target's corpus, and a red run names ``test_case[orders.create.minimal]``
and prints the step, the pointer and both values.

WHY THIS FILE IMPORTS NO PYTEST: the same rule as ``conformance/plugin.py``.
``tools/boundary.toml`` names the third-party packages this layer may import
and a test runner is not one of them; pytest's hooks are found by name, and a
skip is ``unittest.SkipTest``, which the standard library owns.

WHAT THIS LAYER ADDS OVER ``run_corpus``: the terminal summary by provenance.
Per-test rendering loses "how many documented facts hold versus how many
judgments", and that split is the point of recording provenance at all.
"""

from __future__ import annotations

import unittest
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from vendorfake.fidelity.corpus import Case, load_corpus
from vendorfake.fidelity.runner import (
    TARGET_ENV_VAR,
    FidelityTarget,
    resolve_target,
    run_corpus,
    target_from_env,
)

__all__ = ["ARGNAME", "FidelityCaseFailure", "PluginCase", "run_case"]

ARGNAME = "fidelity_case"
"""The parameter a test takes to receive one case. Also the switch this plugin
reads before doing anything, so a pytest run with no such test pays one set
membership and returns."""

_UNCONFIGURED = (
    f"no fidelity target: pass --fidelity-target module:attribute, or set {TARGET_ENV_VAR}. "
    f"This package never guesses a vendor -- see vendorfake.fidelity.runner.FidelityTarget."
)


class FidelityCaseFailure(AssertionError):
    """A corpus case whose expectation did not hold."""


@dataclass(frozen=True, slots=True)
class PluginCase:
    """One case aimed at one target. What a test id names."""

    case: Case
    target: FidelityTarget
    profile: str | None
    validate: bool

    @property
    def case_id(self) -> str:
        return self.case.id

    def __str__(self) -> str:
        return self.case_id


@dataclass
class _Ledger:
    """What this session ran, per provenance. Armed only by ``pytest_generate_tests``."""

    armed: bool = False
    target_name: str = ""
    validated: bool = True
    counts: dict[str, list[int]] = field(default_factory=dict)

    def arm(self, *, target: FidelityTarget, validate: bool) -> None:
        self.armed = True
        self.target_name = target.name
        self.validated = validate
        self.counts = {}

    def record(self, provenance: str, passed: bool) -> None:
        row = self.counts.setdefault(provenance, [0, 0])
        row[0 if passed else 1] += 1


LEDGER = _Ledger()


def run_case(case: PluginCase | None, ledger: _Ledger | None = None) -> None:
    """Execute one case, translating its outcome into pytest's vocabulary."""
    if case is None:
        raise unittest.SkipTest(_UNCONFIGURED)
    report = run_corpus(case.target, (case.case,), profile_override=case.profile, validate=case.validate)
    (ledger if ledger is not None else LEDGER).record(case.case.provenance, report.ok)
    result = report.results[0]
    if result.failure is not None:
        lines = "\n".join(result.failure.lines())
        raise FidelityCaseFailure(
            f"{result.id} ({result.provenance}) {result.title}\n{lines}\nsource: {case.case.source.url}"
        )


# ---------------------------------------------------------------------------
# Hooks. Every one returns immediately unless a test asked for a case.
# ---------------------------------------------------------------------------


def pytest_addoption(parser: Any) -> None:
    group = parser.getgroup("fidelity", "vendorfake fidelity corpus")
    group.addoption(
        "--fidelity-target",
        dest="fidelity_target",
        default=None,
        metavar="MODULE:ATTR",
        help=f"module:attribute publishing a FidelityTarget (or set {TARGET_ENV_VAR})",
    )
    group.addoption(
        "--fidelity-case",
        dest="fidelity_cases",
        action="append",
        default=[],
        metavar="ID",
        help="repeatable; default is every case in the corpus",
    )
    group.addoption(
        "--fidelity-profile",
        dest="fidelity_profile",
        default=None,
        metavar="NAME",
        help="run every case on this profile instead of the case's own",
    )
    group.addoption(
        "--fidelity-no-validate",
        dest="fidelity_no_validate",
        action="store_true",
        default=False,
        help="plain client, no schema validation of responses",
    )


def pytest_configure(config: Any) -> None:
    config.addinivalue_line("markers", "fidelity: one case from a vendorfake fidelity corpus")


def pytest_generate_tests(metafunc: Any) -> None:
    """Expand the corpus into cases, or leave every other test alone."""
    if ARGNAME not in metafunc.fixturenames:
        return
    config = metafunc.config
    spec_text = config.getoption("fidelity_target", None) or target_from_env()
    if not spec_text:
        metafunc.parametrize(ARGNAME, [None], ids=["target-not-configured"])
        return

    target = resolve_target(str(spec_text))
    asked: Sequence[str] = list(config.getoption("fidelity_cases", []) or [])
    profile = config.getoption("fidelity_profile", None)
    validate = not bool(config.getoption("fidelity_no_validate", False))
    cases = load_corpus(target.anchor)
    if asked:
        wanted = set(asked)
        unknown = wanted - {case.id for case in cases}
        if unknown:
            raise LookupError(f"no such case(s): {', '.join(sorted(unknown))}")
        cases = tuple(case for case in cases if case.id in wanted)
    LEDGER.arm(target=target, validate=validate)
    plugin_cases = [PluginCase(case=case, target=target, profile=profile, validate=validate) for case in cases]
    metafunc.parametrize(ARGNAME, plugin_cases, ids=[case.case_id for case in plugin_cases])


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: Any) -> None:
    """Pass and fail counts by provenance, which per-test output cannot show."""
    if not LEDGER.armed:
        return
    write = terminalreporter.write_line
    write("")
    parts = [f"{name}: {row[0]} passed, {row[1]} failed" for name, row in sorted(LEDGER.counts.items())]
    total_failed = sum(row[1] for row in LEDGER.counts.values())
    write(
        f"fidelity: target {LEDGER.target_name!r}, {sum(sum(row) for row in LEDGER.counts.values())} case(s) "
        f"[{'; '.join(parts) or 'nothing ran'}]"
        f"{'' if LEDGER.validated else ' -- responses NOT validated against the schema'}",
        red=total_failed > 0,
    )
