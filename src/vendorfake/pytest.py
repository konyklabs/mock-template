"""The ``vendorfake`` pytest plugin: one marker and three fixtures, nothing else.

Registered as a single ``pytest11`` entry point. The marker names what to build
and takes the same arguments as :func:`vendorfake.testing.unit`, read in one
place, :func:`_marker_arguments`; ``vendorfake_unit`` builds it for a synchronous
test, ``vendorfake_async_unit`` for an ``async def`` one, and
``vendorfake_webhook_receiver`` is the other half of a webhook test. There are no
CLI options, no session hook and no autouse fixture, so a suite that requests
nothing runs identically with ``-p no:vendorfake``, and
``vendorfake.conformance.plugin`` is named explicitly rather than auto-loaded.

``vendorfake.testing`` is imported inside the fixtures, this module loading into
every pytest session. The async fixture is a synchronous function yielding an
object that owns an ``httpx.AsyncClient``, which is what makes it work under
pytest-asyncio in either mode and under anyio with no dependency on either.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vendorfake.testing import Seed, StartedUnit, WebhookReceiver

__all__ = ["MARKER", "vendorfake_async_unit", "vendorfake_unit", "vendorfake_webhook_receiver"]

MARKER = "vendorfake"
"""The marker every fixture in this module reads."""

MARKER_NAME = MARKER

MARKER_LINE = (
    "vendorfake(vendor, profile=None, env=None, seed=None, clock_start=None, unmatched=None, "
    "capabilities=None): the unit vendorfake_unit and vendorfake_async_unit build and yield as a "
    "StartedUnit. Same arguments as vendorfake.testing.unit()."
)

_KEYWORDS = ("profile", "env", "seed", "clock_start", "unmatched", "capabilities")


def pytest_configure(config: pytest.Config) -> None:
    """Register the marker, so ``--strict-markers`` does not reject it."""
    config.addinivalue_line("markers", MARKER_LINE)


def _marker_arguments(request: pytest.FixtureRequest, fixture: str) -> tuple[str, dict[str, Any]]:
    """What the marker on this test asked for, as ``unit()`` takes it, via
    ``get_closest_marker`` so a marker on the test wins. An unmarked test and an
    unknown keyword are both hard failures rather than skips."""
    marker = request.node.get_closest_marker(MARKER)
    if marker is None:
        pytest.fail(
            f"{request.node.nodeid} requested {fixture} with no @pytest.mark.{MARKER}(...) on it. "
            f"Add one naming a vendor: @pytest.mark.{MARKER_LINE}",
            pytrace=False,
        )
    arguments = list(marker.args)
    options: dict[str, Any] = dict(marker.kwargs)
    if len(arguments) > 2:
        pytest.fail(
            f"@pytest.mark.{MARKER}(...) on {request.node.nodeid} takes at most two positional "
            f"arguments -- vendor, then profile -- and got {len(arguments)}. Pass the rest as "
            f"keywords: @pytest.mark.{MARKER_LINE}",
            pytrace=False,
        )
    if arguments:
        vendor = str(arguments[0])
        if len(arguments) > 1:
            options.setdefault("profile", str(arguments[1]))
    else:
        vendor = str(options.pop("vendor", ""))
    if not vendor:
        pytest.fail(
            f"@pytest.mark.{MARKER}(...) on {request.node.nodeid} names no vendor. @pytest.mark.{MARKER_LINE}",
            pytrace=False,
        )
    unknown = sorted(set(options) - set(_KEYWORDS))
    if unknown:
        pytest.fail(
            f"@pytest.mark.{MARKER}(...) on {request.node.nodeid} names unknown keyword(s) {unknown}. "
            f"@pytest.mark.{MARKER_LINE}",
            pytrace=False,
        )
    if "unmatched" in options:
        # The same refusal ``unit()`` makes, as a setup failure that names the
        # test: ``unmatched=True`` is the slip ``Driver.requests`` invites.
        from vendorfake.testing import checked_unmatched

        try:
            checked_unmatched(options["unmatched"])
        except ValueError as exc:
            pytest.fail(f"@pytest.mark.{MARKER}(...) on {request.node.nodeid}: {exc}", pytrace=False)
    return vendor, options


@pytest.fixture
def vendorfake_unit(request: pytest.FixtureRequest) -> Iterator[StartedUnit[Seed]]:
    """A :class:`~vendorfake.testing.StartedUnit` from this test's marker, built
    fresh per test because ids are deterministic per unit. Yielded as
    ``StartedUnit[Seed]``, the vendor arriving as a runtime marker argument; call
    :func:`vendorfake.testing.unit` with the literal for a narrowed seed."""
    from vendorfake.testing import unit

    vendor, options = _marker_arguments(request, "vendorfake_unit")
    with unit(vendor, **options) as started:
        yield started


@pytest.fixture
def vendorfake_async_unit(request: pytest.FixtureRequest) -> Iterator[StartedUnit[Seed]]:
    """The same unit as :func:`vendorfake_unit`, driven through
    :attr:`~vendorfake.testing.StartedUnit.async_client` in an ``async def`` test."""
    from vendorfake.testing import unit

    vendor, options = _marker_arguments(request, "vendorfake_async_unit")
    with unit(vendor, **options) as started:
        yield started


@pytest.fixture
def vendorfake_webhook_receiver() -> Iterator[WebhookReceiver]:
    """A real HTTP receiver on loopback, function-scoped: the same object
    :func:`vendorfake.testing.webhook_receiver` yields."""
    from vendorfake.testing import webhook_receiver

    with webhook_receiver() as receiver:
        yield receiver
