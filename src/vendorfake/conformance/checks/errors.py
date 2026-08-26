"""C04, C05 -- no consumer ever meets anything but the vendor's own error.

C04 asserts it at the edge: the paths a web framework likes to answer for
itself -- an unknown URL, a wrong verb, a body that will not parse -- all come
back shaped by the vendor. C05 asserts it in the middle: every failure kind
the core can raise has a mapping, so no future code path can produce a shape
nobody chose.

Together they are the reason a consumer can write one error handler. A fake
that answers a framework's 404 for an unknown path, or a validation library's
422 for a malformed body, teaches a consumer's code to expect documents the
real vendor never sends.
"""

from __future__ import annotations

from typing import Any

from vendorfake.conformance.env import CONTROL_PREFIX, CheckEnv, RouteRow
from vendorfake.conformance.registry import check
from vendorfake.conformance.types import ConformanceFailure, ConformanceSkip, Requires, require
from vendorfake.core.kernel.types import UnitErrorKind

__all__ = ["error_kinds_are_all_shaped", "the_framework_never_answers_a_consumer"]

CORE_ERROR_KIND_COUNT = 20
"""The number of core failure kinds, written out.

Asserted against the literal and not against ``len(UnitErrorKind)``, because a
check that counts the table it is checking passes for a table with a kind
deleted from it. Changing this number is a deliberate act with a conformance
diff attached.
"""

_UNKNOWN_PATH = "/definitely/not/a/real/path/conformance"
_STARLETTE_ENVELOPE = {"detail"}
_INVALID_JSON = "invalid_json"
_ECHO_PATH = f"{CONTROL_PREFIX}echo"
_MUTATING = frozenset({"POST", "PUT"})


def _body_parsing_route(env: CheckEnv) -> tuple[RouteRow, dict[str, str], str]:
    """A route that will actually attempt to parse the body it is sent.

    "Send a malformed body and see what comes back" says nothing at all if the
    route never reads one. The first enabled vendor route on the shipped vendor
    is ``GET /oauth2/authorize``, whose own evidence line gave the game away --
    ``malformed body -> 400:missing_field``, a missing query parameter -- and a
    unit that answered a validation library's 422 for every genuinely parsed
    body passed this contract unchanged.

    A mutating vendor route first, because that is a route a consumer really
    posts to; the control plane's echo route as the fallback, so a profile
    whose vendor exposes no such route still asks the question rather than
    quietly asking a different one.
    """
    for route in env.vendor_routes(methods=_MUTATING):
        if route.auth is None:
            return route, {}, "the first enabled mutating vendor route needing no credential"
        try:
            headers = env.authorized(route)
        except ConformanceSkip:
            continue
        # Authenticated deliberately: a body is parsed at step 7 or step 8 of
        # the pipeline and auth is step 5, so an anonymous probe is refused
        # before anything reads it and the answer would be about the
        # credential rather than about the body.
        return route, headers, "the first enabled mutating vendor route, authenticated"
    return (
        RouteRow(method="POST", path=_ECHO_PATH, capability="", internal=True),
        {},
        "POST /__unit/echo, this profile offering no drivable mutating vendor route",
    )


