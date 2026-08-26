"""The transport adapter: the only package here that imports a web framework.

Three modules and nothing else. :mod:`~vendorfake.asgi.adapt` converts between
an ASGI request and the core's ``UnitRequest``/``UnitResponse`` without
interpreting either; :mod:`~vendorfake.asgi.app` registers one catch-all route
over a unit; :mod:`~vendorfake.asgi.serve` puts that application on a socket.

The boundary is mechanical, not conventional: ``tools/boundary_check.py`` and
the import-linter contracts in ``pyproject.toml`` both fail the build if
``fastapi``, ``starlette`` or ``uvicorn`` is reached from any path outside
``src/vendorfake/asgi/``. Importing this package is therefore the one place
where a framework enters the process, which is why ``vendorfake.cli`` imports
it inside the ``serve`` subcommand rather than at module level -- and why a
vendor module, which registers routes as plain dataclasses, has nothing to
import a framework *for*.
"""

from vendorfake.asgi.adapt import TRANSPORT, to_response, to_unit_request
from vendorfake.asgi.app import HTTP_METHODS, OPENAPI_PATH, FrameworkTripwire, create_app, registered_methods
from vendorfake.asgi.serve import DEFAULT_HOST, DEFAULT_PORT, bind, bound_port, run_server

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "HTTP_METHODS",
    "OPENAPI_PATH",
    "TRANSPORT",
    "FrameworkTripwire",
    "bind",
    "bound_port",
    "create_app",
    "registered_methods",
    "run_server",
    "to_response",
    "to_unit_request",
]
