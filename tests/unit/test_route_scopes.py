"""Every authenticated route, of every vendor, names a scope.

This lives at the repository level rather than under ``tests/unit/square/``
because the defect it guards is not Square's. A code review of
konyklabs/vendorfake#14 found all six Webhook Subscriptions routes declaring
``auth="bearer"`` and no ``scopes=``, so the seeded read-only token could
register subscribers, delete legitimate ones, and read every subscriber's HMAC
signing key.

The mechanism is what makes it worth a standing test. ``Route.scopes`` defaults
to ``()``, and the kernel's check is ``[s for s in route.scopes if s not in
auth.scopes]`` -- a loop over an empty tuple, whose body never runs. So *no
scope declared* and *every scope satisfied* are the same thing at runtime, and
the omission is invisible at the call site.

Every surface happened to declare its scopes; none had to. The reviewer's point
was that nothing required it -- "adding ``scopes=(...)`` later, or removing it
again in a refactor, changes no test result" -- so fixing six routes was not
fixing the defect.

It iterates :func:`~vendorfake.registry.available_vendors`, so a second vendor
is covered the day it is registered rather than the day someone remembers to
extend a Square-scoped test. An earlier version of this check lived under
``tests/unit/square/`` and called itself repo-wide in its own docstring while
walking only Square's routes; the review caught that too.
"""

from __future__ import annotations

import pytest

from vendorfake.core.kernel.types import Route
from vendorfake.registry import available_vendors, resolve_vendor

#: Routes that authenticate a caller but require no scope, each with the reason.
#: Explicit for the same purpose as ``VendorDefinition.not_supported``: an
#: absence has to be *stated* to be distinguishable from an omission.
SCOPELESS_BY_DESIGN: dict[tuple[str, str, str], str] = {
    ("square", "POST", "/oauth2/token/status"): (
        "RetrieveTokenStatus reports what a token may do. Requiring a scope to "
        "ask that question would stop an under-scoped token discovering why it "
        "is under-scoped."
    ),
    ("square", "POST", "/oauth2/revoke"): (
        "RevokeToken authenticates the application with a client secret rather "
        "than a bearer grant, so there are no granted scopes to check against."
    ),
}


def _routes(vendor_name: str) -> tuple[Route, ...]:
    return tuple(resolve_vendor(vendor_name).routes)


@pytest.mark.parametrize("vendor_name", available_vendors())
def test_every_authenticated_route_names_a_scope_or_is_listed_as_exempt(vendor_name: str) -> None:
    offenders = [
        (vendor_name, route.method, route.path)
        for route in _routes(vendor_name)
        if route.auth is not None
        and not route.scopes
        and (vendor_name, route.method, route.path) not in SCOPELESS_BY_DESIGN
    ]
    assert offenders == [], (
        "these routes authenticate a caller but require no scope, so any valid "
        f"token reaches them: {offenders}. Declare scopes=(...), or add an entry "
        "to SCOPELESS_BY_DESIGN saying why the route legitimately needs none."
    )


def test_the_exemption_list_names_only_routes_that_exist_and_authenticate() -> None:
    """A stale excuse quietly widens the invariant next time it is read as
    precedent, so an exemption for a route that is gone -- or that never
    authenticated -- is itself a failure."""
    live = {
        (name, route.method, route.path)
        for name in available_vendors()
        for route in _routes(name)
        if route.auth is not None
    }
    stale = sorted(key for key in SCOPELESS_BY_DESIGN if key not in live)
    assert stale == [], f"exemptions for routes that do not exist or do not authenticate: {stale}"
