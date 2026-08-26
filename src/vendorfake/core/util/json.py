"""JSON, twice over: one encoding for hashing and one for the wire.

FOR: producing bytes from a Python value in the two different ways this
project needs, and for the two hashes built on the first of them.

INVARIANT (ported verbatim from ``packages/core/src/util/json.ts``): *canonical
JSON is for hashing ONLY — snapshot digests, idempotency request fingerprints,
cursor query fingerprints — never for responses, which must keep the vendor's
own field order.* Sorting a response body would make every vendor example in
the documentation stop matching what this emits; hashing an unsorted body
would make two runs of the same scenario disagree. One rule, two encoders, and
they are never interchangeable.

Three things Python forces a decision on that JavaScript did not:

``compact()`` and the absence of ``undefined``
    ``JSON.stringify`` silently omits a key whose value is ``undefined``, and
    the reference leans on that: ``packages/square/src/model/order.ts`` routes
    every projection through ``compact()`` so an order with no
    ``reference_id`` emits *no* ``reference_id`` key rather than
    ``"reference_id": null``. Python has one ``None`` where JavaScript has both
    ``null`` and ``undefined``, so without ``compact()`` every optional vendor
    field would appear as an explicit ``null`` and the wire format would
    diverge from the vendor's own examples on every response. The rule this
    project adopts, and which the state store depends on, is that **absent
    means absent**: clear a field by removing the key, never by assigning
    ``None``.

Sort order
    ``canonical_json`` sorts keys by Unicode code point (Python's ``sorted``).
    JavaScript's ``Object.keys().sort()`` sorts by UTF-16 code unit; the two
    agree below U+E000 and disagree for supplementary-plane characters. Code
    point order is the cross-language contract here, because a non-Python
    consumer recomputing an entity digest must be able to reproduce it from a
    published rule rather than from a JavaScript implementation detail. The
    reference additionally sorts collection names and entity ids with
    ``localeCompare`` — ICU collation, which puts ``"a"`` before ``"B"`` —
    while sorting object keys by code unit, i.e. two orderings inside one
    hash. Everything here is code point order, everywhere.

Non-finite floats
    ``JSON.stringify(NaN)`` yields ``null``. Python's default yields the bare
    token ``NaN``, which is not JSON at all. Both encoders here pass
    ``allow_nan=False`` and raise instead: an unrepresentable float in a body
    is a defect in whatever produced it, and a loud failure is worth more than
    either a silent ``null`` or an unparseable response.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import TypeVar

__all__ = [
    "canonical_json",
    "compact",
    "digest_of",
    "dump_json",
    "sha256_hex",
]

_T = TypeVar("_T")


def canonical_json(value: object) -> str:
    """Canonical JSON: keys sorted at every depth.

    Used ONLY for hashing (snapshot digests, idempotency request fingerprints,
    cursor query fingerprints) -- never for responses, which must keep the
    vendor's own field order.

    Array order is preserved at every depth: the order of an array is data,
    not presentation.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def dump_json(value: object) -> bytes:
    """The single wire encoder: the exact bytes a response or a webhook carries.

    Key order is whatever the producing code chose, because that order is part
    of the vendor's documented shape. The separators reproduce
    ``JSON.stringify``'s compact form byte for byte, and ``ensure_ascii=False``
    keeps non-ASCII text as UTF-8 rather than expanding it to ``\\uXXXX`` --
    which matters beyond aesthetics, because a webhook signature is computed
    over these bytes.
    """
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
    """Drop keys whose value is ``None`` so response bodies match vendor examples.

    The reference's one-line equivalent is ``compact()`` in ``util/json.ts``:
    "Remove undefined values so response bodies match vendor examples exactly."
    Python has no ``undefined``, so ``None`` carries that meaning here and this
    is the point at which it is erased.

    Shallow, like the reference: it strips one level, and a nested projection
    calls it for itself. Unlike the reference it returns a new dict rather than
    mutating and returning its argument, because the reference's in-place form
    exists only to satisfy TypeScript's ``T extends Record<...>`` and mutating
    a caller's mapping is a bug waiting for a caller who did not expect it.
    """
    out: dict[str, _T] = {}
    for key, value in obj.items():
        if value is not None:
            out[key] = value
    return out
