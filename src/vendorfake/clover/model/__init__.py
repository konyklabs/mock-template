"""Wire models: what Clover's documented JSON looks like, as typed objects.

Nothing here reads the store or raises a ``UnitError``. A model is pure
vocabulary, which is what lets field defaults and the money/timestamp units be
tested without a unit. The surfaces (PRs B-D) parse requests into these and
project entities out through them.
"""

from __future__ import annotations

from vendorfake.clover.model.webhooks import EventWire, PayloadWire

__all__: list[str] = ["EventWire", "PayloadWire"]
