"""The ASGI application: one catch-all route over a synchronous unit.

FOR: putting a socket in front of ``Unit.handle`` and doing nothing else. This
module and :mod:`vendorfake.asgi.adapt` are the only places in the
distribution that import a web framework, and everything about their shape is
chosen to keep that true.

INVARIANT: **the unit answers every request, including every error.** There is
exactly one route -- ``/{full_path:path}`` over the complete verb set -- and it
declares no typed parameters, so the framework has nothing to validate, no
path to fail to match and no method to reject. A framework 404 would be an
error document in the framework's vocabulary rather than the vendor's, and a
consumer testing their error handling against it would be testing Starlette.
A framework 422 would be worse: it would mean the framework parsed a body,
which is the leak the core exists to prevent.

That the property *holds* is not left to reading. Handlers are registered for
the two exceptions a framework answers with -- ``HTTPException`` and
``RequestValidationError`` -- and each one increments a counter before handing
the request to the unit anyway. The counter is reported by
``GET /__unit/health`` as ``framework_answered``, so it is readable over HTTP
from the parent of an out-of-process test, which a module-level list inside the
serving process would not be. Its correct value is 0, forever; a non-zero one
means the catch-all has a hole and names the request that found it.

Two more shapes worth stating, because both are easy to undo by accident:

**No middleware.** Not for compression, not for CORS, not for a request id.
Every middleware in the stack is a chance to rewrite the bytes the unit
produced, and byte-for-byte agreement between this binding and the in-process
one is a conformance contract, not a nicety. Anything a response needs is set
by the vendor's ``decorate`` hook inside the core, where every binding gets it.

**The synchronous core is bridged, not adapted.** ``Unit.handle`` is a plain
``def`` holding a real lock; calling it directly from the event loop would
block every other connection for the duration. ``run_in_threadpool`` gives it
a worker thread, which is also what makes the ``serialized=False`` routes --
draining the webhook queue, advancing a virtual clock -- work at all: they wait
on machinery another request has to feed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from vendorfake.asgi.adapt import to_response, to_unit_request
from vendorfake.core.control.openapi import document_for_unit
from vendorfake.core.kernel.reply import JSON_CONTENT_TYPE
from vendorfake.core.kernel.types import Logger
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.util.json import dump_json

__all__ = ["HTTP_METHODS", "OPENAPI_PATH", "FrameworkTripwire", "create_app", "registered_methods"]

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
"""Every verb the catch-all is registered for.

Complete on purpose and pinned by a test. A verb missing from this tuple is
answered by Starlette with a 405 that never reaches the unit -- which is the
one way the catch-all can have a hole while still looking like a catch-all.
An exotic method outside this set (``PROPFIND``, say) is what the tripwire
below exists to catch, and is the only request in this design a framework can
still answer first."""

OPENAPI_PATH = "/__unit/openapi.json"
"""Where the generated description of the surface is served.

