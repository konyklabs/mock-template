"""The Catalog surface beyond the listing: retrieve, search, upsert.

Every assertion names its source, as in the rest of this suite; where Square
publishes no sentence the test says the behaviour is this unit's judgment.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.square.harness import Harness, first_error
from tests.unit.square.harness import harness as build_harness
from vendorfake.square.seed.constants import (
    COLD_BREW_ITEM_ID,
    TEA_ITEM_ID,
    TEA_MUG_VARIATION_ID,
    TEA_POT_VARIATION_ID,
)


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from build_harness("orders-only")


def retrieve(h: Harness, object_id: str, **query: str) -> Any:
    return h.api.call(method="GET", path=f"/v2/catalog/object/{object_id}", query=query, headers=h.auth)


def search(h: Harness, **body: Any) -> Any:
    return h.api.post("/v2/catalog/search", body, headers=h.auth)


def upsert(h: Harness, obj: dict[str, Any], key: str = "upsert-1") -> Any:
    return h.api.post("/v2/catalog/object", {"idempotency_key": key, "object": obj}, headers=h.auth)


def journal_seq(h: Harness) -> int:
    return int(h.api.get("/__unit/journal").json()["seq"])


# ---------------------------------------------------------------------------
# RetrieveCatalogObject
# ---------------------------------------------------------------------------


def test_retrieve_an_item_nests_its_variations(h: Harness) -> None:
    """The consumer's "resolve an item to its variations" call.
    https://developer.squareup.com/reference/square/catalog-api/retrieve-catalog-object"""
    response = retrieve(h, TEA_ITEM_ID)
    assert response.status == 200, response.text
    body = response.json()
    assert list(body) == ["object"]
    item = body["object"]
    assert item["type"] == "ITEM"
    assert item["id"] == TEA_ITEM_ID
    assert item["item_data"]["name"] == "Tea"
    assert [v["id"] for v in item["item_data"]["variations"]] == [TEA_MUG_VARIATION_ID, TEA_POT_VARIATION_ID]
    assert item["item_data"]["variations"][0]["item_variation_data"]["price_money"] == {
        "amount": 150,
        "currency": "USD",
    }


def test_retrieve_a_variation_with_its_parent_as_a_related_object(h: Harness) -> None:
    """ "include_related_objects: If `true`, the response will include
    additional objects that are related to the requested objects." A
    variation's parent ITEM is the only relation this unit has."""
    plain = retrieve(h, TEA_MUG_VARIATION_ID).json()
    assert "related_objects" not in plain
    assert plain["object"]["item_variation_data"]["item_id"] == TEA_ITEM_ID

    related = retrieve(h, TEA_MUG_VARIATION_ID, include_related_objects="true").json()
    assert [obj["id"] for obj in related["related_objects"]] == [TEA_ITEM_ID]


def test_retrieve_an_unknown_object_is_404(h: Harness) -> None:
    response = retrieve(h, "NOSUCHOBJECT")
    assert response.status == 404
    assert first_error(response)["code"] == "NOT_FOUND"
    assert first_error(response)["field"] == "object_id"


# ---------------------------------------------------------------------------
# SearchCatalogObjects
# ---------------------------------------------------------------------------


def test_search_defaults_to_items_and_reports_latest_time(h: Harness) -> None:
    """`object_types` unspecified returns the top-level types -- ITEM here --
    and `latest_time` is "When the associated product catalog was last
    updated": the newest `updated_at` across the whole catalog."""
    body = search(h).json()
    assert [obj["id"] for obj in body["objects"]] == sorted([COLD_BREW_ITEM_ID, TEA_ITEM_ID])
    assert body["latest_time"] == "2019-03-04T21:54:45.000Z"
    assert "cursor" not in body


def test_search_by_name_prefix_is_case_insensitive(h: Harness) -> None:
    """JUDGMENT, stated in the surface: `tea` finds `Tea`."""
    body = search(h, query={"prefix_query": {"attribute_name": "name", "attribute_prefix": "tea"}}).json()
    assert [obj["item_data"]["name"] for obj in body["objects"]] == ["Tea"]

    variations = search(
        h,
        object_types=["ITEM_VARIATION"],
        query={"prefix_query": {"attribute_name": "name", "attribute_prefix": "M"}},
    ).json()
    assert [obj["item_variation_data"]["name"] for obj in variations["objects"]] == ["Mug"]


def test_search_by_exact_name(h: Harness) -> None:
    body = search(h, query={"exact_query": {"attribute_name": "name", "attribute_value": "Cold Brew"}}).json()
    assert [obj["id"] for obj in body["objects"]] == [COLD_BREW_ITEM_ID]
    none = search(h, query={"exact_query": {"attribute_name": "name", "attribute_value": "Cold"}}).json()
    assert none["objects"] == []


