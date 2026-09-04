"""Inventory on the wire: the record, the level, and the adjustment log.

THE INVENTORY TAG HAS TWO READ MODELS, and a consumer who does not notice will
write the wrong client. Both are documented, both are in this slice:

``Inventory`` (``POST /inventory``, ``GET /inventory/{product_id}``)
    The stored record -- ``id``, ``product_id``, ``outlet_id``,
    ``current_inventory_level`` and the four reorder members. One row per
    product per outlet. This is the thing a stock adjustment moves.

``InventoryLevel`` (``POST /inventory_levels``, ``GET /inventory_levels/{id}``)
    A denormalised REPORT over the same rows: no ``id`` and no ``outlet_id``,
    but a ``location_id``, the product's ``name``, ``brand_id``,
    ``product_type_id`` and ``supplier_id``, a ``root_product_id``, a
    ``total_cost``, and ``reorder_threshold`` where ``Inventory`` says
    ``reorder_point``. JUDGMENT, stated because the specification never
    connects the two: ``reorder_threshold`` and ``reorder_point`` are the same
    number under two names, and ``total_cost`` is
    ``average_cost x current_inventory_level``, which is the only reading that
    makes the example's ``average_cost: 10, current_inventory_level: 4,
    total_cost: 40`` consistent.

**Neither read answers the ``{"data": ..., "version": ...}`` envelope.** All
four declare a bare JSON ARRAY as their 200 body
(``{"items": {"$ref": ".../Inventory"}, "type": "array"}``), and their examples
print one. The stock-adjustment list is the other way round -- it declares
``StockAdjustmentCollection``, which IS the envelope. Both are reproduced as
declared; the envelope is not applied uniformly just because most of this API
uses it.

**Two of the four reads are POSTs.** ``ListInventoryRecords`` and
``ListInventoryLevels`` are ``POST`` operations whose query travels in the
request body. They mutate nothing, so they fire no event.

THE ADJUSTMENT'S SIGN RULES ARE DOCUMENTED, verbatim on
``StockAdjustmentReason``: "Negative reasons (require ``quantity`` < 0):
``DAMAGE``, ``EXPIRY``, ``INTERNAL_USE``, ``THEFT``, ``DONATION``. Positive
reasons (require ``quantity`` > 0): ``STOCK_FOUND``, ``SAMPLE_FOR_SALE``. For
``CUSTOM``, the sign must match the referenced custom reason's ``type``."
:func:`check_reason_sign` is that paragraph as code.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.core.util.json import compact
from vendorfake.lightspeed.entities import InventoryEntity, ProductEntity, StockAdjustmentEntity
from vendorfake.lightspeed.model.scalars import decimal_text, wire_instant, wire_number

__all__ = [
    "ADJUSTMENT_REASON_TYPES",
    "CUSTOM_REASON",
    "MAX_ADJUSTMENTS_PER_BATCH",
    "NEGATIVE_REASONS",
    "POSITIVE_REASONS",
    "REORDER_METHODS",
    "STOCK_ADJUSTMENT_REASONS",
    "CreateStockAdjustmentItem",
    "CreateStockAdjustmentsRequest",
    "InventoryLevelsRequest",
    "InventoryRequest",
    "check_reason_sign",
    "project_inventory",
    "project_inventory_level",
    "project_stock_adjustment",
]

NEGATIVE_REASONS: tuple[str, ...] = ("DAMAGE", "EXPIRY", "INTERNAL_USE", "THEFT", "DONATION")
POSITIVE_REASONS: tuple[str, ...] = ("STOCK_FOUND", "SAMPLE_FOR_SALE")
CUSTOM_REASON = "CUSTOM"
STOCK_ADJUSTMENT_REASONS: tuple[str, ...] = (*NEGATIVE_REASONS, *POSITIVE_REASONS, CUSTOM_REASON)
"""``StockAdjustmentReason``'s enum, in the document's own order."""

ADJUSTMENT_REASON_TYPES: tuple[str, ...] = ("POSITIVE", "NEGATIVE")
"""``CustomInventoryAdjustmentReason.type``'s enum."""

