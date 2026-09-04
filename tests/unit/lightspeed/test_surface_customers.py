"""Customers: the version-cursor list, and the three writes with their own
status codes.

201 on create, 204 on delete -- both different from the Products tag's, and
both what the specification declares.
"""

from __future__ import annotations

import json
from typing import Any

from tests.unit.lightspeed.harness import Harness
from vendorfake.lightspeed.seed import constants as c

CUSTOMERS = "/customers"

SEEDED_IDS = [c.SEED_CUSTOMER_ADA_ID, c.SEED_CUSTOMER_BLAKE_ID, c.SEED_CUSTOMER_NOOR_ID]

NEW = {"first_name": "Sam", "last_name": "Okafor"}


def _create(h: Harness, **body: Any) -> Any:
    return h.post(h.path(CUSTOMERS), json.dumps({**NEW, **body}))


def _ids(h: Harness, **query: str) -> list[str]:
    return [row["id"] for row in h.get(h.path(CUSTOMERS), query=query).json()["data"]]


# -- the list ----------------------------------------------------------------


def test_the_list_answers_the_documented_envelope(h: Harness) -> None:
    body = h.get(h.path(CUSTOMERS)).json()
    assert set(body) == {"data", "version"}
    assert [row["id"] for row in body["data"]] == SEEDED_IDS
    versions = [row["version"] for row in body["data"]]
    assert body["version"] == {"max": max(versions), "min": min(versions)}


def test_the_walk_over_the_list_repeats_no_row_and_loses_none(h: Harness) -> None:
    seen: list[str] = []
    after = 0
    for _ in range(len(SEEDED_IDS) + 2):
        body = h.get(h.path(CUSTOMERS), query={"after": str(after), "page_size": "1"}).json()
        if not body["data"]:
            break
        seen.extend(row["id"] for row in body["data"])
        after = body["version"]["max"]
    assert seen == SEEDED_IDS


def test_a_customer_carries_the_members_every_example_prints(h: Harness) -> None:
    ada = h.get(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_ADA_ID}")).json()["data"]
    assert ada["first_name"] == "Ada"
    assert ada["last_name"] == "Whitcombe"
    assert ada["customer_code"] == "Ada-N4ZJ"
    assert ada["customer_group_id"] == c.SEED_CUSTOMER_GROUP_ID
    assert ada["company_name"] == "Whitcombe Outfitters"
    assert ada["physical_city"] == "Auckland"


def test_the_name_is_derived_and_the_balances_are_numbers(h: Harness) -> None:
    """``CustomerBase`` has no ``name`` member for a caller to set, and the
    three money members are ``format: double``."""
    ada = h.get(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_ADA_ID}")).json()["data"]
    assert ada["name"] == "Ada Whitcombe"
    assert ada["balance"] == -42.5
    assert ada["loyalty_balance"] == 18.25
    assert ada["year_to_date"] == 612.4


def test_a_null_last_name_is_legal_and_is_emitted_as_null(h: Harness) -> None:
    """``Customer.last_name`` is required AND nullable, so the key is always
    there and the value may be null."""
    noor = h.get(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_NOOR_ID}")).json()["data"]
    assert noor["last_name"] is None
    assert noor["name"] == "Noor"


def test_an_absent_optional_member_is_an_absent_key(h: Harness) -> None:
    blake = h.get(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_BLAKE_ID}")).json()["data"]
    assert blake["company_name"] == "Rivera Trail Co"
    assert "email" not in blake
    assert "physical_city" not in blake


def test_one_customer_answers_the_single_record_wrapper(h: Harness) -> None:
    body = h.get(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_BLAKE_ID}")).json()
    assert set(body) == {"data"}


def test_an_unknown_customer_is_a_404_naming_the_parameter(h: Harness) -> None:
    answered = h.get(h.path(f"{CUSTOMERS}/nope"))
    assert answered.status == 404
    assert answered.json()["unit_error"]["field"] == "customer_id"


def test_reads_need_their_documented_scope(h: Harness) -> None:
    answered = h.get(h.path(CUSTOMERS), headers=h.restricted_token("products:read"))
    assert answered.status == 403
    assert answered.json()["unit_error"]["missing"] == ["customers:read"]


# -- create ------------------------------------------------------------------


