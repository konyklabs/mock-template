"""The merchant wire vocabulary.

FOR: what ``GET /v3/merchants/{mId}`` returns, as far as this build models it.

DOCUMENTED, thinly: the merchant reference
(https://docs.clover.com/dev/docs/merchantgetmerchant) lists ``id``, ``name``,
``reseller_id``, ``owner{...}`` and ``address{...}`` among a long expandable
field set, but shows **no example JSON anywhere** -- the one entity in this
package whose reference publishes no response body at all.

JUDGMENT -- the nested shapes. ``owner`` and ``address`` are documented as
objects with undocumented contents on that page, so the minimal fields below
(an owner with an id and a name, a US-style address block) are this project's
reading of what a consumer needs to render a merchant, not Clover's schema. A
consumer must not assert the real API returns exactly these keys.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from vendorfake.core.util.json import compact

__all__ = ["AddressWire", "MerchantWire", "OwnerWire"]

_WIRE = ConfigDict(extra="forbid", frozen=True, strict=True)


class OwnerWire(BaseModel):
    """``owner{...}``. Minimal; JUDGMENT -- see the module docstring."""

    model_config = _WIRE

    id: str
    name: str | None = None

    def wire(self) -> dict[str, Any]:
        return compact({"id": self.id, "name": self.name})


class AddressWire(BaseModel):
    """``address{...}``. Minimal; JUDGMENT -- see the module docstring."""

    model_config = _WIRE

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

    model_config = _WIRE

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
