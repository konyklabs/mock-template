"""The chassis under every vendor's id stream.

FOR: the part of "mint ids that look like this vendor's, deterministically"
that has nothing to do with the vendor -- one seeded stream per unit, salted
away from the chaos stream, restartable at hydrate, and reporting how far it
has advanced. A vendor subclasses :class:`IdStream` and writes only its shape
methods; the two that existed before this module (Square's and Clover's)
carried this chassis twice, line for line.

INVARIANT: **the id stream never consumes the unit's chaos stream.** Both are
seeded from the unit seed, but this one is salted through
:func:`~vendorfake.core.rand.rng.salted_seed`, so adding a probability rule to
a profile does not renumber every generated id -- and a scenario that mints
ten orders draws the same ten ids whether or not a fault fired.

:meth:`IdStream.reseed` is the piece the reference has no need for. Its vendor
object is built per unit by a factory; here a vendor definition may be handed
to a unit that starts, re-hydrates on ``POST /__unit/state/reset``, and must
then mint *the same ids again*. Re-seeding at hydrate is what makes that true,
and it is the vendor's ``hydrate`` that calls it -- the contract is stated
here and kept there, and each vendor's suite pins it against its own unit.

The alphabets are here because every vendor so far draws from a subset of
them; a vendor with a stranger alphabet (Square's base64url tokens) keeps
that one beside its shapes.
"""

from __future__ import annotations

from vendorfake.core.rand.rng import Rng, salted_seed

__all__ = ["HEX", "MIXED_ALNUM", "UPPER_ALNUM", "IdStream"]

UPPER_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
MIXED_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
HEX = "0123456789abcdef"


class IdStream:
    """One deterministic id stream for one unit. Subclass and add shapes.

    ``__slots__`` is declared so a subclass that also declares ``__slots__ =
    ()`` stays dict-free; a subclass that forgets simply gains a ``__dict__``,
    which costs memory and nothing else.
    """

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
        """``length`` characters drawn from ``alphabet``, one draw per character."""
        return "".join(alphabet[self._rng.int(len(alphabet))] for _ in range(length))

    def internal(self, prefix: str) -> str:
        """``<prefix>_`` + 12 hex characters, for ids the vendor does not shape."""
        return f"{prefix}_{self._pick(HEX, 12)}"