def test_a_create_answers_the_documented_201_with_the_whole_record(h: Harness) -> None:
    answered = _create(h, email="sam@consumer.example")
    assert answered.status == 201
    created = answered.json()["data"]
    assert created["name"] == "Sam Okafor"
    assert created["email"] == "sam@consumer.example"
    assert h.get(h.path(f"{CUSTOMERS}/{created['id']}")).json()["data"] == created


def test_the_customer_code_is_generated_in_the_documented_shape(h: Harness) -> None:
    """The examples are ``Tony-N4ZJ`` and ``Tony-AB2W``: the first name, a
    hyphen, four upper-case alphanumerics."""
    from vendorfake.lightspeed.ids import CODE_ALPHABET

    created = _create(h).json()["data"]
    prefix, _, suffix = created["customer_code"].partition("-")
    assert prefix == "Sam"
    assert len(suffix) == 4
    assert set(suffix) <= set(CODE_ALPHABET), f"{suffix!r} leaves the code alphabet"


def test_a_supplied_customer_code_is_kept(h: Harness) -> None:
    created = _create(h, customer_code="TRADE-0001").json()["data"]
    assert created["customer_code"] == "TRADE-0001"


def test_a_new_customer_joins_the_default_group(h: Harness) -> None:
    created = _create(h).json()["data"]
    assert created["customer_group_id"] == c.SEED_CUSTOMER_GROUP_ID
    assert created["balance"] == 0
    assert created["loyalty_balance"] == 0
    assert created["year_to_date"] == 0


def test_a_group_that_does_not_exist_is_a_422_naming_the_field(h: Harness) -> None:
    answered = _create(h, customer_group_id="nope")
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "customer_group_id"


def test_a_create_without_last_name_is_a_422_naming_it(h: Harness) -> None:
    """Required and nullable: an omitted key is refused, an explicit null is
    not."""
    answered = h.post(h.path(CUSTOMERS), json.dumps({"first_name": "Q"}))
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "last_name"
    assert h.post(h.path(CUSTOMERS), json.dumps({"first_name": "Q", "last_name": None})).status == 201


def test_a_malformed_body_is_a_400(h: Harness) -> None:
    assert h.post(h.path(CUSTOMERS), "{not json").status == 400


def test_writes_need_their_documented_scope(h: Harness) -> None:
    answered = h.post(h.path(CUSTOMERS), "{}", headers=h.read_auth)
    assert answered.status == 403
    assert answered.json()["unit_error"]["missing"] == ["customers:write"]


# -- update ------------------------------------------------------------------


def test_an_update_replaces_rather_than_merges(h: Harness) -> None:
    """``PUT`` declares the SAME ``CustomerBase`` body the create does, with no
    partial-update variant anywhere in the document -- so a member the body
    omits is cleared."""
    before = h.get(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_ADA_ID}")).json()["data"]
    assert before["email"] == "ada@consumer.example"
    answered = h.put(
        h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_ADA_ID}"),
        json.dumps({"first_name": "Ada", "last_name": "Whitcombe-Ng"}),
    )
    assert answered.status == 200
    after = answered.json()["data"]
    assert after["name"] == "Ada Whitcombe-Ng"
    assert "email" not in after
    assert "company_name" not in after
    assert after["version"] > before["version"]


def test_an_update_keeps_the_identity_the_body_cannot_carry(h: Harness) -> None:
    """``customer_code`` and the group survive a body that names neither; the
    balances survive because ``CustomerBase`` has no member for them."""
    after = h.put(
        h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_ADA_ID}"),
        json.dumps({"first_name": "Ada", "last_name": "Whitcombe"}),
    ).json()["data"]
    assert after["customer_code"] == "Ada-N4ZJ"
    assert after["customer_group_id"] == c.SEED_CUSTOMER_GROUP_ID
    assert after["balance"] == -42.5


def test_updating_a_customer_that_does_not_exist_is_a_404(h: Harness) -> None:
    assert h.put(h.path(f"{CUSTOMERS}/nope"), json.dumps(NEW)).status == 404


def test_a_malformed_update_body_is_a_400_whichever_customer_it_named(h: Harness) -> None:
    assert h.put(h.path(f"{CUSTOMERS}/nope"), "{not json").status == 400


# -- delete ------------------------------------------------------------------


def test_a_delete_is_the_documented_204_with_no_body(h: Harness) -> None:
    answered = h.delete(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_BLAKE_ID}"))
    assert answered.status == 204
    assert answered.body == b""
    assert "content-type" not in answered.headers


