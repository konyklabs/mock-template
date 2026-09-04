"""The retailer, outlet and register wire shapes, and the register actions' bodies.

Every projection below emits the fields the specification's own response
EXAMPLES print, in the order they print them, with the entity's Lightspeed
``version`` restored from
:data:`~vendorfake.lightspeed.entities.OBJECT_VERSION` -- see that module for
why the stored key is spelled differently.

DOCUMENTED shapes:

``Retailer`` (``RetailerResponse``)
    ``account_status``, ``account_type``, ``activated_at`` (nullable),
    ``country``, ``created_at``, ``culture``, ``currency`` (a
    ``RetailerCurrency`` of ``code`` and ``symbol``), ``discount_product_id``,
    ``domain_prefix``, ``embedded_barcode_option``
    (``none``/``price``/``weight``), ``enable_line_item_consolidation``,
    ``gift_cards``, ``id``, ``loyalty``, ``name``, ``no_tax_group_id``,
    ``on_account``, ``sku_sequence``, ``sso_enabled``, ``store_credit``,
    ``store_url``, ``tax_exclusive``, ``timezone``, ``version``.
    ``Retailer.version`` is typed **string** here where every other resource's
    is ``format: int64`` -- a real inconsistency in the vendor's own document,
    reproduced rather than corrected, and recorded as a deviation for the
    fidelity declaration a later slice fills in.

``Outlet``
    Required: ``id``, ``name``, ``default_tax_id``, ``currency``,
    ``display_prices``, ``time_zone``, ``currency_symbol``, ``attributes``,
    ``version``. The seven ``physical_*`` members, ``email``, ``latitude``,
    ``longitude``, ``phone`` and ``deleted_at`` are nullable, and the
    documented example prints the empty string rather than null for an address
    line it has nothing for -- so absence here is an absent key and an empty
    value is an empty string, which is what the example shows.

``Register``
    ``ask_for_note_on_save`` is ``format: double`` with documented meanings
    (0 never, 1 on save/layby/account/return, 2 always) and
    ``invoice_sequence`` is likewise a number. ``register_close_time`` is
    "Null if currently open"; ``register_open_time`` is "Always in UTC".

``RegisterOpenRequest`` / ``RegisterCloseRequest``
    One optional member each: ``register_open_time`` (a string) and
    ``payments`` (an array of ``RegisterClosePaymentType``, which is
    ``payment_type_id`` plus a string ``total``). Neither declares a
    ``required`` list, so both bodies are legally empty -- ``{}`` opens or
    closes the register with no declared totals.
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
    """``{"register_open_time": "..."}`` -- and the body is legally empty.

    An absent ``register_open_time`` means "now on the unit's clock": the field
    is documented as "Date/time when the register was open. Always in UTC" and
    is not required, so a caller who omits it is opening it now.
    """

    model_config = _REQUEST

    register_open_time: str | None = None


def project_retailer(entity: Mapping[str, Any]) -> dict[str, Any]:
    """The documented ``Retailer`` document.

    ``version`` is a **string**, which is what the schema types it as -- see
    the module docstring. The blocks this unit does not compute from
    (``gift_cards``, ``loyalty``, ``sku_sequence``, ``on_account``) come
    straight from the seed's ``document``.
    """
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
    """The documented ``Register`` document.

    ``register_close_time`` is emitted as an explicit ``null`` while the
    register is open, because the schema documents it as "Null if currently
    open" -- that null is data, not absence, and dropping the key would make a
    consumer unable to tell an open register from an old response.
    """
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
