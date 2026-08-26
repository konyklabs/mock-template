"""vendorfake — high-fidelity fakes of third-party vendor APIs.

Unofficial. Not affiliated with, endorsed by, or connected to any vendor whose
public API a module here imitates. Every behaviour is derived from publicly
published API documentation.

The package is layered, and the layering is enforced mechanically rather than
by convention (``tools/boundary_check.py`` and the import-linter contracts in
``pyproject.toml``):

``vendorfake.core``
    The stateful machinery — journal-backed state store, state machine,
    capability registry, chaos engine, webhook dispatcher, retry, clock, rng.
    Pure Python. It imports no web framework, and nothing in it knows that
    HTTP exists beyond a request/response shape it defines itself.

``vendorfake.asgi``
    The only place FastAPI is imported. Adapts an ASGI request into the
    core's ``UnitRequest`` and returns the core's bytes untouched.

``vendorfake.conformance``
    The contracts a vendor module must honour, expressed independently of
    language and reachable only through the control plane.

``vendorfake.<vendor>``
    One vendor surface: routes, error vocabulary, signature scheme, retry
    schedule, seed adapter.
"""

from vendorfake.registry import available_vendors, create_unit, resolve_vendor

__version__ = "0.0.0"

__all__ = ["__version__", "available_vendors", "create_unit", "resolve_vendor"]
