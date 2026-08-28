"""C17 -- the unit actually authenticates somebody.

The contract this file exists for was, until it was written, the largest hole
in the suite: replacing the whole authentication step in
``core/kernel/unit.py::_run_pipeline`` with ``if False:`` left every contract
green. ``unauthorized`` and ``forbidden_scope`` appeared in the suite only as
*rows of the error table* read from ``GET /__unit/errors`` -- the shapes, never
the behaviour -- so a unit that authenticated nobody, and served every seeded
order to any anonymous caller, was certified conformant.

Three observations and not one, because the three failures are different
failures and a consumer routes on which one they got:

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

__all__ = ["an_auth_required_route_authenticates"]

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
        "On a route declaring auth: no credential is unauthorized, an invented one is "
        "unauthorized, a published one is accepted, and one missing a declared scope is "
        "forbidden_scope -- never the same answer as a missing credential."
    ),
    requires=Requires(auth_route=True, credentials=True),
)
def an_auth_required_route_authenticates(env: CheckEnv) -> str:
    routes = env.auth_routes()
    scoped = [route for route in routes if route.scopes]
    # A route that declares scopes where the vendor has one, because the scope
    # clause is the half that cannot be asked otherwise; any auth route
    # otherwise, so a vendor with no scope vocabulary still gets the other two.
    route = next((row for row in scoped if _under_scoped(env, row) is not None), None) or (scoped or routes)[0]
    good = env.credential_for(route)

    # A profile may preload a rule on exactly this route -- chaos-demo
    # rate-limits every third POST /v2/orders, deterministically the accepted
    # probe below -- and pre-auth faults run before AuthAdapter.resolve, so an
    # intercepted probe answers with a fault kind instead of an auth kind and
    # the acceptance clause observes nothing. Same reason C12, C14 and C18
    # reset first.
    env.client.call("POST", f"{CONTROL_PREFIX}chaos/reset", json_body={})

    anonymous = env.client.call(route.method, route.probe_path, json_body={})
    require(
        anonymous.error_kind == _UNAUTHORIZED,
        f"{route.key} declares auth={route.auth!r} and answered {anonymous.status} with "
        f"x-unit-error={anonymous.error_kind!r} to a request carrying no credential at all, "
        f"expected {_UNAUTHORIZED!r}. Step 5 of core/kernel/unit.py::_run_pipeline is what calls "
        f"the vendor's AuthAdapter.resolve; a route that answers anything else is a route the "
        f"whole authentication layer could be deleted from without anyone noticing.",
    )

    invented = env.client.call(route.method, route.probe_path, json_body={}, headers=_bad(good))
    require(
        invented.error_kind == _UNAUTHORIZED,
        f"{route.key} answered {invented.status} with x-unit-error={invented.error_kind!r} to an "
        f"invented credential in the right header, expected {_UNAUTHORIZED!r}. Presence of the "
        f"header is not authentication: the adapter must resolve the value against real state, "
        f"and a unit that accepts any non-empty string teaches a consumer's tests to pass with a "
        f"credential their production system would reject.",
    )

    accepted = env.client.call(route.method, route.probe_path, json_body={}, headers=dict(good.headers))
    require(
        accepted.error_kind not in _CREDENTIAL_KINDS,
        f"{route.key} answered {accepted.status} with x-unit-error={accepted.error_kind!r} to "
        f"credential {good.label!r}, which GET /__unit/auth publishes as valid and as carrying "
        f"{sorted(good.scopes)} against this route's {sorted(route.scopes)}. A fake that refuses "
        f"its own published credential cannot be driven at all. Either the credential is stale -- "
        f"AuthAdapter.credentials() must be computed from the store, not copied from the seed -- "
        f"or resolve() and credentials() disagree about the same token.",
    )

    weak = _under_scoped(env, route)
    if weak is None:
        # A skip, not a soft pass: three of four clauses held, but a contract
        # that reports PASS while a clause went unasked is gated out with no
        # red and no manifest entry -- undeclared_skips and never_ran both
        # inspect SKIP outcomes only. Raising makes the gap visible to
        # --strict and forces the profile to declare it in expected_skips.
        raise ConformanceSkip(
            f"{route.key} (auth={route.auth!r}, scopes {sorted(route.scopes)}): no published "
            f"credential is under-scoped for any auth route, so the forbidden_scope clause "
            f"cannot be asked -- add a narrower credential to AuthAdapter.credentials() to "
            f"close it. The other three clauses held: anonymous -> "
            f"{anonymous.status}:{anonymous.error_kind}; invented -> {invented.status}:"
            f"{invented.error_kind}; {good.label!r} -> {accepted.status}:{accepted.error_kind or '-'}."
        )

    refused = env.client.call(route.method, route.probe_path, json_body={}, headers=dict(weak.headers))
    require(
        refused.error_kind == _FORBIDDEN_SCOPE,
        f"{route.key} declares scopes {sorted(route.scopes)} and answered {refused.status} with "
        f"x-unit-error={refused.error_kind!r} to credential {weak.label!r}, which carries only "
        f"{sorted(weak.scopes)}. Expected {_FORBIDDEN_SCOPE!r}. The kernel checks Route.scopes "
        f"against the AuthResult at step 5 of core/kernel/unit.py::_run_pipeline, and it is checked "
        f"there rather than in the vendor because a second place to check is a second place to "
        f"forget. Answering {_UNAUTHORIZED!r} instead would send a consumer to re-issue a token "
        f"that is already valid.",
    )
    require(
        refused.error_kind != anonymous.error_kind,
        f"an under-scoped credential and no credential at all both answered "
        f"{refused.error_kind!r}. They are different failures with different fixes -- get a "
        f"credential, versus get a broader grant -- and a consumer cannot act on a unit that "
        f"cannot tell them apart.",
    )
    return (
        f"{route.key} (auth={route.auth!r}, scopes {sorted(route.scopes)}): anonymous -> "
        f"{anonymous.status}:{anonymous.error_kind}; invented -> {invented.status}:"
        f"{invented.error_kind}; {good.label!r} ({len(good.scopes)} scopes) -> {accepted.status}:"
        f"{accepted.error_kind or '-'}; {weak.label!r} ({sorted(weak.scopes)}) -> {refused.status}:"
        f"{refused.error_kind}"
    )
