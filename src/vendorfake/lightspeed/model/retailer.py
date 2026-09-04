"""The retailer, outlet and register wire shapes, and the register actions' bodies.

Each projection emits the fields the specification's own response EXAMPLES
print, in that order, with ``version`` restored from
:data:`~vendorfake.lightspeed.entities.OBJECT_VERSION`.

DOCUMENTED: ``Retailer.version`` is typed **string** where every other
resource's is ``format: int64``, reproduced rather than corrected. ``Outlet``'s
nullable address fields print as empty string, not null, when absent.
``Register.ask_for_note_on_save`` is ``format: double`` (0 never, 1 on
save/layby/account/return, 2 always).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.util.json import compact
from vendorfake.lightspeed.entities import OutletEntity, RegisterEntity, RetailerEntity

__all__ = [
    "EMBEDDED_BARCODE_OPTIONS",
    "RegisterClosePaymentType",
    "RegisterCloseRequest",
    "RegisterOpenRequest",
    "project_outlet",
    "project_register",
    "project_retailer",
]

EMBEDDED_BARCODE_OPTIONS: tuple[str, ...] = ("none", "price", "weight")
"""The documented ``embedded_barcode_option`` enum."""

_REQUEST = ConfigDict(extra="ignore", frozen=True)


class RegisterClosePaymentType(BaseModel):
    """One declared total in a close request. ``total`` is a string on the wire."""

    model_config = _REQUEST

    payment_type_id: str = Field(min_length=1)
    total: str | float | int


class RegisterCloseRequest(BaseModel):
    """``{"payments": [...]}`` -- and the body is legally empty."""

    model_config = _REQUEST

    payments: list[RegisterClosePaymentType] = Field(default_factory=list)


class RegisterOpenRequest(BaseModel):
    """``{"register_open_time": "..."}``; legally empty -- an absent time means
    "now", since the field is documented and not required."""

    model_config = _REQUEST

    register_open_time: str | None = None


def project_retailer(entity: Mapping[str, Any]) -> dict[str, Any]:
    """The documented ``Retailer`` document. ``gift_cards``, ``loyalty``,
    ``sku_sequence`` and ``on_account`` come straight from the seed's
    ``document``, uncomputed."""
    retailer = RetailerEntity.from_entity(entity)
    document = dict(retailer.document)
    projected: dict[str, Any] = {
        "id": retailer.id,
        "name": retailer.name,
        "domain_prefix": retailer.domain_prefix,
        "timezone": retailer.timezone,
        "country": retailer.country,
        "currency": {"code": retailer.currency_code, "symbol": retailer.currency_symbol},
    }
    projected.update(document)
    projected["version"] = str(retailer.object_version)
    return projected


def project_outlet(entity: Mapping[str, Any]) -> dict[str, Any]:
    """The documented ``Outlet`` document, required members first."""
    outlet = OutletEntity.from_entity(entity)
    return compact(
        {
            "id": outlet.id,
            "name": outlet.name,
            "default_tax_id": outlet.default_tax_id,
            "currency": outlet.currency,
            "currency_symbol": outlet.currency_symbol,
            "display_prices": outlet.display_prices,
            "time_zone": outlet.time_zone,
            "attributes": list(outlet.attributes),
            "physical_address_1": outlet.physical_address_1,
            "physical_address_2": outlet.physical_address_2,
            "physical_suburb": outlet.physical_suburb,
            "physical_city": outlet.physical_city,
            "physical_state": outlet.physical_state,
            "physical_postcode": outlet.physical_postcode,
            "physical_country_id": outlet.physical_country_id,
            "email": outlet.email,
            "deleted_at": outlet.deleted_at,
            "version": outlet.object_version,
        }
    )


def project_register(entity: Mapping[str, Any]) -> dict[str, Any]:
    """The documented ``Register`` document. ``register_close_time`` is an
    explicit ``null`` while open (documented "Null if currently open"), not a
    dropped key."""
    register = RegisterEntity.from_entity(entity)
    projected = compact(
        {
            "id": register.id,
            "name": register.name,
            "outlet_id": register.outlet_id,
            "is_open": register.is_open,
            "invoice_prefix": register.invoice_prefix,
            "invoice_suffix": register.invoice_suffix,
            "invoice_sequence": register.invoice_sequence,
            "ask_for_note_on_save": register.ask_for_note_on_save,
            "ask_for_user_on_sale": register.ask_for_user_on_sale,
            "email_receipt": register.email_receipt,
            "print_receipt": register.print_receipt,
            "print_note_on_receipt": register.print_note_on_receipt,
            "is_quick_keys_enabled": register.is_quick_keys_enabled,
            "show_discounts_on_receipts": register.show_discounts_on_receipts,
            "receipt_template_id": register.receipt_template_id,
            "button_layout_id": register.button_layout_id,
            "cash_managed_payment_type_id": register.cash_managed_payment_type_id,
            "register_open_sequence_id": register.register_open_sequence_id,
            "register_open_time": register.register_open_time,
            "deleted_at": register.deleted_at,
            "version": register.object_version,
        }
    )
    projected["register_close_time"] = register.register_close_time
    return projected