Deliberately *not* a control-plane route. The document describes an HTTP
surface, so it is a fact about this binding rather than about the unit, and the
unit's own route table stays the same whichever binding is in front of it. The
same document is printed by ``vendorfake openapi`` with no server running, from
the generator in the core -- which is why the framework's own generator is
switched off entirely rather than merely ignored: pointed at a single catch-all
route it would publish one wildcard entry and call it a description."""


@dataclass
class FrameworkTripwire:
    """A counter of requests the web framework tried to answer by itself.

    Not a log and not a list: a number, because the only place it can be read
    is over HTTP from another process, and a number survives that trip. Each
    hit also carries a description into the unit's logger at ``error`` level,
    so the number tells you *that* the catch-all has a hole and the log tells
    you which request found it.
    """

    count: int = 0
    #: The most recent few hits, for a test failure message that says what
    #: happened. Bounded, because an unbounded list in a long-running server is
    #: a leak, and because after the first hit the invariant is already broken.
    recent: list[str] = field(default_factory=list)
    limit: int = 8

    def get(self) -> int:
        """The count, as the callable ``/__unit/health`` reports through."""
        return self.count

    def record(self, description: str) -> None:
        self.count += 1
        if len(self.recent) < self.limit:
            self.recent.append(description)


def create_app(
    unit: Unit,
    *,
    tripwire: FrameworkTripwire | None = None,
    logger: Logger | None = None,
) -> FastAPI:
    """Build the ASGI application in front of ``unit``.

    A factory, never a module-level ``app = FastAPI()``. A module-level
    application would be constructed on import -- by the CLI's ``--help``, by a
    test collecting a neighbouring module, by anything that touched this
    package -- and would need a unit to exist before anyone asked for one,
    which means a global unit, which means one test's state reaching another's.

    ``tripwire`` is the same object whose ``get`` was handed to ``create_unit``
    as ``framework_answered``. Passing it here and there is the whole wiring:
    the unit reports the number, this application increments it, and neither
    knows anything else about the other.
    """
    fired = FrameworkTripwire() if tripwire is None else tripwire
    log = unit.context.log if logger is None else logger
    vendor = unit.context.vendor

    app = FastAPI(
        title=f"{vendor.display_name} (vendorfake)",
        version=vendor.api_version or "unversioned",
        # The framework's own generator is switched off, not left unused: with
        # one catch-all route it can only describe a wildcard, and a wrong
        # description served at a conventional path is worse than none.
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.unit = unit
    app.state.tripwire = fired
    app.state.methods = HTTP_METHODS

    document = document_for_unit(unit)
    #: Serialised once. The route table is fixed at unit construction, so the
    #: document cannot change while the process runs, and re-encoding it per
    #: request would only add a way for two requests to disagree.
    document_bytes = dump_json(document)

    async def dispatch(request: Request) -> Response:
        """The one path from a socket to the unit and back."""
        unit_request = await to_unit_request(request)
        response = await run_in_threadpool(unit.handle, unit_request)
        if response.delay_ms > 0:
            # The kernel decided *whether* to delay; this binding decides how,
            # and for a server holding a real socket that means awaiting rather
            # than sleeping. `time.sleep` on the worker thread would be nearly
            # as good -- it is not the event loop -- but the pool is finite, so
            # a handful of concurrently delayed requests would stop answering
            # everyone else, which is not what the fault is meant to rehearse.
            #
            # Nothing is short-circuited here the way the in-process transport
            # short-circuits a delay longer than the caller's read timeout: over
            # a socket the client's timeout is the client's business, and it
            # will disconnect on its own. From the caller's point of view served
            # mode behaves exactly as it did when the kernel slept.
            await asyncio.sleep(response.delay_ms / 1000.0)
        return to_response(response)

    async def framework_answered(request: Request, exc: Exception) -> Response:
        """What to do when the framework tried to answer -- which it should not.

        Recording and then dispatching anyway, rather than returning the
        framework's own document. The consumer still gets a vendor-shaped
        response, so a hole in the catch-all cannot present as "your error
        handling is broken"; and the counter, not the response, is where the
        hole is reported.
        """
        fired.record(f"{request.method} {request.url.path}: {type(exc).__name__}: {exc}")
        log.error(
            "the web framework answered a request instead of the unit",
            {
                "method": request.method,
                "path": request.url.path,
                "exception": type(exc).__name__,
                "detail": str(exc),
                "framework_answered": fired.count,
            },
        )
        return await dispatch(request)

    app.add_exception_handler(StarletteHTTPException, framework_answered)
    app.add_exception_handler(RequestValidationError, framework_answered)

    @app.api_route("/{full_path:path}", methods=list(HTTP_METHODS), include_in_schema=False)
    async def catch_all(request: Request) -> Response:
        """No typed parameters, by construction.

        ``request`` is the only argument, and it is a ``Request``, so FastAPI
        has nothing to validate and can raise no ``RequestValidationError``.
        The moment a second parameter appears here -- a ``Form(...)``, a
        ``Body(...)``, even an annotated query string -- the framework starts
        deciding what a body is, and the core stops being the thing under test.
        """
        if request.method in {"GET", "HEAD"} and request.url.path == OPENAPI_PATH:
            return Response(content=document_bytes, status_code=200, headers={"content-type": JSON_CONTENT_TYPE})
        return await dispatch(request)

    return app


def registered_methods(app: FastAPI) -> frozenset[str]:
    """Every method the application's routes actually answer.

    Exists for the test that pins :data:`HTTP_METHODS`. Reading it back off the
    built application rather than off the constant is the point: the constant
    is what we meant, this is what the framework did with it, and a divergence
    between the two is exactly the failure the pin is for.
    """
    methods: set[str] = set()
    for route in app.routes:
        found: Any = getattr(route, "methods", None)
        if found:
            methods.update(str(method).upper() for method in found)
    return frozenset(methods)
