"""The capability registry: the declared set, which are usable, and the
answer a consumer gets reaching for one switched off. INVARIANT: an off
route is NOT hidden -- it answers ``capability_disabled``, naming the
capability, blocker and profile. ``blocked_by`` is three-way (usable / off
itself / a prerequisite off) and checks only the immediate dotted parent,
since :meth:`CapabilityRegistry.disable` removes every descendant already.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from vendorfake.core.kernel.types import CapabilityDecl, Route, UnitError, UnitErrorKind
from vendorfake.core.util.json import compact

__all__ = [
    "CONTROL_CAPABILITY",
    "CapabilityRegistry",
    "CapabilityView",
    "apply_capability_delta",
]

CONTROL_CAPABILITY = "__control"
"""Auto-declared, always enabled, and filtered from every listing, so it
cannot be switched off."""

_CONTROL_DECL = CapabilityDecl(name=CONTROL_CAPABILITY, summary="Unit control plane (always on).")


@dataclass(frozen=True, slots=True)
class CapabilityView:
    """One row of ``GET /__unit/capabilities``. ``blocked_by`` is present
    only when the blocker is some other capability."""

    name: str
    summary: str
    enabled: bool
    kind: str
    requires: tuple[str, ...]
    routes: tuple[str, ...]
    blocked_by: str | None = None

    def as_json(self) -> dict[str, object]:
        """The wire shape. ``blocked_by`` is dropped when absent, not emitted as ``null``."""
        return compact(
            {
                "name": self.name,
                "summary": self.summary,
                "enabled": self.enabled,
                "kind": self.kind,
                "requires": list(self.requires),
                "routes": list(self.routes),
                "blocked_by": self.blocked_by,
            }
        )


class CapabilityRegistry:
    __slots__ = ("_declared", "_enabled", "_profile_name", "_routes_by_capability")

    def __init__(
        self,
        decls: Iterable[CapabilityDecl],
        routes: Iterable[Route],
        enabled: Iterable[str],
        profile_name: str = "default",
    ) -> None:
        self._profile_name = profile_name
        #: Declaration order is preserved; ``view()`` reports in it.
        self._declared: dict[str, CapabilityDecl] = {}
        self._routes_by_capability: dict[str, list[str]] = {}
        self._enabled: set[str] = {CONTROL_CAPABILITY}

        for decl in decls:
            self.declare(decl)
        self.declare(_CONTROL_DECL)
        for route in routes:
            self._routes_by_capability.setdefault(route.capability, []).append(route.key)
        self.set_enabled(enabled)

    # -- declaration --------------------------------------------------------

    def declare(self, decl: CapabilityDecl) -> None:
        """A repeated name replaces the earlier one in place, so redeclaring
        cannot reorder the view."""
        self._declared[decl.name] = decl

    def is_declared(self, name: str) -> bool:
        return name in self._declared

    def names(self) -> tuple[str, ...]:
        """Declared names in declaration order, without the control capability."""
        return tuple(n for n in self._declared if n != CONTROL_CAPABILITY)

    def declaration(self, name: str) -> CapabilityDecl | None:
        return self._declared.get(name)

    def routes_for(self, name: str) -> tuple[str, ...]:
        """Route keys owned by ``name``. A ``behavior`` capability owns none."""
        return tuple(self._routes_by_capability.get(name, ()))

    # -- the profile it is resolving against --------------------------------

    @property
    def profile(self) -> str:
        return self._profile_name

    def set_profile_name(self, name: str) -> None:
        self._profile_name = name

    # -- the enabled set ----------------------------------------------------

    def set_enabled(self, names: Iterable[str]) -> None:
        """Replace the enabled set. Unknown names are rejected loudly."""
        wanted = list(names)
        for name in wanted:
            if name not in self._declared:
                declared = self.names()
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"Unknown capability {name!r}. Declared: {', '.join(declared)}.",
                    field="capabilities",
                    info={"declared": list(declared)},
                )
        self._enabled = {*wanted, CONTROL_CAPABILITY}

    def enable(self, name: str) -> None:
        """Add one name, re-validating the whole set."""
        self.set_enabled([n for n in (*self._enabled, name) if n != CONTROL_CAPABILITY])

    def disable(self, name: str) -> None:
        """Remove one name, its dotted descendants, and its direct dependents
        -- so :meth:`blocked_by` only has to look one level up."""
        remaining = [
            n
            for n in self._enabled
            if n != CONTROL_CAPABILITY and n != name and not n.startswith(f"{name}.") and name not in self._requires(n)
        ]
        self.set_enabled(remaining)

    def apply_delta(self, expr: str) -> None:
        """Apply a ``+a,-b`` delta -- or an absolute list -- to the enabled set."""
        self.set_enabled(apply_capability_delta(self.enabled_names(), expr))

    def enabled_names(self) -> tuple[str, ...]:
        """The enabled set, sorted, without the control capability."""
        return tuple(sorted(n for n in self._enabled if n != CONTROL_CAPABILITY))

    # -- resolution ---------------------------------------------------------

    def _requires(self, name: str) -> Sequence[str]:
        decl = self._declared.get(name)
        return () if decl is None else decl.requires

    def blocked_by(self, name: str) -> str | None:
        """Why ``name`` is unusable, or ``None``: not enabled -> itself; else
        the dotted parent if not enabled; else the first unmet ``requires``.
        """
        if name not in self._enabled:
            return name
        parent = name.rsplit(".", 1)[0] if "." in name else None
        if parent is not None and parent in self._declared and parent not in self._enabled:
            return parent
        for required in self._requires(name):
            if required not in self._enabled:
                return required
        return None

    def is_enabled(self, name: str) -> bool:
        return self.blocked_by(name) is None

    def assert_enabled(self, name: str, route_key: str | None = None) -> None:
        """Raise ``capability_disabled`` unless ``name`` is usable. Names the
        capability and the profile."""
        blocker = self.blocked_by(name)
        if blocker is None:
            return
        if blocker == name:
            because = f"Capability {name!r} is disabled in profile {self._profile_name!r}."
        else:
            because = (
                f"Capability {name!r} is unavailable because its prerequisite "
                f"{blocker!r} is disabled in profile {self._profile_name!r}."
            )
        raise UnitError(
            UnitErrorKind.CAPABILITY_DISABLED,
            detail=(
                f"{because} Enable it in the profile, in VENDORFAKE_CAPABILITIES, or with POST /__unit/capabilities."
            ),
            info=compact(
                {
                    "kind": "capability_disabled",
                    "capability": name,
                    "blocked_by": blocker,
                    "profile": self._profile_name,
                    "route": route_key,
                    "enabled": list(self.enabled_names()),
                }
            ),
        )

    def view(self) -> tuple[CapabilityView, ...]:
        rows: list[CapabilityView] = []
        for name in self.names():
            decl = self._declared[name]
            blocker = self.blocked_by(name)
            rows.append(
                CapabilityView(
                    name=name,
                    summary=decl.summary,
                    enabled=blocker is None,
                    kind=decl.kind,
                    requires=tuple(decl.requires),
                    routes=self.routes_for(name),
                    blocked_by=blocker if blocker is not None and blocker != name else None,
                )
            )
        return tuple(rows)

    def declarations(self) -> Mapping[str, CapabilityDecl]:
        """The declared set, control capability included. Read-only by contract."""
        return dict(self._declared)


def apply_capability_delta(base: Sequence[str], expr: str) -> list[str]:
    """Parse ``+webhooks,-webhooks.chaos`` or the absolute list ``oauth,orders``.
    Any ``+``/``-`` part makes the whole expression a delta; otherwise it
    replaces the set. Order is the base's, with additions appended.
    """
    parts = [part.strip() for part in expr.split(",") if part.strip()]
    if not any(part.startswith(("+", "-")) for part in parts):
        return parts
    result = list(base)
    for part in parts:
        if part.startswith("-"):
            dropped = part[1:]
            result = [n for n in result if n != dropped]
        else:
            added = part.removeprefix("+")
            if added not in result:
                result.append(added)
    return result
