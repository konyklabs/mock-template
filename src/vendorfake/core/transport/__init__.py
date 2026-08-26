"""Bindings: ways of getting a request into a unit.

FOR: holding every adapter that turns some external calling convention into a
:class:`~vendorfake.core.kernel.types.UnitRequest` and hands the resulting
bytes back.

INVARIANT: **a binding converts and nothing else.** It never parses a body, it
never re-serialises a response, and it never decides what a content type means
-- all three belong to the core, which is what keeps "the core does not assume
HTTP" mechanically true. A binding that reached for a form parser would be
putting the content-type decision at the edge, which is precisely the leak the
framework-free-core invariant exists to forbid.

That every binding in this package is framework-free is not an accident of
which ones happen to be written: the ASGI adapter, the only place a web
framework may be imported at all, lives outside the core in
``vendorfake.asgi``. What is here is the proof that the seam is real.
"""
