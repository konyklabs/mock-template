"""JSON, twice over: one encoding for hashing (sorted, never a response) and one for the wire.
Absent means absent: a field is cleared by removing its key, never by assigning ``None``; both
encoders raise on a non-finite float rather than emit an unparseable token.
"""

from __future__ import annotations

import enum
import hashlib
import json
from collections.abc import Mapping
from typing import TypeVar

__all__ = [
    "MISSING",
    "Missing",
    "canonical_json",
    "compact",
    "digest_of",
    "dump_json",
    "sha256_hex",
]

_T = TypeVar("_T")


class Missing(enum.Enum):
    """The type of :data:`MISSING`; narrows on ``is MISSING`` where ``object()`` would not."""

    TOKEN = 0

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "MISSING"


MISSING = Missing.TOKEN
"""Absence: a check reads ``is MISSING``, never ``dict.get(key)``, whose ``None`` default collapses absence with a null."""


def canonical_json(value: object) -> str:
    """Canonical JSON: keys sorted at every depth, for hashing only."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def dump_json(value: object) -> bytes:
    """The wire encoder: exact bytes a response or webhook carries, with non-ASCII text kept as UTF-8."""
    return json.dumps(
        value,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data: str | bytes) -> str:
    """Hex SHA-256 of ``data``; a ``str`` is hashed as its UTF-8 bytes."""
    payload = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(payload).hexdigest()


def digest_of(value: object) -> str:
    """Hex SHA-256 of the canonical JSON of ``value``."""
    return sha256_hex(canonical_json(value))


def compact(obj: Mapping[str, _T | None]) -> dict[str, _T]:
    """Drop keys whose value is ``None``, shallowly, returning a new dict rather than mutating its argument."""
    out: dict[str, _T] = {}
    for key, value in obj.items():
        if value is not None:
            out[key] = value
    return out