REORDER_METHODS: tuple[str, ...] = ("FIXED", "MIN_MAX")
"""``Inventory.reorder_method``'s two non-null values."""

MAX_ADJUSTMENTS_PER_BATCH = 1000
"""DOCUMENTED: "A batch of 1-1000 stock adjustments to create"
(``CreateStockAdjustmentsRequest.description``), and ``maxItems: 1000`` /
``minItems: 1`` on the array."""

_REQUEST = ConfigDict(extra="ignore", frozen=True)


class InventoryRequest(BaseModel):
    """``POST /inventory``'s body. Every member is optional; an empty body
    lists every inventory record the retailer has.

    ``size`` is this operation's page size -- it is NOT called ``page_size``
    here, and it is in the body rather than the query. ``sort_direction`` is
    ``asc``/``desc`` over the version, which is the only order the rows have;
    the schema declares the enum and no default, so anything that is not
    ``desc`` sorts ascending here rather than being refused (JUDGMENT: this is
    an ordering preference, not a field a caller can get wrong in a way that
    changes which rows come back).
    """

    model_config = _REQUEST

    after: int | None = None
    before: int | None = None
    include_deleted: bool = False
    product_id: str | None = None
    size: int | None = Field(default=None, ge=1)
    sort_direction: str | None = None
    variants: bool = False


class InventoryLevelsRequest(BaseModel):
    """``POST /inventory_levels``'s body.

    ``location_ids``, ``product_ids`` and ``root_product_ids`` filter;
    ``offset``/``size`` page. The remaining members
    (``group_variants``, ``include_composites``, ``supplier_ids``,
    ``sort_type``, ``to_be_procured_only``) are accepted and recorded as not
    modelled in ``capabilities.py`` -- this unit has no composites, no supplier
    entity and no per-column sort to apply them to.
    """

    model_config = _REQUEST

    group_variants: bool = False
    include_composites: bool = False
    include_inactive: bool = False
    location_ids: list[str] = Field(default_factory=list)
    offset: int | None = Field(default=None, ge=0)
    product_ids: list[str] = Field(default_factory=list)
    root_product_ids: list[str] = Field(default_factory=list)
    size: int | None = Field(default=None, ge=1)
    sort_direction: str | None = None
    sort_type: str | None = None
    supplier_ids: list[str] = Field(default_factory=list)
    to_be_procured_only: bool = False


class CreateStockAdjustmentItem(BaseModel):
    """One element of ``POST /stock_adjustments``. Four required members, and
    ``quantity`` is a **string** on this schema."""

    model_config = _REQUEST

    outlet_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    quantity: str | float | int
    reason: str = Field(min_length=1)
    custom_inventory_adjustment_reason_id: str | None = None


class CreateStockAdjustmentsRequest(BaseModel):
    """``CreateStockAdjustmentsRequest``: one required array of 1-1000 items."""

    model_config = _REQUEST

    stock_adjustments: list[CreateStockAdjustmentItem] = Field(min_length=1)


def check_reason_sign(
    *,
    reason: str,
    quantity: Decimal,
    field: str,
    custom_reason_type: str | None = None,
) -> None:
    """The documented sign rule for ``reason``. See the module docstring."""
    if reason == CUSTOM_REASON:
        expected = custom_reason_type
    elif reason in NEGATIVE_REASONS:
        expected = "NEGATIVE"
    else:
        expected = "POSITIVE"
    if expected == "NEGATIVE" and quantity >= 0:
        raise _wrong_sign(field, reason, "negative")
    if expected == "POSITIVE" and quantity <= 0:
        raise _wrong_sign(field, reason, "positive")


def _wrong_sign(field: str, reason: str, wanted: str) -> UnitError:
    return UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f"{field} must be {wanted} for the reason {reason}.",
        field=field,
        info={"reason": reason},
    )


