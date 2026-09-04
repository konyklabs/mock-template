"""Bindings: adapters that turn an external calling convention into a
:class:`~vendorfake.core.kernel.types.UnitRequest` and hand the bytes back.

INVARIANT: a binding converts and nothing else. It never parses a body,
re-serialises a response, or decides what a content type means; those belong
to the core. The ASGI adapter, the only place a web framework is imported,
lives outside the core in ``vendorfake.asgi``.
"""
