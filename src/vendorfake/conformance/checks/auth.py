"""C17 -- the unit actually authenticates somebody, on every route that declares auth.

This is a class check, not a check of one route (konyklabs/roadmap#15): every enabled route declaring ``auth`` is asked every question that can be asked of it, and a failure names the route.

Three observations per route, because the three failures are different failures a consumer routes on differently:

* **no credential** must be refused, or the route is public.
* **a valid credential** must be accepted -- not refused for a reason about the credential.
* **an insufficient credential** must be refused as a *scope* failure, never the same answer as a missing credential.

A check obtains a credential without knowing a vendor by reading one from ``GET /__unit/auth``, which publishes ``headers`` verbatim, so nothing here needs to know how a vendor spells its own auth scheme.
"""

from __future__ import annotations

from vendorfake.conformance.env import CONTROL_PREFIX, CheckEnv, Credential, RouteRow
from vendorfake.conformance.registry import check
from vendorfake.conformance.types import ConformanceSkip, Requires, require

__all__ = ["every_auth_route_authenticates"]

_UNAUTHORIZED = "unauthorized"
_FORBIDDEN_SCOPE = "forbidden_scope"
_CREDENTIAL_KINDS = frozenset({_UNAUTHORIZED, _FORBIDDEN_SCOPE, "token_expired", "token_revoked"})
"""Every kind that means "the problem is your credential" -- a negative set, since a probe may legitimately answer ``not_found`` or ``missing_field`` instead; it just may not keep talking about the credential."""

_GARBAGE = "conformance-not-a-real-credential"


def _bad(credential: Credential) -> dict[str, str]:
    """The same headers, with every value replaced by something invented."""
    return {name: _GARBAGE for name in credential.headers}


def _same_mode(env: CheckEnv, route: RouteRow) -> Credential | None:
    """Any published credential of this route's mode, covering or not."""
    return next((credential for credential in env.credentials() if credential.mode == route.auth), None)


def _covering(env: CheckEnv, route: RouteRow) -> Credential | None:
    """A published credential of the right mode carrying every scope this route wants."""
    return next(
        (
            credential
            for credential in env.credentials()
            if credential.mode == route.auth and credential.covers(route.scopes)
        ),
        None,
    )


def _under_scoped(env: CheckEnv, route: RouteRow) -> Credential | None:
    """A published credential of the right mode that lacks a scope this route wants."""
    if not route.scopes:
        return None
    for credential in env.credentials():
        if credential.mode == route.auth and not credential.covers(route.scopes):
            return credential
    return None


