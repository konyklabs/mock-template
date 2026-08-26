"""The seed scenario: fork-owned data, and the loader that turns it into state.

A scenario is a JSON document, not code. A fork author -- or a consumer writing
their own profile -- describes a merchant, its locations, its catalog, the
orders that already exist and the tokens already issued, and gets a unit that
starts in that world. Seeded mutations are journalled with ``seed: true`` so the
dispatcher does not push an ``order.created`` for an order that has existed
since before the process started.

Every seeded entity carries an explicit id, which is what makes two units
seeded from the same document hash identically.
"""

from __future__ import annotations

from vendorfake.square.seed.document import SeedDocument, parse_seed_document
from vendorfake.square.seed.hydrate import SEED_META, hydrate_square

__all__ = ["SEED_META", "SeedDocument", "hydrate_square", "parse_seed_document"]
