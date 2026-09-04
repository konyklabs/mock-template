"""vendorfake -- fakes of third-party vendor APIs, checked against their published schemas. Unofficial, and
not affiliated with, endorsed by or connected to any vendor whose public API a
module here imitates. ``core`` and ``asgi`` are internal, and
``docs/api-contract.md`` says what is public; most consumers want
:mod:`vendorfake.testing` rather than the names re-exported here."""

from vendorfake.registry import (
    available_profiles,
    available_vendors,
    create_unit,
    resolve_vendor,
    routes,
)

__version__ = "0.5.0"
"""The imported code's version, which a source checkout's metadata may not be."""

__all__ = [
    "__version__",
    "available_profiles",
    "available_vendors",
    "create_unit",
    "resolve_vendor",
    "routes",
]