def test_search_since_a_time_is_strictly_after_updated_at(h: Harness) -> None:
    """ "begin_time: Return only objects modified after this timestamp." The
    poll-for-changes call: send back the last `latest_time` and see nothing
    until something changes."""
    everything = search(h, begin_time="2016-01-01T00:00:00Z").json()
    assert [obj["id"] for obj in everything["objects"]] == sorted([COLD_BREW_ITEM_ID, TEA_ITEM_ID])

    newer = search(h, begin_time="2016-11-16T22:25:24.878Z").json()
    assert [obj["id"] for obj in newer["objects"]] == [COLD_BREW_ITEM_ID]

    nothing = search(h, begin_time=everything["latest_time"]).json()
    assert nothing["objects"] == []


def test_an_upsert_is_visible_to_a_since_search(h: Harness) -> None:
    """The whole point of `begin_time`: a change made after the last poll is
    what the next poll returns, and `latest_time` moves with it."""
    before = search(h).json()["latest_time"]
    created = upsert(h, {"type": "ITEM", "id": "#new", "item_data": {"name": "Scone"}}).json()["catalog_object"]
    after = search(h, begin_time=before).json()
    assert [obj["id"] for obj in after["objects"]] == [created["id"]]
    assert after["latest_time"] == created["updated_at"]


def test_search_refuses_a_query_kind_this_unit_does_not_answer(h: Harness) -> None:
    """Refused, naming the key, rather than ignored: an ignored query looks
    exactly like a query that matched everything."""
    response = search(h, query={"text_query": {"keywords": ["tea"]}})
    assert response.status == 400
    assert first_error(response)["field"] == "query.text_query"


def test_search_refuses_an_attribute_other_than_name(h: Harness) -> None:
    response = search(h, query={"prefix_query": {"attribute_name": "sku", "attribute_prefix": "x"}})
    assert response.status == 400
    assert first_error(response)["field"] == "query.prefix_query.attribute_name"


def test_search_pages_with_a_cursor_and_ignores_an_out_of_range_limit(h: Harness) -> None:
    """ "If the supplied limit is negative, zero, or is higher than the maximum
    limit of 1,000, it will be ignored." -- the one list on this vendor where
    a bad limit is documented as ignored rather than refused."""
    first = search(h, limit=1).json()
    assert len(first["objects"]) == 1
    assert first["cursor"]
    second = search(h, limit=1, cursor=first["cursor"]).json()
    assert len(second["objects"]) == 1
    assert "cursor" not in second
    assert first["objects"][0]["id"] != second["objects"][0]["id"]

    ignored = search(h, limit=0).json()
    assert len(ignored["objects"]) == 2


def test_search_related_objects_carries_a_variations_parent_once(h: Harness) -> None:
    body = search(h, object_types=["ITEM_VARIATION"], include_related_objects=True).json()
    assert len(body["objects"]) == 4
    assert [obj["id"] for obj in body["related_objects"]] == sorted([COLD_BREW_ITEM_ID, TEA_ITEM_ID])


# ---------------------------------------------------------------------------
# UpsertCatalogObject
# ---------------------------------------------------------------------------


def test_upsert_creates_an_item_with_variations_and_maps_temporary_ids(h: Harness) -> None:
    """ "To create a new object, use a temporary ID prefixed with `#`" and the
    response's `id_mappings` pairs each with the id minted for it.
    https://developer.squareup.com/reference/square/catalog-api/upsert-catalog-object"""
    seq = journal_seq(h)
    response = upsert(
        h,
        {
            "type": "ITEM",
            "id": "#Scone",
            "item_data": {
                "name": "Scone",
                "description": "Buttery",
                "variations": [
                    {
                        "type": "ITEM_VARIATION",
                        "id": "#Plain",
                        "item_variation_data": {"item_id": "#Scone", "name": "Plain", "price_money": {"amount": 300}},
                    }
                ],
            },
        },
    )
    assert response.status == 200, response.text
    body = response.json()
    item = body["catalog_object"]
    mappings = {row["client_object_id"]: row["object_id"] for row in body["id_mappings"]}
    assert set(mappings) == {"#Scone", "#Plain"}
    assert item["id"] == mappings["#Scone"]
    assert len(item["id"]) == 24
    assert item["is_deleted"] is False
    variation = item["item_data"]["variations"][0]
    assert variation["id"] == mappings["#Plain"]
    assert variation["item_variation_data"] == {
        "item_id": mappings["#Scone"],
        "name": "Plain",
        "pricing_type": "FIXED_PRICING",
        "price_money": {"amount": 300, "currency": "USD"},
    }
    # Both objects carry the same new catalog version, and both were journalled.
    assert variation["version"] == item["version"]
    assert journal_seq(h) == seq + 2
    # And the listing sees it.
    listed = h.api.get("/v2/catalog/list", headers=h.auth).json()["objects"]
    assert mappings["#Scone"] in {obj["id"] for obj in listed}


