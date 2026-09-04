"""Running the registry: one check, or all of them, over profiles and transports.

The framework-free façade -- no pytest, no web framework, no vendor. A container's healthcheck and an external vendor without a test runner both get an exit code from here; the pytest layer states no rule this module does not state inline.

Each check gets its own freshly built unit, never one shared and mutated across checks, so check order is never load-bearing and one contract's failure cannot poison the ones after it.

An unexpected exception is red, never a skip -- but red comes in two kinds: an exception raised inside a check body is a FAILURE of that contract, while one raised while the unit is being constructed or reached is an ERROR, because the contract was never asked and nothing was learned about it.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from importlib import import_module
from typing import Any

import httpx

from vendorfake.conformance.client import ConformanceClient, HttpConformanceClient
from vendorfake.conformance.env import CONTROL_PREFIX, check_env, unmet_precondition
from vendorfake.conformance.registry import CHECKS, expected_skips, find_check
from vendorfake.conformance.report import CheckResult, ConformanceReport
from vendorfake.conformance.types import (
    CheckSpec,
    ConformanceError,
    ConformanceFailure,
    ConformanceSkip,
    ConformanceTarget,
    Outcome,
)

__all__ = [
    "REMOTE_CAVEAT",
    "TARGET_ENV_VAR",
    "declared_skips",
    "remote_target",
    "resolve_target",
    "run_check",
    "run_conformance",
    "select_checks",
    "skip_is_declared",
    "unobserved_contracts",
]

TARGET_ENV_VAR = "VENDORFAKE_CONFORMANCE_TARGET"
"""Where both renderings look for a target when no flag names one -- one spelling, read by the CLI and the pytest plugin."""

REMOTE_TRANSPORT = "http"
"""The only transport a unit somebody else is running can be reached over."""

REMOTE_CAVEAT = (
    "a unit reached over --base-url is SHARED, not rebuilt per check. Capabilities and seed state are "
    "restored before every contract, but two 'fresh' units are the same process, so the determinism "
    "contracts -- C30's read-inertness pair included -- compare a unit with itself and prove less "
    "here than against real pairs; and the contracts that exercise fault injection reset the "
    "chaos engine, which drops rules the profile configured at startup. Point this at a throwaway "
    "container, never at one another test is using."
)
"""Printed by every run against a foreign unit. See :func:`remote_target`."""


def resolve_target(spec: str) -> ConformanceTarget:
    """``my_package.testing:target`` -> the target itself. Shared by the CLI and the pytest plugin so both resolve a target the same way. A zero-argument callable is called, so a vendor may publish either a module-level target or a factory; anything else raises :class:`LookupError` naming what was found."""
    module_name, _, attribute = spec.partition(":")
    found = getattr(import_module(module_name), attribute or "target")
    if isinstance(found, ConformanceTarget):
        return found
    if callable(found):
        built = found()
        if isinstance(built, ConformanceTarget):
            return built
    raise LookupError(
        f"{spec} is {type(found).__name__}, not a ConformanceTarget or a callable returning one. "
        f"Publish a ConformanceTarget -- see vendorfake.conformance.types.ConformanceTarget."
    )


def remote_target(base_url: str) -> ConformanceTarget:
    """A target for a unit somebody else is already running. The suite never starts a server itself, so the address is passed in and this builds a client against it.

    The profile and vendor name are discovered from the control plane rather than passed as flags, since the running unit already knows which profile it loaded. The run is therefore single-profile: the caller passes ``cross_profile=False`` to :func:`run_conformance`, because "every contract passed on some profile" is a statement about a matrix and one container is not one.

    Between checks, capabilities are put back to the set the unit started with and the seed scenario is re-applied. A freshly constructed unit is out of reach by definition -- see :data:`REMOTE_CAVEAT`, which every such run prints.
    """
    probe = HttpConformanceClient(base_url)
    try:
        try:
            answered = probe.call("GET", f"{CONTROL_PREFIX}info")
        except httpx.HTTPError as exc:
            # A refused connection is a usage error, not a crash.
            raise LookupError(f"cannot reach a unit at {base_url}: {type(exc).__name__}: {exc}") from exc
        if answered.status != 200:
            raise LookupError(
                f"GET {base_url.rstrip('/')}{CONTROL_PREFIX}info answered {answered.status}, expected 200. "
                f"--base-url must address a running unit, whose control plane answers on every profile."
            )
        info = answered.json()
        profile = str(info["profile"])
        vendor = info.get("vendor") or {}
        name = str(vendor.get("name") or base_url)
        baseline = tuple(str(row["name"]) for row in info["capabilities"] if row["enabled"])
    finally:
        probe.close()

    @contextmanager
    def open_client(_profile: str, transport: str) -> Iterator[ConformanceClient]:
        if transport != REMOTE_TRANSPORT:
            raise ValueError(f"a remote target speaks only {REMOTE_TRANSPORT!r}, not {transport!r}")
        client = HttpConformanceClient(base_url)
        try:
            _restore(client, baseline)
            yield client
        finally:
            client.close()

    return ConformanceTarget(
        name=name,
        open_client=open_client,
        profiles=(profile,),
        transports=(REMOTE_TRANSPORT,),
    )


def _restore(client: ConformanceClient, capabilities: Sequence[str]) -> None:
    """Put a shared unit back to the shape the run found it in. A non-2xx answer raises, rather than let a later contract silently read a unit an earlier one changed."""
    for method, path, body in (
        ("POST", f"{CONTROL_PREFIX}capabilities", {"set": list(capabilities)}),
        ("POST", f"{CONTROL_PREFIX}state/reset", {}),
    ):
        answered = client.call(method, path, json_body=body)
        if answered.status // 100 != 2:
            raise ConformanceFailure(
                f"{method} {path} answered {answered.status} while restoring the shared unit between "
                f"contracts, so the next contract would read a unit an earlier one had changed. "
                f"Body: {answered.text[:300]}"
            )


def select_checks(ids: Sequence[str] | None) -> tuple[CheckSpec, ...]:
    """The registry, or the named subset in registration order."""
    if not ids:
        return tuple(CHECKS)
    wanted = {check_id.upper() for check_id in ids}
    return tuple(find_check(check_id) for check_id in sorted(wanted))


@contextmanager
def _reached(target: ConformanceTarget, profile: str, transport: str) -> Iterator[Any]:
    """``check_env``, with a construction crash relabelled as an ERROR. Only what ``target.open_client`` does on the way in is reclassified; once a client exists this is a plain pass-through."""
    try:
        manager = check_env(target, profile, transport)
        env = manager.__enter__()
    except ConformanceSkip:
        raise
    except Exception as exc:
        raise ConformanceError(
            f"the unit could not be constructed on profile {profile!r} over {transport!r}: "
            f"{type(exc).__name__}: {exc}\n"
            f"This contract was never asked, so this run says nothing about it -- and it says "
            f"nothing about any other contract either, because they all failed the same way. "
            f"Fix the construction failure first; a unit that refuses to start is a startup "
            f"assertion doing its job, not sixteen violated contracts."
        ) from exc
    try:
        yield env
    finally:
        manager.__exit__(None, None, None)


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
        with _reached(target, profile, transport) as env:
            unmet = unmet_precondition(spec.requires, env)
            if unmet is not None:
                raise ConformanceSkip(unmet)
            detail = spec.fn(env)
    except ConformanceSkip as skip:
        outcome, detail = Outcome.SKIP, str(skip)
    except ConformanceError as error:
        outcome, detail = Outcome.ERROR, str(error)
    except ConformanceFailure as failure:
        outcome, detail = Outcome.FAIL, str(failure)
    except Exception as exc:
        outcome = Outcome.FAIL
        detail = (
            f"the check raised {type(exc).__name__}: {exc}\n"
            f"An unexpected exception from inside a check body is a failure, not a skip: the "
            f"contract was asked and {spec.id} could not be satisfied. An exception from unit "
            f"CONSTRUCTION is reported as ERROR instead -- see _reached()."
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
    cross_profile: bool | None = None,
) -> ConformanceReport:
    """Ask every selected contract of every selected (profile, transport). ``cross_profile`` overrides the derivation for the one case it cannot see: a run against a unit somebody else is running, which honestly declares only the one profile it loaded. Passing ``False`` says "this was never a matrix"; the report then prints the never-ran list as informational rather than failing on it."""
    specs = select_checks(check_ids)
    chosen_profiles = tuple(profiles) if profiles else tuple(target.profiles)
    chosen_transports = tuple(transports) if transports else tuple(target.transports)
    results = tuple(
        run_check(spec, target, profile, transport)
        for transport in chosen_transports
        for profile in chosen_profiles
        for spec in specs
    )
    derived = set(chosen_profiles) >= set(target.profiles) and not check_ids
    return ConformanceReport(
        results=results,
        strict=strict,
        expected_skips=declared_skips(target),
        inapplicable=dict(target.inapplicable),
        unobserved=unobserved_contracts(specs, results) if strict else {},
        cross_profile=derived if cross_profile is None else cross_profile,
    )


def unobserved_contracts(specs: Sequence[CheckSpec], results: Sequence[CheckResult]) -> dict[str, str]:
    """Contracts whose precondition is about the RUN, not the vendor, and that no (profile, transport) in the run could meet. Only the virtual clock today: unlike a missing capability, it is a property of how the unit was started, so a run that never offered one has not skipped for a reason about the vendor -- it has simply not looked, and under ``--strict`` that is a failure rather than a declared skip."""
    out: dict[str, str] = {}
    for spec in specs:
        if not spec.requires.virtual_clock:
            continue
        asked = [result for result in results if result.check_id == spec.id]
        if asked and all(result.outcome is Outcome.SKIP for result in asked):
            profiles = sorted({result.profile for result in asked})
            out[spec.id] = (
                f"no profile in this run ({', '.join(profiles)}) offered a virtual clock, so the "
                f"declared retry schedule was never observed being followed. Start the unit with "
                f"clock.mode=virtual (VENDORFAKE_CLOCK=virtual for a container) on at least one profile."
            )
    return out


def declared_skips(target: ConformanceTarget) -> dict[str, frozenset[str]]:
    """The skip matrix a target answers to: its own when it names one, the manifest's otherwise -- one resolution shared by the report, the pytest plugin and any test that renders a skip."""
    if target.expected_skips is None:
        return expected_skips()
    return {check_id: frozenset(profiles) for check_id, profiles in target.expected_skips.items()}


def skip_is_declared(target: ConformanceTarget, check_id: str, profile: str) -> bool:
    """Whether a skip of ``check_id`` on ``profile`` is one the target declared: in its skip matrix, or as a contract its vendor can never be asked (``inapplicable``)."""
    return check_id in target.inapplicable or profile in declared_skips(target).get(check_id, frozenset())
