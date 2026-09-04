"""The seed document's schema, as a model rather than as a cast.

FOR: stating what a scenario file may contain, so that a typo in one is a
startup failure naming the field instead of a unit that starts with an empty
world and answers 404 to every read.

INVARIANT: **a scenario is validated before a single entity is inserted.**
Every model here sets ``extra="forbid"``; hydration parses the whole document
first, and every reference -- a register's outlet, a token's scopes, a payment
type a close request could name -- must resolve inside the document.

Keys: the top level is snake_case like every JSON this project publishes, and
the entity documents use Lightspeed's own field names, which are snake_case
too, so a documented example pastes straight in. **Money is not in the seed at
all** in this slice: the only amounts this surface carries are the totals a
close request declares.

The Lightspeed ``version`` is NOT in the seed. It is drawn from the retailer's
one monotonically increasing counter at hydrate, in document order, so that two
units stamp the same numbers -- see ``versioning.py``. A seed that pinned a
version would be pinning a number the counter does not know about.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = [
    "SeedDocument",
    "SeedOutlet",
    "SeedPaymentType",
    "SeedPersonalToken",
    "SeedRefreshToken",
    "SeedRegister",
    "SeedRetailer",
    "SeedToken",
    "SeedWebhook",
    "parse_seed_document",
]

_SEED = ConfigDict(extra="forbid")


class SeedRetailer(BaseModel):
    """The one retailer. ``document`` carries the documented blocks this unit
    does not compute from (``gift_cards``, ``loyalty``, ``sku_sequence``,
    ``on_account``, ``store_url`` ...) exactly as the wire should answer
    them."""

    model_config = _SEED

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    domain_prefix: str = Field(min_length=1)
    currency_code: str = Field(min_length=3, max_length=3)
    currency_symbol: str = Field(min_length=1)
    timezone: str = "UTC"
    country: str = Field(default="US", min_length=2, max_length=2)
    document: dict[str, Any] = Field(default_factory=dict)


class SeedOutlet(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    default_tax_id: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)
    currency_symbol: str = Field(min_length=1)
    display_prices: Literal["inclusive", "exclusive"] = "inclusive"
    time_zone: str = "UTC"
    attributes: list[dict[str, str]] = Field(default_factory=list)
    physical_address_1: str | None = None
    physical_suburb: str | None = None
    physical_city: str | None = None
    physical_state: str | None = None
    physical_postcode: str | None = None
    physical_country_id: str | None = None
    email: str | None = None


class SeedRegister(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    outlet_id: str = Field(min_length=1)
    is_open: bool = True
    invoice_prefix: str = ""
    invoice_suffix: str = ""
    invoice_sequence: int = Field(default=1, ge=0)
    #: 0 never, 1 on save/layby/account/return, 2 always -- the documented
    #: meanings of ``Register.ask_for_note_on_save``.
    ask_for_note_on_save: Literal[0, 1, 2] = 1
    ask_for_user_on_sale: bool = False
    email_receipt: bool = False
    print_receipt: bool = True
    print_note_on_receipt: bool = False
    is_quick_keys_enabled: bool = True
    show_discounts_on_receipts: bool = True
    receipt_template_id: str | None = None
    button_layout_id: str | None = None
    cash_managed_payment_type_id: str | None = None
    register_open_sequence_id: str | None = None
    #: RFC 3339. Present exactly when the register starts open.
    register_open_time: str | None = None


class SeedPaymentType(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type_id: int
    disabled: bool = False
    #: An internal type is excluded from the list the ``payment_types:read``
    #: scope grants; seeding one is what makes that testable.
    internal: bool = False
    gateway: bool = False
    name_changed_by_user: bool = False
    config: dict[str, Any] | None = None
    outlet_ids: list[str] = Field(default_factory=list)


class SeedToken(BaseModel):
    """A pre-issued OAuth access token."""

    model_config = _SEED

    id: str = Field(min_length=1)
    access_token: str = Field(min_length=1)
    scopes: list[str] = Field(min_length=1)
    #: Absent means "expires ``access_token_ttl_s`` from the unit's start",
    #: which is what a token issued a moment ago would do.
    expires_in_s: int | None = None


class SeedPersonalToken(BaseModel):
    """A personal token: no expiry, and no way to create one over the API."""

    model_config = _SEED

    id: str = Field(min_length=1)
    access_token: str = Field(min_length=1)
    scopes: list[str] = Field(min_length=1)


class SeedRefreshToken(BaseModel):
    """A refresh token, and the seeded access token it was issued with."""

    model_config = _SEED

    id: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)
    scopes: list[str] = Field(min_length=1)
    access_token_id: str = Field(min_length=1)


class SeedWebhook(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    url: str = Field(min_length=3)
    active: bool = True


class SeedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    comment: list[str] | None = Field(default=None, alias="_comment")
    retailer: SeedRetailer
    outlets: list[SeedOutlet] = Field(default_factory=list)
    registers: list[SeedRegister] = Field(default_factory=list)
    payment_types: list[SeedPaymentType] = Field(default_factory=list)
    tokens: list[SeedToken] = Field(default_factory=list)
    personal_tokens: list[SeedPersonalToken] = Field(default_factory=list)
    refresh_tokens: list[SeedRefreshToken] = Field(default_factory=list)
    webhooks: list[SeedWebhook] = Field(default_factory=list)


def _refuse(path: str, message: str) -> UnitError:
    return UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f"The seed document is not valid at {path}: {message}.",
        field="seed",
        info={"path": path},
    )


def _check_references(doc: SeedDocument) -> None:
    """Every reference resolves inside the document."""
    from vendorfake.lightspeed.config import DEFAULT_SCOPES
    from vendorfake.lightspeed.events import LIGHTSPEED_EVENT_TYPES

    outlet_ids = {outlet.id for outlet in doc.outlets}
    payment_type_ids = {payment_type.id for payment_type in doc.payment_types}
    token_ids = {token.id for token in doc.tokens}
    for index, register in enumerate(doc.registers):
        if register.outlet_id not in outlet_ids:
            raise _refuse(f"registers[{index}].outlet_id", f"outlet {register.outlet_id!r} is absent")
        if register.cash_managed_payment_type_id is not None and (
            register.cash_managed_payment_type_id not in payment_type_ids
        ):
            raise _refuse(
                f"registers[{index}].cash_managed_payment_type_id",
                f"payment type {register.cash_managed_payment_type_id!r} is absent",
            )
        if register.is_open != (register.register_open_time is not None):
            raise _refuse(
                f"registers[{index}].register_open_time",
                "register_open_time is present exactly when the register starts open",
            )
    for index, payment_type in enumerate(doc.payment_types):
        for position, outlet_id in enumerate(payment_type.outlet_ids):
            if outlet_id not in outlet_ids:
                raise _refuse(f"payment_types[{index}].outlet_ids[{position}]", f"outlet {outlet_id!r} is absent")
    for index, refresh in enumerate(doc.refresh_tokens):
        if refresh.access_token_id not in token_ids:
            raise _refuse(
                f"refresh_tokens[{index}].access_token_id", f"access token {refresh.access_token_id!r} is absent"
            )
    for index, webhook in enumerate(doc.webhooks):
        if webhook.type not in LIGHTSPEED_EVENT_TYPES:
            raise _refuse(
                f"webhooks[{index}].type",
                f"{webhook.type!r} is not one of the seven documented WebhookType values",
            )
    granted = set(DEFAULT_SCOPES)
    holders: list[tuple[str, list[list[str]]]] = [
        ("tokens", [token.scopes for token in doc.tokens]),
        ("personal_tokens", [token.scopes for token in doc.personal_tokens]),
        ("refresh_tokens", [token.scopes for token in doc.refresh_tokens]),
    ]
    for holder, scope_lists in holders:
        for index, scopes in enumerate(scope_lists):
            unknown = [scope for scope in scopes if scope not in granted]
            if unknown:
                raise _refuse(f"{holder}[{index}].scopes", f"{unknown} are not scopes this application carries")


def parse_seed_document(raw: object) -> SeedDocument:
    """Validate a seed document, raising the vendor's ``invalid_value`` on the
    ``seed`` field with the offending path in the detail."""
    try:
        doc = SeedDocument.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        path = ".".join(str(part) for part in first.get("loc", ())) or "seed"
        raise _refuse(path, str(first.get("msg", "invalid"))) from exc
    _check_references(doc)
    return doc
