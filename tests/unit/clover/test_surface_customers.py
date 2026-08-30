"""The customers surface: list, filter, create."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.unit.clover.harness import CUSTOMER_ADA, MERCHANT_ID, Harness, harness


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def test_customers_list_and_filter_by_name(h: Harness) -> None:
    body = h.get("/customers").json()
    ada = body["elements"][0]
    assert ada["id"] == CUSTOMER_ADA
    assert ada["firstName"] == "Ada" and ada["lastName"] == "Lovelace"
    assert ada["href"] == f"https://apisandbox.dev.clover.com/v3/merchants/{MERCHANT_ID}/customers/{CUSTOMER_ADA}"
    assert ada["addresses"][0]["city"] == "London"
    assert [e["id"] for e in h.get("/customers", query={"filter": "firstName=Ada"}).json()["elements"]] == [
        CUSTOMER_ADA
    ]
    assert h.get("/customers", query={"filter": "lastName=Lovelace"}).json()["elements"][0]["id"] == CUSTOMER_ADA
    assert h.get("/customers", query={"filter": "lastName=Byron"}).json() == {"elements": []}
    assert h.get("/customers", query={"filter": "email=x"}).status == 400


def test_create_customer_returns_the_record_with_a_minted_id(h: Harness) -> None:
    before = h.journal_len()
    response = h.post(
        "/customers",
        {
            "firstName": "Grace",
            "lastName": "Hopper",
            "addresses": [{"address1": "1 Navy Yard", "city": "Arlington", "state": "VA", "zip": "22202"}],
            "emailAddresses": [{"emailAddress": "grace@example.test"}],  # documented, unmodelled, tolerated
        },
    )
    assert response.status == 200
    customer = response.json()
    assert len(customer["id"]) == 13
    assert customer["firstName"] == "Grace"
    assert customer["addresses"] == [{"address1": "1 Navy Yard", "city": "Arlington", "state": "VA", "zip": "22202"}]
    assert customer["customerSince"] > 10**12
    assert "emailAddresses" not in customer
    assert h.journal_len() == before + 1
    listed = h.get("/customers", query={"filter": f"id={customer['id']}"}).json()["elements"]
    assert listed[0]["lastName"] == "Hopper"


def test_create_customer_needs_a_name_and_journals_nothing_otherwise(h: Harness) -> None:
    before = h.journal_len()
    response = h.post("/customers", {"addresses": []})
    assert response.status == 400
    assert response.json()["unit_error"]["field"] == "firstName"
    too_long = h.post("/customers", {"firstName": "x" * 65})
    assert too_long.status == 400
    assert h.journal_len() == before


def test_customer_routes_need_their_permissions(h: Harness) -> None:
    reader = h.restricted_token("CUSTOMERS_R")
    assert h.api.get(h.path("/customers"), headers=reader).status == 200
    denied = h.api.post(h.path("/customers"), {"firstName": "x"}, headers=reader)
    assert denied.status == 401
    assert denied.json()["message"] == "401 Unauthorized"
