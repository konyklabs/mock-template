"""Toast-shaped identifiers, minted deterministically from
:class:`~vendorfake.core.rand.ids.IdStream`, so a scenario transcript is
stable across runs.

INVARIANT: request ids are a second stream, apart from the entity stream --
:class:`ToastRequestIds` is seeded under its own extra salt so a refused
request never moves the entity stream.

DOCUMENTED (https://doc.toasttab.com/doc/devguide/apiUnderstandingGuidsEntityIdentifiersAndMultilocationIds_V2.html):
a Toast guid is a lowercase UUID shared by every entity type. JUDGMENT: the
version-4 layout is this project's choice; the page shows the format, not
the version.
"""

from __future__ import annotations

from vendorfake.core.rand.ids import HEX, IdStream

__all__ = ["REQUEST_ID_SALT", "ToastIds", "ToastRequestIds"]

_VARIANT = "89ab"

REQUEST_ID_SALT = 0x7EA57A57
"""XOR salt separating the request-id stream from the entity id stream."""


class _UuidStream(IdStream):
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
        return self._uuid()

    # Named aliases; a call site reads as what it mints.

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
        """JWT ``jti`` claim and token-record id (JUDGMENT: undocumented)."""
        return self.guid()


class ToastRequestIds(_UuidStream):
    """The ``requestId`` stream, apart from the entity stream."""

    __slots__ = ()

    def __init__(self, seed: int = 1) -> None:
        super().__init__(seed ^ REQUEST_ID_SALT)

    def reseed(self, seed: int) -> None:
        super().reseed(seed ^ REQUEST_ID_SALT)

    def request_id(self) -> str:
        """A lowercase UUID, the documented ``requestId`` shape."""
        return self._uuid()
