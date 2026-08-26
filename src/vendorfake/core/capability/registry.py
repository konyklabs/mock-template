"""The capability registry.

FOR: holding the declared capability set, resolving which of them are usable
under the active profile, and producing the one answer a consumer gets when
they reach for something that is switched off.

INVARIANT (ported verbatim from ``packages/core/src/capability/registry.ts``):
*a route whose capability is off is NOT hidden.* Hiding it would make
"disabled" indistinguishable from "this vendor has no such endpoint", which is
exactly the ambiguity that wastes a consumer's afternoon. It answers with an
explicit ``capability_disabled`` error naming the capability, the blocker and
the profile, plus the sentence that says how to turn it back on.

Three resolution rules carry the rest of the weight, and all three are ported
exactly because the wording of the error depends on them:

``blocked_by`` is a three-way answer, not a boolean
    ``None`` (usable), the name itself (this capability is off), or some other
    name (a prerequisite is off). The error text differs in all three cases, so
    the return type stays ``str | None`` rather than collapsing to a bool.

The parent check is **one level**, not every ancestor
    ``blocked_by("a.b.c")`` consults ``a.b`` and stops. If ``a.b`` is enabled
    while ``a`` is not, ``a.b.c`` reads as usable. That is the reference's
    behaviour and it is pinned by test rather than tidied, because "disabling a
    parent implicitly disables its children" is implemented on the *write*
    side: :meth:`CapabilityRegistry.disable` removes every dotted descendant
    when it removes a name, so the state where a grandparent is off and a child
    is on is not reachable through the registry's own API.

An unknown name is rejected loudly
    A typo in a profile or in ``VENDORFAKE_CAPABILITIES`` is a startup failure
    listing the declared set, never a silent no-op that leaves a consumer
    wondering why nothing changed.

Wire-format note: this module emits ``blocked_by``, not the reference's
``blockedBy``. The whole Python surface -- profile documents included -- is
snake_case, and one convention that holds everywhere is worth more than
byte-parity with an oracle that is being replaced.
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
"""The control plane's own capability. Auto-declared, always enabled, and
filtered out of every listing -- a consumer can never switch off the endpoint
they would use to switch things back on."""

_CONTROL_DECL = CapabilityDecl(name=CONTROL_CAPABILITY, summary="Unit control plane (always on).")


@dataclass(frozen=True, slots=True)
class CapabilityView:
    """One row of ``GET /__unit/capabilities``.

    ``blocked_by`` is present only when the blocker is some *other* capability.
    ``enabled=False`` with ``blocked_by=None`` therefore means "this one is off
    in its own right", which is the distinction the error wording needs.
    """

    name: str
    summary: str
    enabled: bool
    kind: str
    requires: tuple[str, ...]
    routes: tuple[str, ...]
    blocked_by: str | None = None

    def as_json(self) -> dict[str, object]:
        """The wire shape. ``blocked_by`` is dropped when absent rather than
        emitted as ``null``, which is this project's absent-means-absent rule."""
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
    """Declared capabilities, the enabled subset, and the route index over them."""

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
        """Add a declaration. A repeated name replaces the earlier one in place,
        keeping its position, so a vendor cannot reorder the view by redeclaring."""
        self._declared[decl.name] = decl

    def is_declared(self, name: str) -> bool:
        return name in self._declared

    def names(self) -> tuple[str, ...]:
        """Declared names in declaration order, without the control capability."""
        return tuple(n for n in self._declared if n != CONTROL_CAPABILITY)

    def declaration(self, name: str) -> CapabilityDecl | None:
        return self._declared.get(name)

    def routes_for(self, name: str) -> tuple[str, ...]:
        """Route keys owned by ``name``, in route-declaration order.

        A ``behavior`` capability owns none: it gates conduct, not surface.
        Conformance asserts that in both directions.
        """
        return tuple(self._routes_by_capability.get(name, ()))

    # -- the profile it is resolving against --------------------------------

    @property
    def profile(self) -> str:
        return self._profile_name

    def set_profile_name(self, name: str) -> None:
        self._profile_name = name

    # -- the enabled set ----------------------------------------------------

    def set_enabled(self, names: Iterable[str]) -> None:
        """Replace the enabled set. Unknown names are rejected loudly.

        A typo in a profile is a startup failure listing what *was* declared,
        not a silent no-op. The control capability is added back unconditionally.
        """
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
        """Remove one name, its dotted descendants, and its direct dependents.

        Disabling a parent implicitly disables its children -- that is enforced
        here, on the write side, which is why :meth:`blocked_by` only has to
        look one level up. ``requires`` is followed one level too: a capability
        that directly lists ``name`` goes with it, but a capability that
        requires *that* one does not, matching the reference exactly.
        """
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
        """The enabled set, sorted by code point, without the control capability.

        Sorted because it is published in an error body and in ``/__unit/info``,
        and an unordered set would make two identical runs produce two
        different documents.
        """
        return tuple(sorted(n for n in self._enabled if n != CONTROL_CAPABILITY))

    # -- resolution ---------------------------------------------------------

    def _requires(self, name: str) -> Sequence[str]:
        decl = self._declared.get(name)
        return () if decl is None else decl.requires

    def blocked_by(self, name: str) -> str | None:
        """Why ``name`` is unusable, or ``None`` when it is usable.

        Resolution order, and it is contract: not in the enabled set -> itself;
        else the *immediate* dotted parent when it is declared and not enabled;
        else the first unmet entry of ``requires``; else ``None``.
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
        """Raise ``capability_disabled`` unless ``name`` is usable.

        The detail names the capability *and* the profile, because "it is off"
        without "off where" sends a consumer looking in the wrong file.
        """
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
        """One row per declared capability, in declaration order."""
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

    If **any** comma-separated part carries a ``+`` or ``-`` the whole
    expression is a delta against ``base``; otherwise it replaces the set with
    the literal list. Mixed expressions do not exist: one sign anywhere makes
    the whole thing a delta, which is the reference's rule and the only one
    that reads unambiguously in a shell.

    Order is the base's order with additions appended, matching the reference's
    ``Set`` semantics (re-adding an existing name does not move it).
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
