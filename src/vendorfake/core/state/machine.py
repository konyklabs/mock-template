"""Declarative entity lifecycles.

FOR: letting a vendor state which values a lifecycle field may hold and which
moves between them are legal, as *data*, and having the core enforce it. The
core raises a core-generic error and the vendor's shaper turns it into that
vendor's wording, so adding a lifecycle to a new vendor is a table, not code.

INVARIANT: **a self-transition is illegal unless the state declares it.** This
is a rebuild, not a port, and this is the reason. The reference
(``packages/core/src/state/machine.ts``) short-circuits twice on identity --
``canTransition`` opens with ``if (from === to) return true;`` and
``assertTransition`` with ``if (from === to) return;`` -- so a state can always
move to itself. The observable consequence in the reference, confirmed by
probe: paying an order that is already COMPLETED returns 200, replaces the
tenders and bumps the version again. That is a double payment that the
lifecycle was there to prevent. ``UpdateOrder`` escapes it only because it
happens to call ``assert_mutable`` first; ``PayOrder`` does not.

Some self-transitions are genuinely legal -- re-sending a state a resource is
already in is an idempotent no-op for an OPEN order -- so the answer is not to
forbid them but to make the vendor *say so*, per state, with
:attr:`StateDef.allow_self`. Silence now means "no", which is the safe reading,
and any vendor that wants the reference's behaviour writes it down where a
reviewer can see it.

Second defect, second rebuild: **terminality is derived, never stored.** The
reference keeps a ``terminal?: boolean`` flag beside the ``to`` list it can
contradict, so ``{terminal: true, to: ['PAID']}`` and ``{terminal: false, to:
[]}`` are both expressible and both meaningless. Here a state is terminal
exactly when it can move nowhere, which is unrepresentable-otherwise rather
than checked-somewhere. What remains checkable is checked at construction:
every ``to`` target and the initial state must be declared, and a terminal
state may not also allow a self-transition.

Everything else carries over unchanged, including the two error kinds and the
detail wording, because the wording is asserted: an unknown target value is
``invalid_value`` with the full ``allowed`` list (the caller sent a typo), a
legal-shaped but forbidden move is ``invalid_transition`` (the caller sent a
real value at the wrong time), and the ``invalid_transition`` detail differs by
terminality so that "you cannot do that yet" and "you cannot do that any more"
do not read the same. :meth:`StateMachine.assert_mutable` refuses **any**
mutation of a terminal entity, not just a state change -- which is why paying
a CANCELED order fails even though nothing about the payment mentions state.
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
    """One state in a lifecycle."""

    #: Prose for ``GET /__unit/machines``; a lifecycle a consumer cannot read
    #: is a lifecycle a consumer will guess at.
    summary: str = ""
    #: States this one may move to. Empty means terminal.
    to: tuple[str, ...] = ()
    #: Whether re-declaring this state is a legal no-op. Defaults to ``False``:
    #: see the module docstring for why silence means no.
    allow_self: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "to", tuple(self.to))
        if not self.to and self.allow_self:
            raise ValueError("a terminal state (no outbound transitions) cannot allow a self-transition")

    @property
    def terminal(self) -> bool:
        """Derived, never stored. A state that can move nowhere is the end."""
        return not self.to


@dataclass(frozen=True, slots=True)
class MachineDef:
    """A lifecycle: which entity field holds it, where it starts, what exists."""

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
        #: Named ``definition`` rather than the reference's ``def``, which is a
        #: Python keyword.
        self.definition = definition

    @property
    def field(self) -> str:
        return self.definition.field

    @property
    def initial(self) -> str:
        return self.definition.initial

    def states(self) -> list[str]:
        """Declared state names, in declaration order.

        Declaration order, not sorted: it is the order the vendor wrote the
        lifecycle in, which reads as the lifecycle, and it is what an
        ``allowed`` list in an error should show a consumer.
        """
        return list(self.definition.states)

    def is_terminal(self, state: str) -> bool:
        """Whether ``state`` can move nowhere. An undeclared state is not
        terminal -- it is not a state at all, and :meth:`assert_transition`
        answers for it with ``invalid_value``."""
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
        """Raise unless the move is legal.

        Two different failures, deliberately kept apart. ``invalid_value``
        means the target is not a state of this machine at all, and carries
        every state that is, because the caller has sent a typo.
        ``invalid_transition`` means the target is real but not reachable from
        here, and carries only what *is* reachable, because the caller has sent
        a real value at the wrong moment. Collapsing the two would tell a
        consumer with a typo to consult a lifecycle diagram, and a consumer
        with a sequencing bug to check their spelling.
        """
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
        """Refuse **any** mutation of an entity in a terminal state.

        Not just a state change: a refunded order does not get new line items
        either. Callers put this before every write, and before
        :meth:`assert_transition` where both apply, so that "this is finished"
        is reported ahead of "this move is not allowed" -- the first explains
        the second.
        """
        if self.is_terminal(from_state):
            raise UnitError(
                UnitErrorKind.INVALID_TRANSITION,
                detail=f"{subject} is in the terminal {self.field} {from_state} and cannot be updated.",
                field=self.field,
                info={"from": from_state, "terminal": True},
            )

    def describe(self) -> dict[str, Any]:
        """The machine as ``GET /__unit/machines`` publishes it.

        Built here rather than in the control plane so that ``terminal`` is
        derived in exactly one place, which is what lets a conformance check
        assert ``terminal == (to == [])`` over the wire and have that mean
        something about the enforcement rather than about the report.
        """
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
