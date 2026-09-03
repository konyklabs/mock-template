"""The vendor-neutral tail of every error table.

FOR: the things a vendor's ``ErrorShaper`` does that have nothing to do with
that vendor's envelope -- the ``unit_error`` sidecar (as a body key or as
headers), the two mechanism headers, and the import-time check that its table
is total -- so that a vendor writes its table and its envelope and nothing
else. Square and Clover each carried all of this, line for line, before this
module.

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

**Where it rides is a separate switch.** Until konyklabs/roadmap#71 the sidecar
was always a ``unit_error`` body key, which put it inside a contract Square's
and Toast's own envelopes are documented enough to be asserted on -- a
consumer substituting a recorded real response for this fake's would see one
extra field the real vendor never sends. ``errors.sidecar`` (profile-level,
default ``"headers"``, env ``VENDORFAKE_ERROR_SIDECAR``) now says whether the
same dict :func:`unit_error_sidecar` builds rides as the ``unit_error`` body
key (``"body"``, the v0.1 behaviour, DEPRECATED), as :func:`sidecar_headers`'
four ``Vendorfake-*`` headers (``"headers"``, the default), or both.

The headers
-----------
``retry-after`` on a 429, when the vendor's switch is on, from the chaos
rule's ``retry_after_seconds`` or a one-second fallback (the reference's
``Number(info.retryAfterSeconds ?? 1)``); ``x-unit-capability`` on a 501,
naming the capability that was off. A vendor adds its own documented headers
around these -- Clover's ``X-RateLimit-*`` set, for one. The four
``Vendorfake-*`` sidecar headers are a fourth thing this module builds, on the
same ``errors.sidecar`` switch described above rather than unconditionally.

**Every ``Vendorfake-Error-*`` header value is ASCII by construction.**
``UnitError.info`` is consumer-supplied text reaching the wire verbatim -- a
chaos rule's own ``id`` (:mod:`vendorfake.core.chaos.faults`), a store entity
id (:mod:`vendorfake.core.state.store`), a pydantic ``loc`` path that for an
extra-forbidden key is the consumer's own key name
(:func:`vendorfake.core.config.models.unit_error_from_validation`) -- and none
of it is guaranteed ASCII. A header value is not: httpx encodes one as ASCII
building the in-process response and raises outright on anything outside it;
the ASGI stack encodes one as Latin-1 and turns the refusal this module was
shaping into an unrelated 500 for anything above ``U+00FF``. So
:data:`ERROR_INFO_HEADER` is JSON built with ``ensure_ascii=True`` -- not
:func:`~vendorfake.core.util.json.dump_json`, whose ``ensure_ascii=False`` is
right for wire-body and webhook-signature bytes and wrong here -- and
:data:`ERROR_FIELD_HEADER` percent-encodes anything outside ASCII printable
punctuation as UTF-8 per RFC 3986, so a consumer reading it back with
``urllib.parse.unquote`` gets the original string. This was a regression: the
identical request under ``errors.sidecar: body`` never had the problem,
because a JSON *body* is free to carry UTF-8 and only the *header* form needs
this rule.
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

Provenance = Literal["documented", "judgment"]
"""Where a row's HTTP status comes from. A real field, surfaced on the wire."""

#: The ``retry-after`` value a rate-limited response carries when the header
#: is on and the chaos rule supplied no interval. One second.
DEFAULT_RETRY_AFTER = "1"

#: The four headers :func:`sidecar_headers` emits. ``Vendorfake-`` rather than
#: the existing ``x-unit-`` prefix (:data:`~vendorfake.core.kernel.unit.REQUEST_ID_HEADER`-style,
#: and ``x-unit-error`` itself, stamped by ``Unit._shape`` on every refusal
#: regardless of the sidecar) because these four carry the sidecar's *content*
#: -- an opt-in, switchable document -- where ``x-unit-error`` is a mechanism
#: header the kernel always sends; giving the two the same prefix would make a
#: consumer's header allow-list unable to tell "always there" from
#: "only when the sidecar is on" apart.
ERROR_KIND_HEADER = "Vendorfake-Error-Kind"
STATUS_PROVENANCE_HEADER = "Vendorfake-Status-Provenance"
ERROR_FIELD_HEADER = "Vendorfake-Error-Field"
ERROR_INFO_HEADER = "Vendorfake-Error-Info"

