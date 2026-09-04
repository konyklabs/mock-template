"""The ASGI application: one catch-all route over a synchronous unit. The unit
answers every request, including every error; no typed parameters and no
middleware, and ``Unit.handle`` runs via ``run_in_threadpool``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from vendorfake.asgi.adapt import to_response, to_unit_request
from vendorfake.core.control.openapi import document_for_unit
from vendorfake.core.kernel.reply import JSON_CONTENT_TYPE
from vendorfake.core.kernel.types import Logger, TransportDirective
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.util.json import dump_json

__all__ = [
    "HTTP_METHODS",
    "OPENAPI_PATH",
    "TransportFaultAbort",
    "create_app",
    "registered_methods",
]

HTTP_METHODS: tuple[str, ...] = (
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
    "TRACE",
)
"""Every verb the catch-all is registered for; a missing one gets a Starlette 405."""

OPENAPI_PATH = "/__unit/openapi.json"
"""Where the generated surface description is served, per binding."""


class TransportFaultAbort(Exception):
    """Raised from a streaming body for ``connection_reset``/``empty_response``
    and never caught; the status line is sent, so uvicorn aborts the connection."""


async def _aborted_body() -> AsyncIterator[bytes]:
    raise TransportFaultAbort()
    yield b""  # type: ignore[unreachable]  # pragma: no cover - makes this an async generator


async def _slow_body(body: bytes, chunk_bytes: int, chunk_delay_ms: int) -> AsyncIterator[bytes]:
    """Stream ``body`` in ``chunk_bytes`` pieces, awaiting ``chunk_delay_ms``."""
    chunk_bytes = max(1, chunk_bytes)
    for offset in range(0, len(body), chunk_bytes):
        if offset > 0:
            await asyncio.sleep(chunk_delay_ms / 1000.0)
        yield body[offset : offset + chunk_bytes]


def _directive_response(status: int, headers: dict[str, str], directive: TransportDirective, body: bytes) -> Response:
    if directive.kind == "slow_body":
        # Kernel-resolved: an explicit ``0`` delay is honoured as given.
        chunk_bytes = directive.chunk_bytes if directive.chunk_bytes > 0 else 64
        chunk_delay_ms = directive.chunk_delay_ms
        return StreamingResponse(_slow_body(body, chunk_bytes, chunk_delay_ms), status_code=status, headers=headers)
    # connection_reset / empty_response: see TransportFaultAbort.
    return StreamingResponse(_aborted_body(), status_code=status, headers=headers)


def create_app(
    unit: Unit,
    *,
    logger: Logger | None = None,
) -> FastAPI:
    """Build the ASGI application in front of ``unit``; a factory, never a global."""
    log = unit.context.log if logger is None else logger
    vendor = unit.context.vendor

    app = FastAPI(
        title=f"{vendor.display_name} (vendorfake)",
        version=vendor.api_version or "unversioned",
        # Off, not unused: over one catch-all route it describes only a wildcard.
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.unit = unit
    app.state.methods = HTTP_METHODS

    document = document_for_unit(unit)
    document_bytes = dump_json(document)

    async def dispatch(request: Request) -> Response:
        """The one path from a socket to the unit and back."""
        unit_request = await to_unit_request(request)
        response = await run_in_threadpool(unit.handle, unit_request)
        if response.transport is not None:
            return _directive_response(response.status, dict(response.headers), response.transport, response.body)
        if response.delay_ms > 0:
            # Awaited, not slept: the threadpool is finite.
            await asyncio.sleep(response.delay_ms / 1000.0)
        return to_response(response)

    async def framework_answered(request: Request, exc: Exception) -> Response:
        """The framework tried to answer; log it and dispatch to the unit anyway."""
        log.error(
            "the web framework answered a request instead of the unit",
            {
                "method": request.method,
                "path": request.url.path,
                "exception": type(exc).__name__,
                "detail": str(exc),
            },
        )
        return await dispatch(request)

    app.add_exception_handler(StarletteHTTPException, framework_answered)
    app.add_exception_handler(RequestValidationError, framework_answered)

    @app.api_route("/{full_path:path}", methods=list(HTTP_METHODS), include_in_schema=False)
    async def catch_all(request: Request) -> Response:
        """No typed parameters: a second one would let the framework parse a body."""
        if request.method in {"GET", "HEAD"} and request.url.path == OPENAPI_PATH:
            return Response(content=document_bytes, status_code=200, headers={"content-type": JSON_CONTENT_TYPE})
        return await dispatch(request)

    return app


def registered_methods(app: FastAPI) -> frozenset[str]:
    """Every method the built routes answer, for the test that pins HTTP_METHODS."""
    methods: set[str] = set()
    for route in app.routes:
        found: Any = getattr(route, "methods", None)
        if found:
            methods.update(str(method).upper() for method in found)
    return frozenset(methods)
