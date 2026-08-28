"""Running the registry: one check, or all of them, over profiles and transports.

FOR: being the framework-free façade. No pytest, no web framework, no vendor.
A container's healthcheck and an external vendor without a test runner both get
an exit code from here; the pytest layer is a second rendering over the same
registry, and the only rule it states that this module does not state inline is
the one this module states in the report -- every contract passed on at least
one profile.

INVARIANT: **each check gets its own freshly built unit.** The reference shares
one unit across all ten of its checks and mutates it -- capabilities toggled in
one, chaos rules installed in another -- which makes check order load-bearing
and lets a failure part-way through one contract poison every contract after
it. In-process construction costs milliseconds. Isolation removes the whole
class of flake, and it makes "two fresh units agree" the ordinary case rather
than a special one.

SECOND INVARIANT: **an unexpected exception is red, never a skip.** A suite
that turned a crash into a skip would report the emptiest possible run as its
cleanest. But red comes in two kinds and they are reported as two: an
exception raised *inside* a check body is a FAILURE of that contract, while an
exception raised while the unit is being CONSTRUCTED or reached is an ERROR --
the contract was never asked, so nothing at all was learned about it.

That split was bought with a measurement. Deleting one core-gated capability
declaration from a vendor makes ``Unit`` refuse to start, which used to print
``[FAIL] C11`` -- indistinguishable from C11 having been asked and having
found the declaration missing, and identical to what every other contract
printed at the same moment. One line of the report now says which happened.
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
    "remote_target",
    "resolve_target",
    "run_check",
    "run_conformance",
    "select_checks",
]

TARGET_ENV_VAR = "VENDORFAKE_CONFORMANCE_TARGET"
"""Where both renderings look for a target when no flag names one.

One spelling, read by the CLI and by the pytest plugin, so that a vendor
wiring the suite into CI sets it once and both entry points obey it.
"""

REMOTE_TRANSPORT = "http"
"""The only transport a unit somebody else is running can be reached over."""

REMOTE_CAVEAT = (
    "a unit reached over --base-url is SHARED, not rebuilt per check. Capabilities and seed state are "
    "restored before every contract, but two 'fresh' units are the same process, so the determinism "
    "contracts compare a unit with itself; and the contracts that exercise fault injection reset the "
    "chaos engine, which drops rules the profile configured at startup. Point this at a throwaway "
    "container, never at one another test is using."
)
"""Printed by every run against a foreign unit. See :func:`remote_target`."""


def resolve_target(spec: str) -> ConformanceTarget:
    """``my_package.testing:target`` -> the target itself.

    Lives here rather than in either entry point because the CLI and the pytest
    plugin must resolve a target the same way; two spellings of "find the
    vendor" is two places for them to disagree about what was tested.

    A zero-argument callable is called, so a vendor may publish either a
    module-level target or a factory; anything else raises :class:`LookupError`
    naming what was found, because "your target is a string" is a better
    failure than an attribute error thrown from inside the runner.
    """
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
    """A target for a unit somebody else is already running.

    FOR: pointing the contracts at a container. The suite never starts a
    server -- that would mean importing the framework the core exists to stay
    clear of -- so the address is passed in and this builds a client against
    it.

    The profile and the vendor's name are DISCOVERED from the control plane
    rather than passed as flags: the running unit already knows which profile
    it loaded, and a flag would only introduce a second answer that could be
    wrong. The run is therefore single-profile, which is why the caller passes
    ``cross_profile=False`` to :func:`run_conformance`: "every contract passed
    on some profile" is a statement about a matrix, and one container is not
    one.

    WHAT IS RESTORED BETWEEN CHECKS, and what cannot be. Capabilities are put
    back to the set the unit started with and the seed scenario is re-applied,
    so a contract that toggles a capability or writes an entity does not
    change what the next one sees. A freshly *constructed* unit is out of
    reach by definition -- see :data:`REMOTE_CAVEAT`, which every such run
    prints.
    """
    probe = HttpConformanceClient(base_url)
    try:
        try:
            answered = probe.call("GET", f"{CONTROL_PREFIX}info")
        except httpx.HTTPError as exc:
            # A refused connection is a usage error, not a crash: the caller
            # typed an address, and the message has to be the address.
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
    """Put a shared unit back to the shape the run found it in.

    A restore that quietly failed would be worse than no restore at all: every
    later contract would be reading a unit an earlier one had changed, and the
    report would call it conformance. So a non-2xx answer raises, and
    :func:`run_check` turns that into a FAIL naming the route.
    """
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
    """``check_env``, with a construction crash relabelled as an ERROR.

    The whole of the distinction lives here, in the narrowest place it can:
    only what ``target.open_client`` does on the way *in* is reclassified.
    Once a client exists the context is a plain pass-through, so an exception
    a check body raises is still that check body's failure and cannot be
    laundered into "the harness is broken".
    """
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
    """Ask every selected contract of every selected (profile, transport).

    ``cross_profile`` overrides the derivation for the one case the derivation
    cannot see: a run against a unit somebody else is running. Such a target
    honestly declares the single profile it loaded, so "this run covered every
    profile the target declares" is true and the anti-vacuity rule would fire
    on every contract the container's profile cannot meet. Passing ``False``
    says "this was never a matrix"; nothing else may pass it, and the report
    prints the resulting never-ran list as informational rather than dropping
    it.
    """
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
        expected_skips=expected_skips(),
        cross_profile=derived if cross_profile is None else cross_profile,
    )
