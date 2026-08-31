"""Toast-shaped identifiers, minted deterministically.

FOR: producing ids that look like the ones in Toast's own documentation, from
a seeded stream, so that a transcript of a scenario is the same on every run
and can be diffed between runs. The stream itself -- seeding, salting away
from the chaos stream, re-seeding at hydrate, the draw count -- is
:class:`~vendorfake.core.rand.ids.IdStream`; this module is only the shapes.

INVARIANT: **request ids are a second stream, apart from the entity stream.**
Every ErrorMessage carries a ``requestId`` (apiResponsesAndErrors.html), so a
refused request draws one -- and the package rule is that a 4xx must not move
the entity id stream, or a scenario's order guids would depend on how many
typos a consumer made on the way. :class:`ToastRequestIds` is therefore
seeded from the same unit seed under its own extra salt; the two streams
cannot interleave.

The one documented shape
------------------------
DOCUMENTED (https://doc.toasttab.com/doc/devguide/apiUnderstandingGuidsEntityIdentifiersAndMultilocationIds_V2.html):
a Toast guid is a lowercase UUID, e.g. ``2071fb81-988b-4d75-b8dc-c5c17cff9706``,
for restaurants, orders, checks, selections, menu items, webhook events and
request ids alike. The version-4 layout (the ``4`` and variant nibbles) is this
project's choice for plausibility -- the page shows the format, not the
version -- and is labelled JUDGMENT for that reason.

``multiLocationId`` is a numeric string ("100000000171239701", same page) on
menu entities and is *not* minted here: those entities are read-only reference
data from the seed, and the seed carries their ids. ``referenceId`` -- the
small integer keys of the Menus V3 maps -- likewise.
"""

from __future__ import annotations

from vendorfake.core.rand.ids import HEX, IdStream

__all__ = ["REQUEST_ID_SALT", "ToastIds", "ToastRequestIds"]

#: RFC 4122 variant nibble: 8, 9, a or b.
_VARIANT = "89ab"

REQUEST_ID_SALT = 0x7EA57A57
"""XOR salt separating the request-id stream from the entity id stream (which
is itself salted off the chaos stream). This project's constant."""


class _UuidStream(IdStream):
    """The one Toast shape, shared by both streams."""

    __slots__ = ()

    def _uuid(self) -> str:
        """A v4-layout lowercase UUID. JUDGMENT on the version nibble."""
        hexes = self._pick(HEX, 30)
        variant = _VARIANT[self._rng.int(len(_VARIANT))]
        return f"{hexes[0:8]}-{hexes[8:12]}-4{hexes[12:15]}-{variant}{hexes[15:18]}-{hexes[18:30]}"


class ToastIds(_UuidStream):
    """Toast's shapes over the core's stream: everything is a guid."""

    __slots__ = ()

    def guid(self) -> str:
        """The one documented shape, for every entity Toast assigns a guid to."""
        return self._uuid()

    # Named aliases, so a call site reads as what it mints and a future shape
    # difference (Toast documents none) is a one-line change.

    def order(self) -> str:
        return self.guid()

    def check(self) -> str:
        return self.guid()

    def selection(self) -> str:
        return self.guid()

    def payment(self) -> str:
        return self.guid()

    def applied_discount(self) -> str:
        return self.guid()

    def token_id(self) -> str:
        """The JWT ``jti`` claim and the token record's id (JUDGMENT: Toast
        documents no token identifier at all)."""
        return self.guid()

    def event(self) -> str:
        """A webhook envelope's ``guid`` -- documented as the event guid
        (https://doc.toasttab.com/doc/devguide/apiMessageDataSchema.html)."""
        return self.guid()


class ToastRequestIds(_UuidStream):
    """The ``requestId`` stream, apart from the entity stream: the same unit
    seed under one more salt, so drawing from one never moves the other."""

    __slots__ = ()

    def __init__(self, seed: int = 1) -> None:
        super().__init__(seed ^ REQUEST_ID_SALT)

    def reseed(self, seed: int) -> None:
        super().reseed(seed ^ REQUEST_ID_SALT)

    def request_id(self) -> str:
        """A lowercase UUID, the documented ``requestId`` shape."""
        return self._uuid()
