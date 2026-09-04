"""The arithmetic behind ``/prices`` and every order write. Pure, and in cents.

DOCUMENTED (https://doc.toasttab.com/doc/devguide/apiOrderPrices.html): the
guide's worked example (899 x 0.0625 tax) fixes a selection's
``preDiscountPrice``/``price``/``tax``/``appliedTaxes`` and a check's
``amount``/``taxAmount``/``totalAmount``; :func:`tax_on` reproduces it exactly,
half-up to the cent. Tax rates come from ``/config/v2/taxRates`` with a
documented ``roundingType`` (toast-config-api.yaml).

JUDGMENT (Toast documents the fields, not the rules): quantity multiplies unit
price, rounding half-up; a modifier is its own selection inheriting the
parent's tax rates, priced by option price times its own quantity times the
parent's; a pre-modifier scales by ``multiplicationFactor`` or replaces the
price with ``fixedPrice``; check totals sum the selections' post-discount
prices and taxes; PERCENT/FIXED discounts are capped at the target; a
non-PERCENT rate contributes no tax.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

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


def _round(value: Decimal, rounding: str, *, field: str | None = None) -> int:
    """``value`` to whole cents, or the documented 400 when it overflows
    Decimal's context -- naming ``field`` when given, never a 500
    (konyklabs/roadmap#41)."""
    try:
        return int(value.quantize(Decimal(1), rounding=_ROUNDING.get(rounding, ROUND_HALF_UP)))
    except InvalidOperation:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                f"{field} multiplies out to an amount too large to price."
                if field
                else "An amount in this order multiplies out too large to price or total."
            ),
            field=field,
        ) from None


def quantity_price(unit_cents: int, quantity: float, factor: float = 1.0, *, field: str | None = None) -> int:
    """``unit x quantity x factor``, half-up to the cent."""
    return _round(Decimal(unit_cents) * Decimal(str(quantity)) * Decimal(str(factor)), "HALF_UP", field=field)


def tax_on(cents: int, rate: TaxRate) -> int:
    """The tax one rate levies on ``cents``, rounded as the rate says."""
    if rate.type != "PERCENT":
        return 0
    return _round(Decimal(cents) * rate.rate, rate.rounding)


APPLIED_TAX_NAMESPACE = uuid.UUID("7c0b6d1e-3a5f-4d2b-9e8c-2f1a0b9c8d7e")
"""JUDGMENT: an ``AppliedTaxRate`` needs its own ``guid``; this unit derives
one via uuid5 from the selection and rate, stable and off the id stream
(konyklabs/roadmap#56)."""


def taxes_on(cents: int, rates: Sequence[TaxRate], *, owner: str = "") -> list[dict[str, Any]]:
    """The documented ``appliedTaxes`` entries for ``cents`` under ``rates``;
    ``owner`` is the selection the tax applies to."""
    return [
        {
            # Unsaved (``/prices``): no owner means no guid, per the documented priced order.
            "guid": str(uuid.uuid5(APPLIED_TAX_NAMESPACE, f"{owner}:{rate.guid}")) if owner else None,
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
