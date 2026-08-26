"""The vocabulary every conformance check and every runner shares.

FOR: stating what a contract *is* -- an id, a name, a precondition and a
function that drives a unit and returns evidence -- in a module that imports
nothing but the standard library, so the shape of the suite survives being
read by someone who has never seen this codebase.

INVARIANT: **a check asserts with :func:`require`, never with ``assert``.**
``python -O`` strips ``assert`` statements. A conformance suite that silently
stopped asserting would report a green run over a unit it never examined,
which is the worst failure mode this project has; ``tools/boundary_check.py``
bans the statement across the whole package so the rule cannot be observed
here and forgotten in a check.

SECOND INVARIANT: **an unmet precondition is a SKIP, never a PASS.** A profile
that switches a capability off makes some contracts unaskable. Reporting those
as passes is how a suite comes to certify behaviour nothing exercised. The
runner turns them into skips with the reason printed, and the report refuses
to be ``ok`` when a check skipped everywhere -- see
:mod:`vendorfake.conformance.report`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - types only
    from contextlib import AbstractContextManager

    from vendorfake.conformance.client import ConformanceClient
    from vendorfake.conformance.env import CheckEnv

__all__ = [
    "CheckFn",
    "CheckSpec",
    "ConformanceFailure",
    "ConformanceSkip",
    "ConformanceTarget",
    "Outcome",
    "Requires",
    "require",
]


class Outcome(StrEnum):
    """What one check said about one (profile, transport)."""

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


class ConformanceFailure(AssertionError):
    """A contract was violated.

    The message names the file or the declaration to change. "assertion
    failed" at three in the morning is worth nothing.
    """


class ConformanceSkip(Exception):
    """A precondition is absent, so the contract could not be asked.

    Never a pass. Under ``--strict`` an *undeclared* skip is a failure; the
    declared ones live in ``manifest.json`` as data.
    """


def require(condition: object, message: str) -> None:
    """Assert ``condition``, or raise :class:`ConformanceFailure`.

    ``message`` must name what to change -- a file, a declaration, a route --
    not merely what was observed. Every caller in this package is written that
    way and reviewers should keep it so.
    """
    if not condition:
        raise ConformanceFailure(message)


@dataclass(frozen=True, slots=True)
class Requires:
    """What a check needs the unit to have before it can say anything.

    Every field is resolved at runtime by asking the control plane -- never by
    a per-profile skip list in code, which is a place for a lie to hide and
    which drifts from the profile the moment either changes.
    """

    #: At least one enabled, non-internal route to send a request to.
    surface_route: bool = False
    #: At least one enabled, non-internal route that is a POST or a PUT.
    mutating_route: bool = False
    #: The vendor declares a webhook signer.
    signer: bool = False
    #: The signer names the delivery headers its signature occupies.
    signature_headers: bool = False
    #: The vendor declares at least one state machine.
    machines: bool = False
    #: The seed scenario loaded at least one entity.
    seed: bool = False
    #: The ``chaos`` capability -- whatever the core calls it -- is enabled.
    chaos: bool = False
    #: The vendor declares an in-band (magic-value) trigger.
    in_band_trigger: bool = False
    #: Webhook delivery is switched on and its capability is enabled.
    webhooks: bool = False
    #: The delivery sink is the in-memory one, so its answers can be programmed.
    memory_sink: bool = False
    #: The target can open a second, out-of-process client for the same unit.
    both_transports: bool = False


CheckFn = Callable[["CheckEnv"], str]
"""A check: given an environment, drive the unit and return its evidence.

The return value is prose describing what was actually observed -- route
counts, the digest, the status sequence -- so that a green run says what it
proved rather than merely that it finished.
"""


@dataclass(frozen=True, slots=True)
class CheckSpec:
    """One registered contract."""

    #: Stable public identifier, ``C01`` upward. Never reused, never renumbered.
    id: str
    name: str
    #: One sentence: what this check asserts. Printed by ``--list``.
    asserts: str
    requires: Requires
    fn: CheckFn


@dataclass(frozen=True, slots=True)
class ConformanceTarget:
    """What a vendor points the suite at. The entire external contract.

    ``open_client(profile, transport)`` MUST yield a client for a *freshly
    constructed* unit on every call: determinism checks build two, and every
    check gets its own so that check order is never load-bearing.

    The target owns what a transport means. That is what keeps this package
    free of any web framework: an ``"http"`` transport is a client against a
    base URL -- a server someone else started, or a container -- and never a
    server this package knows how to run.
    """

    name: str
    open_client: Callable[[str, str], AbstractContextManager[ConformanceClient]]
    profiles: Sequence[str] = field(default=("full",))
    transports: Sequence[str] = field(default=("inprocess",))
