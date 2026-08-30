"""The three reference shapes every Toast document is built from.

DOCUMENTED (toast-orders-api.yaml, toast-config-api.yaml):

* ``ToastReference`` -- ``{guid, entityType}``;
* ``ExternalReference`` -- ``{guid, entityType, externalId}``, the shape an
  order, check or selection is addressed by;
* ``ConfigReference`` -- ``{guid, entityType, multiLocationId}``, how a
  selection names its menu item.

The ``entityType`` strings seen in the documentation: ``Order``, ``Check``,
``MenuItemSelection``, ``MenuItem``, ``MenuGroup``, ``DiningOption``,
``Discount``, ``AppliedCustomDiscount``, ``SELECTION`` (the last only in the
``/applicableDiscounts`` answer).

On the way in a reference needs only its ``guid``: the create example sends
``{"guid": "...", "entityType": "DiningOption"}`` and a consumer copying a
reference back from a GET sends the whole thing, so ``extra="ignore"`` and
``entityType`` optional. On the way out every field is emitted.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.util.json import compact

__all__ = ["ConfigReferenceWire", "ExternalReferenceWire", "RefRequest", "ToastReferenceWire"]

_REQUEST = ConfigDict(extra="ignore", frozen=True)
_WIRE = ConfigDict(extra="forbid", frozen=True, strict=True)


class RefRequest(BaseModel):
    """A reference as a caller sends it: the guid is what matters."""

    model_config = _REQUEST

    guid: str = Field(min_length=1)
    entityType: str | None = None
    externalId: str | None = None


class ToastReferenceWire(BaseModel):
    model_config = _WIRE

    guid: str
    entityType: str

    def wire(self) -> dict[str, Any]:
        return {"guid": self.guid, "entityType": self.entityType}


class ExternalReferenceWire(BaseModel):
    """``externalId`` is emitted as ``null`` when absent: the documented Order
    example shows ``"externalId": null``, so this is the one reference whose
    absent field is spelled rather than dropped."""

    model_config = _WIRE

    guid: str
    entityType: str
    externalId: str | None = None

    def wire(self) -> dict[str, Any]:
        return {"guid": self.guid, "entityType": self.entityType, "externalId": self.externalId}


class ConfigReferenceWire(BaseModel):
    model_config = _WIRE

    guid: str
    entityType: str
    multiLocationId: str | None = None

    def wire(self) -> dict[str, Any]:
        return compact({"guid": self.guid, "entityType": self.entityType, "multiLocationId": self.multiLocationId})
