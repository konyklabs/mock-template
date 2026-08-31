"""The stock API vocabulary: the inventory item document and the two request bodies.

DOCUMENTED (toast-stock-api.yaml v1.0.0, https://doc.toasttab.com/doc/devguide/apiUsingTheStockApi.html):
an inventory item is ``{"guid", "itemGuidValidity": "VALID", "status":
"IN_STOCK", "quantity": null, "multiLocationId", "versionId"}``;
``itemGuidValidity`` is ``VALID | INVALID`` (read-only); ``status`` is
``IN_STOCK | QUANTITY | OUT_OF_STOCK``; ``quantity`` is a nullable double,
present only with ``QUANTITY``; ``versionId`` is "for future use".

``POST /inventory/search`` takes ``{"guids": [], "multiLocationIds": [],
"versionIds": []}``; ``PUT /inventory/update`` takes an array of items
addressed by ``guid`` or ``multiLocationId`` carrying ``status`` and, for
``QUANTITY`` only, ``quantity`` > 0 -- "Do not include a quantity value for
... IN_STOCK or OUT_OF_STOCK".

JUDGMENT: a searched guid that names no item answers a row with
``itemGuidValidity: INVALID`` and null everything else -- the field exists
for exactly this, and refusing the whole search for one typo would hide the
other results.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["STATUSES", "StockSearchRequest", "StockUpdateRequest", "invalid_stock_row", "project_stock"]

STATUSES: tuple[str, ...] = ("IN_STOCK", "QUANTITY", "OUT_OF_STOCK")

_REQUEST = ConfigDict(extra="ignore", frozen=True)


class StockSearchRequest(BaseModel):
    model_config = _REQUEST

    guids: list[str] = Field(default_factory=list)
    multiLocationIds: list[str] = Field(default_factory=list)
    versionIds: list[str] = Field(default_factory=list)


class StockUpdateRequest(BaseModel):
    """One element of the ``PUT /inventory/update`` array."""

    model_config = _REQUEST

    guid: str | None = None
    multiLocationId: str | None = None
    status: Literal["IN_STOCK", "QUANTITY", "OUT_OF_STOCK"]
    quantity: float | None = None
    versionId: str | None = None


def project_stock(stored: Mapping[str, Any]) -> dict[str, Any]:
    """The documented document, key order from the page's example."""
    return {
        "guid": str(stored["id"]),
        "itemGuidValidity": "VALID",
        "status": stored.get("status"),
        "quantity": stored.get("quantity"),
        "multiLocationId": stored.get("multiLocationId"),
        "versionId": stored.get("versionId"),
    }


def invalid_stock_row(guid: str | None, multi_location_id: str | None) -> dict[str, Any]:
    return {
        "guid": guid,
        "itemGuidValidity": "INVALID",
        "status": None,
        "quantity": None,
        "multiLocationId": multi_location_id,
        "versionId": None,
    }
