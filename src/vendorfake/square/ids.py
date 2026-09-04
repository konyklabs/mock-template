"""Square-shaped identifiers, minted deterministically from a seeded stream
(:class:`~vendorfake.core.rand.ids.IdStream`), matching the shapes in Square's
response examples so a transcript reproduces byte-identical."""

from __future__ import annotations

from vendorfake.core.rand.ids import HEX, MIXED_ALNUM, UPPER_ALNUM, IdStream

__all__ = ["SquareIds"]

#: Square's tokens are base64url-shaped, so the alphabet carries ``-`` and ``_``.
_TOKEN_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


class SquareIds(IdStream):
    """Square's shapes over the core's stream."""

    __slots__ = ()

    # -- entities ----------------------------------------------------------

    def order(self) -> str:
        """``CAIS`` + 23 mixed-case alphanumerics, 27 characters in all."""
        return f"CAIS{self._pick(MIXED_ALNUM, 23)}"

    def line_item_uid(self) -> str:
        return self._pick(MIXED_ALNUM, 22)

    def fulfillment_uid(self) -> str:
        """The same shape as a line-item uid: 22 mixed-case alphanumerics."""
        return self._pick(MIXED_ALNUM, 22)

    def location(self) -> str:
        return self._pick(UPPER_ALNUM, 13)

    def merchant(self) -> str:
        return self._pick(UPPER_ALNUM, 13)

    def catalog_object(self) -> str:
        return self._pick(UPPER_ALNUM, 24)

    def inventory_change(self) -> str:
        """24 upper-case alphanumerics. JUDGMENT: inferred from Square's examples, not a documented rule."""
        return self._pick(UPPER_ALNUM, 24)

    def tender(self) -> str:
        return self._pick(MIXED_ALNUM, 27)

    def payment(self) -> str:
        """29 mixed-case alphanumerics, from Square's CreatePayment example
        (https://developer.squareup.com/reference/square/payments-api/create-payment)."""
        return self._pick(MIXED_ALNUM, 29)

    def customer(self) -> str:
        """26 upper-case alphanumerics, the shape of Square's customer ids."""
        return self._pick(UPPER_ALNUM, 26)

    def uuid(self) -> str:
        """UUID-shaped, ``8-4-4-4-12`` lowercase hex, from the seeded stream (not ``uuid4``)."""
        hex32 = self._pick(HEX, 32)
        return f"{hex32[:8]}-{hex32[8:12]}-{hex32[12:16]}-{hex32[16:20]}-{hex32[20:]}"

    # -- OAuth -------------------------------------------------------------

    def authorization_code(self) -> str:
        """``sq0cgb-`` + 22 token characters. The prefix is asserted by test."""
        return f"sq0cgb-{self._pick(_TOKEN_CHARS, 22)}"

    def access_token(self) -> str:
        return f"EAAA{self._pick(_TOKEN_CHARS, 60)}"

    def refresh_token(self) -> str:
        return f"EQAA{self._pick(_TOKEN_CHARS, 60)}"

    # -- webhooks ----------------------------------------------------------

    def subscription(self) -> str:
        """``wbhk_`` + 32 lowercase hex characters."""
        return f"wbhk_{self._pick(HEX, 32)}"

    def signature_key(self) -> str:
        return self._pick(_TOKEN_CHARS, 22)
