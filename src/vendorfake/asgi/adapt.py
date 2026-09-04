"""ASGI request in, unit request out; unit response in, ASGI response out.
Converts, never parses: the body is read once as bytes and returned untouched,
since webhook signatures sign received bytes. ``python-multipart`` is not a
dependency, so no ``Form(...)`` or ``request.json()`` can appear here."""

from __future__ import annotations

try:
    from starlette.requests import Request
    from starlette.responses import Response
except ImportError as exc:
    raise ImportError("vendorfake serve needs the 'serve' extra: pip install 'vendorfake[serve]'") from exc

from vendorfake.core.kernel.types import UnitError, UnitErrorKind, UnitRequest, UnitResponse
from vendorfake.core.kernel.unit import make_request

__all__ = [
    "MAX_BODY_BYTES",
    "TRANSPORT",
    "request_headers",
    "request_path",
    "request_query",
    "to_response",
    "to_unit_request",
]

TRANSPORT = "http"
"""``UnitRequest.transport`` for everything this adapter builds."""

MAX_BODY_BYTES = 8 * 1024 * 1024
"""The largest body the adapter will read; a vendor's own limits are lower."""


def _body_too_large() -> UnitError:
    return UnitError(UnitErrorKind.BAD_REQUEST, detail=f"request body exceeds {MAX_BODY_BYTES} bytes")


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


async def _read_body(request: Request) -> bytes:
    """Read the body, refusing an oversized one before or during the read.

    A declared ``content-length`` above :data:`MAX_BODY_BYTES` is refused
    unread; otherwise the body is streamed and the read stops the moment the
    running total crosses the limit, so a caller sending an unbounded body
    with no declared length cannot make this read further than the limit
    either.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            over_declared = int(declared) > MAX_BODY_BYTES
        except ValueError:
            over_declared = False  # An unparsable content-length is the transport's problem, not this one's.
        if over_declared:
            raise _body_too_large()
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise _body_too_large()
        chunks.append(chunk)
    return b"".join(chunks)


async def to_unit_request(request: Request) -> UnitRequest:
    """Build the :class:`UnitRequest`; ``make_request`` mints or reuses the request id.

    Raises :class:`UnitError` (``bad_request``) instead, unread, for a body
    over :data:`MAX_BODY_BYTES` -- see :func:`_read_body`. The caller (the
    ASGI app's ``dispatch``) shapes that through the vendor's own error
    table, since no :class:`UnitRequest` exists yet for the unit's pipeline
    to answer.
    """
    raw_body = await _read_body(request)
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
