"""Turning a validated scenario into store state.

FOR: the one function ``ToastVendor.hydrate`` calls -- at start and again on
``POST /__unit/state/reset``.

INVARIANT: **a seeded mutation is marked as one.** Every insert carries
``{"seed": True}`` in its journal meta, which is what stops the webhook
dispatcher pushing an event for a record that existed before the process
started.

SECOND INVARIANT: **seeded ids come from the document, never from the id
stream.** The only hydrate-time values are the token expirations and creation
instants, both volatile fields the digest ignores.
"""

from __future__ import annotations

from vendorfake.core.kernel.types import UnitContext
from vendorfake.toast.config import ToastConfig
from vendorfake.toast.entities import COL, RestaurantEntity, TokenEntity
from vendorfake.toast.seed.document import SeedDocument, parse_seed_document

__all__ = ["SEED_META", "hydrate_toast"]

SEED_META = {"seed": True, "operation_id": "SeedScenario"}


def hydrate_toast(ctx: UnitContext, seed: object, config: ToastConfig) -> SeedDocument | None:
    """Load ``seed`` into ``ctx.store``; ``None`` loads nothing and is legal."""
    if seed is None:
        return None
    doc = parse_seed_document(seed)
    _insert_restaurant(ctx, doc)
    _insert_tokens(ctx, doc, config)
    return doc


def _insert_restaurant(ctx: UnitContext, doc: SeedDocument) -> None:
    restaurant = doc.restaurant
    ctx.store.collection(COL.restaurants).insert(
        RestaurantEntity(
            id=restaurant.guid,
            general=restaurant.general.model_dump(exclude_none=True),
            location=dict(restaurant.location),
            urls=dict(restaurant.urls),
            schedules=dict(restaurant.schedules),
            delivery=dict(restaurant.delivery),
            onlineOrdering=dict(restaurant.onlineOrdering),
            prepTimes=dict(restaurant.prepTimes),
        ).to_entity(),
        SEED_META,
    )


def _insert_tokens(ctx: UnitContext, doc: SeedDocument, config: ToastConfig) -> None:
    """Expirations come from the configured TTL at hydrate time."""
    tokens = ctx.store.collection(COL.tokens)
    now = int(ctx.clock.now())
    for token in doc.tokens:
        tokens.insert(
            TokenEntity(
                id=token.id,
                access_token=token.access_token,
                client_id=token.client_id or config.client_id,
                partner_guid=config.partner_guid,
                expires_at_ms=now + config.access_token_ttl_ms,
                scopes=tuple(config.scopes if token.scopes is None else token.scopes),
                createdDate=now,
            ).to_entity(),
            SEED_META,
        )
