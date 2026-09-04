"""The seed scenario (a JSON document: merchant, catalog, orders, tokens) and the loader that turns it into unit
state. Seeded mutations are journalled ``seed: true`` so replay doesn't re-dispatch webhooks for pre-existing state."""

from __future__ import annotations

from vendorfake.square.seed.document import SeedDocument, parse_seed_document
from vendorfake.square.seed.hydrate import SEED_META, hydrate_square

__all__ = ["SEED_META", "SeedDocument", "hydrate_square", "parse_seed_document"]
