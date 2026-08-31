"""Square-shaped identifiers, minted deterministically.

FOR: producing ids that *look* like the ones in Square's own documentation
examples, from a seeded stream, so that a transcript of a scenario is the same
on every run and can be diffed between runs.

The stream itself -- seeding, salting away from the chaos stream, re-seeding
at hydrate, the draw count -- is :class:`~vendorfake.core.rand.ids.IdStream`;
this module is only the shapes.

Shapes, from the response examples on developer.squareup.com/reference/square:

===============  ============================================
order            ``CAISENgvlJ6jLWAzERDzjyHVybY``  (27 chars)
payment          ``R2B3Z8WMVt3EAmzYWLZvz7Y69EbZY`` (29 chars)
location         ``18YC4JDH91E1H``                (13 chars)
merchant         ``MLQW2MYBY81PZ``                (13 chars)
catalog object   ``W62UWFY35CWMYGVWK6TWJDNI``     (24 chars)
customer         ``QPTXM8PQNX3Q726ZYHPMNP46XC``   (26 chars)
loyalty ids      ``79b807d2-d786-46a9-933b-918028d7a8c5`` (UUID-shaped)
authorization    ``sq0cgb-xJPZ8rwCk7KfapZz815Grw``
access token     ``EAAAl3ikZIe18J-2-cHlV2bL4-...``
subscription     ``wbhk_b35f6b3145074cf9ad513610786c19d5``
===============  ============================================

Consumers pattern-match on id shape more often than they admit -- log
scrapers, fixture assertions, column widths -- so the shapes are part of the
contract even though no Square document states them as a rule.

:meth:`SquareIds.reseed` is the piece the reference has no need for. Its vendor
object is built per unit by a factory; here a vendor definition may be handed
to a unit that starts, re-hydrates on ``POST /__unit/state/reset``, and must
then mint *the same ids again*. Re-seeding at hydrate is what makes that true.
"""

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
        """24 upper-case alphanumerics -- the shape of an InventoryAdjustment
        or InventoryPhysicalCount id in Square's examples. JUDGMENT on the
        shape; the examples show it without stating a rule."""
        return self._pick(UPPER_ALNUM, 24)

    def tender(self) -> str:
        return self._pick(MIXED_ALNUM, 27)

    def payment(self) -> str:
        """29 mixed-case alphanumerics, the shape of the CreatePayment example
        (https://developer.squareup.com/reference/square/payments-api/create-payment)."""
        return self._pick(MIXED_ALNUM, 29)

    def customer(self) -> str:
        """26 upper-case alphanumerics, the shape of Square's customer ids."""
        return self._pick(UPPER_ALNUM, 26)

    def uuid(self) -> str:
        """UUID-shaped, ``8-4-4-4-12`` lowercase hex, from the stream.

        Loyalty programs, accounts, mappings and events all carry this shape
        in Square's examples. Drawn from the seeded stream rather than from
        ``uuid4`` so a scenario reproduces its ids.
        """
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
