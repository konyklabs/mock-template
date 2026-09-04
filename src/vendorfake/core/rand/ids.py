"""The chassis under every vendor's id stream: one seeded stream per unit, salted away from the
chaos stream via :func:`~vendorfake.core.rand.rng.salted_seed`. A vendor subclasses
:class:`IdStream` and writes only its shape methods; :meth:`reseed` restarts it at hydrate.
"""

from __future__ import annotations

from vendorfake.core.rand.rng import Rng, salted_seed

__all__ = ["HEX", "MIXED_ALNUM", "UPPER_ALNUM", "IdStream"]

UPPER_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
MIXED_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
HEX = "0123456789abcdef"


class IdStream:
    """One deterministic id stream for one unit; subclass and declare ``__slots__ = ()`` to stay dict-free."""

    __slots__ = ("_rng",)

    def __init__(self, seed: int = 1) -> None:
        self._rng = Rng(salted_seed(seed))

    def reseed(self, seed: int) -> None:
        self._rng = Rng(salted_seed(seed))

    @property
    def draw_count(self) -> int:
        return self._rng.draw_count

    def _pick(self, alphabet: str, length: int) -> str:
        return "".join(alphabet[self._rng.int(len(alphabet))] for _ in range(length))

    def internal(self, prefix: str) -> str:
        return f"{prefix}_{self._pick(HEX, 12)}"
