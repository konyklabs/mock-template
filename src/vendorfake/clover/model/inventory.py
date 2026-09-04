"""The inventory item wire vocabulary: what a Clover inventory item document
carries, so the surface and its tests share one vocabulary.

The field set and defaults are DOCUMENTED by the create-item response example
on https://docs.clover.com/dev/docs/inventorycreateitem (PARTIAL: an echo,
not a stated rule), except ``isRevenue`` -- JUDGMENT False, disagreeing with
the example's ``true``, since "counts as revenue" should not be silently
claimed for an unclassified item. Create requires ``name`` and ``price``;
``stockCount`` (deprecated) is not modelled here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.util.json import compact

__all__ = ["ITEM_EXPANDABLE", "ItemCreateRequest", "ItemPatchRequest", "ItemWire", "PriceType", "project_item"]

_REQUEST = ConfigDict(extra="ignore", frozen=True)
"""Tolerates documented-but-unmodelled fields (``isAgeRestricted``, ...)
rather than 400ing; see ``model/order.py``'s ``_REQUEST``."""


class PriceType(StrEnum):
    FIXED = "FIXED"
    VARIABLE = "VARIABLE"
    PER_UNIT = "PER_UNIT"


class ItemWire(BaseModel):
    model_config = _REQUEST

    id: str
    name: str
    #: Integer cents. Required on create (inventorycreateitem).
    price: int
    hidden: bool = False
    available: bool = True
    priceType: PriceType = PriceType.FIXED
    defaultTaxRates: bool = True
    #: JUDGMENT default; see the module docstring.
    isRevenue: bool = False
    sku: str | None = None
    code: str | None = None
    #: Unix milliseconds, per the documented example.
    modifiedTime: int | None = None

    def wire(self) -> dict[str, Any]:
        return compact(
            {
                "id": self.id,
                "hidden": self.hidden,
                "available": self.available,
                "name": self.name,
                "price": self.price,
                "priceType": self.priceType.value,
                "defaultTaxRates": self.defaultTaxRates,
                "isRevenue": self.isRevenue,
                "sku": self.sku,
                "code": self.code,
                "modifiedTime": self.modifiedTime,
            }
        )


class ItemCreateRequest(BaseModel):
    """DOCUMENTED: name and price are required
    (https://docs.clover.com/dev/docs/inventorycreateitem); rest defaults
    per :class:`ItemWire`."""

    model_config = _REQUEST

    name: str = Field(min_length=1)
    price: int = Field(ge=0)
    hidden: bool | None = None
    available: bool | None = None
    priceType: PriceType | None = None
    defaultTaxRates: bool | None = None
    isRevenue: bool | None = None
    sku: str | None = None
    code: str | None = None


class ItemPatchRequest(BaseModel):
    """Sparse update: only the fields sent change
    (https://docs.clover.com/dev/reference/inventoryupdateitem)."""

    model_config = _REQUEST

    name: str | None = Field(default=None, min_length=1)
    price: int | None = Field(default=None, ge=0)
    hidden: bool | None = None
    available: bool | None = None
    priceType: PriceType | None = None
    defaultTaxRates: bool | None = None
    isRevenue: bool | None = None
    sku: str | None = None
    code: str | None = None


ITEM_EXPANDABLE: frozenset[str] = frozenset({"modifierGroups", "taxRates"})
"""DOCUMENTED expansion names
(https://docs.clover.com/dev/docs/managing-modifier-groups-modifiers)."""


def project_item(
    entity: Mapping[str, Any],
    expand: Iterable[str] = (),
    *,
    modifier_groups: Sequence[Mapping[str, Any]] = (),
    tax_rates: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """A stored item as Clover JSON, with the requested expansions.
    ``modifierGroups`` -> documented ``{"elements": [{..., modifierIds}]}``
    (comma-joined). ``taxRates`` -> JUDGMENT shape, no example: same
    ``elements`` wrapper as every other Clover expansion."""
    wanted = frozenset(expand)
    wire = ItemWire.model_validate(entity).wire()
    if "modifierGroups" in wanted:
        wire["modifierGroups"] = {
            "elements": [
                compact(
                    {
                        "id": group.get("id"),
                        "name": group.get("name"),
                        "showByDefault": group.get("showByDefault"),
                        "modifierIds": ",".join(str(m) for m in group.get("modifierIds", [])) or None,
                    }
                )
                for group in modifier_groups
            ]
        }
    if "taxRates" in wanted:
        wire["taxRates"] = {
            "elements": [
                compact(
                    {
                        "id": rate.get("id"),
                        "name": rate.get("name"),
                        "rate": rate.get("rate"),
                        "isDefault": rate.get("isDefault"),
                    }
                )
                for rate in tax_rates
            ]
        }
    return wire