def project_inventory(entity: Mapping[str, Any]) -> dict[str, Any]:
    """The documented ``Inventory`` record, members in alphabetical order.

    ``average_cost``, ``reorder_amount``, ``reorder_method``, ``reorder_point``
    and ``reorder_target`` are all ``nullable`` on the schema and the vendor's
    own example prints ``"reorder_amount": null`` -- so an unset one is an
    explicit null, not an absent key. ``deleted_at`` is the exception: the
    example omits it entirely for a live row.
    """
    record = InventoryEntity.from_entity(entity)
    projected: dict[str, Any] = {
        "average_cost": wire_number(record.average_cost),
        "current_inventory_level": wire_number(record.current_inventory_level),
        "id": record.id,
        "outlet_id": record.outlet_id,
        "product_id": record.product_id,
        "quantity_to_procure": wire_number(record.quantity_to_procure),
        "reorder_amount": wire_number(record.reorder_amount),
        "reorder_method": record.reorder_method,
        "reorder_point": wire_number(record.reorder_point),
        "reorder_target": wire_number(record.reorder_target),
        "version": record.object_version,
    }
    if record.deleted_at is not None:
        projected["deleted_at"] = record.deleted_at
    return dict(sorted(projected.items()))


def project_inventory_level(entity: Mapping[str, Any], product: Mapping[str, Any]) -> dict[str, Any]:
    """The documented ``InventoryLevel`` report row for one stored record.

    Every member the schema declares is emitted; the ones this unit cannot
    resolve (``brand_id``, ``product_type_id``, ``supplier_id`` -- the Brands,
    Product Types and Suppliers tags are deferred) are omitted rather than
    guessed at, because absence is absence.

    ``reorder_amount``, ``reorder_target`` and ``reorder_threshold`` are typed
    ``number`` here and NOT nullable, where their ``Inventory`` counterparts
    are -- so a record with no reorder rule reports them as ``0`` on this
    report and as ``null`` on the record. That is the vendor's own pair of
    schemas, not a choice made here; ``reorder_method`` is nullable on both and
    stays null.
    """
    record = InventoryEntity.from_entity(entity)
    typed = ProductEntity.from_entity(product)
    document = dict(typed.document)
    average = Decimal(record.average_cost or "0")
    level = Decimal(record.current_inventory_level or "0")
    projected: dict[str, Any] = {
        "average_cost": wire_number(record.average_cost or "0"),
        "current_inventory_level": wire_number(record.current_inventory_level),
        "location_id": record.outlet_id,
        "name": typed.name,
        "product_id": typed.id,
        "quantity_to_procure": wire_number(record.quantity_to_procure),
        "reorder_amount": wire_number(record.reorder_amount or "0"),
        "reorder_method": record.reorder_method,
        "reorder_target": wire_number(record.reorder_target or "0"),
        # The same number `Inventory` calls `reorder_point`. See the module
        # docstring.
        "reorder_threshold": wire_number(record.reorder_point or "0"),
        # A variant reports its parent as the root; a product with no parent is
        # its own root.
        "root_product_id": typed.variant_parent_id or typed.id,
        "total_cost": wire_number(decimal_text(average * level, field="total_cost")),
    }
    for key in ("brand_id", "product_type_id", "supplier_id"):
        value = document.get(key)
        if value is not None:
            projected[key] = value
    return dict(sorted(projected.items()))


def project_stock_adjustment(entity: Mapping[str, Any]) -> dict[str, Any]:
    """The documented ``StockAdjustment``. ``quantity`` stays a string."""
    row = StockAdjustmentEntity.from_entity(entity)
    return dict(
        sorted(
            compact(
                {
                    "created_at": wire_instant(_opt_text(entity.get("created_at"))),
                    "custom_inventory_adjustment_reason_id": row.custom_inventory_adjustment_reason_id,
                    "id": row.id,
                    "outlet_id": row.outlet_id,
                    "product_id": row.product_id,
                    "quantity": row.quantity,
                    "reason": row.reason,
                    "updated_at": wire_instant(_opt_text(entity.get("updated_at"))),
                    "user_id": row.user_id,
                    "version": row.object_version,
                }
            ).items()
        )
    )


def _opt_text(value: Any) -> str | None:
    return None if value is None else str(value)
