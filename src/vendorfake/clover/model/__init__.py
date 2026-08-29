"""Wire models: what Clover's documented JSON looks like, as typed objects.

Nothing here reads the store or raises a ``UnitError``. A model is pure
vocabulary, which is what lets field defaults and the money/timestamp units be
tested without a unit. The surfaces (PRs B-D) parse requests into these and
project entities out through them.
"""

from __future__ import annotations

__all__: list[str] = []
