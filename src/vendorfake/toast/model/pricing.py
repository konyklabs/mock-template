"""The arithmetic behind ``/prices`` and every order write. Pure, and in cents.

DOCUMENTED (https://doc.toasttab.com/doc/devguide/apiOrderPrices.html): a
selection of one 8.99 item at a 0.0625 PERCENT tax rate carries
``preDiscountPrice`` 8.99, ``price`` 8.99, ``tax`` 0.56 and one
``appliedTaxes`` entry with ``rate`` 0.0625 and ``taxAmount`` 0.56; the check
carries ``amount`` 8.99, ``taxAmount`` 0.56, ``totalAmount`` 9.55. Tax rates
come from ``/config/v2/taxRates`` with a ``roundingType`` of ``HALF_UP``,
``HALF_EVEN``, ``ALWAYS_UP`` or ``ALWAYS_DOWN`` (toast-config-api.yaml), and
an item names its rates in ``taxInfo`` (toast-menus-api-v3.yaml).
:func:`tax_on` reproduces the example: 899 x 0.0625 = 56.1875 -> 56 half-up.

JUDGMENT, each labelled here because Toast documents the fields and not the
rules:

* **quantity** multiplies the unit price and the result rounds half-up to the
  cent (``quantity`` is a double; a fractional one is weight or volume);
* **a modifier is a selection of its own** with its own ``price``, ``tax`` and
  ``appliedTaxes``, and inherits its parent's tax rates (the V3
  ``modifierOptionTaxInfo`` is not modelled); its price is the option's price
  times its own quantity times the parent's;
* **a pre-modifier** on a modifier scales the option price by
  ``multiplicationFactor`` (``NO`` is 0, ``EXTRA`` is 2 in the seed), or
  replaces it with ``fixedPrice`` when that is set;
* **check amount** is the sum of every selection's and modifier's ``price``
  (post item-level discount) minus check-level discounts; **taxAmount** is the
  sum of every selection's ``tax``, computed on the discounted selection price
  and unaffected by check-level discounts; **totalAmount** is the sum;
* a **PERCENT** discount takes ``percentage`` percent of the target, a
  **FIXED** one takes ``amount``; both are capped at the target so a price
  never goes negative;
* rates typed anything but ``PERCENT`` contribute no tax (``FIXED``, ``TABLE``
  and ``EXTERNAL`` rates have no documented arithmetic here).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Any

__all__ = ["TaxRate", "discount_amount", "quantity_price", "tax_on", "taxes_on"]

_ROUNDING = {
    "HALF_UP": ROUND_HALF_UP,
    "HALF_EVEN": ROUND_HALF_EVEN,
    "ALWAYS_UP": ROUND_CEILING,
    "ALWAYS_DOWN": ROUND_FLOOR,
}


@dataclass(frozen=True, slots=True)
class TaxRate:
    """One ``/config/v2/taxRates`` row, as the arithmetic needs it."""

    guid: str
    name: str
    #: A fraction: 0.0625 is 6.25%.
    rate: Decimal
    rounding: str = "HALF_UP"
    type: str = "PERCENT"

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> TaxRate:
        raw = entity.get("rate")
        rate = Decimal(str(raw)) if isinstance(raw, int | float | str) and not isinstance(raw, bool) else Decimal(0)
        return cls(
            guid=str(entity["id"]),
            name=str(entity.get("name", "")),
            rate=rate,
            rounding=str(entity.get("roundingType", "HALF_UP")),
            type=str(entity.get("type", "PERCENT")),
        )


def _round(value: Decimal, rounding: str) -> int:
    return int(value.quantize(Decimal(1), rounding=_ROUNDING.get(rounding, ROUND_HALF_UP)))


def quantity_price(unit_cents: int, quantity: float, factor: float = 1.0) -> int:
    """``unit x quantity x factor``, half-up to the cent."""
    return _round(Decimal(unit_cents) * Decimal(str(quantity)) * Decimal(str(factor)), "HALF_UP")


def tax_on(cents: int, rate: TaxRate) -> int:
    """The tax one rate levies on ``cents``, rounded as the rate says."""
    if rate.type != "PERCENT":
        return 0
    return _round(Decimal(cents) * rate.rate, rate.rounding)


def taxes_on(cents: int, rates: Sequence[TaxRate]) -> list[dict[str, Any]]:
    """The ``appliedTaxes`` entries (cents) for ``cents`` under ``rates``, in
    the documented shape: ``{taxRate{guid, entityType}, name, rate, taxAmount,
    type}``."""
    return [
        {
            "entityType": "AppliedTaxRate",
            "taxRate": {"guid": rate.guid, "entityType": "TaxRate"},
            "name": rate.name,
            "rate": float(rate.rate),
            "taxAmount": tax_on(cents, rate),
            "type": rate.type,
        }
        for rate in rates
    ]


def discount_amount(target_cents: int, discount: Mapping[str, Any]) -> int:
    """What a ``/config/v2/discounts`` row takes off ``target_cents``."""
    kind = str(discount.get("type", ""))
    if kind in ("PERCENT", "OPEN_PERCENT"):
        percentage = discount.get("percentage")
        share = (
            Decimal(str(percentage))
            if isinstance(percentage, int | float) and not isinstance(percentage, bool)
            else Decimal(0)
        )
        taken = _round(Decimal(target_cents) * share / Decimal(100), "HALF_UP")
    elif kind in ("FIXED", "OPEN_FIXED", "FIXED_TOTAL"):
        amount = discount.get("amount")
        taken = amount if isinstance(amount, int) and not isinstance(amount, bool) else 0
    else:
        taken = 0
    return max(0, min(target_cents, taken))