def test_a_deleted_customer_leaves_the_list_and_keeps_its_id(h: Harness) -> None:
    assert h.delete(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_BLAKE_ID}")).status == 204
    assert c.SEED_CUSTOMER_BLAKE_ID not in _ids(h)
    assert sorted(_ids(h, deleted="true")) == sorted(SEEDED_IDS)
    gone = h.get(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_BLAKE_ID}")).json()["data"]
    assert gone["deleted_at"].endswith("Z")


def test_deleting_a_customer_that_does_not_exist_is_a_404(h: Harness) -> None:
    assert h.delete(h.path(f"{CUSTOMERS}/nope")).status == 404


# -- the events ---------------------------------------------------------------


def test_each_write_delivers_exactly_one_customer_update(h: Harness) -> None:
    """The webhooks page: the event covers "create/delete/modify"."""
    created = h.post(
        h.path("/webhooks"),
        json.dumps({"active": True, "type": "customer.update", "url": "https://consumer.example/hooks/customers"}),
    )
    assert created.status == 201
    new_id = _create(h).json()["data"]["id"]
    assert h.put(h.path(f"{CUSTOMERS}/{new_id}"), json.dumps({"first_name": "Sam", "last_name": "Lee"})).status == 200
    assert h.delete(h.path(f"{CUSTOMERS}/{new_id}")).status == 204
    assert len(h.deliveries()) == 3
    records = h.api.get("/__unit/webhooks/deliveries").json()["deliveries"]
    assert [row["event_type"] for row in records] == ["customer.update"] * 3


def test_the_delivered_payload_is_the_customers_own_wire_shape(h: Harness) -> None:
    from urllib.parse import parse_qsl

    from vendorfake.lightspeed.model.webhooks import PAYLOAD_FIELD
    from vendorfake.lightspeed.signer import verify_lightspeed_signature

    assert (
        h.post(
            h.path("/webhooks"),
            json.dumps({"active": True, "type": "customer.update", "url": "https://consumer.example/hooks/customers"}),
        ).status
        == 201
    )
    created = _create(h).json()["data"]
    delivered = h.deliveries()[0]
    assert verify_lightspeed_signature(c.SEED_CLIENT_SECRET, delivered.body, delivered.headers["X-Signature"])
    payload = json.loads(dict(parse_qsl(delivered.body.decode("utf-8")))[PAYLOAD_FIELD])
    assert payload == created


def test_a_seeded_customer_announces_nothing(h: Harness) -> None:
    assert h.deliveries() == []


# -- a deleted customer stays readable and stops being writable ---------------


def test_a_deleted_customer_is_still_readable(h: Harness) -> None:
    """The delete is SOFT, so the row keeps its id and ``?deleted=true`` still
    lists it. Nothing here changes; it is the premise of the two tests
    below."""
    assert h.delete(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_BLAKE_ID}")).status == 204
    read = h.get(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_BLAKE_ID}"))
    assert read.status == 200
    assert read.json()["data"]["deleted_at"]


def test_updating_a_deleted_customer_is_a_404(h: Harness) -> None:
    """JUDGMENT. It used to answer 200 and fire another ``customer.update``,
    leaving the caller's own state saying the customer exists and is current
    while every default list omitted it. A retry or a race on a cleanup path
    is exactly how a consumer meets that, and no response distinguished it
    from a successful update of a live customer."""
    assert h.delete(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_BLAKE_ID}")).status == 204
    answered = h.put(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_BLAKE_ID}"), json.dumps(NEW))
    assert answered.status == 404
    assert answered.json()["unit_error"]["field"] == "customer_id"
    # And the row is untouched: no second `deleted_at`, no new version.
    assert h.get(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_BLAKE_ID}")).json()["data"]["last_name"] != NEW["last_name"]


def test_deleting_a_deleted_customer_is_a_404(h: Harness) -> None:
    """A repeat delete is not a second 204: it would re-stamp ``deleted_at``
    and announce a change that did not happen."""
    assert h.delete(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_BLAKE_ID}")).status == 204
    stamped = h.get(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_BLAKE_ID}")).json()["data"]["deleted_at"]
    assert h.delete(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_BLAKE_ID}")).status == 404
    assert h.get(h.path(f"{CUSTOMERS}/{c.SEED_CUSTOMER_BLAKE_ID}")).json()["data"]["deleted_at"] == stamped
