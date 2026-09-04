"""The merchant wire vocabulary: what ``GET /v3/merchants/{mId}`` returns.

DOCUMENTED, thinly (https://docs.clover.com/dev/docs/merchantgetmerchant):
``id``, ``name``, ``reseller_id``, ``owner{...}``, ``address{...}``, no
example JSON. JUDGMENT: ``owner``/``address`` are objects with undocumented
contents, so the minimal fields below are this project's reading, not
Clover's schema.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from vendorfake.core.util.json import compact

__all__ = ["AddressWire", "MerchantWire", "OwnerWire"]

_RESPONSE = ConfigDict(extra="forbid", frozen=True, strict=True)
"""Projection-only: this read-only GET never parses an inbound body, so a
wrong type here is this unit's own bug, not a consumer's."""


class OwnerWire(BaseModel):
    """``owner{...}``. Minimal; JUDGMENT -- see the module docstring."""

    model_config = _RESPONSE

    id: str
    name: str | None = None

    def wire(self) -> dict[str, Any]:
        return compact({"id": self.id, "name": self.name})


class AddressWire(BaseModel):
    """``address{...}``. Minimal; JUDGMENT -- see the module docstring."""

    model_config = _RESPONSE

    address1: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    country: str | None = None

    def wire(self) -> dict[str, Any]:
        return compact(
            {
                "address1": self.address1,
                "city": self.city,
                "state": self.state,
                "zip": self.zip,
                "country": self.country,
            }
        )


class MerchantWire(BaseModel):
    """One merchant, as this build models it."""

    model_config = _RESPONSE

    id: str
    name: str
    owner: OwnerWire | None = None
    address: AddressWire | None = None

    def wire(self) -> dict[str, Any]:
        return compact(
            {
                "id": self.id,
                "name": self.name,
                "owner": None if self.owner is None else self.owner.wire(),
                "address": None if self.address is None else self.address.wire(),
            }
        )
