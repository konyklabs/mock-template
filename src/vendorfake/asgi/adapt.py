"""ASGI request in, unit request out; unit response in, ASGI response out.

FOR: being the whole of the translation between a web framework and the core,
in one small file a reviewer can read end to end and satisfy themselves that
nothing is interpreted on the way through.

INVARIANT: **it converts, and it does not parse.** The body is read once, as
bytes, with ``await request.body()``. There is no ``Form(...)`` parameter, no
``await request.form()``, no ``await request.json()`` and no ``.stream()``
anywhere in this package. That is not fastidiousness: it is the defect this
whole project was rebuilt to make unrepresentable. FastAPI needs
``python-multipart`` to read a body even when that body is plain
``application/x-www-form-urlencoded`` -- a ``Form(...)`` parameter raises at
import time without it and ``request.form()`` raises at request time -- so the
moment the adapter decides how to read a body, the content-type decision has
moved to the transport edge and the core is no longer content-type general.
Two of the three implementations that preceded this one failed exactly there.
``python-multipart`` is deliberately not a dependency of this distribution, so
the mistake cannot be made quietly.

SECOND INVARIANT: **bytes in, bytes out, untouched.** ``raw_body`` is the exact
received bytes because webhook signature schemes sign received bytes, and
``UnitResponse.body`` goes back into a bare ``starlette.responses.Response``
rather than into a ``JSONResponse``, because handing a framework an object to
render is the same defect from the other end. A re-serialisation would keep
every assertion in this repository passing while silently changing what a
consumer is testing against.

Three normalisations are worth naming, because each one is a place where a
second binding would drift:

**The path is the raw, still-escaped path.** ``scope["path"]`` has already been
percent-decoded by the server, so ``/v2/orders/a%2Fb`` arrives there as
``/v2/orders/a/b`` -- three segments where the consumer sent two. The router
does its own per-segment decoding and rejects a malformed escape as a bad
request, which it can only do if it is given the escapes. ``scope["raw_path"]``
carries them; it is optional in the ASGI specification, so a decoded fallback
is kept, and both uvicorn and httpx's ASGI transport supply it.

**Repeated headers are joined with ``", ``".** That is what Node's http module
does to the reference's ``req.headers`` before it ever sees them, and joining
is what RFC 9110 says a repeated field means. ``dict(request.headers)`` would
silently keep only the first value.

**Repeated query parameters are handed on as pairs, not as a dict.**
``UnitRequest`` carries two views in every binding: ``query`` is a plain
``str -> str`` mapping in which a repeated key keeps its last value, and
``query_all`` is ``str -> Sequence[str]`` with every value in arrival order,
so ``query[k] == query_all[k][-1]`` always holds. ``dict(query_params)`` would
build the first view by throwing the second away, and Starlette's own
multi-valued ``QueryParams`` must not leak through either, or this binding
would see a list where the others see a string. ``multi_items()`` is the
lossless form, and ``make_request`` derives both views from it exactly as it
does for the other bindings.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from vendorfake.core.kernel.types import UnitRequest, UnitResponse
from vendorfake.core.kernel.unit import make_request

__all__ = ["TRANSPORT", "request_headers", "request_path", "request_query", "to_response", "to_unit_request"]

TRANSPORT = "http"
"""``UnitRequest.transport`` for everything this adapter builds.

The value a conformance check compares against when it asserts that the same
scenario answered identically over two bindings, so it is a constant here
rather than a literal at the call site."""


def request_path(request: Request) -> str:
    """The path with its percent-escapes intact.

    See the module docstring: ``scope["path"]`` is decoded and would re-segment
    a path containing an encoded separator. ``raw_path`` is optional in the
    ASGI specification, hence the fallback -- which is the decoded path, i.e.
    the behaviour of a server that does not publish the raw one, rather than a
    failure.

    The specification also says ``raw_path`` excludes the query string, but a
    server that left it on would hand the query to ``make_request`` twice --
    once here, once through ``request_query`` -- and double every parameter,
    so it is cut at the first ``?`` regardless.
    """
    raw = request.scope.get("raw_path")
    if isinstance(raw, bytes):
        # Percent-escapes are ASCII by construction; anything else in a path is
        # already UTF-8 bytes the router will hand on unchanged. latin-1 is the
        # lossless byte-to-str mapping, so nothing can raise here.
        return raw.split(b"?", 1)[0].decode("latin-1")
    return request.url.path


def request_headers(request: Request) -> dict[str, str]:
    """Header names lowercased, repeated names joined with ``", ``".

    Starlette lowercases names for us; the join is ours, and it is the
    difference between a consumer's two ``accept`` headers arriving as one
    field and one of them vanishing.
    """
    headers: dict[str, str] = {}
    for name, value in request.headers.items():
        lowered = name.lower()
        existing = headers.get(lowered)
        headers[lowered] = value if existing is None else f"{existing}, {value}"
    return headers


def request_query(request: Request) -> list[tuple[str, str]]:
    """Every query pair in arrival order, blank values kept; nothing collapsed here."""
    return request.query_params.multi_items()


async def to_unit_request(request: Request) -> UnitRequest:
    """Build the :class:`UnitRequest` for one ASGI request.

    ``await request.body()`` is the only body read in this package, and it is
    the reason this function is the only ``async`` thing in the translation:
    everything below the seam is synchronous.

    The request id is not minted here. ``make_request`` takes the inbound
    ``x-unit-request-id`` when the caller supplied one and a fresh UUID
    otherwise -- shared with every other binding, because a correlation id that
    depended on which transport carried the call would correlate nothing.
    """
    raw_body = await request.body()
    return make_request(
        method=request.method,
        path=request_path(request),
        query=request_query(request),
        headers=request_headers(request),
        raw_body=raw_body,
        transport=TRANSPORT,
    )


def to_response(res: UnitResponse) -> Response:
    """Wrap the unit's bytes without touching them.

    ``starlette.responses.Response`` is the only response class this package
    constructs. ``JSONResponse`` would re-serialise a parsed body,
    ``PlainTextResponse`` would re-encode it, and either would break both the
    byte-for-byte agreement between bindings and the raw-body guarantee the
    signature scheme rests on.

    Starlette adds ``content-length`` of its own, and over a socket the server
    adds ``date`` and ``server``. Those three, plus the per-binding
    ``x-unit-request-id``, are the named exclusions when two bindings are
    compared header by header; everything the unit set survives unchanged.
    """
    return Response(content=res.body, status_code=res.status, headers=dict(res.headers))
