"""Clover-shaped identifiers, minted deterministically.

FOR: producing ids that *look* like the ones in Clover's own documentation
examples, from a seeded stream, so that a transcript of a scenario is the same
on every run and can be diffed between runs.

INVARIANT: **the id stream never consumes the unit's chaos stream.** Both are
seeded from the unit seed, but this one is salted through
:func:`~vendorfake.core.rand.rng.salted_seed`, so adding a probability rule to
a profile does not renumber every generated id -- and a scenario that mints
ten orders draws the same ten ids whether or not a fault fired.

Two shapes, two confidence levels
---------------------------------
**Entity ids: 13 uppercase alphanumerics.** PARTIAL -- consistent across every
official example (``DRKVJT2ZRRRSC``, ``XYZVJT2ZRRRSC``, ``VXQEY3VMTT74T``,
``KFRPRVCZ73JHM``, ``MXHW24RNRHW16`` across the webhooks, orders and
pagination pages, e.g. https://docs.clover.com/dev/docs/paginating-elements),
but no Clover page states the format as a rule. Consumers pattern-match on id
shape more often than they admit -- log scrapers, fixture assertions, column
widths -- so the shape is followed while its provenance is labelled.

**Tokens and codes: UUID-format strings.** JUDGMENT -- the v2 OAuth docs show
only ``{access_token}``-style placeholders, and Clover's legacy examples and
the documented webhook artifacts (``{"verificationCode": "<uuid>"}``,
``X-Clover-Auth: <uuid>`` on https://docs.clover.com/dev/docs/webhooks) are
UUID-shaped, so access tokens, refresh tokens, authorization codes and both
webhook codes are all minted as lowercase UUIDs here. The version-4 layout
(the ``4`` and variant nibbles) is this project's choice for plausibility, not
a documented Clover property.

:meth:`CloverIds.reseed` exists for the same reason ``SquareIds.reseed`` does:
a unit that re-hydrates on ``POST /__unit/state/reset`` must mint *the same
ids again*, so the stream restarts from the seed rather than continuing.
"""

from __future__ import annotations

from vendorfake.core.rand.rng import Rng, salted_seed

__all__ = ["CloverIds"]

_UPPER_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_HEX = "0123456789abcdef"
#: RFC 4122 variant nibble: 8, 9, a or b.
_VARIANT = "89ab"


class CloverIds:
    """One deterministic id stream for one unit."""

    __slots__ = ("_rng",)

    def __init__(self, seed: int = 1) -> None:
        self._rng = Rng(salted_seed(seed))

    def reseed(self, seed: int) -> None:
        """Restart the stream from ``seed``, salted as at construction.

        Called at hydrate, so that re-seeding a unit reproduces its ids rather
        than continuing the stream from wherever the previous scenario left it.
        """
        self._rng = Rng(salted_seed(seed))

    @property
    def draw_count(self) -> int:
        """Draws taken so far -- how a report shows the stream advanced."""
        return self._rng.draw_count

    def _pick(self, alphabet: str, length: int) -> str:
        return "".join(alphabet[self._rng.int(len(alphabet))] for _ in range(length))

    def _entity(self) -> str:
        """13 uppercase alphanumerics -- the one shape every Clover example uses."""
        return self._pick(_UPPER_ALNUM, 13)

    def _uuid(self) -> str:
        """A v4-layout UUID from the stream. See the module docstring; JUDGMENT."""
        hexes = self._pick(_HEX, 30)
        variant = _VARIANT[self._rng.int(len(_VARIANT))]
        return f"{hexes[0:8]}-{hexes[8:12]}-4{hexes[12:15]}-{variant}{hexes[15:18]}-{hexes[18:30]}"

    # -- entities: 13-char uppercase alphanumeric (PARTIAL) -----------------

    def merchant(self) -> str:
        return self._entity()

    def order(self) -> str:
        return self._entity()

    def line_item(self) -> str:
        return self._entity()

    def item(self) -> str:
        """An inventory item, e.g. ``NEWITEM123ABC`` in
        https://docs.clover.com/dev/docs/inventorycreateitem's response."""
        return self._entity()

    def order_type(self) -> str:
        return self._entity()

    # -- tokens and codes: UUID-format (JUDGMENT) ---------------------------

    def access_token(self) -> str:
        return self._uuid()

    def refresh_token(self) -> str:
        return self._uuid()

    def authorization_code(self) -> str:
        return self._uuid()

    def verification_code(self) -> str:
        """The code Clover POSTs to a callback URL during webhook verification:
        ``{"verificationCode": "<uuid>"}`` (https://docs.clover.com/dev/docs/webhooks)."""
        return self._uuid()

    def webhook_auth_code(self) -> str:
        """The static ``X-Clover-Auth`` header value, documented as a UUID sent
        "in every message header after the webhook callback URL is validated"
        (https://docs.clover.com/dev/docs/webhooks)."""
        return self._uuid()

    # -- internal ----------------------------------------------------------

    def internal(self, prefix: str) -> str:
        """``<prefix>_`` + 12 hex characters, for ids Clover does not shape."""
        return f"{prefix}_{self._pick(_HEX, 12)}"
