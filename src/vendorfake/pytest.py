"""The ``vendorfake`` pytest plugin: a marker and two fixtures, nothing else.

FOR: driving a unit from pytest with no fixture of your own to write. Every
consumer suite gets this by installing vendorfake -- registered as a single
``pytest11`` entry point, ``vendorfake`` -- and it does exactly three things: a
marker that names what to build, ``vendorfake_unit`` that builds it, and
``vendorfake_webhook_receiver`` for the other half of a webhook test. Nothing
else: no CLI options, no session hook, no autouse fixture. Installing this
plugin is inert for a suite that never requests either fixture -- a consumer
suite with no marker and no fixture request runs identically with the plugin
loaded and with ``-p no:vendorfake``, which is what
``tests/unit/testing/test_pytest_plugin.py`` proves with ``pytester``.

Since konyklabs/roadmap#71 (D3) this is the *only* ``pytest11`` entry point
vendorfake registers. Before, installing the wheel also auto-loaded
``vendorfake.conformance.plugin`` -- five ``--conformance-*`` options and a
``pytest_sessionfinish`` hook that could flip a totally unrelated suite's exit
code -- into every consumer's pytest run, whether or not that suite had ever
heard of the conformance registry. That plugin still exists and still works;
it is loaded explicitly now (``-p vendorfake.conformance.plugin``, or the
``--pyargs vendorfake.conformance`` form the README shows), which is the shape
a consumer choosing to *run* the conformance suite already uses.

``import pytest`` below is the real third-party package, not this module: a
bare ``import`` in Python 3 is always absolute, so a module named
``vendorfake/pytest.py`` importing ``pytest`` does not import itself. Pytest
only ever imports this module through its plugin manager, which means pytest
is already the process running -- so, unlike ``vendorfake.conformance``
(``tools/boundary.toml`` holds it to one third-party import, ``httpx``, so its
framework-free CLI never pays for a test runner it does not need), this
module carries no such constraint and imports pytest freely, the way every
``pytest11`` plugin does.

Stream A adds ``vendorfake_async_unit`` to this module for the async seam; if
this file already exists in your worktree at rebase time, merge additively --
its marker and hooks are shared with that fixture.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Any

import pytest

from vendorfake.testing import StartedUnit, WebhookReceiver, unit, webhook_receiver

__all__ = ["vendorfake_unit", "vendorfake_webhook_receiver"]

MARKER_NAME = "vendorfake"
MARKER_LINE = (
    "vendorfake(vendor, profile='full', env=None, seed=None, clock_start=None): "
    "the unit vendorfake_unit builds and yields as a StartedUnit"
)


def pytest_configure(config: pytest.Config) -> None:
    """Register the marker, so ``--strict-markers`` in a consumer suite does
    not reject it. The only hook this module defines besides the two
    fixtures -- no CLI options, no session hook."""
    config.addinivalue_line("markers", MARKER_LINE)


@pytest.fixture
def vendorfake_unit(request: pytest.FixtureRequest) -> Iterator[StartedUnit]:
    """A :class:`~vendorfake.testing.StartedUnit` built from this test's
    ``@pytest.mark.vendorfake(...)``.

    Function-scoped and built fresh per test, the same grain as
    :func:`vendorfake.testing.unit` itself: a unit is cheap enough (a few
    milliseconds) that sharing one across tests would only be trading that
    for cross-test state.

    The marker is required, not optional: a fixture request with nothing to
    build it from is a test author's mistake -- the fix is one decorator --
    not a precondition the environment failed to meet, so this fails naming
    the fix rather than skipping.
    """
    marker = request.node.get_closest_marker(MARKER_NAME)
    if marker is None:
        pytest.fail(
            f"{request.node.nodeid} requested vendorfake_unit with no @pytest.mark.{MARKER_NAME}(...) on it. "
            f"Add one naming a vendor: @pytest.mark.{MARKER_LINE}",
            pytrace=False,
        )
    if not marker.args:
        pytest.fail(
            f"@pytest.mark.{MARKER_NAME}(...) on {request.node.nodeid} names no vendor. @pytest.mark.{MARKER_LINE}",
            pytrace=False,
        )
    vendor: str = marker.args[0]
    kwargs: dict[str, Any] = dict(marker.kwargs)
    profile: str = kwargs.pop("profile", "full")
    env: Mapping[str, str] | None = kwargs.pop("env", None)
    seed: int | None = kwargs.pop("seed", None)
    clock_start: datetime | str | None = kwargs.pop("clock_start", None)
    if kwargs:
        pytest.fail(
            f"@pytest.mark.{MARKER_NAME}(...) on {request.node.nodeid} names unknown keyword(s) "
            f"{sorted(kwargs)}. @pytest.mark.{MARKER_LINE}",
            pytrace=False,
        )
    with unit(vendor, profile, env=env, seed=seed, clock_start=clock_start) as started:
        yield started


@pytest.fixture
def vendorfake_webhook_receiver() -> Iterator[WebhookReceiver]:
    """A real HTTP receiver on loopback, function-scoped, for the other half
    of a webhook test -- the same object :func:`vendorfake.testing.webhook_receiver`
    yields, so ``receiver.url`` and ``receiver.received`` need no new
    vocabulary here."""
    with webhook_receiver() as receiver:
        yield receiver
