"""The transport claim, exercised against a real vendor rather than a stub.

One unit answers the same logical Square request over three bindings, and the
third one is the point: a request that arrives as a *file* has no status line,
no headers a framework supplied, no content type a middleware negotiated and no
body a framework parsed. If the core had grown an HTTP assumption anywhere --
in the router, in the body reader, in the error shaper, in the way a delivery
is signed -- the file-drop case is where it would show up as a failure, because
nothing here could have supplied any of it.

The socket half of the same claim lives in ``tests/integration``, which is
strictly out of process. This file stays in process on purpose: it is the cheap
version that runs on every save, and the expensive version proves the thing
this one cannot, which is that uvicorn's own parser is not in the way.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from tests.unit.square.harness import LEDGER, SURFACE, Harness, Silent
from vendorfake import create_unit
from vendorfake.asgi import FrameworkTripwire, create_app
from vendorfake.core.transport.filedrop import serve_file_drop
from vendorfake.fidelity.validate import ValidatingClient
from vendorfake.square.config import SQUARE_API_VERSION
from vendorfake.square.seed.constants import (
    SEED_ACCESS_TOKEN,
    SEED_LOCATION_ID,
    SEED_OPEN_ORDER_ID,
    TEA_MUG_VARIATION_ID,
)

#: Headers a binding is entitled to differ on. The request id is minted per
#: binding and can never match; content-length is Starlette's. Everything the
#: unit itself set has to survive both journeys, which is what makes the rest
#: of the comparison meaningful.
BINDING_HEADERS = frozenset({"x-unit-request-id", "content-length"})


@dataclass(frozen=True, slots=True)
class Bound(Harness):
    """A started unit, its in-process client, and the application in front of it.

    A subclass rather than a second object because every helper in this file
    wants ``h.auth`` and ``h.api``, and the only thing the transport tests add
    is the third binding.
    """

    app: Any = None


@pytest.fixture
def h() -> Iterator[Bound]:
    """A full-profile unit plus its ASGI application, sharing one tripwire.

    Built here rather than through the shared harness because this file needs
    the application object as well as the in-process client, and the tripwire
    has to exist before the unit so the control plane can close over it.
    """
    tripwire = FrameworkTripwire()
    unit = create_unit(
        vendor="square",
        profile="full",
        logger=Silent(),
        framework_answered=tripwire.get,
    )
    try:
        yield Bound(
            unit=unit,
            api=ValidatingClient(unit, SURFACE, LEDGER),
            app=create_app(unit, tripwire=tripwire, logger=Silent()),
        )
    finally:
        unit.stop()


def over_http(app: Any, method: str, url: str, **kwargs: Any) -> httpx.Response:
    """One request through the ASGI application, on its own event loop."""

    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://unit.test") as client:
            return await client.request(method, url, **kwargs)

    return anyio.run(run)


# ---------------------------------------------------------------------------
# HTTP and in process.
# ---------------------------------------------------------------------------


def test_the_same_order_comes_back_byte_identical_over_both_bindings(h: Bound) -> None:
    """Bytes, not a decoded object. A re-serialising adapter -- a
    ``JSONResponse(model)``, a compression middleware, a helpful header rewrite
    -- produces an equal *object* and different *bytes*, and the webhook
    signature is computed over bytes.
    """
    path = f"/v2/orders/{SEED_OPEN_ORDER_ID}"
    auth = {"authorization": f"Bearer {SEED_ACCESS_TOKEN}"}

    via_http = over_http(h.app, "GET", path, headers=auth)
    in_proc = h.api.get(path, headers=h.auth)

    assert via_http.status_code == 200
    assert in_proc.status == 200
    assert via_http.content == in_proc.body

    compared = {name: value for name, value in via_http.headers.items() if name not in BINDING_HEADERS}
    expected = {name: value for name, value in in_proc.headers.items() if name not in BINDING_HEADERS}
    assert compared == expected


def test_the_vendor_stamps_its_own_headers_on_the_http_path(h: Bound) -> None:
    """`decorate` runs at the kernel's `finish()`, so it cannot be something
    the adapter adds -- which is exactly why it is asserted here."""
    response = over_http(
        h.app,
        "GET",
        f"/v2/orders/{SEED_OPEN_ORDER_ID}",
        headers={"authorization": f"Bearer {SEED_ACCESS_TOKEN}"},
    )
    assert response.headers["square-version"] == SQUARE_API_VERSION
    assert response.headers["x-unit-vendor"] == "square"


def test_the_framework_answered_nothing(h: Bound) -> None:
    over_http(h.app, "GET", "/no/such/path")
    over_http(h.app, "TRACE", f"/v2/orders/{SEED_OPEN_ORDER_ID}")
    assert h.api.get("/__unit/health").json()["framework_answered"] == 0


# ---------------------------------------------------------------------------
# A request that arrives as a file.
# ---------------------------------------------------------------------------


def test_a_create_order_that_arrives_as_a_file_is_answered_and_priced(h: Bound, tmp_path: Path) -> None:
    """Two catalog line items at 150 each: the total is the catalog's, so the
    file-drop path went through the same handler, the same store and the same
    projection as the socket path would have."""
    drop = serve_file_drop(h.unit, tmp_path)
    (drop.in_dir / "create.request.json").write_text(
        json.dumps(
            {
                "method": "POST",
                "path": "/v2/orders",
                "headers": {"authorization": f"Bearer {SEED_ACCESS_TOKEN}"},
                "body": {
                    "idempotency_key": "filedrop-1",
                    "order": {
                        "location_id": SEED_LOCATION_ID,
                        "line_items": [{"catalog_object_id": TEA_MUG_VARIATION_ID, "quantity": "3"}],
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    results = drop.poll()
    assert [result.name for result in results] == ["create"]

    answer = json.loads((drop.out_dir / "create.response.json").read_text(encoding="utf-8"))
    assert answer["status"] == 200
    assert answer["body"]["order"]["total_money"] == {"amount": 450, "currency": "USD"}

    # The state that request created is visible over another binding, which is
    # the half that proves the file-drop call reached the real store.
    created_id = answer["body"]["order"]["id"]
    assert h.api.get(f"/v2/orders/{created_id}", headers=h.auth).status == 200


def test_the_file_drop_binding_carries_a_body_the_core_can_read(h: Bound, tmp_path: Path) -> None:
    """The echo route reports what the CORE parsed, over a binding with no
    sockets, so this proves the file-drop path delivers a body the kernel reads
    the same way it reads an HTTP one.

    WHAT THIS USED TO ASSERT, AND WHY IT WAS NOTHING. It was called
    "says which binding carried it" and its docstring claimed `/__unit/echo`
    reports the transport. It does not -- the handler returns `content_type`,
    `raw_len`, `fields`, `fields_multi` and an optional `json`, and no
    transport anywhere. Its only real assertion was
    `FILE_DROP_TRANSPORT == "filedrop"`, an imported constant compared to a
    literal identical to its own definition, which is true whether or not the
    request happened at all.

    Three separate reviews reported it on byte-identical code before this one,
    the third noting the first two went unaddressed. It is fixed here rather
    than deferred a fourth time, because a test named for a guarantee it does
    not check is worse than no test: it answers the question "is this covered?"
    with a yes.

    The transport claim is dropped rather than made true. Making the echo route
    report its binding would break C10, which compares that route's bytes
    across bindings and requires them identical -- so the honest fix is to
    assert what this binding really guarantees.
    """
    drop = serve_file_drop(h.unit, tmp_path)
    (drop.in_dir / "echo.request.json").write_text(
        json.dumps(
            {
                "method": "POST",
                "path": "/__unit/echo",
                "headers": {"content-type": "application/x-www-form-urlencoded"},
                "raw_body": "grant_type=authorization_code&scope=one&scope=two",
            }
        ),
        encoding="utf-8",
    )
    drop.poll()

    answer = json.loads((drop.out_dir / "echo.response.json").read_text(encoding="utf-8"))
    assert answer["status"] == 200, answer
    body = answer["body"]
    # The core parsed a form body that arrived as a FILE. Content-type
    # generality is not an HTTP property, and this is where that is provable.
    assert body["content_type"] == "application/x-www-form-urlencoded"
    assert body["fields"]["grant_type"] == "authorization_code"
    assert body["fields_multi"]["scope"] == ["one", "two"]
    assert body["raw_len"] == len("grant_type=authorization_code&scope=one&scope=two")


def test_a_shaped_square_error_survives_the_file_drop_binding(h: Bound, tmp_path: Path) -> None:
    """A 404 over a binding with no status line at all.

    The status is the vendor's, carried in the response document rather than in
    a header a framework wrote, which is the sharpest form of the claim that
    error shaping is not an HTTP concern.
    """
    drop = serve_file_drop(h.unit, tmp_path)
    (drop.in_dir / "missing.request.json").write_text(
        json.dumps(
            {
                "method": "GET",
                "path": "/v2/orders/CAISNOSUCHORDER",
                "headers": {"authorization": f"Bearer {SEED_ACCESS_TOKEN}"},
            }
        ),
        encoding="utf-8",
    )
    drop.poll()

    answer = json.loads((drop.out_dir / "missing.response.json").read_text(encoding="utf-8"))
    assert answer["status"] == 404
    assert answer["body"]["errors"][0]["code"] == "NOT_FOUND"
    assert answer["headers"]["square-version"] == SQUARE_API_VERSION
