"""The vocabulary conformance checks and runners share: an id, a name, a precondition, and a function that drives a unit and returns evidence. Asserts use :func:`require`, never ``assert``; an unmet precondition is a SKIP, never a PASS."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - types only
    from contextlib import AbstractContextManager

    from vendorfake.conformance.client import ConformanceClient
    from vendorfake.conformance.env import CheckEnv

__all__ = [
    "CheckFn",
    "CheckSpec",
    "ConformanceError",
    "ConformanceFailure",
    "ConformanceSkip",
    "ConformanceTarget",
    "Outcome",
    "Requires",
    "require",
]


class Outcome(StrEnum):
    """What one check said about one (profile, transport). ``ERROR`` means the unit never got far enough to be asked; ``FAIL`` means it was asked and answered. Both are red, but only ``FAIL`` names a contract to fix."""

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"


class ConformanceFailure(AssertionError):
    """A contract was violated. The message names the file or declaration to change."""


class ConformanceError(Exception):
    """The unit could not be driven far enough for any contract to be asked. Raised where a unit is constructed or reached, never inside a check body."""


class ConformanceSkip(Exception):
    """A precondition is absent, so the contract could not be asked -- never a pass. Under ``--strict`` an undeclared skip is a failure; declared ones live in ``manifest.json`` as data."""


def require(condition: object, message: str) -> None:
    """Assert ``condition``, or raise :class:`ConformanceFailure`. ``message`` must name what to change -- a file, a declaration, a route -- not merely what was observed."""
    if not condition:
        raise ConformanceFailure(message)


@dataclass(frozen=True, slots=True)
class Requires:
    """What a check needs the unit to have before it can say anything. Every field is resolved at runtime against the control plane, never a per-profile skip list in code."""

    surface_route: bool = False
    mutating_route: bool = False
    signer: bool = False
    signature_headers: bool = False
    machines: bool = False
    seed: bool = False
    chaos: bool = False
    in_band_trigger: bool = False
    webhooks: bool = False
    memory_sink: bool = False
    both_transports: bool = False
    auth_route: bool = False
    credentials: bool = False
    example_body: bool = False
    mutating_example: bool = False
    idempotent_example: bool = False
    #: ...and some OTHER route also declares an idempotency spec (two ops, one key).
    two_idempotent_routes: bool = False
    paginated_route: bool = False
    webhooks_chaos: bool = False
    virtual_clock: bool = False
    out_of_process: bool = False
    #: A unit built with a partial seed document layered over the profile's (``ConformanceTarget.open_with_seed_overlay``).
    seed_overlay: bool = False


CheckFn = Callable[["CheckEnv"], str]
"""A check: drive the unit, return a short description of what was observed."""


@dataclass(frozen=True, slots=True)
class CheckSpec:
    """One registered contract."""

    id: str
    name: str
    #: One sentence: what this check asserts. Printed by ``--list``.
    asserts: str
    requires: Requires
    fn: CheckFn


@dataclass(frozen=True, slots=True)
class ConformanceTarget:
    """What a vendor points the suite at -- the entire external contract. ``open_client(profile, transport)`` MUST yield a client for a freshly constructed unit on every call, so check order is never load-bearing. An ``"http"`` transport is a client against a base URL this package never runs itself."""

    name: str
    open_client: Callable[[str, str], AbstractContextManager[ConformanceClient]]
    profiles: Sequence[str] = field(default=("full",))
    transports: Sequence[str] = field(default=("inprocess",))
    #: Transports whose units run in a separate OS process.
    out_of_process: Sequence[str] = field(default=())
    #: Path parameters naming the TENANT a credential is scoped to, filled with the seeded value (e.g. Clover's ``/merchants/{mId}/...``).
    path_params: Mapping[str, str] = field(default_factory=dict)
    #: Check id -> profiles where a skip is expected and permanent for this target. ``None`` means the committed manifest's matrix.
    expected_skips: Mapping[str, Sequence[str]] | None = None
    #: Builds a unit with ``overlay`` laid over the profile's seed and yields a client onto it. ``None`` if the target cannot build one, in which case that contract skips instead of passing unmeasured.
    open_with_seed_overlay: Callable[[str, Mapping[str, Any]], AbstractContextManager[ConformanceClient]] | None = None
    #: Check id -> why this vendor can never be asked it (e.g. no documented idempotency key), the escape from the anti-vacuity rule.
    inapplicable: Mapping[str, str] = field(default_factory=dict)
