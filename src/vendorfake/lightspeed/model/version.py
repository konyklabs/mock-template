"""The two response envelopes every Lightspeed route answers with, as strict
models. DOCUMENTED: ``Version`` is ``{"max": int|null, "min": int|null}``,
both keys always present with ``null`` marking an empty result; a collection
response is ``{"data": [...], "version": {...}}``; a single-record response
is ``{"data": {...}}``. The counter that produces the numbers lives in
:mod:`vendorfake.lightspeed.versioning`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = ["CollectionWire", "RecordWire", "VersionWire"]

_WIRE = ConfigDict(extra="forbid", frozen=True)


class VersionWire(BaseModel):
    """``{"max": int|null, "min": int|null}``. Both keys always emitted."""

    model_config = _WIRE

    max: int | None
    min: int | None

    def wire(self) -> dict[str, Any]:
        return {"max": self.max, "min": self.min}


class CollectionWire(BaseModel):
    """``{"data": [...], "version": {...}}`` -- every list route's answer."""

    model_config = _WIRE

    data: Sequence[Mapping[str, Any]]
    version: VersionWire

    def wire(self) -> dict[str, Any]:
        return {"data": [dict(row) for row in self.data], "version": self.version.wire()}


class RecordWire(BaseModel):
    """``{"data": {...}}`` -- every single-record route's answer."""

    model_config = _WIRE

    data: Mapping[str, Any]

    def wire(self) -> dict[str, Any]:
        return {"data": dict(self.data)}
