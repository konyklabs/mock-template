"""The kernel: what a request, a response, a route, an error and a vendor are,
without reference to HTTP or to any web framework.

INVARIANT: the seam between the core and any transport is
``Unit.handle(UnitRequest) -> UnitResponse``, and ``raw_body``/``body`` are
already bytes, so a binding can neither re-parse nor re-serialise what is under
test.
"""
