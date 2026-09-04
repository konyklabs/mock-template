"""Linear segment routing over ``{name}`` path templates.

Turns a method and a path into the one :class:`Route` that answers it plus the parameters that path carried, and
otherwise distinguishes "no such path" from "that path, wrong verb".

INVARIANT: **the control-plane namespace belongs to the core.** ``/__unit/`` is where a unit exposes itself, so a
vendor route beginning with it would shadow or be shadowed by machinery every consumer relies on, depending only on
registration order. :meth:`Router.add` raises at construction and names the offending route.

A malformed percent-escape is a bad request: :func:`percent_decode` raises ``invalid_value`` on ``path``, which the
vendor's shaper turns into a 400. ``provenance: judgment``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote

from vendorfake.core.kernel.types import Route, UnitError, UnitErrorKind

__all__ = [
    "INTERNAL_PATH_PREFIX",
    "Match",
    "MatchOutcome",
    "MethodNotAllowed",
    "NoRoute",
    "Router",
    "is_control_path",
    "percent_decode",
    "split_path",
]

INTERNAL_PATH_PREFIX: Final = "/__unit/"
"""Reserved for the unit's own control plane."""

#: A percent escape is exactly ``%`` followed by two hex digits. Anything else
#: -- ``%zz``, a trailing ``%``, ``%4`` -- is malformed.
_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


def split_path(path: str) -> list[str]:
    """``/v2/orders/x`` -> ``['v2', 'orders', 'x']``; empty segments dropped,
    so ``/v2//orders`` and ``/v2/orders/`` match the same route."""
    return [segment for segment in path.split("/") if segment]


def percent_decode(segment: str, *, path: str) -> str:
    """Decode one path segment, raising ``invalid_value`` on anything malformed: a bad escape (``%zz``) caught by the
    regex, and an escape decoding to invalid UTF-8 (``%C3%28``) caught by ``errors="strict"``."""
    if _ESCAPE.search(segment):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"The path contains a malformed percent-escape: {segment!r}.",
            field="path",
            info={"path": path, "segment": segment},
        )
    try:
        return unquote(segment, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"The path segment {segment!r} is not valid percent-encoded UTF-8.",
            field="path",
            info={"path": path, "segment": segment},
        ) from exc


@dataclass(frozen=True, slots=True)
class Match:
    """The route that answers, and the parameters the path carried."""

    route: Route
    params: dict[str, str]


@dataclass(frozen=True, slots=True)
class MethodNotAllowed:
    """The path exists; this verb does not. ``allowed`` is sorted and unique."""

    allowed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NoRoute:
    """Nothing in the table has this shape."""


MatchOutcome = Match | MethodNotAllowed | NoRoute
"""Three outcomes, not two: ``405`` and ``404`` are different answers."""


@dataclass(frozen=True, slots=True)
class _Compiled:
    route: Route
    segments: tuple[str, ...]
    param_names: tuple[str, ...]


def _is_param(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}") and len(segment) > 2


class Router:
    """The route table, in registration order. First match wins."""

    __slots__ = ("_compiled",)

    def __init__(self, routes: Iterable[Route] = ()) -> None:
        self._compiled: list[_Compiled] = []
        for route in routes:
            self.add(route)

    def add(self, route: Route) -> None:
        """Compile and append one route, refusing a reserved vendor path.
        Checked here so every path into the table passes the same gate."""
        if not route.internal and is_control_path(route.path):
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=(
                    f"Route {route.key!r} is not allowed: {INTERNAL_PATH_PREFIX!r} is reserved for the "
                    "unit's own control plane, which every consumer reaches at a fixed address."
                ),
                field="routes",
                info={"route": route.key, "reserved_prefix": INTERNAL_PATH_PREFIX},
            )
        segments = tuple(split_path(route.path))
        self._compiled.append(
            _Compiled(
                route=route,
                segments=segments,
                param_names=tuple(s[1:-1] for s in segments if _is_param(s)),
            )
        )

    def routes(self) -> tuple[Route, ...]:
        """Every registered route, in registration order."""
        return tuple(c.route for c in self._compiled)

    def param_names(self, route: Route) -> tuple[str, ...]:
        """The ``{name}`` placeholders in ``route``, in path order."""
        for compiled in self._compiled:
            if compiled.route is route:
                return compiled.param_names
        return ()

    def match(self, method: str, path: str) -> MatchOutcome:
        """Find the route for ``method`` and ``path``. The scan collects every path-shaped candidate before giving up,
        so a 405's ``allowed`` list is complete, and returns on the first method match, so registration order
        decides between two routes of the same shape."""
        wanted = split_path(path)
        path_matches: list[_Compiled] = []
        for compiled in self._compiled:
            if len(compiled.segments) != len(wanted):
                continue
            params: dict[str, str] = {}
            ok = True
            for template, got in zip(compiled.segments, wanted, strict=True):
                if _is_param(template):
                    params[template[1:-1]] = percent_decode(got, path=path)
                elif template != got:
                    ok = False
                    break
            if not ok:
                continue
            path_matches.append(compiled)
            if compiled.route.method.upper() == method.upper():
                return Match(route=compiled.route, params=params)
        if path_matches:
            return MethodNotAllowed(allowed=tuple(sorted({c.route.method.upper() for c in path_matches})))
        return NoRoute()


def is_control_path(path: str) -> bool:
    """True for the control-plane namespace: the bare prefix or anything under it. Bare ``/__unit`` counts -- it
    matches no registered route but is still the observer's own traffic rather than a vendor 404."""
    return path == INTERNAL_PATH_PREFIX.rstrip("/") or path.startswith(INTERNAL_PATH_PREFIX)


def assert_no_reserved_paths(routes: Sequence[Route]) -> None:
    """Raise if any non-internal route claims the control-plane namespace: the rule :meth:`Router.add` enforces, for a
    caller holding a list, not a router."""
    offenders = [route.key for route in routes if not route.internal and is_control_path(route.path)]
    if not offenders:
        return
    raise UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f"{INTERNAL_PATH_PREFIX!r} is reserved for the control plane; offending routes: {', '.join(offenders)}.",
        field="routes",
        info={"routes": offenders, "reserved_prefix": INTERNAL_PATH_PREFIX},
    )
