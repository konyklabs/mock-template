"""The seed scenario: what a clover unit's world looks like at start.

Minimal at this commit -- one merchant, so ``vendorfake serve --vendor
clover`` can complete the OAuth dance out of the box (authorize needs a
merchant to name in its redirect). The full scenario -- items, employees,
tenders, order types, tax rates, a seeded token -- lands in PR E of
konyklabs/roadmap#34 and extends :class:`SeedDocument` rather than replacing
it.
"""

from __future__ import annotations

from vendorfake.clover.seed.document import SeedDocument, SeedMerchant, parse_seed_document
from vendorfake.clover.seed.hydrate import SEED_META, hydrate_clover

__all__ = ["SEED_META", "SeedDocument", "SeedMerchant", "hydrate_clover", "parse_seed_document"]
