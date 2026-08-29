"""The inventory item wire vocabulary.

FOR: stating once what a Clover inventory item document carries, so the PR-C
surface and its tests share one vocabulary.

The field set and most defaults are DOCUMENTED by the create-item response
example on https://docs.clover.com/dev/docs/inventorycreateitem, verbatim:

    {"id": "NEWITEM123ABC", "hidden": false, "available": true,
     "name": "Craft Beer", "price": 750, "priceType": "FIXED",
     "defaultTaxRates": true, "isRevenue": true,
     "modifiedTime": 1755786102000, "isAgeRestricted": true}

Create requires ``name`` and ``price`` (same page); ``price`` is integer cents
("$20.99 ... 2099"); ``priceType`` is FIXED|VARIABLE|PER_UNIT; ``stockCount``
is deprecated in favour of ``itemStock`` and neither is modelled here.

Defaults, labelled:

* ``hidden=False``, ``available=True``, ``priceType=FIXED``,
  ``defaultTaxRates=True`` -- read off the example above, where the create
  request set none of them. PARTIAL: an example's echo, not a stated rule.
* ``isRevenue=False`` -- JUDGMENT, and the one default that *disagrees* with
  the example. The example response shows ``isRevenue: true``, but the page
  does not show whether the request set it, so it evidences nothing about the
  default; ``False`` is chosen because "counts as revenue" is a bookkeeping
  claim this fake should not silently make about an item nobody classified.
  A consumer must not learn a default for this flag from either the example
  or this fake.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from vendorfake.core.util.json import compact

__all__ = ["ItemWire", "PriceType"]

_WIRE = ConfigDict(extra="forbid", frozen=True, strict=True)


class PriceType(StrEnum):
    """The three documented ``priceType`` values."""

    FIXED = "FIXED"
    VARIABLE = "VARIABLE"
    PER_UNIT = "PER_UNIT"


class ItemWire(BaseModel):
    """One inventory item. ``name`` and ``price`` required, as on create."""

    model_config = _WIRE

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
