"""Lightspeed-shaped identifiers, minted deterministically over
:class:`~vendorfake.core.rand.ids.IdStream`.

DOCUMENTED: every ``id`` in the specification's examples is a lowercase,
version-1-shaped UUID (version nibble is JUDGMENT); tokens and codes are NOT
UUIDs, since the authorization page's ``access_token`` example is an opaque,
shapeless string. INVARIANT: credentials are a second stream, apart from the
entity stream, so a rejected credential draw never renumbers entity ids.
"""

from __future__ import annotations

from vendorfake.core.rand.ids import HEX, MIXED_ALNUM, IdStream

__all__ = ["CODE_ALPHABET", "CREDENTIAL_SALT", "LightspeedCredentialIds", "LightspeedIds"]

#: RFC 4122 variant nibble: 8, 9, a or b.
_VARIANT = "89ab"

#: Version-1 layout the spec's own examples show. JUDGMENT: pages show the format, not
#: the version.
_VERSION = "1"

CREDENTIAL_SALT = 0x1C575EED
"""XOR salt separating the credential stream from the entity id stream."""

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
"""JUDGMENT: the vendor shows the code shape (``Tony-N4ZJ``), never the alphabet;
``I``/``O``/``0``/``1`` are excluded so a code read off a receipt is unambiguous."""

_CUSTOMER_CODE_LENGTH = 4

_ACCESS_TOKEN_LENGTH = 40
_REFRESH_TOKEN_LENGTH = 40
_AUTHORIZATION_CODE_LENGTH = 32


class _UuidStream(IdStream):
    """The one Lightspeed entity shape, shared by both streams."""

    __slots__ = ()

    def _uuid(self) -> str:
        hexes = self._pick(HEX, 30)
        variant = _VARIANT[self._rng.int(len(_VARIANT))]
        return f"{hexes[0:8]}-{hexes[8:12]}-{_VERSION}{hexes[12:15]}-{variant}{hexes[15:18]}-{hexes[18:30]}"


class LightspeedIds(_UuidStream):
    """Lightspeed's shapes over the core's stream: every entity is a UUID."""

    __slots__ = ()

    def uuid(self) -> str:
        """The one documented entity shape."""
        return self._uuid()

    # Named aliases, so a call site reads as what it mints.

    def outlet(self) -> str:
        return self.uuid()

    def register(self) -> str:
        return self.uuid()

    def register_closure(self) -> str:
        return self.uuid()

    def payment_type(self) -> str:
        return self.uuid()

    def product(self) -> str:
        return self.uuid()

    def product_family(self) -> str:
        return self.uuid()

    def product_code(self) -> str:
        return self.uuid()

    def product_supplier(self) -> str:
        return self.uuid()

    def inventory(self) -> str:
        return self.uuid()

    def stock_adjustment(self) -> str:
        return self.uuid()

    def customer(self) -> str:
        return self.uuid()

    def customer_group(self) -> str:
        return self.uuid()

    def webhook(self) -> str:
        """JUDGMENT: ``Webhook.id`` is typed a plain string in the spec; minted as a UUID here."""
        return self.uuid()

    def sale(self) -> str:
        return self.uuid()

    def sale_line_item(self) -> str:
        """A caller MAY supply its own (``SaleLineItem.id``), in which case nothing is drawn."""
        return self.uuid()

    def sale_payment(self) -> str:
        return self.uuid()

    def sequence_id(self) -> str:
        return self.uuid()


class LightspeedCredentialIds(IdStream):
    """The credential stream: the same unit seed under one more salt."""

    __slots__ = ()

    def __init__(self, seed: int = 1) -> None:
        super().__init__(seed ^ CREDENTIAL_SALT)

    def reseed(self, seed: int) -> None:
        super().reseed(seed ^ CREDENTIAL_SALT)

    def customer_code(self) -> str:
        """Mints the four-character suffix of ``Customer.customer_code`` (``Tony-N4ZJ``)."""
        return self._pick(CODE_ALPHABET, _CUSTOMER_CODE_LENGTH)

    def access_token(self) -> str:
        """JUDGMENT: opaque bearer string, deliberately not UUID-shaped since the vendor states no shape."""
        return self._pick(MIXED_ALNUM, _ACCESS_TOKEN_LENGTH)

    def refresh_token(self) -> str:
        return self._pick(MIXED_ALNUM, _REFRESH_TOKEN_LENGTH)

    def authorization_code(self) -> str:
        """The single-use ``code`` the stand-in ``GET /connect`` redirects with."""
        return self._pick(MIXED_ALNUM, _AUTHORIZATION_CODE_LENGTH)
