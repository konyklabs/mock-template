"""The vendor-neutral tail of every error table: the ``unit_error`` sidecar,
the two mechanism headers, and the import-time totality check. Every row states
its status's provenance, ``"documented"`` or ``"judgment"``, as a real field
published by the sidecar and by ``GET /__unit/errors``. ``errors.sidecar``
(default ``"headers"``, env ``VENDORFAKE_ERROR_SIDECAR``) puts the sidecar in
the ``unit_error`` body key (``"body"``, DEPRECATED), in the four
``Vendorfake-*`` headers, or both. INVARIANTS: **the table is exhaustive,
checked at import, as a raise**, because ``python -O`` strips an ``assert``;
and **every ``Vendorfake-Error-*`` header value is ASCII by construction**,
since httpx raises on a non-ASCII header value and the ASGI stack 500s above
``U+00FF`` while ``UnitError.info`` carries consumer-supplied text verbatim.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import quote

from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.core.util.json import compact
from vendorfake.core.util.numbers import as_str

__all__ = [
    "DEFAULT_RETRY_AFTER",
    "ERROR_FIELD_HEADER",
    "ERROR_INFO_HEADER",
    "ERROR_KIND_HEADER",
    "STATUS_PROVENANCE_HEADER",
    "Provenance",
    "assert_error_table_total",
    "header_text",
    "mechanism_headers",
    "sidecar_headers",
    "unit_error_sidecar",
]

#: Where a row's HTTP status comes from. A real field, surfaced on the wire.
Provenance = Literal["documented", "judgment"]

#: The ``retry-after`` used when the chaos rule supplied no interval.
DEFAULT_RETRY_AFTER = "1"

#: The four headers :func:`sidecar_headers` emits. ``Vendorfake-`` and not the
#: kernel's ``x-unit-``, so an allow-list tells sidecar from mechanism header.
ERROR_KIND_HEADER = "Vendorfake-Error-Kind"
STATUS_PROVENANCE_HEADER = "Vendorfake-Status-Provenance"
ERROR_FIELD_HEADER = "Vendorfake-Error-Field"
ERROR_INFO_HEADER = "Vendorfake-Error-Info"

#: ``quote``'s ``safe`` set for :data:`ERROR_FIELD_HEADER`: every ASCII
#: printable except ``%``, which must stay encoded to survive ``unquote``.
_FIELD_HEADER_SAFE = "".join(chr(code) for code in range(0x21, 0x7F) if chr(code) != "%")


def _ascii_json(value: object) -> str:
    """The extra sidecar keys, as one JSON header value guaranteed ASCII."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def header_text(value: str) -> str:
    """Free text as a header value: ASCII untouched, the rest percent-encoded UTF-8 (RFC 3986)."""
    return quote(value, safe=_FIELD_HEADER_SAFE)


def assert_error_table_total(table: Mapping[UnitErrorKind, object] | Mapping[str, object], *, name: str) -> None:
    """Raise unless ``table`` maps every kind once; keys may be kinds or their
    string values, and ``name`` names what to fix."""
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
    """The ``unit_error`` document: ``info``, the reserved keys, the vendor's extras, ``None`` dropped. INVARIANT:
    **``UnitError.info`` is a published channel** -- every key reaches the wire verbatim."""
    return compact(
        {
            **dict(err.info or {}),
            "kind": err.kind.value,
            "status_provenance": provenance,
            **extra,
        }
    )


def sidecar_headers(sidecar: Mapping[str, Any]) -> dict[str, str]:
    """:func:`unit_error_sidecar`'s dict, reshaped as headers: kind and provenance always, ``field`` when supplied, the
    rest as one compact JSON document omitted when empty, so the header set never varies."""
    reserved = ("kind", "status_provenance", "field")
    headers: dict[str, str] = {
        ERROR_KIND_HEADER: as_str(sidecar.get("kind"), ""),
        STATUS_PROVENANCE_HEADER: as_str(sidecar.get("status_provenance"), ""),
    }
    field = sidecar.get("field")
    if field is not None:
        headers[ERROR_FIELD_HEADER] = header_text(as_str(field, ""))
    extra = {key: value for key, value in sidecar.items() if key not in reserved}
    if extra:
        headers[ERROR_INFO_HEADER] = _ascii_json(extra)
    return headers


def mechanism_headers(err: UnitError, *, retry_after_header: bool) -> dict[str, str]:
    """The headers the core's error mechanism implies, independent of vendor."""
    headers: dict[str, str] = {}
    info = err.info or {}
    if err.kind is UnitErrorKind.RATE_LIMITED and retry_after_header:
        headers["retry-after"] = as_str(info.get("retry_after_seconds"), DEFAULT_RETRY_AFTER)
    if err.kind is UnitErrorKind.CAPABILITY_DISABLED:
        headers["x-unit-capability"] = as_str(info.get("capability"), "")
    return headers
