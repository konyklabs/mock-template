"""Wire models: what Clover's documented JSON looks like, as typed objects.

A model is pure vocabulary -- it never reads the store or raises a
``UnitError`` -- so field defaults and unit conversions can be tested without
running a unit.
"""

from __future__ import annotations

from vendorfake.clover.model.webhooks import EventWire, PayloadWire

__all__: list[str] = ["EventWire", "PayloadWire"]
