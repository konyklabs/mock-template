"""Running the registry: one check, or all of them, over profiles and transports.

FOR: being the framework-free façade. No pytest, no web framework, no vendor.
A container's healthcheck and an external vendor without a test runner both get
an exit code from here; the pytest layer is a second rendering over the same
registry and adds no assertion of its own.

INVARIANT: **each check gets its own freshly built unit.** The reference shares
one unit across all ten of its checks and mutates it -- capabilities toggled in
one, chaos rules installed in another -- which makes check order load-bearing
and lets a failure part-way through one contract poison every contract after
it. In-process construction costs milliseconds. Isolation removes the whole
class of flake, and it makes "two fresh units agree" the ordinary case rather
than a special one.

SECOND INVARIANT: **an unexpected exception is a FAILURE, never a skip.** A
vendor whose unit cannot even be constructed must go red. A suite that turned
a crash into a skip would report the emptiest possible run as its cleanest.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from vendorfake.conformance.env import check_env, unmet_precondition
from vendorfake.conformance.registry import CHECKS, expected_skips, find_check
from vendorfake.conformance.report import CheckResult, ConformanceReport
from vendorfake.conformance.types import (
    CheckSpec,
    ConformanceFailure,
    ConformanceSkip,
    ConformanceTarget,
    Outcome,
)

__all__ = ["run_check", "run_conformance", "select_checks"]


def select_checks(ids: Sequence[str] | None) -> tuple[CheckSpec, ...]:
    """The registry, or the named subset in registration order."""
    if not ids:
        return tuple(CHECKS)
    wanted = {check_id.upper() for check_id in ids}
    return tuple(find_check(check_id) for check_id in sorted(wanted))


def run_check(
    spec: CheckSpec,
    target: ConformanceTarget,
    profile: str,
    transport: str = "inprocess",
) -> CheckResult:
    """Ask one contract of one freshly built unit."""
    started = time.perf_counter()
    outcome = Outcome.PASS
    detail = ""
    try:
        with check_env(target, profile, transport) as env:
            unmet = unmet_precondition(spec.requires, env)
            if unmet is not None:
                raise ConformanceSkip(unmet)
            detail = spec.fn(env)
    except ConformanceSkip as skip:
        outcome, detail = Outcome.SKIP, str(skip)
    except ConformanceFailure as failure:
        outcome, detail = Outcome.FAIL, str(failure)
    except Exception as exc:
        outcome = Outcome.FAIL
        detail = (
            f"the check raised {type(exc).__name__}: {exc}\n"
            f"An unexpected exception is a failure, not a skip: the unit could not be driven "
            f"far enough to say anything about {spec.id}."
        )
    return CheckResult(
        check_id=spec.id,
        name=spec.name,
        profile=profile,
        transport=transport,
        outcome=outcome,
        detail=detail,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )


def run_conformance(
    target: ConformanceTarget,
    *,
    profiles: Sequence[str] | None = None,
    transports: Sequence[str] | None = None,
    check_ids: Sequence[str] | None = None,
    strict: bool = False,
) -> ConformanceReport:
    """Ask every selected contract of every selected (profile, transport)."""
    specs = select_checks(check_ids)
    chosen_profiles = tuple(profiles) if profiles else tuple(target.profiles)
    chosen_transports = tuple(transports) if transports else tuple(target.transports)
    results = tuple(
        run_check(spec, target, profile, transport)
        for transport in chosen_transports
        for profile in chosen_profiles
        for spec in specs
    )
    return ConformanceReport(
        results=results,
        strict=strict,
        expected_skips=expected_skips(),
        cross_profile=set(chosen_profiles) >= set(target.profiles) and not check_ids,
    )
