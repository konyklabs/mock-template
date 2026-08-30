"""Turning a validated scenario into store state.

FOR: the one function ``CloverVendor.hydrate`` calls -- at start and again on
``POST /__unit/state/reset`` -- and therefore the one place that decides what
a unit's world looks like.

INVARIANT: **a seeded mutation is marked as one.** Every insert carries
``{"seed": True}`` in its journal meta, which is what stops PR D's dispatcher
pushing an event for a record that has existed since before the process
started, and what lets a journal assertion in a test tell scenario writes from
request traffic.
"""

from __future__ import annotations

from vendorfake.clover.entities import COL, MerchantEntity
from vendorfake.clover.seed.document import SeedDocument, parse_seed_document
from vendorfake.core.kernel.types import UnitContext

__all__ = ["SEED_META", "hydrate_clover"]

SEED_META = {"seed": True, "operation_id": "SeedScenario"}
"""Journal meta on every seeded write. See the module docstring."""


def hydrate_clover(ctx: UnitContext, seed: object) -> SeedDocument | None:
    """Load ``seed`` into ``ctx.store``; ``None`` (a profile with no seed)
    loads nothing and is legal. Returns the document that was loaded so a
    caller can ask what the unit was seeded with without re-reading the file."""
    if seed is None:
        return None
    doc = parse_seed_document(seed)
    merchant = doc.merchant
    ctx.store.collection(COL.merchants).insert(
        MerchantEntity(
            id=merchant.id,
            name=merchant.name,
            currency=merchant.currency,
            owner=None if merchant.owner is None else merchant.owner.model_dump(exclude_none=True),
            address=None if merchant.address is None else merchant.address.model_dump(exclude_none=True),
        ).to_entity(),
        SEED_META,
    )
    return doc
