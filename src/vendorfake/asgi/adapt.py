"""ASGI request in, unit request out; unit response in, ASGI response out.
Converts, never parses: the body is read once as bytes and returned untouched,
since webhook signatures sign received bytes. ``python-multipart`` is not a
dependency, so no ``Form(...)`` or ``request.json()`` can appear here."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from vendorfake.core.kernel.types import UnitRequest, UnitResponse
from vendorfake.core.kernel.unit import make_request

__all__ = ["TRANSPORT", "request_headers", "request_path", "request_query", "to_response", "to_unit_request"]

TRANSPORT = "http"
"""``UnitRequest.transport`` for everything this adapter builds."""


def request_path(request: Request) -> str:
    """The path with percent-escapes intact: ``scope["path"]`` is decoded and would
    re-segment an encoded separator. ``raw_path`` is optional, hence the fallback."""
    raw = request.scope.get("raw_path")
    if isinstance(raw, bytes):
        # latin-1 is the lossless byte-to-str mapping, so nothing can raise here.
        return raw.split(b"?", 1)[0].decode("latin-1")
    return request.url.path


def request_headers(request: Request) -> dict[str, str]:
    """Header names lowercased, repeated names joined with ``", ``" per RFC 9110."""
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
    """Build the :class:`UnitRequest`; ``make_request`` mints or reuses the request id."""
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
    """Wrap the unit's bytes untouched; ``JSONResponse`` would re-serialise them."""
    return Response(content=res.body, status_code=res.status, headers=dict(res.headers))
