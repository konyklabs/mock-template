"""Clover-shaped identifiers, minted deterministically from a seeded stream
(:class:`~vendorfake.core.rand.ids.IdStream`) so a scenario transcript is
reproducible across runs.

PARTIAL: entity ids are 13 uppercase alphanumerics, consistent across every
official example (e.g. ``KFRPRVCZ73JHM``,
https://docs.clover.com/dev/docs/paginating-elements) but never stated as a
rule. JUDGMENT: tokens and codes are lowercase v4-layout UUIDs, matching the
webhook docs' examples (https://docs.clover.com/dev/docs/webhooks); the v4
layout itself is not documented.
"""

from __future__ import annotations

from vendorfake.core.rand.ids import HEX, UPPER_ALNUM, IdStream

__all__ = ["CloverIds"]

_VARIANT = "89ab"


class CloverIds(IdStream):
    __slots__ = ()

    def _entity(self) -> str:
        return self._pick(UPPER_ALNUM, 13)

    def _uuid(self) -> str:
        hexes = self._pick(HEX, 30)
        variant = _VARIANT[self._rng.int(len(_VARIANT))]
        return f"{hexes[0:8]}-{hexes[8:12]}-4{hexes[12:15]}-{variant}{hexes[15:18]}-{hexes[18:30]}"

    def merchant(self) -> str:
        return self._entity()

    def order(self) -> str:
        return self._entity()

    def line_item(self) -> str:
        return self._entity()

    def item(self) -> str:
        return self._entity()

    def order_type(self) -> str:
        return self._entity()

    def customer(self) -> str:
        return self._entity()

    def payment(self) -> str:
        return self._entity()

    def print_event(self) -> str:
        return self._entity()

    def access_token(self) -> str:
        return self._uuid()

    def refresh_token(self) -> str:
        return self._uuid()

    def authorization_code(self) -> str:
        return self._uuid()

    def verification_code(self) -> str:
        """The ``verificationCode`` Clover POSTs during webhook verification."""
        return self._uuid()

    def webhook_auth_code(self) -> str:
        """The static ``X-Clover-Auth`` header value sent with every delivery."""
        return self._uuid()
