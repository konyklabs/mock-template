"""The seeded random stream, for chaos-rule probabilities and vendor id generation; reported by
``/__unit/info`` so a run is replayable. Chaos *triggering* never consults it: ``probability`` is
evaluated last, after every deterministic condition has already passed.
"""

from __future__ import annotations

import builtins
import math
import random

__all__ = ["ID_SEED_SALT", "Rng", "salted_seed"]

#: XOR salt separating an id stream from the chaos stream.
ID_SEED_SALT = 0x51754152


def salted_seed(seed: int) -> int:
    return (seed ^ ID_SEED_SALT) & 0xFFFFFFFF


class Rng:
    """One seeded, resettable stream of draws; ``builtins.int`` avoids the shadow cast by ``int()`` below."""

    __slots__ = ("_draws", "_random", "seed")

    def __init__(self, seed: builtins.int) -> None:
        self.seed: builtins.int = seed & 0xFFFFFFFF
        self._random = random.Random(self.seed)
        self._draws = 0

    def next(self) -> builtins.float:
        self._draws += 1
        return self._random.random()

    def int(self, max_exclusive: builtins.int) -> builtins.int:
        return math.floor(self.next() * max_exclusive)

    def hex(self, n_bytes: builtins.int) -> builtins.str:
        return "".join(f"{self.int(256):02x}" for _ in range(n_bytes))

    def reset(self) -> None:
        self._random = random.Random(self.seed)
        self._draws = 0

    @property
    def draw_count(self) -> builtins.int:
        return self._draws
