"""The conformance suite: what "correct" means, independently of language.

FOR: making a rebuild verifiable rather than trusted, and giving a second
vendor a definition of done it can run against itself on the day it is
written.

INVARIANT: **a check reaches a unit only through
:class:`~vendorfake.conformance.client.ConformanceClient`.** No check receives
a unit object, imports a vendor, or knows a path it did not read from
``/__unit/routes``. That is what makes these contracts a specification rather
than a Python artifact: a consumer written in another language can execute the
same list against a running container, and the transport axis exercises every
contract rather than only the one about transports.

WHAT THIS PACKAGE MAY IMPORT. The core, the standard library, and ``httpx``.
Never a web framework -- ``tools/boundary_check.py`` asserts it twice, by
scanning imports and by importing every module here in a subprocess with
``fastapi``, ``starlette`` and ``uvicorn`` blocked at the meta path. An HTTP
transport is a client against a base URL that somebody else is serving, never
a server this package knows how to start.

HOW IT IS RUN. Three entry points over one registry, never three lists.
``run_conformance(target)`` is the framework-free façade; ``python -m
vendorfake.conformance`` is that façade with an exit code, and takes either a
``--target module:attribute`` or a ``--base-url`` pointing at a unit somebody
else is already running; ``pytest --pyargs vendorfake.conformance`` renders the
same registry as one test per (contract x profile). The pytest layer states no
contract the façade does not -- its one session-level rule is the anti-vacuity
rule :class:`~vendorfake.conformance.report.ConformanceReport` already applies,
restored in the only place a per-test rendering can assert it. A vendor
supplies one :class:`~vendorfake.conformance.types.ConformanceTarget` and gets
every contract.
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
    remote_target,
    resolve_target,
    run_check,
    run_conformance,
    select_checks,
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
]

del _checks
