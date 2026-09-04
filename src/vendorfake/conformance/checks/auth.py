"""C17 -- the unit actually authenticates somebody, on every route that says so.

The contract this file exists for was, until it was written, the largest hole
in the suite: replacing the whole authentication step in
``core/kernel/unit.py::_run_pipeline`` with ``if False:`` left every contract
green. ``unauthorized`` and ``forbidden_scope`` appeared in the suite only as
*rows of the error table* read from ``GET /__unit/errors`` -- the shapes, never
the behaviour -- so a unit that authenticated nobody, and served every seeded
order to any anonymous caller, was certified conformant.

Then it was a check of one route. The third adversarial round
(konyklabs/roadmap#10, findings N-3a and N-3b; tracked as konyklabs/roadmap#15)
skipped authentication for ``ListLocations`` alone, and separately removed
scope enforcement from every route *except* the one this check probed, and the
matrix stayed green both times. A check that asks one route out of sixteen can
be satisfied by a unit that is correct on exactly that route. So this is now a
class check in the same sense C03 is: every enabled route that declares
``auth`` is asked every question that can be asked of it, and a failure names
the route.

Three observations per route and not one, because the three failures are
different failures and a consumer routes on which one they got:

* **no credential** must be refused. Otherwise the route is public.
* **a valid credential** must be accepted -- that is, must not be refused *for
  a reason about the credential*. A check that only asserted refusals would
  pass against a unit that refused everything, which is a fake nobody can use.
* **an insufficient credential** must be refused as a *scope* failure. A unit
  that collapsed this into ``unauthorized`` would send a consumer to re-check
  their token when what they need is a different grant, and one that collapsed
  it the other way would let an under-scoped integration pass its tests and
  fail in production.

HOW A CHECK OBTAINS A CREDENTIAL WITHOUT KNOWING A VENDOR. It reads one from
``GET /__unit/auth``, which publishes ``headers`` verbatim -- the whole
instruction, name and value -- so nothing here knows that a bearer scheme
spells itself ``Authorization: Bearer``, and a vendor whose scheme is a signed
query parameter would be asked exactly the same three questions.
"""

from __future__ import annotations

from vendorfake.conformance.env import CONTROL_PREFIX, CheckEnv, Credential, RouteRow
from vendorfake.conformance.registry import check
from vendorfake.conformance.types import ConformanceSkip, Requires, require

__all__ = ["every_auth_route_authenticates"]

_UNAUTHORIZED = "unauthorized"
_FORBIDDEN_SCOPE = "forbidden_scope"
_CREDENTIAL_KINDS = frozenset({_UNAUTHORIZED, _FORBIDDEN_SCOPE, "token_expired", "token_revoked"})
"""Every kind that means "the problem is your credential".

Named as a set because the acceptance clause is a *negative*: a probe aimed at
an id no seed contains is entitled to answer ``not_found``, and a route with a
required idempotency key is entitled to answer ``missing_field``. What it may
not do is keep talking about the credential.
"""

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

    # A profile may preload a rule on a route probed here -- chaos-demo
    # rate-limits every third POST /v2/orders -- and pre-auth faults run
    # before AuthAdapter.resolve, so an intercepted probe answers with a fault
    # kind instead of an auth kind and the acceptance clause observes nothing.
    # Same reason C12, C14 and C18 reset first.
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
        # A skip, not a soft pass: the other clauses held on every route, but a
        # contract that reports PASS while a clause went unasked everywhere is
        # gated out with no red and no manifest entry -- undeclared_skips and
        # never_ran both inspect SKIP outcomes only. Raising makes the gap
        # visible to --strict and forces the profile to declare it.
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
