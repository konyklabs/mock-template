"""vendorfake.agent.explain -- looking up and rendering one thing a unit knows about.

FOR: the ``vendorfake explain`` subcommand, and nothing else. Every lookup
here goes through the same channel a running unit would answer -- the control
plane's ``GET /__unit/routes`` and ``GET /__unit/errors``, or the same
in-memory tables ``vendorfake routes``/``faults``/``profiles`` already read --
so an answer here can never be a second, hand-maintained copy of a fact the
rest of the package already publishes. There is deliberately no sixth lookup
that reaches into a vendor module directly: if it is not on the wire somewhere,
``explain`` does not know it either, which is the same discipline the control
plane itself is built on (see ``core/control/plane.py``'s module docstring).

Every ``explain_*`` function raises ``ValueError`` naming the thing it could
not find and listing what it *could* have found, in the style
``vendorfake.registry.resolve_vendor`` already established: a typo is a
refusal that lists the real answers, not a silent ``None``. ``vendorfake.cli``
is the only caller and turns that into a ``SystemExit``, exactly as it already
does for every ``ValueError`` a registry lookup raises.
"""

from __future__ import annotations

import json as _json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "explain_error",
    "explain_fault",
    "explain_header",
    "explain_profile",
    "explain_route",
    "render_error",
    "render_fault",
    "render_header",
    "render_profile",
    "render_route",
]


def _check_profile(vendor: str, profile: str) -> None:
    """Refuse an unknown ``profile`` before a unit is built from it.

    ``create_unit`` would eventually raise ``vendorfake.core.kernel.types.UnitError``
    for a bad profile -- naming an absolute filesystem path, not the vendor's
    real profile names, and not a ``ValueError`` this module's callers (and
    ``cli.py:_explain``) know how to turn into a clean refusal. Checked here,
    the same way ``explain_error`` checks ``kind`` against
    ``UnitErrorKind`` before calling ``create_unit`` at all, in the same
    message shape :func:`explain_profile` already uses for a bad profile
    name.
    """
    from vendorfake.registry import available_profiles

    offered = sorted(row.name for row in available_profiles(vendor))
    if profile not in offered:
        raise ValueError(f"no profile named {profile!r} for vendor {vendor!r}. Available: {', '.join(offered)}")


# ---------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------


def explain_route(vendor: str, profile: str, operation_id: str) -> dict[str, Any]:
    """One row of ``GET /__unit/routes``, matched by ``operation_id``.

    Read through a real unit's control plane rather than
    ``vendorfake.registry.routes``: that function trims ``auth`` off every row
    for its own reasons (see ``registry.RouteInfo``'s docstring), and ``auth``
    is exactly what this command promises.

    A route carries no structured doc-citation field today -- vendor
    documentation citations live in comments beside each ``Route(...)``
    construction, not on the ``Route`` dataclass itself -- so this never emits
    one. Nothing here loses information if that changes: a future ``citation``
    key would arrive from the same ``GET /__unit/routes`` row this reads.
    """
    from vendorfake.core.transport.inprocess import in_process
    from vendorfake.registry import create_unit

    _check_profile(vendor, profile)
    built = create_unit(vendor=vendor, profile=profile)
    try:
        response = in_process(built).get("/__unit/routes")
        table = _json.loads(response.text)["routes"]
    finally:
        built.stop()

    for row in table:
        if row.get("operation_id") == operation_id:
            return dict(row)
    offered = sorted({row["operation_id"] for row in table if row.get("operation_id")})
    raise ValueError(
        f"no route with operation_id {operation_id!r} on {vendor!r} (profile {profile!r}). "
        f"Available: {', '.join(offered) if offered else '(none)'}"
    )


