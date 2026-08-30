"""Square-shaped identifiers, minted deterministically.

FOR: producing ids that *look* like the ones in Square's own documentation
examples, from a seeded stream, so that a transcript of a scenario is the same
on every run and can be diffed between runs.

INVARIANT: **the id stream never consumes the unit's chaos stream.** Both are
seeded from the unit seed, but this one is salted through
:func:`~vendorfake.core.rand.rng.salted_seed`, so adding a probability rule to
a profile does not renumber every generated id -- and a scenario that mints
ten orders draws the same ten ids whether or not a fault fired.

Shapes, from the response examples on developer.squareup.com/reference/square:

===============  ============================================
order            ``CAISENgvlJ6jLWAzERDzjyHVybY``  (27 chars)
location         ``18YC4JDH91E1H``                (13 chars)
merchant         ``MLQW2MYBY81PZ``                (13 chars)
catalog object   ``W62UWFY35CWMYGVWK6TWJDNI``     (24 chars)
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

from vendorfake.core.rand.rng import Rng, salted_seed

__all__ = ["SquareIds"]

_UPPER_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_MIXED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
#: Square's tokens are base64url-shaped, so the alphabet carries ``-`` and ``_``.
_TOKEN_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_HEX = "0123456789abcdef"


class SquareIds:
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

    # -- entities ----------------------------------------------------------

    def order(self) -> str:
        """``CAIS`` + 23 mixed-case alphanumerics, 27 characters in all."""
        return f"CAIS{self._pick(_MIXED, 23)}"

    def line_item_uid(self) -> str:
        return self._pick(_MIXED, 22)

    def fulfillment_uid(self) -> str:
        """The same shape as a line-item uid: 22 mixed-case alphanumerics."""
        return self._pick(_MIXED, 22)

    def location(self) -> str:
        return self._pick(_UPPER_ALNUM, 13)

    def merchant(self) -> str:
        return self._pick(_UPPER_ALNUM, 13)

    def catalog_object(self) -> str:
        return self._pick(_UPPER_ALNUM, 24)

    def tender(self) -> str:
        return self._pick(_MIXED, 27)

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
        return f"wbhk_{self._pick(_HEX, 32)}"

    def signature_key(self) -> str:
        return self._pick(_TOKEN_CHARS, 22)

    # -- internal ----------------------------------------------------------

    def internal(self, prefix: str) -> str:
        """``<prefix>_`` + 12 hex characters, for ids Square does not shape."""
        return f"{prefix}_{self._pick(_HEX, 12)}"
