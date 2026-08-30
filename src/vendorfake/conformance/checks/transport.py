"""C10, C15, C23 -- the transport carries bytes, content types and query strings, and changes none.

C10 is the contract that keeps the in-process binding honest. Every other
check is cheap because it runs in process; that economy is only sound if the
two bindings answer identically, so this one compares them byte for byte over
the same requests.

C15 is constraint 2, expressed as a contract. A body sent as
``application/x-www-form-urlencoded`` must reach the handler as fields. It is
asked through ``POST /__unit/echo`` rather than through a vendor's own token
endpoint, deliberately: the guarantee belongs to the core's body reader, so
vendor number two inherits it rather than rediscovering the trap. It is the
exact shape that broke two of three implementations before this one.

C23 is C15's twin for the query string. A repeated query key must reach the
handler with every value, and a bare key with an empty one; a binding that
built ``dict(query_params)`` would pass every other contract while silently
dropping all but the last value.
"""

from __future__ import annotations

from vendorfake.conformance.client import FORM_CONTENT_TYPE
from vendorfake.conformance.env import CONTROL_PREFIX, CheckEnv
from vendorfake.conformance.registry import check
from vendorfake.conformance.types import Requires, require

__all__ = [
    "a_form_encoded_body_reaches_the_handler",
    "a_repeated_query_parameter_reaches_the_handler",
    "bindings_agree_byte_for_byte",
]

EXCLUDED_HEADERS: frozenset[str] = frozenset(
    {
        # Minted per binding: the two can never match, by design.
        "x-unit-request-id",
        # Added by the server, not by the unit.
        "content-length",
        "date",
        "server",
        "connection",
        "transfer-encoding",
    }
)
"""Headers excluded from the byte-for-byte comparison, named rather than waved
at. Everything the *unit* set must survive the transport; these five are set by
whatever is carrying it, and the request id is deliberately per-binding."""


@check(
    id="C10",
    name="transport: the HTTP and in-process bindings agree byte for byte",
    asserts=(
        "The same requests over both bindings return the same status, the same response bytes and "
        "the same headers, excluding the five a server owns."
    ),
    requires=Requires(both_transports=True, surface_route=True),
)
def bindings_agree_byte_for_byte(env: CheckEnv) -> str:
    here = "inprocess" if env.transport != "inprocess" else "http"
    route = env.first_vendor_route()
    probes: tuple[tuple[str, str], ...] = (
        ("GET", f"{CONTROL_PREFIX}routes"),
        ("GET", f"{CONTROL_PREFIX}errors"),
        ("GET", "/definitely/not/a/real/path/conformance"),
        (route.method, route.probe_path),
    )
    with env.fresh(transport=here) as other:
        compared = 0
        for method, path in probes:
            mine = env.client.call(method, path, json_body={})
            theirs = other.client.call(method, path, json_body={})
            require(
                mine.status == theirs.status,
                f"{method} {path}: status differs between bindings -- {env.transport} answered "
                f"{mine.status}, {here} answered {theirs.status}. Both bindings hand the same "
                f"UnitRequest to the same Unit.handle; a difference here is the transport "
                f"answering for itself.",
            )
            require(
                mine.body == theirs.body,
                f"{method} {path}: response bytes differ between bindings "
                f"({len(mine.body)} vs {len(theirs.body)} bytes).\n"
                f"  {env.transport}: {mine.body[:200]!r}\n"
                f"  {here}: {theirs.body[:200]!r}\n"
                f"asgi/adapt.py must return Response(content=<the core's bytes>) and never "
                f"JSONResponse(parsed) -- re-encoding changes separators, key order or unicode "
                f"escaping, and a webhook signature covers the bytes that were sent.",
            )
            names = (set(mine.headers) | set(theirs.headers)) - EXCLUDED_HEADERS
            differing = sorted(name for name in names if mine.headers.get(name) != theirs.headers.get(name))
            require(
                not differing,
                f"{method} {path}: headers {differing} differ between bindings.\n"
                f"  {env.transport}: {[(name, mine.headers.get(name)) for name in differing]}\n"
                f"  {here}: {[(name, theirs.headers.get(name)) for name in differing]}\n"
                f"Excluded from this comparison, deliberately: {sorted(EXCLUDED_HEADERS)}. "
                f"Everything else was set by the unit and must survive the transport unchanged -- "
                f"a middleware that rewrites or compresses is the usual cause.",
            )
            compared += 1
    return (
        f"{compared} probes identical in status, bytes and headers across the {env.transport} and "
        f"{here} bindings; excluded {sorted(EXCLUDED_HEADERS)}"
    )