def test_upsert_updates_an_existing_object_under_its_catalog_version(h: Harness) -> None:
    """ "When updating an object, the version supplied must match the version
    in the database, otherwise the write will be rejected as conflicting."
    https://developer.squareup.com/reference/square/objects/CatalogObject"""
    current = retrieve(h, TEA_ITEM_ID).json()["object"]
    seq = journal_seq(h)

    stale = upsert(h, {"type": "ITEM", "id": TEA_ITEM_ID, "version": 1, "item_data": {"name": "Herbal Tea"}})
    assert stale.status == 400
    assert first_error(stale)["code"] == "VERSION_MISMATCH"
    # A rejected upsert journals nothing.
    assert journal_seq(h) == seq

    missing = upsert(h, {"type": "ITEM", "id": TEA_ITEM_ID, "item_data": {"name": "Herbal Tea"}}, key="upsert-2")
    assert missing.status == 400
    assert first_error(missing)["field"] == "object.version"

    updated = upsert(
        h,
        {"type": "ITEM", "id": TEA_ITEM_ID, "version": current["version"], "item_data": {"name": "Herbal Tea"}},
        key="upsert-3",
    )
    assert updated.status == 200, updated.text
    item = updated.json()["catalog_object"]
    assert item["item_data"]["name"] == "Herbal Tea"
    assert item["version"] > current["version"]
    # The variations survive: they are separate objects, untouched by this write.
    assert [v["id"] for v in item["item_data"]["variations"]] == [TEA_MUG_VARIATION_ID, TEA_POT_VARIATION_ID]
    assert journal_seq(h) == seq + 1


def test_upsert_refuses_a_caller_chosen_id_for_a_new_object(h: Harness) -> None:
    """Square mints catalog ids; an id that is neither temporary nor existing
    is refused rather than silently honoured."""
    response = upsert(h, {"type": "ITEM", "id": "MYOWNID", "item_data": {"name": "X"}})
    assert response.status == 400
    assert first_error(response)["field"] == "object.id"


def test_upsert_refuses_a_type_this_unit_does_not_model(h: Harness) -> None:
    response = upsert(h, {"type": "CATEGORY", "id": "#c", "category_data": {"name": "Drinks"}})
    assert response.status == 400
    assert first_error(response)["field"] == "object.type"


def test_a_conflicting_variation_leaves_the_whole_request_unwritten(h: Harness) -> None:
    """The invariant: every object is resolved and version-checked before the
    first write, so a bad third object leaves no half-written item behind."""
    seq = journal_seq(h)
    response = upsert(
        h,
        {
            "type": "ITEM",
            "id": "#Scone",
            "item_data": {
                "name": "Scone",
                "variations": [
                    {
                        "type": "ITEM_VARIATION",
                        "id": TEA_MUG_VARIATION_ID,
                        "version": 1,
                        "item_variation_data": {"name": "Mug", "price_money": {"amount": 1}},
                    }
                ],
            },
        },
    )
    assert response.status == 400
    assert first_error(response)["code"] == "VERSION_MISMATCH"
    assert journal_seq(h) == seq
    assert (
        search(h, query={"exact_query": {"attribute_name": "name", "attribute_value": "Scone"}}).json()["objects"] == []
    )


def test_upsert_requires_an_idempotency_key_and_replays_under_it(h: Harness) -> None:
    """`idempotency_key` is documented as required on UpsertCatalogObject; the
    kernel enforces it and replays the stored answer on a retry."""
    without = h.api.post("/v2/catalog/object", {"object": {"type": "ITEM", "id": "#x"}}, headers=h.auth)
    assert without.status == 400
    assert first_error(without)["field"] == "idempotency_key"

    first = upsert(h, {"type": "ITEM", "id": "#Scone", "item_data": {"name": "Scone"}}, key="upsert-replay")
    again = upsert(h, {"type": "ITEM", "id": "#Scone", "item_data": {"name": "Scone"}}, key="upsert-replay")
    assert first.status == again.status == 200
    assert first.json() == again.json()


def test_upsert_needs_items_write_which_the_read_only_token_lacks(h: Harness) -> None:
    """ITEMS_WRITE, per https://developer.squareup.com/docs/oauth-api/square-permissions."""
    response = h.api.post(
        "/v2/catalog/object",
        {"idempotency_key": "ro", "object": {"type": "ITEM", "id": "#x", "item_data": {"name": "X"}}},
        headers=h.read_auth,
    )
    assert response.status == 403
    assert first_error(response)["code"] == "INSUFFICIENT_SCOPES"
