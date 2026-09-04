"""The two response envelopes every Lightspeed route answers with, as strict models.

DOCUMENTED, from the specification's own component schemas:

``Version`` (the collection envelope's second member)
    "An object containing the highest and lowest version numbers for all items
    of the returned collection", with ``max`` and ``min`` both
    ``format: int64, nullable: true`` and both REQUIRED -- "``null`` when the
    result set is empty". So the two keys are always present and the values,
    not the keys, carry the emptiness.

``<Thing>Collection`` (``OutletCollection``, ``RegisterCollection``,
``PaymentTypeCollection``)
    ``{"data": [...], "version": {...}}``, with both members required on the
    two that declare a ``required`` list.

``<Thing>Response`` (``RetailerResponse``, ``OutletResponse``,
``RegisterResponse``, ``RegisterPaymentsSummaryResponse``)
    ``{"data": {...}}`` -- one record, wrapped in the same top-level key.

The models are here and the *construction* is in
:mod:`vendorfake.lightspeed.versioning`, which owns the counter these numbers
come from; this module is the shape, so that a surface stating what it answers
reads as a type rather than as a dict literal.
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