@check(
    id="C15",
    name="transport: a form-encoded body reaches the handler as fields",
    asserts=(
        "POST /__unit/echo with application/x-www-form-urlencoded reports the fields, with a "
        "repeated key visible as last-wins and as a list, and no JSON document."
    ),
)
def a_form_encoded_body_reaches_the_handler(env: CheckEnv) -> str:
    answered = env.client.call(
        "POST",
        f"{CONTROL_PREFIX}echo",
        form=[
            ("grant_type", "authorization_code"),
            ("scope", "first"),
            ("scope", "second"),
        ],
    )
    require(
        answered.status == 200,
        f"POST /__unit/echo with a form-encoded body answered {answered.status}: {answered.text}. "
        f"This is the exact request shape that broke two of three prior implementations. A web "
        f"framework needs a multipart dependency to read ANY form body, even a urlencoded one, so "
        f"an adapter that declares Form(...) or calls request.form() fails here -- and this "
        f"distribution deliberately does not depend on that package, so the mistake cannot be "
        f"papered over by installing it. Read the body once in asgi/adapt.py and let "
        f"core/kernel/types.py::HandlerArgs.form parse it.",
    )
    body = answered.json()
    require(
        body["content_type"] == FORM_CONTENT_TYPE,
        f"the handler saw content type {body['content_type']!r}, expected {FORM_CONTENT_TYPE!r}. "
        f"The content type must reach the core intact; a transport that normalised or dropped it "
        f"has decided how to parse the body on the core's behalf.",
    )
    fields = dict(body["fields"])
    multi = {str(key): list(value) for key, value in dict(body["fields_multi"]).items()}
    require(
        fields.get("grant_type") == "authorization_code",
        f"the form field 'grant_type' arrived as {fields.get('grant_type')!r}. The body reached the "
        f"handler unparsed or empty.",
    )
    require(
        fields.get("scope") == "second",
        f"a repeated form key resolved to {fields.get('scope')!r} in the scalar view, expected the "
        f"last value 'second'. Last-wins is the scalar rule; a caller who needs both uses the "
        f"multi view.",
    )
    require(
        multi.get("scope") == ["first", "second"],
        f"a repeated form key arrived as {multi.get('scope')!r} in the multi view, expected "
        f"['first', 'second']. Both values must survive: a form body is a multimap and collapsing "
        f"it at the transport loses information no later layer can recover.",
    )
    require(
        "json" not in body,
        "the handler reported a parsed JSON document for a form-encoded body. The body reader "
        "chooses by content type; reporting a JSON key here means it guessed.",
    )
    return (
        f"form body parsed as {len(fields)} fields; repeated key last-wins {fields.get('scope')!r} "
        f"and multi {multi.get('scope')}; no JSON document reported"
    )


@check(
    id="C23",
    name="transport: a repeated query parameter reaches the handler whole",
    asserts=(
        "POST /__unit/echo?flag with scope=first&scope=second reports query with the last value, "
        "query_all with every value in order, and the bare key as an empty string."
    ),
)
def a_repeated_query_parameter_reaches_the_handler(env: CheckEnv) -> str:
    answered = env.client.call(
        "POST",
        f"{CONTROL_PREFIX}echo?flag",
        query=[("scope", "first"), ("scope", "second"), ("limit", "2")],
    )
    require(
        answered.status == 200,
        f"POST /__unit/echo with a query string answered {answered.status}: {answered.text}. "
        f"The query must reach the route unchanged; a transport that rejects or rewrites it has "
        f"decided the request's shape on the core's behalf.",
    )
    body = answered.json()
    query = {str(key): str(value) for key, value in dict(body["query"]).items()}
    query_all = {str(key): list(value) for key, value in dict(body["query_all"]).items()}
    require(
        query.get("scope") == "second",
        f"a repeated query key resolved to {query.get('scope')!r} in the scalar view, expected the "
        f"last value 'second'. Last-wins is the scalar rule for query and form alike.",
    )
    require(
        query_all.get("scope") == ["first", "second"],
        f"a repeated query key arrived as {query_all.get('scope')!r} in query_all, expected "
        f"['first', 'second']. Every value must survive the transport: dict(query_params) in an "
        f"adapter keeps one and discards the rest, and no later layer can recover them.",
    )
    require(
        query.get("flag") == "" and query_all.get("flag") == [""],
        f"a bare query key arrived as {query.get('flag')!r} / {query_all.get('flag')!r}, expected "
        f"'' / ['']. A key sent with no value is a value the handler must be able to see; parse "
        f"the query with blank values kept.",
    )
    require(
        query.get("limit") == "2" and query_all.get("limit") == ["2"],
        f"a query key sent once arrived as {query.get('limit')!r} / {query_all.get('limit')!r}, "
        f"expected '2' / ['2'] in both views.",
    )
    return (
        f"query {query}; repeated key last-wins {query.get('scope')!r} and query_all "
        f"{query_all.get('scope')}; bare key kept as {query.get('flag')!r}"
    )
