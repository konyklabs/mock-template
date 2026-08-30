"""Request shapes for customers, payment records and print events.

FOR: the three write bodies outside orders and inventory, stated as models so
the surfaces read typed fields and the vendor's error vocabulary names the
missing one.

Provenance:

* **Customer** -- ``firstName``, ``lastName`` ("Maximum 64 characters"),
  ``addresses[{address1, address2, city, state, zip, country}]``
  (https://docs.clover.com/dev/reference/customerscreatecustomer). JUDGMENT:
  at least one of the two names is required; the page says only that "the
  request body cannot be null".
* **Payment record** -- "Payment must include a `positive amount` and a valid
  `tender ID`" (https://docs.clover.com/dev/reference/ordercreatepaymentfororder);
  ``employee{id}``, ``offline`` ("Indicates if the tender option is offline",
  default false), ``tipAmount``, ``taxAmount`` ("Tax amount paid"), ``note``
  are documented fields of the same object.
* **Print event** -- ``{"orderRef": {"id"}}`` is the whole documented request
  (https://docs.clover.com/dev/docs/printing-orders-rest-api).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.clover.model.order import RefRequest

__all__ = ["AddressRequest", "CustomerCreateRequest", "PaymentCreateRequest", "PrintEventRequest"]

_REQUEST = ConfigDict(extra="ignore", frozen=True)


class AddressRequest(BaseModel):
    model_config = _REQUEST

    address1: str | None = None
    address2: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    country: str | None = None


class CustomerCreateRequest(BaseModel):
    model_config = _REQUEST

    firstName: str | None = Field(default=None, max_length=64)
    lastName: str | None = Field(default=None, max_length=64)
    addresses: list[AddressRequest] | None = None


class PaymentCreateRequest(BaseModel):
    model_config = _REQUEST

    tender: RefRequest
    amount: int
    employee: RefRequest | None = None
    offline: bool = False
    tipAmount: int | None = None
    taxAmount: int | None = None
    note: str | None = None


class PrintEventRequest(BaseModel):
    model_config = _REQUEST

    orderRef: RefRequest
