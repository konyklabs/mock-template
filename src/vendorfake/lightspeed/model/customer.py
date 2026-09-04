"""The customer wire shape, and the one body that creates and updates it.

DOCUMENTED: ``CustomerBase`` is the single create/update body shared by
``POST /customers`` and ``PUT /customers/{customer_id}``. ``name`` is derived
and not settable; ``customer_code`` is generated when omitted, via
:func:`generate_customer_code`; ``balance``, ``loyalty_balance`` and
``year_to_date`` are read-only.

JUDGMENT: ``first_name``/``last_name`` are the only required members and both
nullable -- read as "key required, value may be null" -- so an omitted key is
a 422 but an explicit ``null`` is accepted. Customer groups are read-only in
this slice (Customer Groups tag deferred, ``capabilities.py``:
``customer-groups``); the scenario seeds one default group every customer
belongs to unless a body names another that exists.

Key order is alphabetical, matching every documented response example.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from vendorfake.core.util.json import compact
from vendorfake.lightspeed.entities import CustomerEntity, CustomerGroupEntity
from vendorfake.lightspeed.model.scalars import wire_instant, wire_number

__all__ = [
    "CUSTOMER_DOCUMENT_FIELDS",
    "CustomerBody",
    "customer_document",
    "generate_customer_code",
    "project_customer",
    "project_customer_group",
]

CUSTOMER_DOCUMENT_FIELDS: tuple[str, ...] = (
    "company_name",
    "custom_field_1",
    "custom_field_2",
    "custom_field_3",
    "custom_field_4",
    "date_of_birth",
    "do_not_email",
    "enable_loyalty",
    "enable_promotional_sms",
    "fax",
    "gender",
    "mobile",
    "note",
    "on_account_limit",
    "phone",
    "physical_address_1",
    "physical_address_2",
    "physical_city",
    "physical_country_id",
    "physical_postcode",
    "physical_state",
    "physical_suburb",
    "postal_address_1",
    "postal_address_2",
    "postal_city",
    "postal_country_id",
    "postal_postcode",
    "postal_state",
    "postal_suburb",
    "tax_id",
    "twitter",
    "website",
)
"""The members ``CustomerBase`` stores verbatim -- addresses, contact details,
custom fields and flags -- named once so create, update and seed cannot
disagree. ``CustomerEntity`` types the rest."""

_BOOLEAN_DOCUMENT_FIELDS = frozenset({"do_not_email", "enable_loyalty", "enable_promotional_sms"})
_NUMERIC_DOCUMENT_FIELDS = frozenset({"on_account_limit"})

_REQUEST = ConfigDict(extra="ignore", frozen=True)


class CustomerBody(BaseModel):
    """``CustomerBase``: the body of both ``POST`` and ``PUT``; see the module
    docstring for the nullable-vs-required reading of ``first_name``/``last_name``."""

    model_config = _REQUEST

    first_name: str | None
    last_name: str | None
    customer_code: str | None = None
    customer_group_id: str | None = None
    email: str | None = None
    company_name: str | None = None
    custom_field_1: str | None = None
    custom_field_2: str | None = None
    custom_field_3: str | None = None
    custom_field_4: str | None = None
    date_of_birth: str | None = None
    do_not_email: bool | None = None
    enable_loyalty: bool | None = None
    enable_promotional_sms: bool | None = None
    fax: str | None = None
    gender: str | None = None
    mobile: str | None = None
    note: str | None = None
    on_account_limit: float | int | str | None = None
    phone: str | None = None
    physical_address_1: str | None = None
    physical_address_2: str | None = None
    physical_city: str | None = None
    physical_country_id: str | None = None
    physical_postcode: str | None = None
    physical_state: str | None = None
    physical_suburb: str | None = None
    postal_address_1: str | None = None
    postal_address_2: str | None = None
    postal_city: str | None = None
    postal_country_id: str | None = None
    postal_postcode: str | None = None
    postal_state: str | None = None
    postal_suburb: str | None = None
    tax_id: str | None = None
    twitter: str | None = None
    website: str | None = None

    def document(self) -> dict[str, Any]:
        """The stored pass-through block, in :data:`CUSTOMER_DOCUMENT_FIELDS`
        order. A member the body did not carry is absent, never null."""
        return customer_document({key: getattr(self, key) for key in CUSTOMER_DOCUMENT_FIELDS})


def customer_document(values: Mapping[str, Any]) -> dict[str, Any]:
    """``values`` reduced to the documented pass-through members, in the fixed
    order :data:`CUSTOMER_DOCUMENT_FIELDS` gives, with absent ones dropped."""
    return compact({key: values.get(key) for key in CUSTOMER_DOCUMENT_FIELDS})


def generate_customer_code(first_name: str | None, suffix: str) -> str:
    """``("Tony", "N4ZJ")`` -> ``"Tony-N4ZJ"``; no first name gets the suffix
    alone, not a leading hyphen."""
    prefix = (first_name or "").strip()
    return f"{prefix}-{suffix}" if prefix else suffix


def project_customer(entity: Mapping[str, Any]) -> dict[str, Any]:
    """The documented ``Customer`` document, alphabetical order.
    ``first_name``/``last_name``/``name`` are emitted even when null; every
    other optional member is absent when unset."""
    customer = CustomerEntity.from_entity(entity)
    document = dict(customer.document)
    projected: dict[str, Any] = {
        "balance": wire_number(customer.balance),
        "created_at": wire_instant(_opt_text(entity.get("created_at"))),
        "customer_code": customer.customer_code,
        "customer_group_id": customer.customer_group_id,
        "first_name": customer.first_name,
        "id": customer.id,
        "last_name": customer.last_name,
        "loyalty_balance": wire_number(customer.loyalty_balance),
        "name": customer.name,
        "updated_at": wire_instant(_opt_text(entity.get("updated_at"))),
        "version": customer.object_version,
        "year_to_date": wire_number(customer.year_to_date),
    }
    if customer.email is not None:
        projected["email"] = customer.email
    for key in CUSTOMER_DOCUMENT_FIELDS:
        value = document.get(key)
        if value is None:
            continue
        if key in _BOOLEAN_DOCUMENT_FIELDS:
            projected[key] = bool(value)
        elif key in _NUMERIC_DOCUMENT_FIELDS:
            projected[key] = wire_number(str(value))
        else:
            projected[key] = value
    if customer.deleted_at is not None:
        projected["deleted_at"] = customer.deleted_at
    return dict(sorted(projected.items()))


def project_customer_group(entity: Mapping[str, Any]) -> dict[str, Any]:
    """The documented ``CustomerGroup``. No group routes in this slice; read
    only by the customer surface, to check a body's ``customer_group_id``."""
    group = CustomerGroupEntity.from_entity(entity)
    return dict(
        sorted(
            compact(
                {
                    "created_at": wire_instant(_opt_text(entity.get("created_at"))),
                    "deleted_at": group.deleted_at,
                    "group_id": group.group_id,
                    "id": group.id,
                    "name": group.name,
                    "retailer_id": group.retailer_id,
                    "updated_at": wire_instant(_opt_text(entity.get("updated_at"))),
                    "version": group.object_version,
                }
            ).items()
        )
    )


def _opt_text(value: Any) -> str | None:
    return None if value is None else str(value)
