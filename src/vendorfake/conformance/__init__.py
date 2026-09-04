"""The conformance suite: what "correct" means, independent of language.

A check reaches a unit only through :class:`~vendorfake.conformance.client.ConformanceClient` -- never a unit object, an import of a vendor, or a path not read from ``/__unit/routes`` -- so the same contract list can run from another language against a running container.

This package imports only the core, the standard library and ``httpx``, never a web framework (enforced by ``tools/boundary_check.py``). An HTTP transport is a client against a base URL somebody else is serving, never a server this package starts.

Three entry points share one registry: ``run_conformance(target)``; ``python -m vendorfake.conformance``, the same façade with an exit code via ``--target`` or ``--base-url``; and ``pytest --pyargs vendorfake.conformance -p vendorfake.conformance.plugin``, one test per (contract x profile) -- both flags are required since ``--pyargs`` only selects tests (see ``plugin.py``). A vendor supplies one :class:`~vendorfake.conformance.types.ConformanceTarget` and gets every contract.
"""

from __future__ import annotations

import vendorfake.conformance.checks as _checks
from vendorfake.conformance.client import (
    ConformanceClient,
    ConformanceResponse,
    HttpConformanceClient,
    InProcessConformanceClient,
)
from vendorfake.conformance.env import CheckEnv, concrete_path
from vendorfake.conformance.registry import CHECKS, check, expected_skips, find_check, load_manifest
from vendorfake.conformance.report import CheckResult, ConformanceReport, format_report
from vendorfake.conformance.runner import (
    REMOTE_CAVEAT,
    TARGET_ENV_VAR,
    declared_skips,
    remote_target,
    resolve_target,
    run_check,
    run_conformance,
    select_checks,
    skip_is_declared,
)
from vendorfake.conformance.types import (
    CheckSpec,
    ConformanceFailure,
    ConformanceSkip,
    ConformanceTarget,
    Outcome,
    Requires,
    require,
)

__all__ = [
    "CHECKS",
    "REMOTE_CAVEAT",
    "TARGET_ENV_VAR",
    "CheckEnv",
    "CheckResult",
    "CheckSpec",
    "ConformanceClient",
    "ConformanceFailure",
    "ConformanceReport",
    "ConformanceResponse",
    "ConformanceSkip",
    "ConformanceTarget",
    "HttpConformanceClient",
    "InProcessConformanceClient",
    "Outcome",
    "Requires",
    "check",
    "concrete_path",
    "declared_skips",
    "expected_skips",
    "find_check",
    "format_report",
    "load_manifest",
    "remote_target",
    "require",
    "resolve_target",
    "run_check",
    "run_conformance",
    "select_checks",
    "skip_is_declared",
]

del _checks
