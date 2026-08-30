"""The same integration, against the container -- what CI runs when the service
under test lives in another language or another process.

Needs Docker and an image: ``VENDORFAKE_IMAGE=vendorfake:verify uv run
--extra container pytest test_container.py``. Skipped otherwise, so the
in-process suite stays runnable on a laptop without Docker.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator

import httpx
import pytest

testcontainers = pytest.importorskip("testcontainers.core.container")
DockerContainer = testcontainers.DockerContainer

IMAGE = os.environ.get("VENDORFAKE_IMAGE")
pytestmark = pytest.mark.skipif(not IMAGE, reason="set VENDORFAKE_IMAGE to the image tag to run the container tests")


def _vendor_container(vendor: str, profile: str = "full") -> Iterator[httpx.Client]:
    container = (
        DockerContainer(IMAGE)
        .with_env("VENDORFAKE_VENDOR", vendor)
        .with_env("VENDORFAKE_PROFILE", profile)
        .with_exposed_ports(8080)
    )
    with container:
        base_url = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8080)}"
        with httpx.Client(base_url=base_url, timeout=10.0) as client:
            deadline = time.monotonic() + 60
            while True:
                try:
                    if client.get("/__unit/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert time.monotonic() < deadline, f"{IMAGE} did not become healthy in 60s"
                time.sleep(0.25)
            yield client


@pytest.fixture(scope="module")
def square_http() -> Iterator[httpx.Client]:
    yield from _vendor_container("square")


@pytest.fixture(scope="module")
def clover_http() -> Iterator[httpx.Client]:
    yield from _vendor_container("clover")


SQUARE_AUTH = {"Authorization": "Bearer EAAAl-unit-seeded-access-token-full-scopes"}
CLOVER_AUTH = {"Authorization": "Bearer unit-seeded-clover-access-token-full-permissions"}
CLOVER_MERCHANT = "/v3/merchants/HRVSTRYE12345"


def test_square_in_a_container_creates_and_pays_an_order(square_http: httpx.Client) -> None:
    assert square_http.get("/__unit/health").json()["vendor"] == "square"
    created = square_http.post(
        "/v2/orders",
        headers=SQUARE_AUTH,
        json={
            "idempotency_key": "container-1",
            "order": {
                "location_id": "18YC4JDH91E1H",
                "line_items": [{"catalog_object_id": "2TZFAOHWGG7PAK2QEXWYPZSP", "quantity": "1"}],
            },
        },
    )
    assert created.status_code == 200, created.text
    order = created.json()["order"]
    paid = square_http.post(
        f"/v2/orders/{order['id']}/pay",
        headers=SQUARE_AUTH,
        json={"idempotency_key": "container-1-pay", "order_version": order["version"]},
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["order"]["state"] == "COMPLETED"


def test_clover_in_a_container_pays_an_atomic_order(clover_http: httpx.Client) -> None:
    assert clover_http.get("/__unit/health").json()["vendor"] == "clover"
    created = clover_http.post(
        f"{CLOVER_MERCHANT}/atomic_order/orders",
        headers=CLOVER_AUTH,
        json={"orderCart": {"orderType": {"id": "KFRPRVCZ73JHM"}, "lineItems": [{"item": {"id": "CRAFTBEER0750"}}]}},
    )
    assert created.status_code == 200, created.text
    order = created.json()
    paid = clover_http.post(
        f"{CLOVER_MERCHANT}/orders/{order['id']}/payments",
        headers=CLOVER_AUTH,
        json={"tender": {"id": "TENDEREXTRN01"}, "employee": {"id": "EMPLBARISTA01"}, "amount": order["total"]},
    )
    assert paid.status_code == 200, paid.text
    fetched = clover_http.get(f"{CLOVER_MERCHANT}/orders/{order['id']}", headers=CLOVER_AUTH).json()
    assert (fetched["state"], fetched["paymentState"]) == ("locked", "PAID")
