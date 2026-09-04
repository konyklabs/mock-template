"""The customer wire shape, and the one body that creates and updates it.

DOCUMENTED SCHEMAS: ``Customer`` (47 members), ``CustomerCollection``
(``data`` + ``version``), ``CustomerResponse`` (``data``), ``CustomerBase``
(the create AND update body -- ``POST /customers`` and
``PUT /customers/{customer_id}`` declare the same one), and ``CustomerGroup``.

``first_name`` and ``last_name`` are the only required members, on both
schemas -- and both are also ``nullable``. This package reads that pair as
"the key must be present; the value may be null", so a body that omits either
is a 422 naming it and a body that sends ``null`` is accepted. That reading is
JUDGMENT and is stated here because the two annotations genuinely conflict.

``name`` IS DERIVED AND NOT SETTABLE. ``CustomerBase`` has no ``name`` member
at all, and the response examples print ``"first_name": "Anthony",
"last_name": "Stark", "name": "Anthony Stark"``. So the surface computes it and
a caller cannot send one.

``customer_code`` IS GENERATED when the caller does not supply one. The
documented examples are ``Tony-N4ZJ`` and ``Tony-AB2W`` -- a first-name
fragment, a hyphen, four upper-case alphanumerics -- and ``CustomerBase``
carries the member, so a caller may set it. The generated form is
:func:`generate_customer_code` and its alphabet is ``ids.CODE_ALPHABET``.

THE THREE MONEY MEMBERS are read-only here. ``balance``, ``loyalty_balance``
and ``year_to_date`` are ``format: double`` on ``Customer`` and absent from
``CustomerBase``, so nothing a consumer can send moves one, and no operation in
issue #94's scoped surface does either. They stay where the scenario put them.

CUSTOMER GROUPS ARE READ-ONLY. The Customer Groups tag's seven operations are
deferred, so the scenario seeds the retailer's one default group, every
customer belongs to it unless a body names another, and a body naming a group
that does not exist is a 422. Recorded in ``capabilities.py`` under
``customer-groups``.

KEY ORDER is alphabetical, as every response example in the specification
prints it.
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
"""The members ``CustomerBase`` declares that this package stores verbatim --
addresses, contact details, the four custom fields and the three flags. Named
once here so the create path, the update path and the seed loader cannot
disagree about the list. ``CustomerEntity`` types the rest."""

_BOOLEAN_DOCUMENT_FIELDS = frozenset({"do_not_email", "enable_loyalty", "enable_promotional_sms"})
_NUMERIC_DOCUMENT_FIELDS = frozenset({"on_account_limit"})

_REQUEST = ConfigDict(extra="ignore", frozen=True)


class CustomerBody(BaseModel):
    """``CustomerBase``: the body of both ``POST`` and ``PUT``.

    ``first_name`` and ``last_name`` are declared with ``None`` as an allowed
    VALUE and no default, so Pydantic requires the key and accepts the null --
    which is the reading the module docstring records.
    """

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
    """``("Tony", "N4ZJ")`` -> ``"Tony-N4ZJ"``.

    A customer with no first name gets the suffix alone rather than a leading
    hyphen: ``first_name`` is nullable, and ``-N4ZJ`` would read as a typo.
    """
    prefix = (first_name or "").strip()
    return f"{prefix}-{suffix}" if prefix else suffix


def project_customer(entity: Mapping[str, Any]) -> dict[str, Any]:
    """The documented ``Customer`` document, members in alphabetical order.

    ``first_name``, ``last_name`` and ``name`` are emitted even when null,
    because all three are nullable members every example prints; every other
    optional member is absent when unset.
    """
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
    """The documented ``CustomerGroup``. Read by nothing on the wire in this
    slice -- there are no group routes -- and by the customer surface, which
    checks that a body's ``customer_group_id`` names one."""
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
