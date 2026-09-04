"""Request shapes for customers, payment records and print events -- the
three write bodies outside orders and inventory.

Customer (https://docs.clover.com/dev/reference/customerscreatecustomer):
JUDGMENT requires at least one name; the page only says the body cannot be
null. Payment record
(https://docs.clover.com/dev/reference/ordercreatepaymentfororder): "must
include a positive amount and a valid tender ID." Print event
(https://docs.clover.com/dev/docs/printing-orders-rest-api): ``{"orderRef":
{"id"}}`` is the whole documented request.
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
    tipAmount: int | None = Field(default=None, ge=0)
    taxAmount: int | None = Field(default=None, ge=0)
    note: str | None = None


class PrintEventRequest(BaseModel):
    model_config = _REQUEST

    orderRef: RefRequest
