"""The inventory wire vocabulary: what a batch change accepts, and the counts
and changes that go back out.

Shapes from
https://developer.squareup.com/reference/square/objects/InventoryChange,
https://developer.squareup.com/reference/square/objects/InventoryPhysicalCount,
https://developer.squareup.com/reference/square/objects/InventoryAdjustment and
https://developer.squareup.com/reference/square/objects/InventoryCount.

INVARIANT: **quantity is a decimal string, on the way in and on the way out.**
"The number of items affected ... as a decimal string. The number can support
up to 5 digits after the decimal point." A JSON number is refused (strict
validation), and a string that is not a decimal, or has more than five
fractional digits, is ``invalid_value`` naming the field. Stored quantities
are normalised -- ``"25"``, ``"1.5"``, never ``"1.50"`` or ``"25.0"`` -- so a
count reads back the same however it was written.

SHRINK (prototype): ``TRANSFER`` changes, ``measurement_unit``,
``total_price_money``, ``reference_id``, the employee / team-member fields and
every ``InventoryState`` other than ``IN_STOCK`` as a *tracked* count are not
modelled; see :mod:`vendorfake.square.surface.inventory` for what an
adjustment into or out of ``IN_STOCK`` does.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.core.util.json import compact
from vendorfake.square.entities import InventoryCountEntity

__all__ = [
    "CHANGE_TYPES",
    "INVENTORY_STATES",
    "IN_STOCK",
    "ITEM_VARIATION_TYPE",
    "QUANTITY_SCALE",
    "AdjustmentRequest",
    "BatchChangeInventoryRequest",
    "BatchRetrieveInventoryCountsRequest",
    "InventoryChangeRequest",
    "PhysicalCountRequest",
    "format_quantity",
    "parse_quantity",
    "project_inventory_count",
]

IN_STOCK = "IN_STOCK"
ITEM_VARIATION_TYPE = "ITEM_VARIATION"

CHANGE_TYPES: tuple[str, ...] = ("PHYSICAL_COUNT", "ADJUSTMENT", "TRANSFER")
"""``InventoryChangeType``.
https://developer.squareup.com/reference/square/enums/InventoryChangeType"""

INVENTORY_STATES: tuple[str, ...] = (
    "CUSTOM",
    "IN_STOCK",
    "SOLD",
    "RETURNED_BY_CUSTOMER",
    "RESERVED_FOR_SALE",
    "SOLD_ONLINE",
    "ORDERED_FROM_VENDOR",
    "RECEIVED_FROM_VENDOR",
    "IN_TRANSIT_TO",
    "NONE",
    "WASTE",
    "UNLINKED_RETURN",
    "COMPOSED",
    "DECOMPOSED",
    "SUPPORTED_BY_NEWER_VERSION",
    "IN_TRANSIT",
)
"""``InventoryState``, the documented vocabulary an adjustment may name.
https://developer.squareup.com/reference/square/enums/InventoryState"""

QUANTITY_SCALE = 5
""""up to 5 digits after the decimal point"."""

_REQUEST = ConfigDict(extra="ignore", frozen=True, strict=True)


def parse_quantity(raw: str, field: str) -> Decimal:
    """A decimal-string quantity, or ``invalid_value`` naming the field."""
    try:
        value = Decimal(raw.strip())
    except (InvalidOperation, ValueError) as exc:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} must be a decimal string, for example '53' or '1.5'.",
            field=field,
            info={"supplied": raw},
        ) from exc
    if not value.is_finite():
        raise UnitError(UnitErrorKind.INVALID_VALUE, detail=f"{field} must be finite.", field=field)
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > QUANTITY_SCALE:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} can carry at most {QUANTITY_SCALE} digits after the decimal point.",
            field=field,
            info={"supplied": raw},
        )
    return value


def format_quantity(value: Decimal) -> str:
    """The normalised string form: no trailing zeros, no exponent, ``0`` for
    zero however it was reached."""
    normalised = value.normalize()
    if normalised == 0:
        return "0"
    text = format(normalised, "f")
    return text


def project_inventory_count(entity: Mapping[str, Any]) -> dict[str, Any]:
    """A stored count as Square's ``InventoryCount``, in the documented order."""
    count = InventoryCountEntity.from_entity(entity)
    return compact(
        {
            "catalog_object_id": count.catalog_object_id,
            "catalog_object_type": count.catalog_object_type,
            "state": count.state,
            "location_id": count.location_id,
            "quantity": count.quantity,
            "calculated_at": count.calculated_at or _opt_str(entity.get("updated_at")),
        }
    )


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


# ---------------------------------------------------------------------------
# Requests.
# ---------------------------------------------------------------------------


class PhysicalCountRequest(BaseModel):
    """``physical_count``: "Represents the quantity of an item variation that
    is physically present at a specific location, verified by a seller or a
    seller's employee." ``state`` is the state counted; this unit tracks
    ``IN_STOCK``. ``occurred_at`` is "A client-generated RFC 3339-formatted
    timestamp that indicates when the physical count was examined".
    """

    model_config = _REQUEST

    catalog_object_id: str = Field(min_length=1)
    location_id: str = Field(min_length=1)
    quantity: str = Field(min_length=1)
    state: str | None = None
    reference_id: str | None = None
    occurred_at: str | None = None


class AdjustmentRequest(BaseModel):
    """``adjustment``: "Represents a change in state or quantity of product
    inventory at a particular time and location." A quantity moves
    ``from_state`` to ``to_state``.
    """

    model_config = _REQUEST

    catalog_object_id: str = Field(min_length=1)
    location_id: str = Field(min_length=1)
    quantity: str = Field(min_length=1)
    from_state: str = Field(min_length=1)
    to_state: str = Field(min_length=1)
    reference_id: str | None = None
    occurred_at: str | None = None


class InventoryChangeRequest(BaseModel):
    """One entry of ``changes``: a ``type`` and the block named for it."""

    model_config = _REQUEST

    type: str = Field(min_length=1)
    physical_count: PhysicalCountRequest | None = None
    adjustment: AdjustmentRequest | None = None


class BatchChangeInventoryRequest(BaseModel):
    """``POST /v2/inventory/changes/batch-create``.
    https://developer.squareup.com/reference/square/inventory-api/batch-change-inventory

    ``idempotency_key`` is required ("Min Length 1, Max Length 128") and read
    by the kernel. ``ignore_unchanged_counts``: "Indicates whether the current
    physical count should be ignored if the quantity is unchanged since the
    last physical count. Default: `true`."
    """

    model_config = _REQUEST

    idempotency_key: str | None = Field(default=None, max_length=128)
    changes: list[InventoryChangeRequest] = Field(default_factory=list)
    ignore_unchanged_counts: bool = True


class BatchRetrieveInventoryCountsRequest(BaseModel):
    """``POST /v2/inventory/counts/batch-retrieve``.
    https://developer.squareup.com/reference/square/inventory-api/batch-retrieve-inventory-counts

    Every filter optional: "catalog_object_ids: The filter to return results
    by CatalogObject ID. The filter is applicable only when set." Likewise
    ``location_ids`` and ``states``; ``updated_after`` "Return results whose
    `calculated_at` value is after the given time". ``limit``: "Min 1".
    """

    model_config = _REQUEST

    catalog_object_ids: list[str] | None = None
    location_ids: list[str] | None = None
    states: list[str] | None = None
    updated_after: str | None = None
    cursor: str | None = None
    limit: int | None = None
