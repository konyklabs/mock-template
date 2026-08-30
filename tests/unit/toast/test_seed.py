"""The shipped scenario: it parses, it is what the constants say, it loads
deterministically, and a wrong one is refused by name."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.toast.harness import Harness, harness
from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.toast.entities import COL, RestaurantEntity, TokenEntity
from vendorfake.toast.seed import constants as c
from vendorfake.toast.seed.document import parse_seed_document


@pytest.fixture
def document() -> dict[str, Any]:
    return dict(json.loads(c.DEFAULT_SEED_PATH.read_text(encoding="utf-8")))


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def test_the_shipped_scenario_parses_and_matches_the_constants(document: dict[str, Any]) -> None:
    doc = parse_seed_document(document)
    assert doc.restaurant.guid == c.SEED_RESTAURANT_GUID
    assert doc.restaurant.general.name == c.SEED_RESTAURANT_NAME
    assert doc.restaurant.general.managementGroupGuid == c.SEED_MANAGEMENT_GROUP_GUID
    assert doc.restaurant.general.timeZone == "America/New_York"
    assert doc.restaurant.general.closeoutHour == 4
    assert doc.restaurant.general.currencyCode == "USD"
    tokens = {t.access_token: t for t in doc.tokens}
    assert tokens[c.SEED_ACCESS_TOKEN].scopes is None  # the client's full set
    assert tuple(tokens[c.SEED_READ_ONLY_ACCESS_TOKEN].scopes or ()) == c.SEED_READ_ONLY_SCOPES


def test_a_misspelled_key_is_a_startup_failure_naming_it(document: dict[str, Any]) -> None:
    broken = copy.deepcopy(document)
    broken["tokns"] = broken.pop("tokens")
    with pytest.raises(UnitError) as caught:
        parse_seed_document(broken)
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "seed"
    assert "tokns" in str(caught.value)


def test_a_closeout_hour_outside_the_documented_range_is_refused(document: dict[str, Any]) -> None:
    broken = copy.deepcopy(document)
    broken["restaurant"]["general"]["closeoutHour"] = 13
    with pytest.raises(UnitError) as caught:
        parse_seed_document(broken)
    assert caught.value.info is not None and caught.value.info["path"] == "restaurant.general.closeoutHour"


def test_the_store_holds_the_restaurant_and_the_two_tokens(h: Harness) -> None:
    store = h.unit.context.store
    restaurant = RestaurantEntity.from_entity(store.collection(COL.restaurants).require(c.SEED_RESTAURANT_GUID))
    assert restaurant.name == c.SEED_RESTAURANT_NAME
    assert restaurant.time_zone == "America/New_York"
    assert restaurant.closeout_hour == 4
    assert restaurant.management_group_guid == c.SEED_MANAGEMENT_GROUP_GUID
    assert restaurant.wire()["guid"] == c.SEED_RESTAURANT_GUID
    assert list(restaurant.wire())[:2] == ["guid", "general"]
    assert store.collection(COL.tokens).size == 2


def test_seeded_writes_are_marked_as_seeded(h: Harness) -> None:
    entries = h.api.get("/__unit/journal").json()["entries"]
    assert entries and all(e["meta"].get("seed") is True for e in entries)
    assert {e["meta"]["operation_id"] for e in entries} == {"SeedScenario"}


def test_the_seeded_tokens_carry_the_documented_ttl_and_their_scopes(h: Harness) -> None:
    tokens = h.unit.context.store.collection(COL.tokens)
    full = TokenEntity.from_entity(tokens.find(lambda t: t.get("access_token") == c.SEED_ACCESS_TOKEN) or {})
    read_only = TokenEntity.from_entity(
        tokens.find(lambda t: t.get("access_token") == c.SEED_READ_ONLY_ACCESS_TOKEN) or {}
    )
    now = h.unit.context.clock.now()
    assert abs(full.expires_at_ms - (now + 19168 * 1000)) < 5000
    assert full.scopes == c.SEED_SCOPES
    assert full.partner_guid == c.SEED_PARTNER_GUID
    assert full.client_id == c.SEED_CLIENT_ID
    assert read_only.scopes == c.SEED_READ_ONLY_SCOPES


def test_two_units_seeded_alike_hash_alike_and_reset_rebuilds_the_same_world() -> None:
    digests = []
    for _ in range(2):
        for h in harness():
            digests.append(h.unit.context.store.entity_digest())
    assert digests[0] == digests[1]
    for h in harness():
        before = h.unit.context.store.entity_digest()
        h.unit.context.store.collection(COL.tokens).insert({"id": "extra", "access_token": "x"}, {"operation_id": "T"})
        assert h.unit.context.store.entity_digest() != before
        assert h.api.post("/__unit/state/reset").status == 200
        assert h.unit.context.store.entity_digest() == before