@check(
    id="C04",
    name="errors: the framework never answers a consumer",
    asserts=(
        "An unknown path, a wrong method and a malformed body are all vendor-shaped, carry "
        "x-unit-error, are never a validation framework's 422, and leave framework_answered at 0."
    ),
    requires=Requires(surface_route=True),
)
def the_framework_never_answers_a_consumer(env: CheckEnv) -> str:
    missing = env.client.call("GET", _UNKNOWN_PATH)
    require(
        missing.status == 404,
        f"an unknown path answered {missing.status}, expected 404 (core/kernel/unit.py::handle "
        f"falls through to vendor.errors.not_found when the router matches nothing).",
    )
    require(
        missing.error_kind == "not_found",
        f"the 404 for an unknown path carries x-unit-error={missing.error_kind!r}. Every answer the "
        f"unit produces goes through _finish, which stamps the kind; an answer without it did not "
        f"come from the unit.",
    )
    require(
        missing.text.strip(),
        "the 404 body is empty, so a consumer's log line says nothing. ErrorShaper.not_found must "
        "return the vendor's own document.",
    )
    try:
        document: Any = missing.json()
    except ValueError as exc:
        raise ConformanceFailure(
            f"the 404 body is not JSON: {exc}. A consumer parses every error the same way; an HTML "
            f"or plain-text 404 is a web framework's default page reaching them."
        ) from exc
    require(
        not (isinstance(document, dict) and set(document) == _STARLETTE_ENVELOPE),
        "the 404 body is the web framework's own {'detail': ...} envelope, not the vendor's. "
        "asgi/app.py must own every path with a catch-all route and register handlers for the "
        "framework's HTTP exception and validation-error types, so nothing is answered above the "
        "unit.",
    )

    route = env.first_vendor_route()
    wrong_method = "PATCH" if route.method != "PATCH" else "DELETE"
    wrong = env.client.call(wrong_method, route.probe_path, json_body={})
    require(
        wrong.error_kind == "method_not_allowed",
        f"{wrong_method} {route.probe_path} answered {wrong.status} with "
        f"x-unit-error={wrong.error_kind!r}, expected 'method_not_allowed'. A web framework answers "
        f"405 itself unless the catch-all route in asgi/app.py claims every verb and lets "
        f"core/kernel/router.py decide.",
    )

    parser, credential, why = _body_parsing_route(env)
    malformed = env.client.call(
        parser.method,
        parser.probe_path,
        body=b"{not json",
        headers={**credential, "content-type": "application/json"},
    )
    require(
        malformed.error_kind == _INVALID_JSON,
        f"a malformed body sent to {parser.key} answered {malformed.status} with "
        f"x-unit-error={malformed.error_kind!r}, expected {_INVALID_JSON!r}. This probe must land "
        f"on a route that READS a body: aimed at the vendor's first route it hit "
        f"{env.first_vendor_route().key}, which answered `missing_field` for an absent query "
        f"parameter and never parsed anything, so the clause below could not fail however the "
        f"vendor shaped a parse error.",
    )
    require(
        malformed.status != 422,
        f"a malformed body sent to {parser.key} answered 422, which is a validation library's "
        f"error envelope reaching a consumer. The adapter must declare no request model: "
        f"asgi/adapt.py reads await request.body() once and the core parses, raising "
        f"UnitErrorKind.INVALID_JSON for the vendor to shape into its own document.",
    )

    health = env.client.call("GET", f"{CONTROL_PREFIX}health").json()
    require(
        health.get("framework_answered") == 0,
        f"after three deliberately wrong requests, framework_answered is "
        f"{health.get('framework_answered')!r}. The tripwire in asgi/app.py counted a request the "
        f"framework answered by itself: that consumer received a document no vendor wrote. The "
        f"three requests above are chosen to be exactly the ones a framework likes to answer for "
        f"itself, so a hole in the catch-all shows up here and nowhere else.",
    )
    return (
        f"unknown path -> {missing.status}:{missing.error_kind}; {wrong_method} {route.probe_path} -> "
        f"{wrong.status}:{wrong.error_kind}; malformed body to {parser.key} ({why}) -> "
        f"{malformed.status}:{malformed.error_kind}; framework_answered {health.get('framework_answered')!r}"
    )


@check(
    id="C05",
    name="errors: the vendor shaper covers every core error kind",
    asserts=(
        "GET /__unit/errors maps all twenty core error kinds to a 4xx or 5xx with a non-empty body, "
        "and shapes the no-route answer too."
    ),
)
def error_kinds_are_all_shaped(env: CheckEnv) -> str:
    document = env.get_json(f"{CONTROL_PREFIX}errors")
    rows: list[dict[str, Any]] = list(document["kinds"])
    reported = [str(row["kind"]) for row in rows]
    core_kinds = tuple(kind.value for kind in UnitErrorKind)

    require(
        len(core_kinds) == CORE_ERROR_KIND_COUNT,
        f"the core declares {len(core_kinds)} error kinds, and this contract is written against "
        f"{CORE_ERROR_KIND_COUNT}. Adding or removing a kind is a change to what every vendor must "
        f"map: update CORE_ERROR_KIND_COUNT in conformance/checks/errors.py in the same commit as "
        f"core/kernel/types.py::UnitErrorKind, so the two can never drift silently.",
    )
    require(
        len(reported) == CORE_ERROR_KIND_COUNT,
        f"GET /__unit/errors reported {len(reported)} kinds, expected {CORE_ERROR_KIND_COUNT}. The "
        f"route enumerates UnitErrorKind itself (core/control/plane.py::errors) so that a kind the "
        f"vendor forgot still appears; a short list means the route is enumerating the vendor's "
        f"table instead.",
    )
    absent = sorted(set(core_kinds) - set(reported))
    require(
        not absent,
        f"GET /__unit/errors does not report {absent}. Those kinds can be raised by the core and a "
        f"consumer would meet whatever the shaper does with an unknown one.",
    )

    problems: list[str] = []
    for row in rows:
        kind = str(row["kind"])
        status = int(row["status"])
        body = row.get("body")
        if not 400 <= status <= 599:
            problems.append(
                f"{kind} is shaped as HTTP {status}. Every core failure is a 4xx or a 5xx: a "
                f"success status for a failure makes a consumer's error handling unreachable. "
                f"Fix the vendor's error table."
            )
        if body in (None, "", {}, []):
            problems.append(
                f"{kind} is shaped with an empty body, so a consumer logging the failure records "
                f"nothing. The vendor's ErrorShaper must produce its own document for every kind."
            )
    no_route = document["no_route"]
    if not 400 <= int(no_route["status"]) <= 599:
        problems.append(
            f"the no-route answer is HTTP {no_route['status']}. ErrorShaper.not_found is what a "
            f"consumer meets for a typo in a URL and it must be a 4xx."
        )
    require(not problems, "\n".join(problems))

    statuses = sorted({int(row["status"]) for row in rows})
    return (
        f"all {len(reported)} core error kinds shaped as 4xx/5xx with non-empty bodies "
        f"(statuses seen: {statuses}); no-route -> {no_route['status']}"
    )
