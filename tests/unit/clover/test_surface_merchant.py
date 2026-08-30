"""The merchant surface: the record and its four configuration lists."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.unit.clover.harness import (
    EMPLOYEE_BARISTA,
    EMPLOYEE_OWNER,
    MERCHANT_ID,
    ORDER_TYPE_DINE_IN,
    SERVICE_CHARGE_DEFAULT,
    TENDER_CASH,
    Harness,
    harness,
)
from vendorfake.clover.entities import COL


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def test_the_merchant_record_carries_id_name_owner_and_address(h: Harness) -> None:
    body = h.get("").json()
    assert body["id"] == MERCHANT_ID
    assert body["name"] == "Harvest & Rye"
    assert body["owner"]["id"] == EMPLOYEE_OWNER
    assert body["address"]["city"] == "Springfield"
    assert "currency" not in body  # internal to this unit, not a documented merchant field


def test_employees_list_the_documented_fields(h: Harness) -> None:
    body = h.get("/employees").json()
    assert [e["id"] for e in body["elements"]] == [EMPLOYEE_OWNER, EMPLOYEE_BARISTA]
    owner = body["elements"][0]
    assert owner["href"] == f"https://apisandbox.dev.clover.com/v3/merchants/{MERCHANT_ID}/employees/{EMPLOYEE_OWNER}"
    assert owner["name"] == "R. Harvest"
    assert owner["role"] == "ADMIN"
    assert "version" not in owner and "created_at" not in owner


def test_tenders_list_label_and_label_key(h: Harness) -> None:
    body = h.get("/tenders").json()
    cash = next(e for e in body["elements"] if e["id"] == TENDER_CASH)
    assert cash["label"] == "Cash"
    assert cash["labelKey"] == "com.clover.tender.cash"
    assert cash["opensCashDrawer"] is True
    assert cash["href"].endswith(f"/tenders/{TENDER_CASH}")


def test_order_types_list_label(h: Harness) -> None:
    body = h.get("/order_types").json()
    dine_in = next(e for e in body["elements"] if e["id"] == ORDER_TYPE_DINE_IN)
    assert dine_in["label"] == "Dine In"
    assert dine_in["taxable"] is True
    assert dine_in["isDefault"] is True  # documented on an order type, projected


def test_the_default_service_charge_and_its_documented_scale(h: Harness) -> None:
    """'Percent to charge times 10000, for example, 12.5% will be 125000'
    (merchantgetdefaultservicecharge)."""
    body = h.get("/default_service_charge").json()
    assert body == {"id": SERVICE_CHARGE_DEFAULT, "name": "Service", "percentageDecimal": 180000, "enabled": True}
    h.unit.context.store.collection(COL.service_charges).delete(SERVICE_CHARGE_DEFAULT)
    assert h.get("/default_service_charge").status == 404


def test_lists_page_with_limit_and_offset(h: Harness) -> None:
    first = h.get("/employees", query={"limit": "1"}).json()["elements"]
    second = h.get("/employees", query={"limit": "1", "offset": "1"}).json()["elements"]
    assert [e["id"] for e in first] == [EMPLOYEE_OWNER]
    assert [e["id"] for e in second] == [EMPLOYEE_BARISTA]


def test_merchant_routes_need_their_permissions(h: Harness) -> None:
    orders_only = h.restricted_token("ORDERS_R")
    for suffix in ("", "/employees", "/tenders", "/order_types", "/default_service_charge"):
        response = h.api.get(h.path(suffix), headers=orders_only)
        assert response.status == 401, suffix
        assert response.json()["message"] == "401 Unauthorized"
