"""The seed scenario and its loader.

Two things are asserted that the reference could not assert at all, because it
cast its parsed JSON straight to an interface: that a misspelled key is a
startup failure naming the key, and that the constants module and the shipped
document have not drifted apart.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.square.harness import Harness
from tests.unit.square.harness import harness as build_harness
from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.square.entities import COL, CatalogObjectEntity, OrderEntity, TokenEntity
from vendorfake.square.seed import constants as seed_constants
from vendorfake.square.seed.document import parse_seed_document
from vendorfake.square.seed.hydrate import SEED_META


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from build_harness("full")


@pytest.fixture
def document() -> dict[str, Any]:
    return json.loads(seed_constants.DEFAULT_SEED_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The document, and the constants that name its contents
# ---------------------------------------------------------------------------


def test_the_shipped_scenario_parses(document: dict[str, Any]) -> None:
    doc = parse_seed_document(document)
    assert doc.merchant.id == seed_constants.SEED_MERCHANT_ID
    assert doc.comment, "the provenance comment travels with the document"


def test_every_constant_names_something_the_document_contains(document: dict[str, Any]) -> None:
    """The drift check. A constant that stopped matching would otherwise be a
    fixture that silently asserts nothing."""
    doc = parse_seed_document(document)
    assert {location.id for location in doc.locations} == {
        seed_constants.SEED_LOCATION_ID,
        seed_constants.SEED_KIOSK_LOCATION_ID,
    }
    assert doc.catalog is not None
    catalog_ids = {item.id for item in doc.catalog.items} | {
        variation.id for item in doc.catalog.items for variation in item.variations
    }
    assert catalog_ids == {
        seed_constants.TEA_ITEM_ID,
        seed_constants.TEA_MUG_VARIATION_ID,
        seed_constants.TEA_POT_VARIATION_ID,
        seed_constants.COLD_BREW_ITEM_ID,
        seed_constants.COLD_BREW_SMALL_VARIATION_ID,
        seed_constants.COLD_BREW_LARGE_VARIATION_ID,
    }
    assert {order.id for order in doc.orders} == {
        seed_constants.SEED_OPEN_ORDER_ID,
        seed_constants.SEED_COMPLETED_ORDER_ID,
    }
    assert {token.access_token for token in doc.tokens} == {
        seed_constants.SEED_ACCESS_TOKEN,
        seed_constants.SEED_READ_ONLY_ACCESS_TOKEN,
    }
    assert {token.refresh_token for token in doc.tokens} == {
        seed_constants.SEED_REFRESH_TOKEN,
        seed_constants.SEED_READ_ONLY_REFRESH_TOKEN,
    }
    by_access = {token.access_token: token.scopes for token in doc.tokens}
    assert by_access[seed_constants.SEED_ACCESS_TOKEN] == seed_constants.SEED_SCOPES
    assert by_access[seed_constants.SEED_READ_ONLY_ACCESS_TOKEN] == seed_constants.SEED_READ_ONLY_SCOPES


def test_a_misspelled_key_is_a_startup_failure_naming_it(document: dict[str, Any]) -> None:
    """The reference casts `seed as SeedDocument`, so `locatoins` produces a
    unit with no locations and the first symptom is an order that cannot be
    created."""
    document["locatoins"] = document.pop("locations")
    with pytest.raises(UnitError) as caught:
        parse_seed_document(document)
    assert caught.value.kind is UnitErrorKind.INTERNAL
    assert "locatoins" in str(caught.value)


def test_a_missing_scenario_is_refused() -> None:
    with pytest.raises(UnitError) as caught:
        parse_seed_document(None)
    assert "No seed scenario" in str(caught.value)


# ---------------------------------------------------------------------------
# What hydration builds
# ---------------------------------------------------------------------------


def test_the_store_holds_what_the_document_describes(h: Harness) -> None:
    stats = h.api.get("/__unit/state").json()
    # Empty collections are not reported: the store counts what it holds, and
    # `authorization_codes` exists only once a code has been issued.
    assert stats["entities"] == {
        "merchants": 1,
        "locations": 2,
        "catalog_objects": 6,
        "orders": 2,
        "tokens": 2,
    }


def test_a_seeded_line_item_inherits_its_price_from_the_catalog(h: Harness) -> None:
    """A seeded line names a variation and nothing else; price, variation name
    and item name all come from the catalog, which is what a real CreateOrder
    does too."""
    order = OrderEntity.from_entity(
        h.unit.context.store.collection(COL.orders).require(seed_constants.SEED_OPEN_ORDER_ID)
    )
    tea_mug = next(item for item in order.line_items if item.uid == "seed_li_tea_mug")
    assert tea_mug.base_price_money.amount == 150
    assert tea_mug.variation_name == "Mug"
    assert tea_mug.name == "Tea"
    assert order.currency == "USD"
    assert order.merchant_id == seed_constants.SEED_MERCHANT_ID


def test_a_seeded_order_keeps_its_stated_timestamps_and_version(h: Harness) -> None:
    """ "an order was created last Tuesday at version 4" has to be expressible,
    or SearchOrders date filtering cannot be reproducible."""
    stored = h.unit.context.store.collection(COL.orders).require(seed_constants.SEED_COMPLETED_ORDER_ID)
    assert stored["created_at"] == "2026-07-15T08:00:00.000Z"
    assert stored["updated_at"] == "2026-07-15T08:05:00.000Z"
    assert stored["version"] == 3


def test_seeded_writes_are_marked_as_seeded(h: Harness) -> None:
    """Without the mark, subscribing to a fresh unit would deliver a backlog of
    events for history and a consumer counting webhooks would be counting the
    scenario."""
    entries = h.api.get("/__unit/journal").json()["entries"]
    assert entries, "hydration journals"
    assert all(entry["meta"] == SEED_META for entry in entries)


def test_a_seeded_token_expires_on_the_configured_ttl(h: Harness) -> None:
    """`hydrate` resolves the profile's `vendor` block before loading, so the
    TTL a profile sets is the one seeded tokens are stamped with."""
    for scoped in build_harness("oauth-only", env={"VENDORFAKE_VENDOR_ACCESS_TOKEN_TTL_MS": "3600000"}):
        token = TokenEntity.from_entity(scoped.unit.context.store.collection(COL.tokens).require("tok_seed_full"))
        issued = scoped.unit.context.clock.iso_seconds(3_600_000)
        assert token.expires_at == issued


def test_absent_optionals_are_absent_not_null(h: Harness) -> None:
    """The rule the whole entity layer exists to keep. A location with no phone
    number has no `phone_number` key, and a catalog ITEM has no `price_money`."""
    kiosk = h.unit.context.store.collection(COL.locations).require(seed_constants.SEED_KIOSK_LOCATION_ID)
    assert "phone_number" not in kiosk
    item = h.unit.context.store.collection(COL.catalog).require(seed_constants.TEA_ITEM_ID)
    assert "price_money" not in item
    assert CatalogObjectEntity.from_entity(item).object_type == "ITEM"


def test_two_units_seeded_alike_hash_alike() -> None:
    """Every seeded entity carries an explicit id, which is what makes this
    true -- and what a conformance check will later assert across a restart."""
    digests = []
    for h in build_harness("oauth-only"):
        digests.append(h.unit.context.store.entity_digest())
    for h in build_harness("oauth-only"):
        digests.append(h.unit.context.store.entity_digest())
    assert digests[0] == digests[1]


def test_an_order_naming_an_unknown_location_is_refused(document: dict[str, Any]) -> None:
    """A scenario that loaded anyway would produce an order whose totals are
    silently zero."""
    from vendorfake.square.config import SquareConfig
    from vendorfake.square.seed.hydrate import hydrate_square

    document["orders"][0]["location_id"] = "NOSUCHLOCATION"
    for h in build_harness("oauth-only"):
        h.unit.context.store.reset()
        with pytest.raises(UnitError) as caught:
            hydrate_square(h.unit.context, document, SquareConfig())
        assert "unknown location" in str(caught.value)


def test_a_line_item_naming_an_unknown_variation_is_refused(document: dict[str, Any]) -> None:
    from vendorfake.square.config import SquareConfig
    from vendorfake.square.seed.hydrate import hydrate_square

    document["orders"][0]["line_items"][0]["catalog_object_id"] = "NOSUCHVARIATION"
    for h in build_harness("oauth-only"):
        h.unit.context.store.reset()
        with pytest.raises(UnitError) as caught:
            hydrate_square(h.unit.context, document, SquareConfig())
        assert "unknown catalog variation" in str(caught.value)


def test_a_line_item_pointing_at_an_item_rather_than_a_variation_is_refused(
    document: dict[str, Any],
) -> None:
    """One collection holds both, as Square does, so "it exists" is not enough."""
    from vendorfake.square.config import SquareConfig
    from vendorfake.square.seed.hydrate import hydrate_square

    document["orders"][0]["line_items"][0]["catalog_object_id"] = seed_constants.TEA_ITEM_ID
    for h in build_harness("oauth-only"):
        h.unit.context.store.reset()
        with pytest.raises(UnitError):
            hydrate_square(h.unit.context, document, SquareConfig())


def test_a_line_item_with_no_catalog_object_needs_its_own_price(document: dict[str, Any]) -> None:
    from vendorfake.square.config import SquareConfig
    from vendorfake.square.seed.hydrate import hydrate_square

    del document["orders"][0]["line_items"][0]["catalog_object_id"]
    for h in build_harness("oauth-only"):
        h.unit.context.store.reset()
        with pytest.raises(UnitError) as caught:
            hydrate_square(h.unit.context, document, SquareConfig())
        assert "has no price" in str(caught.value)


def test_resetting_a_unit_rebuilds_the_same_world(h: Harness) -> None:
    """`POST /__unit/state/reset` re-hydrates, which is why the id stream is
    re-seeded there rather than merely continued."""
    before = h.unit.context.store.entity_digest()
    h.api.post("/__unit/state/reset", {})
    assert h.unit.context.store.entity_digest() == before
