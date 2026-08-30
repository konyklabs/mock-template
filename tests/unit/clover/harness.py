"""One started Clover unit, driven in process, for the behaviour suites.

Deliberately thin, like the Square harness: it knows the app credentials the
``full`` profile sets and how to walk the authorize redirect, because every
OAuth test needs both.

Until PR E ships the seed scenario, the store starts empty, so the harness
inserts the one merchant OAuth needs -- marked ``{"seed": True}`` in its
journal meta the way a real seed write would be, so it never reads as request
traffic in a journal assertion.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from vendorfake import create_unit
from vendorfake.clover.entities import COL, MerchantEntity
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.transport.inprocess import InProcessClient, InProcessResponse, in_process

CLIENT_ID = "UNITCLOVERAPP"
CLIENT_SECRET = "unit-clover-app-secret"
CONFIGURED_REDIRECT_URI = "https://example.test/oauth/callback"
"""The three values ``profiles/full.json`` sets."""

MERCHANT_ID = "HRVSTRYE12345"
"""13 uppercase characters, matching the entity-id shape."""


class Silent:
    """A logger that says nothing, so a passing run prints no unit banner."""

    def debug(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def info(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def warn(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def error(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...


@dataclass(frozen=True, slots=True)
class Harness:
    """A started unit and the client that drives it."""

    unit: Unit
    api: InProcessClient

    def authorize(self, **query: str) -> InProcessResponse:
        """``GET /oauth/v2/authorize`` with ``client_id`` already filled in."""
        return self.api.call(method="GET", path="/oauth/v2/authorize", query={"client_id": CLIENT_ID, **query})

    def code(self, **query: str) -> str:
        """Walk the authorize redirect and return the authorization code."""
        response = self.authorize(**query)
        location = response.headers["location"]
        return parse_qs(urlsplit(location).query)["code"][0]

    def token(self, **fields: Any) -> InProcessResponse:
        """``POST /oauth/v2/token`` as JSON, ``client_id`` filled in."""
        return self.api.post("/oauth/v2/token", {"client_id": CLIENT_ID, **fields})

    def exchange(self) -> dict[str, Any]:
        """A whole high-trust flow: authorize, exchange, return the 4 fields."""
        response = self.token(client_secret=CLIENT_SECRET, code=self.code())
        assert response.status == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def refresh(self, **fields: Any) -> InProcessResponse:
        """``POST /oauth/v2/refresh`` as JSON, ``client_id`` filled in."""
        return self.api.post("/oauth/v2/refresh", {"client_id": CLIENT_ID, **fields})

    def journal_len(self) -> int:
        return len(self.api.get("/__unit/journal").json()["entries"])


def harness(profile: str = "full", **kwargs: Any) -> Iterator[Harness]:
    """Start a unit with the merchant inserted, yield it, stop it after."""
    unit = create_unit(vendor="clover", profile=profile, logger=Silent(), **kwargs)
    unit.context.store.collection(COL.merchants).insert(
        MerchantEntity(id=MERCHANT_ID, name="Harvest & Rye").to_entity(),
        {"operation_id": "TestSeed", "seed": True},
    )
    try:
        yield Harness(unit=unit, api=in_process(unit))
    finally:
        unit.stop()
