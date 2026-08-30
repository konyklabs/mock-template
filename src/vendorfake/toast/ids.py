"""Toast-shaped identifiers, minted deterministically.

FOR: producing ids that look like the ones in Toast's own documentation, from a
seeded stream, so that a transcript of a scenario is the same on every run and
can be diffed between runs.

INVARIANT: **the id stream never consumes the unit's chaos stream.** Both are
seeded from the unit seed, but this one is salted through
:func:`~vendorfake.core.rand.rng.salted_seed`, so adding a probability rule to
a profile does not renumber every generated id.

SECOND INVARIANT: **request ids are a third stream, apart from both.** Every
ErrorMessage carries a ``requestId`` (apiResponsesAndErrors.html), so a refused
request draws one -- and the package rule is that a 4xx must not move the
entity id stream, or a scenario's order guids would depend on how many typos a
consumer made on the way. :class:`ToastRequestIds` is therefore seeded from the
same unit seed under its own salt; the two streams cannot interleave.

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

:meth:`ToastIds.reseed` exists so a unit that re-hydrates on
``POST /__unit/state/reset`` mints the same ids again.
"""

from __future__ import annotations

from vendorfake.core.rand.rng import Rng, salted_seed

__all__ = ["REQUEST_ID_SALT", "ToastIds", "ToastRequestIds"]

_HEX = "0123456789abcdef"
#: RFC 4122 variant nibble: 8, 9, a or b.
_VARIANT = "89ab"

REQUEST_ID_SALT = 0x7EA57A57
"""XOR salt separating the request-id stream from the entity id stream (which
is itself salted off the chaos stream). This project's constant."""


def _uuid(rng: Rng) -> str:
    """A v4-layout lowercase UUID from ``rng``. JUDGMENT on the version nibble."""
    hexes = "".join(_HEX[rng.int(len(_HEX))] for _ in range(30))
    variant = _VARIANT[rng.int(len(_VARIANT))]
    return f"{hexes[0:8]}-{hexes[8:12]}-4{hexes[12:15]}-{variant}{hexes[15:18]}-{hexes[18:30]}"


class ToastIds:
    """One deterministic entity-id stream for one unit."""

    __slots__ = ("_rng",)

    def __init__(self, seed: int = 1) -> None:
        self._rng = Rng(salted_seed(seed))

    def reseed(self, seed: int) -> None:
        """Restart the stream from ``seed``, salted as at construction."""
        self._rng = Rng(salted_seed(seed))

    @property
    def draw_count(self) -> int:
        """Draws taken so far -- how a report shows the stream advanced."""
        return self._rng.draw_count

    def guid(self) -> str:
        """The one documented shape, for every entity Toast assigns a guid to."""
        return _uuid(self._rng)

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

    def internal(self, prefix: str) -> str:
        """``<prefix>_`` + 12 hex characters, for ids Toast does not shape --
        the subscription stand-in's records."""
        return f"{prefix}_{''.join(_HEX[self._rng.int(len(_HEX))] for _ in range(12))}"


class ToastRequestIds:
    """The ``requestId`` stream, apart from the entity stream. See the module
    docstring's second invariant."""

    __slots__ = ("_rng",)

    def __init__(self, seed: int = 1) -> None:
        self._rng = Rng(salted_seed(seed ^ REQUEST_ID_SALT))

    def reseed(self, seed: int) -> None:
        self._rng = Rng(salted_seed(seed ^ REQUEST_ID_SALT))

    @property
    def draw_count(self) -> int:
        return self._rng.draw_count

    def request_id(self) -> str:
        """A lowercase UUID, the documented ``requestId`` shape."""
        return _uuid(self._rng)
