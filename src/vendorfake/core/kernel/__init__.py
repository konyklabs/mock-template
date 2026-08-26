"""The kernel: the contract every other module is written against.

FOR: defining what a request, a response, a route, an error and a vendor are,
without reference to HTTP or to any web framework.

INVARIANT: the seam between the core and any transport is
``Unit.handle(UnitRequest) -> UnitResponse`` and nothing else crosses it.
``UnitRequest.raw_body`` is the exact received bytes and ``UnitResponse.body``
is already-serialised bytes, so a binding can neither re-parse nor
re-serialise what is under test.
"""
