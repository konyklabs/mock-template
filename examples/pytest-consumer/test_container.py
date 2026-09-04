"""The same integration, against the container -- what CI runs when the service
under test lives in another language or another process.

Needs Docker and an image: ``VENDORFAKE_IMAGE=vendorfake:verify uv run
--extra container pytest test_container.py``. Skipped otherwise, so the
in-process suite stays runnable on a laptop without Docker.

Nothing below is hard-coded. Every token, header, merchant path and guid comes
out of the manifest -- ``GET /__unit/manifest`` here, or a JSON file named by
``VENDORFAKE_MANIFEST_<VENDOR>`` when the same tests run against a real vendor
sandbox, where a setup script writes that file from the sandbox account. That
is the whole point of the document: below the fixture, these are the requests
your service makes, against whichever world it was pointed at.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

testcontainers = pytest.importorskip("testcontainers.core.container")
DockerContainer = testcontainers.DockerContainer

IMAGE = os.environ.get("VENDORFAKE_IMAGE")
pytestmark = pytest.mark.skipif(not IMAGE, reason="set VENDORFAKE_IMAGE to the image tag to run the container tests")

MANIFEST_ENV_PREFIX = "VENDORFAKE_MANIFEST_"
"""``VENDORFAKE_MANIFEST_SQUARE=/path/to/square.json`` and friends. Set one and
the fixture reads the file instead of asking the control plane -- the deployed
world, where there is no control plane to ask."""


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


@pytest.fixture(scope="module")
def toast_http() -> Iterator[httpx.Client]:
    yield from _vendor_container("toast")


@pytest.fixture(scope="module")
def manifest(request: pytest.FixtureRequest) -> Callable[[str], dict[str, Any]]:
    """``manifest("square")`` -- the document describing whichever world this
    run is pointed at.

    ``VENDORFAKE_MANIFEST_<VENDOR>`` names a JSON file and wins; otherwise the
    vendor's container is asked. A file and a container are meant to describe
    the same deployment, so point the client at the world the file describes.
    Read once per vendor per module: the document does not change under a run.
    """
    cache: dict[str, dict[str, Any]] = {}

    def load(vendor: str) -> dict[str, Any]:
        if vendor not in cache:
            path = os.environ.get(f"{MANIFEST_ENV_PREFIX}{vendor.upper()}")
            if path:
                cache[vendor] = json.loads(Path(path).read_text(encoding="utf-8"))
            else:
                client: httpx.Client = request.getfixturevalue(f"{vendor}_http")
                answered = client.get("/__unit/manifest")
                assert answered.status_code == 200, answered.text
                cache[vendor] = answered.json()
        return cache[vendor]

    return load


def auth_headers(document: dict[str, Any]) -> dict[str, str]:
    """The manifest's most capable caller credential, as headers to send.

    Most scopes wins; a tie goes to the most complete instruction, which is how
    a vendor that names its tenant in a second header (Toast) is picked over
    the same token without it. An application secret is skipped: it
    authenticates the app to the vendor, not a call on a merchant's behalf.
    """
    offered = [row for row in document["credentials"] if "secret" not in row["mode"]]
    assert offered, f"the {document['vendor']} manifest offers no caller credential"
    offered.sort(key=lambda row: (len(row["scopes"]), len(row["headers"])))
    return dict(offered[-1]["headers"])


def first_id(document: dict[str, Any], collection: str) -> str:
    """One id out of the manifest, with a failure that names what was missing
    rather than an ``IndexError`` twenty lines into a request body."""
    ids = document["ids"].get(collection, [])
    assert ids, f"the {document['vendor']} manifest has no {collection!r} to address"
    return str(ids[0])


def test_square_in_a_container_creates_and_pays_an_order(
    square_http: httpx.Client, manifest: Callable[[str], dict[str, Any]]
) -> None:
    document = manifest("square")
    headers = auth_headers(document)
    created = square_http.post(
        "/v2/orders",
        headers=headers,
        json={
            "idempotency_key": "container-1",
            "order": {
                "location_id": first_id(document, "locations"),
                # An ad-hoc line item, priced in the body: the manifest carries
                # ids, not which catalogue object happens to be orderable.
                "line_items": [
                    {"name": "Soup", "quantity": "1", "base_price_money": {"amount": 955, "currency": "USD"}}
                ],
            },
        },
    )
    assert created.status_code == 200, created.text
    order = created.json()["order"]
    paid = square_http.post(
        f"/v2/orders/{order['id']}/pay",
        headers=headers,
        json={"idempotency_key": "container-1-pay", "order_version": order["version"]},
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["order"]["state"] == "COMPLETED"


def test_clover_in_a_container_pays_an_atomic_order(
    clover_http: httpx.Client, manifest: Callable[[str], dict[str, Any]]
) -> None:
    document = manifest("clover")
    headers = auth_headers(document)
    merchant = f"/v3/merchants/{first_id(document, 'merchants')}"
    created = clover_http.post(
        f"{merchant}/atomic_order/orders",
        headers=headers,
        json={
            "orderCart": {
                "orderType": {"id": first_id(document, "order_types")},
                "lineItems": [{"item": {"id": first_id(document, "items")}}],
            }
        },
    )
    assert created.status_code == 200, created.text
    order = created.json()
    paid = clover_http.post(
        f"{merchant}/orders/{order['id']}/payments",
        headers=headers,
        json={
            "tender": {"id": first_id(document, "tenders")},
            "employee": {"id": first_id(document, "employees")},
            "amount": order["total"],
        },
    )
    assert paid.status_code == 200, paid.text
    fetched = clover_http.get(f"{merchant}/orders/{order['id']}", headers=headers).json()
    assert (fetched["state"], fetched["paymentState"]) == ("locked", "PAID")


def test_toast_in_a_container_pays_a_check_in_dollars(
    toast_http: httpx.Client, manifest: Callable[[str], dict[str, Any]]
) -> None:
    document = manifest("toast")
    headers = auth_headers(document)
    created = toast_http.post(
        "/orders/v2/orders",
        headers=headers,
        json={
            "entityType": "Order",
            "diningOption": {"guid": first_id(document, "dining_options"), "entityType": "DiningOption"},
            "checks": [
                {
                    "entityType": "Check",
                    "selections": [
                        {
                            "item": {"guid": first_id(document, "menu_items"), "entityType": "MenuItem"},
                            "quantity": 1,
                        }
                    ],
                }
            ],
        },
    )
    assert created.status_code == 200, created.text
    order = created.json()
    check = order["checks"][0]
    # Dollars over the socket too: one menu item billed in cents would be a
    # three-figure integer here, not a sum a diner would recognise.
    assert 0 < check["totalAmount"] < 100, check["totalAmount"]
    paid = toast_http.post(
        f"/orders/v2/orders/{order['guid']}/checks/{check['guid']}/payments",
        headers=headers,
        json=[
            {
                "type": "OTHER",
                "amount": check["totalAmount"],
                "tipAmount": 0,
                "otherPayment": {"guid": first_id(document, "alternate_payment_types")},
            }
        ],
    )
    assert paid.status_code == 200, paid.text
    fetched = toast_http.get(f"/orders/v2/orders/{order['guid']}", headers=headers).json()
    # An OTHER payment covering the balance closes the check outright, as in
    # `test_toast.py`. This file asserted `PAID` until the manifest rewrite --
    # wrongly, and nothing caught it, because the container tests are skipped
    # unless Docker and an image are both present.
    assert fetched["checks"][0]["paymentStatus"] == "CLOSED"