def render_route(row: Mapping[str, Any]) -> str:
    lines = [
        f"{row['method']} {row['path']}",
        f"  operation_id : {row.get('operation_id') or '(none)'}",
        f"  capability   : {row['capability']}",
        f"  auth         : {row.get('auth') or '(none -- unauthenticated)'}",
    ]
    if row.get("summary"):
        lines.append(f"  summary      : {row['summary']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# fault
# ---------------------------------------------------------------------------


def explain_fault(name: str) -> dict[str, Any]:
    """One entry of ``core.chaos.rules.BUILTIN_FAULTS``, matched by name.

    Read from the catalogue itself rather than from
    ``core.chaos.faults.FAULT_PARAM_KEYS``/``FAULT_DESCRIPTIONS``: those two
    are *derived* from ``BUILTIN_FAULTS`` (see their docstrings) and carry no
    ``provenance``, which this command promises alongside params and
    description.
    """
    from vendorfake.core.chaos.rules import BUILTIN_FAULTS

    for spec in BUILTIN_FAULTS:
        if spec.name == name:
            return spec.as_json()
    offered = ", ".join(spec.name for spec in BUILTIN_FAULTS)
    raise ValueError(f"no fault named {name!r}. Available: {offered}")


def render_fault(data: Mapping[str, Any]) -> str:
    lines = [
        str(data["name"]),
        f"  scope      : {data['scope']}",
        f"  provenance : {data['provenance']}",
        f"  summary    : {data['summary']}",
    ]
    if data.get("params"):
        lines.append(f"  params     : {data['params']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------


def explain_profile(vendor: str, name: str) -> dict[str, Any]:
    """One entry of ``vendorfake.registry.available_profiles(vendor)``.

    A bad ``vendor`` surfaces as ``available_profiles``'s own ``ValueError``
    (via ``resolve_vendor``), naming the real vendors -- not reworded here,
    so the two callers of a bad vendor name (this and ``vendorfake profiles``)
    say the identical thing.
    """
    from vendorfake.registry import available_profiles

    found = available_profiles(vendor)
    for row in found:
        if row.name == name:
            return {
                "vendor": row.vendor,
                "name": row.name,
                "summary": row.summary,
                "capabilities": list(row.capabilities),
                "seed": row.seed,
            }
    offered = ", ".join(row.name for row in found)
    raise ValueError(f"no profile named {name!r} for vendor {vendor!r}. Available: {offered}")


def render_profile(data: Mapping[str, Any]) -> str:
    capabilities = data.get("capabilities") or []
    return "\n".join(
        [
            f"{data['vendor']}/{data['name']}",
            f"  summary      : {data['summary']}",
            f"  capabilities : {', '.join(capabilities) if capabilities else '(none)'}",
            f"  seed         : {data.get('seed') or '(none)'}",
        ]
    )


# ---------------------------------------------------------------------------
# error
# ---------------------------------------------------------------------------


def explain_error(vendor: str, profile: str, kind: str) -> dict[str, Any]:
    """One row of ``GET /__unit/errors``'s ``kinds`` array, matched by ``kind``.

    ``kind`` is checked against :class:`~vendorfake.core.kernel.types.UnitErrorKind`
    before a unit is even built, so a typo is refused with the real twenty
    names rather than a route-shaped 404 from the control plane itself.
    ``profile`` is checked the same way, by :func:`_check_profile`.
    """
    from vendorfake.core.kernel.types import UnitErrorKind
    from vendorfake.core.transport.inprocess import in_process
    from vendorfake.registry import create_unit

    valid = {member.value for member in UnitErrorKind}
    if kind not in valid:
        raise ValueError(f"no error kind named {kind!r}. Available: {', '.join(sorted(valid))}")

    _check_profile(vendor, profile)
    built = create_unit(vendor=vendor, profile=profile)
    try:
        response = in_process(built).get("/__unit/errors")
        rows = _json.loads(response.text)["kinds"]
    finally:
        built.stop()

    for row in rows:
        if row["kind"] == kind:
            return dict(row)
    # Unreachable: every vendor's ErrorShaper.assert_error_table_total() (see
    # core/kernel/shaping.py) refuses at import time to leave any of the
    # twenty kinds unmapped, which runs long before create_unit above could
    # have returned a unit at all.
    raise AssertionError(  # pragma: no cover - guarded by assert_error_table_total at import time
        f"{vendor!r}'s error table is missing {kind!r} despite passing its own totality check"
    )


def render_error(data: Mapping[str, Any]) -> str:
    body = data.get("body")
    body_text = body if isinstance(body, str) else _json.dumps(body, sort_keys=True)
    headers = data.get("headers") or {}
    return "\n".join(
        [
            str(data["kind"]),
            f"  status     : {data['status']}",
            f"  provenance : {data['provenance']}",
            f"  headers    : {', '.join(sorted(headers)) if headers else '(none)'}",
            f"  body       : {body_text}",
        ]
    )


# ---------------------------------------------------------------------------
# header
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _HeaderInfo:
    """One ``Vendorfake-*`` header, catalogued for :func:`explain_header`."""

    name: str
    summary: str
    present_when: str

    def as_json(self) -> dict[str, str]:
        return {"name": self.name, "summary": self.summary, "present_when": self.present_when}


def _display(header: str) -> str:
    """``vendorfake-near-miss`` -> ``Vendorfake-Near-Miss``.

    HTTP header names are case-insensitive on the wire, so the constants this
    reads from spell some in lower-case and some in Title-Case (see
    ``core/kernel/shaping.py`` and ``core/kernel/nearmiss.py``). Every doc and
    docstring in this package that writes one out for a person uses
    Title-Case, so that is the one spelling this command matches on lookup and
    prints on output.
    """
    return "-".join(part.capitalize() for part in header.split("-"))


def _headers() -> tuple[_HeaderInfo, ...]:
    from vendorfake.core.kernel.nearmiss import NEAR_MISS_HEADER
    from vendorfake.core.kernel.shaping import (
        ERROR_FIELD_HEADER,
        ERROR_INFO_HEADER,
        ERROR_KIND_HEADER,
        STATUS_PROVENANCE_HEADER,
    )

    sidecar_when = "on a refusal, when this unit's errors.sidecar is 'headers' (the default) or 'both'"
    return (
        _HeaderInfo(
            _display(ERROR_KIND_HEADER),
            "The UnitErrorKind the refusal was raised with, e.g. 'rate_limited'.",
            sidecar_when,
        ),
        _HeaderInfo(
            _display(STATUS_PROVENANCE_HEADER),
            "Whether the vendor documents this status for this error kind ('documented'), or this "
            "project chose it ('judgment').",
            sidecar_when,
        ),
        _HeaderInfo(
            _display(ERROR_FIELD_HEADER),
            "The request field the error is about, in the vendor's dot notation; percent-encoded "
            "outside ASCII printable punctuation.",
            sidecar_when + ", and only when the error names a field",
        ),
        _HeaderInfo(
            _display(ERROR_INFO_HEADER),
            "UnitError.info as ASCII-escaped JSON -- the same machine-readable context the 'body' "
            "sidecar form would carry under the unit_error key.",
            sidecar_when + ", and only when the error carries info",
        ),
        _HeaderInfo(
            _display(NEAR_MISS_HEADER),
            "A compact JSON array of the closest routes to a request that matched none, best match "
            "first: {route, score, operation_id}.",
            "on any response to a request that matched no route, over HTTP (served or container) -- "
            "in process the same information is an UnmatchedRequest exception by default",
        ),
    )


def explain_header(name: str) -> dict[str, str]:
    key = _display(name)
    for info in _headers():
        if info.name == key:
            return info.as_json()
    offered = ", ".join(info.name for info in _headers())
    raise ValueError(f"no header named {name!r}. Available: {offered}")


def render_header(data: Mapping[str, str]) -> str:
    return "\n".join(
        [
            data["name"],
            f"  present when : {data['present_when']}",
            f"  {data['summary']}",
        ]
    )
