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

Of those four, ``vendorfake.core`` and ``vendorfake.asgi`` are internal and
may change in any release. What is public, and what the deprecation policy
covers, is written down once in ``docs/api-contract.md`` and pinned by
``tests/unit/test_public_api.py``.

The five names re-exported here are the whole answer to "how do I build a
unit": ask what exists (:func:`~vendorfake.registry.available_vendors`,
:func:`~vendorfake.registry.available_profiles`,
:func:`~vendorfake.registry.routes`), then build one
(:func:`~vendorfake.registry.create_unit`,
:func:`~vendorfake.registry.resolve_vendor`). They are re-exported rather
than left in :mod:`vendorfake.registry` alone because discovery and
construction are one task, and a consumer who has just been told to import
``create_unit`` from the package root should not have to learn a second module
name to find out which profiles that vendor ships. Both spellings work;
neither is deprecated.

Most consumers want none of these: :mod:`vendorfake.testing`'s ``unit()`` and
``served()`` build a unit *and* hand back a driver, a client and a seed.
"""

from vendorfake.registry import (
    available_profiles,
    available_vendors,
    create_unit,
    resolve_vendor,
    routes,
)

__version__ = "0.1.0"
"""The distribution's version, as release-please writes it.

Read it rather than ``importlib.metadata.version("vendorfake")`` when what you
want is the version of the code that is *imported*: the two disagree in a
source checkout, which is where every test in this repository runs.
"""

__all__ = [
    "__version__",
    "available_profiles",
    "available_vendors",
    "create_unit",
    "resolve_vendor",
    "routes",
]
