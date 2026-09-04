"""Declarative entity lifecycles: a vendor declares the legal states and moves as data
and the core enforces them, raising a core-generic error the vendor's shaper rewords.

Two invariants. **A self-transition is illegal unless the state declares it** with
:attr:`StateDef.allow_self`, so paying an already-paid order cannot silently pay it
twice; and **terminality is derived, never stored**, a state being terminal exactly when
it can move nowhere. Two error kinds stay distinct: ``invalid_value`` for a target that
is not a state of this machine, ``invalid_transition`` for one not reachable from here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = ["MachineDef", "StateDef", "StateMachine"]


@dataclass(frozen=True, slots=True)
class StateDef:
    summary: str = ""
    #: States this one may move to. Empty means terminal.
    to: tuple[str, ...] = ()
    #: Whether re-declaring this state is a legal no-op.
    allow_self: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "to", tuple(self.to))
        if not self.to and self.allow_self:
            raise ValueError("a terminal state (no outbound transitions) cannot allow a self-transition")

    @property
    def terminal(self) -> bool:
        return not self.to


@dataclass(frozen=True, slots=True)
class MachineDef:
    #: Entity field holding the state value, e.g. ``"state"``.
    field: str
    initial: str
    states: Mapping[str, StateDef]

    def __post_init__(self) -> None:
        if not self.field:
            raise ValueError("a machine needs the name of the entity field holding its state")
        states = dict(self.states)
        if not states:
            raise ValueError(f"machine on field {self.field!r} declares no states")
        if self.initial not in states:
            raise ValueError(f"initial state {self.initial!r} is not declared; declared: {sorted(states)}")
        for name, state in states.items():
            for target in state.to:
                if target not in states:
                    raise ValueError(
                        f"state {name!r} lists an undeclared transition target {target!r}; declared: {sorted(states)}"
                    )
        object.__setattr__(self, "states", MappingProxyType(states))


class StateMachine:
    """Enforces one :class:`MachineDef`. Holds no entity and no store."""

    __slots__ = ("definition",)

    def __init__(self, definition: MachineDef) -> None:
        self.definition = definition

    @property
    def field(self) -> str:
        return self.definition.field

    @property
    def initial(self) -> str:
        return self.definition.initial

    def states(self) -> list[str]:
        """Declared state names, in declaration order (not sorted)."""
        return list(self.definition.states)

    def is_terminal(self, state: str) -> bool:
        """Whether ``state`` can move nowhere. An undeclared state is not terminal;
        :meth:`assert_transition` answers for it with ``invalid_value``."""
        declared = self.definition.states.get(state)
        return declared is not None and declared.terminal

    def can_transition(self, from_state: str, to_state: str) -> bool:
        """Whether the move is legal. A self-transition needs ``allow_self``."""
        declared = self.definition.states.get(from_state)
        if declared is None:
            return False
        if from_state == to_state:
            return declared.allow_self
        return to_state in declared.to

    def assert_transition(self, from_state: str, to_state: str, subject: str) -> None:
        """Raise unless the move is legal: ``invalid_value`` if the target is not a state of
        this machine, carrying every state that is; ``invalid_transition`` if it is real but
        unreachable from here, carrying only what is reachable."""
        if to_state not in self.definition.states:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"'{to_state}' is not a valid {self.field} for {subject}.",
                field=self.field,
                info={"allowed": self.states()},
            )
        if self.can_transition(from_state, to_state):
            return
        terminal = self.is_terminal(from_state)
        declared = self.definition.states.get(from_state)
        raise UnitError(
            UnitErrorKind.INVALID_TRANSITION,
            detail=(
                f"{subject} is in the terminal {self.field} {from_state} and cannot be updated."
                if terminal
                else f"{subject} cannot move from {from_state} to {to_state}."
            ),
            field=self.field,
            info={
                "from": from_state,
                "to": to_state,
                "terminal": terminal,
                "allowed": list(declared.to) if declared is not None else [],
            },
        )

    def assert_mutable(self, from_state: str, subject: str) -> None:
        """Refuse **any** mutation of an entity in a terminal state, not just a state change.
        Callers run this before every write, and before :meth:`assert_transition`."""
        if self.is_terminal(from_state):
            raise UnitError(
                UnitErrorKind.INVALID_TRANSITION,
                detail=f"{subject} is in the terminal {self.field} {from_state} and cannot be updated.",
                field=self.field,
                info={"from": from_state, "terminal": True},
            )

    def describe(self) -> dict[str, Any]:
        """The machine as ``GET /__unit/machines`` publishes it, built here so ``terminal``
        is derived in one place."""
        return {
            "field": self.field,
            "initial": self.initial,
            "states": {
                name: {
                    "summary": state.summary,
                    "to": list(state.to),
                    "allow_self": state.allow_self,
                    "terminal": state.terminal,
                }
                for name, state in self.definition.states.items()
            },
        }
