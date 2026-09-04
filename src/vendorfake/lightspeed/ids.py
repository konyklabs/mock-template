"""Lightspeed-shaped identifiers, minted deterministically.

FOR: producing ids that look like the ones in Lightspeed's own documentation
examples, from a seeded stream, so that a transcript of a scenario is the same
on every run and can be diffed between runs. The stream itself -- seeding,
salting away from the chaos stream, re-seeding at hydrate, the draw count --
is :class:`~vendorfake.core.rand.ids.IdStream`; this module is only the shapes.

THE ONE DOCUMENTED SHAPE. Every ``id`` in the specification's own response
examples is a lowercase UUID: ``b1e04bd8-f019-11e3-a0f5-b8ca3a64f8f4`` (an
outlet), ``b8ca3a65-0183-11e4-fbb5-2816e25ffc51`` (a register),
``0adaafb3-6583-11e5-fb60-fd093076e9d3`` (a payment type),
``0ab9b350-ab7f-11ba-fc5c-6e58d999006f`` (a register closure). Several schemas
type the field ``format: uuid`` outright (``Retailer.id``,
``SaleRequestBase.customer_id``). The examples are version-1-shaped -- the
third group starts with ``11e3``/``11e4`` -- and this stream mints the same
layout for that reason; the *version nibble* is JUDGMENT either way, because
no page states one.

TOKENS AND CODES ARE NOT UUIDS. The authorization page's example
``access_token`` is an opaque string with no stated shape, so
:meth:`LightspeedIds.access_token` mints a long mixed-alphanumeric value that
is obviously not a UUID -- a consumer must treat it as opaque, and a
UUID-shaped token would invite exactly the parsing this warns against
(JUDGMENT).

INVARIANT: **request ids are a second stream, apart from the entity stream.**
Lightspeed publishes no request identifier, so this package's errors carry
none -- but the split is kept anyway for the same reason Toast's is: a stream
used by refusals must not renumber a scenario's entity ids. Here the second
stream mints the *credentials* (tokens, codes), which a 4xx path can draw
from, so a rejected token exchange never moves an outlet's id.
"""

from __future__ import annotations

from vendorfake.core.rand.ids import HEX, MIXED_ALNUM, IdStream

__all__ = ["CREDENTIAL_SALT", "LightspeedCredentialIds", "LightspeedIds"]

#: RFC 4122 variant nibble: 8, 9, a or b.
_VARIANT = "89ab"

#: The version-1 layout the specification's own examples show. JUDGMENT: the
#: pages show the format, never the version.
_VERSION = "1"

CREDENTIAL_SALT = 0x1C575EED
"""XOR salt separating the credential stream from the entity id stream (which
is itself salted off the chaos stream). This project's constant."""

_ACCESS_TOKEN_LENGTH = 40
_REFRESH_TOKEN_LENGTH = 40
_AUTHORIZATION_CODE_LENGTH = 32


class _UuidStream(IdStream):
    """The one Lightspeed entity shape, shared by both streams."""

    __slots__ = ()

    def _uuid(self) -> str:
        """A lowercase UUID in the version-1 layout the examples show."""
        hexes = self._pick(HEX, 30)
        variant = _VARIANT[self._rng.int(len(_VARIANT))]
        return f"{hexes[0:8]}-{hexes[8:12]}-{_VERSION}{hexes[12:15]}-{variant}{hexes[15:18]}-{hexes[18:30]}"


class LightspeedIds(_UuidStream):
    """Lightspeed's shapes over the core's stream: every entity is a UUID."""

    __slots__ = ()

    def uuid(self) -> str:
        """The one documented entity shape."""
        return self._uuid()

    # Named aliases, so a call site reads as what it mints and a future shape
    # difference (Lightspeed documents none) is a one-line change.

    def outlet(self) -> str:
        return self.uuid()

    def register(self) -> str:
        return self.uuid()

    def register_closure(self) -> str:
        """The ``register_closure_id`` the payments summary reports and the
        ``register_closure.create`` webhook carries. There is no REST resource
        for a closure; this unit synthesises one at close."""
        return self.uuid()

    def payment_type(self) -> str:
        return self.uuid()

    def webhook(self) -> str:
        """``Webhook.id`` -- typed a plain string in the specification, minted
        as the same UUID shape as every other id here (JUDGMENT)."""
        return self.uuid()

    def sale(self) -> str:
        """``Sale.id`` -- ``format: uuid``, "Auto-generated object ID"."""
        return self.uuid()

    def sale_line_item(self) -> str:
        """``SaleResponseLineItem.id`` -- ``format: uuid``. A caller MAY supply
        its own (``SaleLineItem.id``, "Existing line item ID"), in which case
        nothing is drawn."""
        return self.uuid()

    def sale_payment(self) -> str:
        """``SaleResponsePayment.id`` -- "Auto-generated payment ID"."""
        return self.uuid()

    def sequence_id(self) -> str:
        """``register_open_sequence_id`` -- "**internal** The ID of the current
        register closure object"."""
        return self.uuid()


class LightspeedCredentialIds(IdStream):
    """The credential stream, apart from the entity stream: the same unit seed
    under one more salt, so drawing a token never moves an outlet's id."""

    __slots__ = ()

    def __init__(self, seed: int = 1) -> None:
        super().__init__(seed ^ CREDENTIAL_SALT)

    def reseed(self, seed: int) -> None:
        super().reseed(seed ^ CREDENTIAL_SALT)

    def access_token(self) -> str:
        """An opaque bearer string. Deliberately not UUID-shaped: the vendor
        states no shape and a consumer must not parse one."""
        return self._pick(MIXED_ALNUM, _ACCESS_TOKEN_LENGTH)

    def refresh_token(self) -> str:
        """The rotating refresh credential; same opacity as the access token."""
        return self._pick(MIXED_ALNUM, _REFRESH_TOKEN_LENGTH)

    def authorization_code(self) -> str:
        """The single-use ``code`` the stand-in ``GET /connect`` redirects with."""
        return self._pick(MIXED_ALNUM, _AUTHORIZATION_CODE_LENGTH)
