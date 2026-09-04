"""The shipped scenario, and the constants that name it.

The point of the first test is that ``seed/constants.py`` and
``default.seed.json`` cannot drift: a hand-edit to either is a red test rather
than a fixture that quietly stops matching.
"""

from __future__ import annotations

import json

import pytest

from tests.unit.lightspeed.harness import Harness
from vendorfake.core.kernel.types import UnitError
from vendorfake.lightspeed.entities import COL, OBJECT_VERSION, RegisterEntity, TokenEntity
from vendorfake.lightspeed.seed import constants as c
from vendorfake.lightspeed.seed.document import parse_seed_document
from vendorfake.lightspeed.versioning import FIRST_VERSION


def _document() -> dict[str, object]:
    return dict(json.loads(c.DEFAULT_SEED_PATH.read_text(encoding="utf-8")))


def test_every_constant_names_something_the_document_contains() -> None:
    doc = parse_seed_document(_document())
    assert doc.retailer.id == c.SEED_RETAILER_ID
    assert doc.retailer.name == c.SEED_RETAILER_NAME
    assert doc.retailer.domain_prefix == c.SEED_DOMAIN_PREFIX
    assert [outlet.id for outlet in doc.outlets] == [c.SEED_OUTLET_MAIN_ID, c.SEED_OUTLET_SECOND_ID]
    assert [register.id for register in doc.registers] == [c.SEED_REGISTER_MAIN_ID, c.SEED_REGISTER_SECOND_ID]
    assert [payment_type.id for payment_type in doc.payment_types] == [
        c.SEED_PAYMENT_TYPE_CASH_ID,
        c.SEED_PAYMENT_TYPE_CARD_ID,
        c.SEED_PAYMENT_TYPE_INTERNAL_ID,
    ]
    assert [token.access_token for token in doc.tokens] == [c.SEED_ACCESS_TOKEN, c.SEED_READ_ONLY_ACCESS_TOKEN]
    assert [token.access_token for token in doc.personal_tokens] == [c.SEED_PERSONAL_ACCESS_TOKEN]
    assert [token.refresh_token for token in doc.refresh_tokens] == [c.SEED_REFRESH_TOKEN]
    assert [hook.id for hook in doc.webhooks] == [c.SEED_WEBHOOK_ID]
    assert doc.webhooks[0].type == c.SEED_WEBHOOK_TYPE
    assert doc.webhooks[0].url == c.SEED_WEBHOOK_URL


def test_the_scenario_loads_what_it_says_it_loads(h: Harness) -> None:
    stats = h.api.get("/__unit/state").json()["entities"]
    assert stats == {
        "customers": 1,
        "oauth_apps": 1,
        "outlets": 2,
        "payment_types": 3,
        "products": 2,
        "refresh_tokens": 1,
        "registers": 2,
        "retailer": 1,
        "sales": 3,
        "subscriptions": 1,
        "tokens": 3,
    }


def test_one_register_starts_open_and_one_closed(h: Harness) -> None:
    """So that a close (and the webhook it fires) and an open each need no
    setup."""
    registers = {
        row["id"]: RegisterEntity.from_entity(row) for row in h.unit.context.store.collection(COL.registers).all()
    }
    assert registers[c.SEED_REGISTER_MAIN_ID].is_open is True
    assert registers[c.SEED_REGISTER_SECOND_ID].is_open is False


def test_the_read_only_token_carries_no_write_scope(h: Harness) -> None:
    stored = h.unit.context.store.collection(COL.tokens).find(
        lambda entity: entity.get("access_token") == c.SEED_READ_ONLY_ACCESS_TOKEN
    )
    assert stored is not None
    scopes = set(TokenEntity.from_entity(stored).scopes)
    assert not scopes & {"register:open", "register:close", "webhooks"}


def test_versions_ascend_from_the_first_and_are_unique(h: Harness) -> None:
    """One retailer-global sequence across every resource type, so no two
    seeded entities share a number."""
    store = h.unit.context.store
    versions = [
        row[OBJECT_VERSION]
        for collection in (COL.retailer, COL.outlets, COL.registers, COL.payment_types)
        for row in store.collection(collection).all()
    ]
    assert versions == list(range(FIRST_VERSION, FIRST_VERSION + 8))


def test_two_units_hydrate_to_the_same_digest() -> None:
    """C06's claim, asserted here too, because a uuid4() or a time.time() in
    hydrate is the usual cause and this suite is where it would be introduced."""
    from tests.unit.lightspeed.harness import harness

    digests = []
    for _ in range(2):
        gen = harness()
        started = next(gen)
        digests.append(started.api.get("/__unit/state").json()["digest"])
        gen.close()
    assert digests[0] == digests[1]


def test_a_reset_reproduces_the_scenario(h: Harness) -> None:
    before = h.api.get("/__unit/state").json()["digest"]
    assert h.put(h.path(f"/registers/{c.SEED_REGISTER_MAIN_ID}/actions/close"), "{}").status == 200
    assert h.api.get("/__unit/state").json()["digest"] != before
    assert h.api.post("/__unit/state/reset", {}).status in (200, 204)
    assert h.api.get("/__unit/state").json()["digest"] == before


# -- the schema refuses what it should ---------------------------------------


def test_a_register_naming_an_absent_outlet_is_refused() -> None:
    document = _document()
    document["registers"] = [
        {"id": "r1", "name": "R", "outlet_id": "nope", "is_open": False},
    ]
    with pytest.raises(UnitError) as caught:
        parse_seed_document(document)
    assert "outlet 'nope' is absent" in str(caught.value)


def test_an_open_register_without_an_open_time_is_refused() -> None:
    document = _document()
    registers = document["registers"]
    assert isinstance(registers, list)
    registers[0] = {**registers[0], "register_open_time": None}
    with pytest.raises(UnitError) as caught:
        parse_seed_document(document)
    assert "register_open_time" in str(caught.value)


def test_a_webhook_on_an_undocumented_event_is_refused() -> None:
    document = _document()
    document["webhooks"] = [{"id": "w1", "type": "sale.deleted", "url": "https://x.example/h"}]
    with pytest.raises(UnitError) as caught:
        parse_seed_document(document)
    assert "WebhookType" in str(caught.value)


def test_a_token_claiming_a_scope_the_application_lacks_is_refused() -> None:
    document = _document()
    # A documented scope this application never carries -- consignments are
    # outside issue #94's scoped surface. See test_auth.py's twin.
    document["personal_tokens"] = [{"id": "t1", "access_token": "x", "scopes": ["consignments:read"]}]
    with pytest.raises(UnitError) as caught:
        parse_seed_document(document)
    assert "consignments:read" in str(caught.value)


def test_an_unknown_key_is_refused_by_name() -> None:
    document = _document()
    document["outlets_typo"] = []
    with pytest.raises(UnitError) as caught:
        parse_seed_document(document)
    assert "outlets_typo" in str(caught.value)
