"""The seeded random stream.

FOR: the two places this project genuinely needs randomness -- a chaos rule
that asks for a probability, and vendor id generation -- with a seed that
lives in the profile and is reported by ``/__unit/info``, so a run is
replayable from its own report.

INVARIANT: chaos *triggering* never consults this. Triggering is counter-based
so "the third create fails" is a fact rather than a flake, and ``probability``
is evaluated last, after every deterministic condition has already passed --
otherwise the RNG stream would depend on traffic the rule was never going to
fire on.

Why this is not the reference's generator
-----------------------------------------
``packages/core/src/rand/rng.ts`` implements mulberry32: ``Math.imul``,
``>>> 0`` and 32-bit wraparound. Transliterating it would buy exactly one
thing, ids byte-identical to the TypeScript implementation, and nothing needs
that: every literal id assertion in the reference suite is a seed-document
constant or a prefix pattern (``^CAIS``, ``^sq0cgb-``, ``^wbhk_[0-9a-f]{32}$``).
What must survive is the contract -- one seeded stream, resettable, seed
reported, never consulted for deterministic triggering -- and it does. Ids are
deterministic *within* this implementation, and that is a declared decision
rather than an accident of porting.

Two details that are not free:

``random()`` only, never ``randrange``/``choice``
    CPython documents the Mersenne Twister's ``random()`` output as stable
    across releases; the distribution helpers built on it are not contractually
    stable the same way. Every draw here goes through ``next()``, and every
    integer is derived from it arithmetically, so a Python upgrade that changed
    a helper cannot silently renumber ids. The pinning test asserts a literal
    stream from a fixed seed, so if it ever does change it changes in a diff.

The ``0x51754152`` salt
    ``packages/square/src/ids.ts`` constructs its generator as
    ``new Rng((seed ^ 0x5175_4152) >>> 0)`` -- "Salted so id generation never
    consumes the chaos engine's RNG stream." Without it, adding a chaos rule
    that asks for a probability would change every generated id in the run.
    The ``>>> 0`` becomes an explicit ``& 0xFFFFFFFF``, because Python's ints
    do not wrap.
"""

from __future__ import annotations

import builtins
import math
import random

__all__ = ["ID_SEED_SALT", "Rng", "salted_seed"]

#: XOR salt separating an id stream from the chaos stream. Ported verbatim
#: from ``packages/square/src/ids.ts``.
ID_SEED_SALT = 0x51754152


def salted_seed(seed: int) -> int:
    """Derive an independent stream's seed from the unit's seed.

    ``& 0xFFFFFFFF`` reproduces JavaScript's ``>>> 0``: the reference's seed is
    a uint32 and Python's integers would otherwise carry sign and width the
    original never had.
    """
    return (seed ^ ID_SEED_SALT) & 0xFFFFFFFF


class Rng:
    """One seeded, resettable stream of draws.

    Annotations inside the class say ``builtins.int`` because the ``int()``
    method shadows the builtin in the class namespace, and a bare ``int`` in a
    later signature would then name the method rather than the type. The method
    keeps the reference's name; the annotations say which ``int`` they mean.
    """

    __slots__ = ("_draws", "_random", "seed")

    def __init__(self, seed: builtins.int) -> None:
        self.seed: builtins.int = seed & 0xFFFFFFFF
        self._random = random.Random(self.seed)
        self._draws = 0

    def next(self) -> builtins.float:
        """The next draw in [0, 1). Every other method is built on this one."""
        self._draws += 1
        return self._random.random()

    def int(self, max_exclusive: builtins.int) -> builtins.int:
        """A draw in [0, ``max_exclusive``), derived arithmetically from ``next()``."""
        return math.floor(self.next() * max_exclusive)

    def hex(self, n_bytes: builtins.int) -> builtins.str:
        """Deterministic lowercase hex string of ``n_bytes`` bytes."""
        return "".join(f"{self.int(256):02x}" for _ in range(n_bytes))

    def reset(self) -> None:
        """Rewind to the seed. A repeated scenario draws the same stream."""
        self._random = random.Random(self.seed)
        self._draws = 0

    @property
    def draw_count(self) -> builtins.int:
        """How many draws have been taken -- surfaced so a report can show that
        a deterministic run consumed no randomness at all."""
        return self._draws
