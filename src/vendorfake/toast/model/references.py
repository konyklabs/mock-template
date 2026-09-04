"""The three reference shapes every Toast document is built from.

DOCUMENTED (toast-orders-api.yaml, toast-config-api.yaml):
``ToastReference`` is ``{guid, entityType}``; ``ExternalReference`` adds
``externalId``; ``ConfigReference`` adds ``multiLocationId``.

On the way in a reference needs only its ``guid`` -- the create example sends
``{"guid": "...", "entityType": "DiningOption"}`` -- so ``extra="ignore"`` and
``entityType`` is optional; on the way out every field is emitted.
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
