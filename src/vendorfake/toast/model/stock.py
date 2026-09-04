"""The stock API vocabulary: the inventory item document and the two request bodies.

DOCUMENTED (toast-stock-api.yaml v1.0.0, https://doc.toasttab.com/doc/devguide/apiUsingTheStockApi.html):
an inventory item is ``{guid, itemGuidValidity, status, quantity,
multiLocationId, versionId}``; ``status`` is ``IN_STOCK | QUANTITY |
OUT_OF_STOCK``, with ``quantity`` present only for ``QUANTITY``.

DOCUMENTED: a searched guid naming no item answers a row with
``itemGuidValidity: INVALID``, ``status: OUT_OF_STOCK`` and the string
``"null"`` for identifiers it lacks, rather than refusing the whole search.
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
    """DOCUMENTED (apiUsingTheStockApi.html): an unknown identifier answers
    ``status: OUT_OF_STOCK`` and the STRING ``"null"`` -- never a JSON null --
    for whichever identifiers the row lacks (konyklabs/roadmap#56)."""
    return {
        "guid": guid if guid is not None else "null",
        "itemGuidValidity": "INVALID",
        "status": "OUT_OF_STOCK",
        "quantity": None,
        "multiLocationId": multi_location_id if multi_location_id is not None else "null",
        "versionId": guid if guid is not None else "null",
    }
