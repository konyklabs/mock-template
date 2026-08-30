"""The vendor-neutral tail of every error table.

FOR: the three things a vendor's ``ErrorShaper`` does that have nothing to do
with that vendor's envelope -- the ``unit_error`` sidecar, the two mechanism
headers, and the import-time check that its table is total -- so that a
vendor writes its table and its envelope and nothing else. Square and Clover
each carried all three, line for line, before this module.

INVARIANT: **the table is exhaustive, checked at import, as a raise.** A
missing row would otherwise present as one error kind answering 500 while the
other nineteen behaved, and ``python -O`` strips an ``assert``, so
:func:`assert_error_table_total` raises. This is the TypeScript
``Record<UnitErrorKind, Mapping>`` in the only form Python can enforce at run
time; a vendor module calls it at its bottom, once, with its table.

Provenance
----------
Every row of every table says where its HTTP status came from:
``"documented"`` when the vendor publishes it, ``"judgment"`` when this
project chose it. It is a real field and not a comment because two things
publish it: the sidecar on every error, and ``GET /__unit/errors`` through
``ErrorShaper.describe`` -- so a consumer can ask a unit which of its statuses
the vendor actually documents.

The sidecar
-----------
A deliberate, namespaced deviation from every vendor's wire format: a consumer
that reads only the vendor's own envelope never sees it, and a consumer
debugging this fake gets the machine-readable reason without parsing prose.
Reserved keys go **last**, so an ``info`` document that happens to carry a
``kind`` of its own cannot clobber what the sidecar exists to report. Each
vendor switches it with ``"error_sidecar": false`` in a profile's ``vendor``
block.

The headers
-----------
``retry-after`` on a 429, when the vendor's switch is on, from the chaos
rule's ``retry_after_seconds`` or a one-second fallback (the reference's
``Number(info.retryAfterSeconds ?? 1)``); ``x-unit-capability`` on a 501,
naming the capability that was off. A vendor adds its own documented headers
around these -- Clover's ``X-RateLimit-*`` set, for one.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.core.util.json import compact
from vendorfake.core.util.numbers import as_str

__all__ = [
    "DEFAULT_RETRY_AFTER",
    "Provenance",
    "assert_error_table_total",
    "mechanism_headers",
    "unit_error_sidecar",
]

Provenance = Literal["documented", "judgment"]
"""Where a row's HTTP status comes from. A real field, surfaced on the wire."""

#: The ``retry-after`` value a rate-limited response carries when the header
#: is on and the chaos rule supplied no interval. One second.
DEFAULT_RETRY_AFTER = "1"


def assert_error_table_total(table: Mapping[UnitErrorKind, object] | Mapping[str, object], *, name: str) -> None:
    """Raise unless ``table`` maps every :class:`UnitErrorKind` exactly once.

    Called at module import by every vendor's ``errors`` module on its table,
    and at unit construction on what ``ErrorShaper.describe()`` returns --
    which is keyed by the kinds' *values*, so either spelling of a key is
    accepted. ``name`` is the table's own so the failure names what to fix.
    """
    present = {kind.value if isinstance(kind, UnitErrorKind) else str(kind) for kind in table}
    expected = {kind.value for kind in UnitErrorKind}
    if present != expected:
        missing = sorted(expected - present)
        extra = sorted(present - expected)
        raise RuntimeError(
            f"{name} must map every UnitErrorKind exactly once; "
            f"missing: {missing or 'none'}; unknown: {extra or 'none'}"
        )


def unit_error_sidecar(err: UnitError, provenance: Provenance, **extra: Any) -> dict[str, Any]:
    """The ``unit_error`` document for one error: its ``info``, then the
    reserved keys, then whatever the vendor adds, ``None`` values dropped."""
    return compact(
        {
            **dict(err.info or {}),
            "kind": err.kind.value,
            "status_provenance": provenance,
            **extra,
        }
    )


def mechanism_headers(err: UnitError, *, retry_after_header: bool) -> dict[str, str]:
    """The headers the core's error mechanism implies, independent of vendor."""
    headers: dict[str, str] = {}
    info = err.info or {}
    if err.kind is UnitErrorKind.RATE_LIMITED and retry_after_header:
        headers["retry-after"] = as_str(info.get("retry_after_seconds"), DEFAULT_RETRY_AFTER)
    if err.kind is UnitErrorKind.CAPABILITY_DISABLED:
        headers["x-unit-capability"] = as_str(info.get("capability"), "")
    return headers
