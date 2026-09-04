"""Inventory on the wire: the record, the level, and the adjustment log.

DOCUMENTED (``api-2026-07``): two read models cover the same rows.
``Inventory`` is the stored record, one per product per outlet;
``InventoryLevel`` is a denormalised report with ``location_id`` in place of
``id``/``outlet_id`` and ``reorder_threshold`` for ``Inventory``'s
``reorder_point``. JUDGMENT: those two names are the same number, and
``total_cost`` is ``average_cost x current_inventory_level``, since the spec
never states either. All four reads answer a bare JSON array, not the
envelope the stock-adjustment list uses.

The adjustment's documented sign rule (negative reasons need ``quantity`` <
0, positive need > 0, ``CUSTOM`` matches its reason's ``type``) is
:func:`check_reason_sign` as code.
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
"""DOCUMENTED: ``CreateStockAdjustmentsRequest``'s ``maxItems``/``minItems``."""

_REQUEST = ConfigDict(extra="ignore", frozen=True)


class InventoryRequest(BaseModel):
    """``POST /inventory``'s body. Every member is optional; an empty body
    lists every record. ``size`` (not ``page_size``) is in the body."""

    model_config = _REQUEST

    after: int | None = None
    before: int | None = None
    include_deleted: bool = False
    product_id: str | None = None
    size: int | None = Field(default=None, ge=1)
    sort_direction: str | None = None
    variants: bool = False


class InventoryLevelsRequest(BaseModel):
    """``POST /inventory_levels``'s body. The remaining members are accepted
    but not modelled, per ``capabilities.py``."""

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
    """One element of ``POST /stock_adjustments``; ``quantity`` is a string here."""

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
    """The documented ``Inventory`` record, alphabetical order. Nullable
    reorder members emit ``null`` when unset; ``deleted_at`` is omitted for a
    live row."""
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
    ``brand_id``/``product_type_id``/``supplier_id`` are omitted when
    unresolvable. Unlike ``Inventory``, the reorder-amount members are NOT
    nullable, so a record with no reorder rule reports ``0``.
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
        # `Inventory`'s `reorder_point`, renamed.
        "reorder_threshold": wire_number(record.reorder_point or "0"),
        # A variant's root is its parent; else itself.
        "root_product_id": typed.variant_parent_id or typed.id,
        # allow_negative: an oversold sale or shrinkage adjustment can leave this negative.
        "total_cost": wire_number(decimal_text(average * level, field="total_cost", allow_negative=True)),
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
