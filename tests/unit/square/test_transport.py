"""One unit answers the same Square request byte-identically over the
in-process and ASGI bindings. The socket half lives in ``tests/integration``."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import anyio
import httpx
import pytest

from tests.unit.square.harness import LEDGER, SURFACE, Harness, Silent
from vendorfake import create_unit
from vendorfake.asgi import create_app
from vendorfake.fidelity.validate import ValidatingClient
from vendorfake.square.config import SQUARE_API_VERSION
from vendorfake.square.seed.constants import (
    SEED_ACCESS_TOKEN,
    SEED_OPEN_ORDER_ID,
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
    """A full-profile unit plus its ASGI application."""
    unit = create_unit(
        vendor="square",
        profile="full",
        logger=Silent(),
    )
    try:
        yield Bound(
            unit=unit,
            api=ValidatingClient(unit, SURFACE, LEDGER),
            app=create_app(unit, logger=Silent()),
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


# ---------------------------------------------------------------------------
# A request that arrives as a file.
# ---------------------------------------------------------------------------