@check(
    id="C17",
    name="auth: a credential is required, honoured, and checked for scope",
    asserts=(
        "On EVERY route declaring auth: no credential is unauthorized, an invented one is "
        "unauthorized, a published one is accepted, and -- on every route declaring scopes -- one "
        "missing a declared scope is forbidden_scope, never the same answer as a missing credential."
    ),
    requires=Requires(auth_route=True, credentials=True),
)
def every_auth_route_authenticates(env: CheckEnv) -> str:
    routes = env.auth_routes()

    # Pre-auth chaos faults run before AuthAdapter.resolve and would mask the auth outcome; reset first, as C12/C14/C18 do.
    env.client.call("POST", f"{CONTROL_PREFIX}chaos/reset", json_body={})

    problems: list[str] = []
    accepted_on: list[str] = []
    scoped_on: list[str] = []
    unaskable_scope: list[str] = []

    for route in routes:
        anonymous = env.client.call(route.method, route.probe_path, json_body={})
        if anonymous.error_kind != _UNAUTHORIZED:
            problems.append(
                f"{route.key} declares auth={route.auth!r} and answered {anonymous.status} with "
                f"x-unit-error={anonymous.error_kind!r} to a request carrying no credential at all, "
                f"expected {_UNAUTHORIZED!r}. Step 5 of core/kernel/unit.py::_run_pipeline is what calls "
                f"the vendor's AuthAdapter.resolve on EVERY route declaring auth; a route that answers "
                f"anything else is a route the whole authentication layer could be deleted from without "
                f"anyone noticing -- and it was, for one route, while the suite stayed green."
            )

        shaped = _same_mode(env, route)
        if shaped is None:
            problems.append(
                f"{route.key} declares auth={route.auth!r} and GET /__unit/auth publishes no credential "
                f"of that mode, so nothing can ever be accepted there: the route is describable and "
                f"undrivable. AuthAdapter.credentials() must publish one credential per mode a route uses."
            )
            continue

        invented = env.client.call(route.method, route.probe_path, json_body={}, headers=_bad(shaped))
        if invented.error_kind != _UNAUTHORIZED:
            problems.append(
                f"{route.key} answered {invented.status} with x-unit-error={invented.error_kind!r} to an "
                f"invented credential in the right header, expected {_UNAUTHORIZED!r}. Presence of the "
                f"header is not authentication: the adapter must resolve the value against real state, "
                f"and a unit that accepts any non-empty string teaches a consumer's tests to pass with a "
                f"credential their production system would reject."
            )

        good = _covering(env, route)
        if good is None:
            problems.append(
                f"{route.key} wants scopes {sorted(route.scopes)} and no credential published at "
                f"/__unit/auth carries all of them, so the route can never be driven. Either publish a "
                f"covering credential from AuthAdapter.credentials() or narrow Route.scopes."
            )
        else:
            accepted = env.client.call(route.method, route.probe_path, json_body={}, headers=dict(good.headers))
            if accepted.error_kind in _CREDENTIAL_KINDS:
                problems.append(
                    f"{route.key} answered {accepted.status} with x-unit-error={accepted.error_kind!r} to "
                    f"credential {good.label!r}, which GET /__unit/auth publishes as valid and as carrying "
                    f"{sorted(good.scopes)} against this route's {sorted(route.scopes)}. A fake that refuses "
                    f"its own published credential cannot be driven at all. Either the credential is stale "
                    f"-- AuthAdapter.credentials() must be computed from the store, not copied from the seed "
                    f"-- or resolve() and credentials() disagree about the same token."
                )
            else:
                accepted_on.append(route.key)

        if not route.scopes:
            continue
        weak = _under_scoped(env, route)
        if weak is None:
            unaskable_scope.append(route.key)
            continue
        refused = env.client.call(route.method, route.probe_path, json_body={}, headers=dict(weak.headers))
        if refused.error_kind != _FORBIDDEN_SCOPE:
            problems.append(
                f"{route.key} declares scopes {sorted(route.scopes)} and answered {refused.status} with "
                f"x-unit-error={refused.error_kind!r} to credential {weak.label!r}, which carries only "
                f"{sorted(weak.scopes)}. Expected {_FORBIDDEN_SCOPE!r}. The kernel checks Route.scopes "
                f"against the AuthResult at step 5 of core/kernel/unit.py::_run_pipeline, on every route "
                f"and not on a chosen one: enforcement removed everywhere but one route left the suite "
                f"green while it asked only that route. Answering {_UNAUTHORIZED!r} instead would send a "
                f"consumer to re-issue a token that is already valid."
            )
        elif refused.error_kind == anonymous.error_kind:
            problems.append(
                f"{route.key}: an under-scoped credential and no credential at all both answered "
                f"{refused.error_kind!r}. They are different failures with different fixes -- get a "
                f"credential, versus get a broader grant -- and a consumer cannot act on a unit that "
                f"cannot tell them apart."
            )
        else:
            scoped_on.append(route.key)

    require(not problems, "\n".join(problems))

    scoped = [route for route in routes if route.scopes]
    if scoped and not scoped_on:
        # A skip, not a soft pass: a clause that went unasked everywhere must be visible to --strict.
        raise ConformanceSkip(
            f"{len(scoped)} route(s) declare scopes ({', '.join(unaskable_scope)}) and no published "
            f"credential is under-scoped for any of them, so the forbidden_scope clause cannot be asked "
            f"-- add a narrower credential to AuthAdapter.credentials() to close it. The other clauses "
            f"held on all {len(routes)} auth routes; {len(accepted_on)} accepted their published credential."
        )
    tail = f"; scope clause unaskable on {sorted(unaskable_scope)}" if unaskable_scope else ""
    return (
        f"{len(routes)} routes declare auth: every one refused no credential and an invented one as "
        f"{_UNAUTHORIZED}; {len(accepted_on)} accepted a published credential; {len(scoped_on)} of "
        f"{len(scoped)} scoped routes refused an under-scoped credential as {_FORBIDDEN_SCOPE}{tail}"
    )