#: Every ASCII printable character except ``%``, as :func:`urllib.parse.quote`'s
#: ``safe`` set for :data:`ERROR_FIELD_HEADER`. Letters and digits are already
#: unreserved as far as ``quote`` is concerned; naming the rest here is what
#: keeps ordinary punctuation in a field path (``order.line_items[0].uid``)
#: untouched. ``%`` is deliberately excluded from ``safe`` -- it must stay
#: percent-encoded itself, or a literal ``%`` followed by two hex digits would
#: be misread on the way back through ``urllib.parse.unquote``.
_FIELD_HEADER_SAFE = "".join(chr(code) for code in range(0x21, 0x7F) if chr(code) != "%")


def _ascii_json(value: object) -> str:
    """The extra sidecar keys, as one JSON header value guaranteed ASCII.

    Deliberately not :func:`~vendorfake.core.util.json.dump_json`: that
    encoder's ``ensure_ascii=False`` is right for the wire body and the
    webhook-signature bytes it exists for, and wrong for a header, which every
    HTTP stack this project touches encodes as ASCII or Latin-1 rather than
    UTF-8. ``ensure_ascii=True`` escapes non-ASCII text and keys alike as
    ``\\uXXXX``, which is what makes the result safe to hand a client's header
    dict unchanged.
    """
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def header_text(value: str) -> str:
    """Free text as a header value -- ``field``, a chaos rule id, anything a consumer typed: ASCII passes through untouched, anything
    else is percent-encoded UTF-8 (RFC 3986) via :data:`_FIELD_HEADER_SAFE`.

    ``field`` is free text rather than JSON already -- a chaos rule id, a
    store entity id, a pydantic ``loc`` segment -- so it needs its own escape
    rather than :func:`_ascii_json`'s.
    """
    return quote(value, safe=_FIELD_HEADER_SAFE)


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
    reserved keys, then whatever the vendor adds, ``None`` values dropped.

    **``UnitError.info`` is a published channel, and this is why.** Every key
    in it reaches the wire verbatim, deliberately -- a consumer debugging this
    fake gets the machine-readable reason without parsing prose, and a
    vendor's own override travels here too. Nothing filters it, so nothing
    internal may be put in it: a flag the core wants to send a shaper travels
    as an argument to :meth:`ErrorShaper.shape`, where the type checker sees
    it and the wire does not. This is a rule rather than a filter on purpose;
    a reserved-prefix convention would be one more thing to remember at every
    future call site, and stripping keys here would make ``info`` mean
    something different depending on where you read it.
    """
    return compact(
        {
            **dict(err.info or {}),
            "kind": err.kind.value,
            "status_provenance": provenance,
            **extra,
        }
    )


def sidecar_headers(sidecar: Mapping[str, Any]) -> dict[str, str]:
    """:func:`unit_error_sidecar`'s dict, reshaped as headers.

    FOR: ``errors.sidecar: "headers"`` (the default since konyklabs/roadmap#71)
    and ``"both"``. One place builds these from the sidecar dict so every
    vendor's ``errors.py`` calls it instead of each writing ``x-vendorfake-*``
    headers by hand -- exactly the reasoning that put :func:`unit_error_sidecar`
    itself here rather than in each vendor's table.

    Four headers, not one per ``info`` key: :data:`ERROR_KIND_HEADER` and
    :data:`STATUS_PROVENANCE_HEADER` carry the two keys every sidecar has;
    :data:`ERROR_FIELD_HEADER` carries ``field`` only when a vendor supplied
    one (Square's own body already names the field, so its sidecar never
    does); everything else -- every ``info`` key and any further vendor extra
    -- is one compact JSON document in :data:`ERROR_INFO_HEADER`, omitted
    when there is nothing left to put in it. A header per ``info`` key would
    make the header *set* vary with the error, which is a worse contract for
    a consumer's client than one header whose *value* varies.

    Every value here is ASCII, by construction rather than by luck: see "the
    headers" in the module docstring for why a header cannot carry what a
    JSON body can. :data:`ERROR_FIELD_HEADER` goes through
    :func:`_field_header_value` and :data:`ERROR_INFO_HEADER` through
    :func:`_ascii_json`; :data:`ERROR_KIND_HEADER` and
    :data:`STATUS_PROVENANCE_HEADER` need neither, because a
    :class:`~vendorfake.core.kernel.types.UnitErrorKind` value and a
    :data:`Provenance` literal are closed, always-ASCII vocabularies.
    """
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
