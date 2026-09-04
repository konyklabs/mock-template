"""The ``vendorfake`` pytest plugin: one marker and three fixtures, nothing else.

FOR: driving a unit from pytest with no fixture of your own to write. Every
consumer suite gets this by installing vendorfake -- registered as a single
``pytest11`` entry point, ``vendorfake`` -- and it does exactly four things: a
marker that names what to build, ``vendorfake_unit`` that builds it for a
synchronous test, ``vendorfake_async_unit`` that builds the same unit for an
``async def`` test, and ``vendorfake_webhook_receiver`` for the other half of
a webhook test. Nothing else: no CLI options, no session hook, no autouse
fixture. Installing this plugin is inert for a suite that never requests a
fixture -- a consumer suite with no marker and no fixture request runs
identically with the plugin loaded and with ``-p no:vendorfake``, which is
what ``tests/unit/testing/test_pytest_plugin.py`` proves with ``pytester``::

    import pytest


    @pytest.mark.vendorfake("square")
    async def test_the_refresh_path(vendorfake_async_unit):
        seed = vendorfake_async_unit.seed
        answered = await vendorfake_async_unit.async_client.post(
            "/oauth2/token",
            json={
                "client_id": seed.application_id,
                "client_secret": seed.application_secret,
                "grant_type": "refresh_token",
                "refresh_token": seed.refresh_token,
            },
        )
        assert answered.status_code == 200

ONE MARKER FOR THE WHOLE PLUGIN, not one per fixture: a test that switches
from the synchronous fixture to the async one is switching how it calls the
unit, not which unit it wants, and a second marker name would make that a
two-line edit with one line easy to forget. The marker takes the same
arguments as :func:`vendorfake.testing.unit` -- ``vendor``, then ``profile``,
``env``, ``seed``, ``clock_start``, ``unmatched``, ``capabilities`` -- and
:func:`_marker_arguments` is the one place they are read.

Since konyklabs/roadmap#71 (D3) this is the *only* ``pytest11`` entry point
vendorfake registers. Before, installing the wheel also auto-loaded
``vendorfake.conformance.plugin`` -- five ``--conformance-*`` options and a
``pytest_sessionfinish`` hook that could flip a totally unrelated suite's exit
code -- into every consumer's pytest run, whether or not that suite had ever
heard of the conformance registry. That plugin still exists and still works;
it is loaded explicitly now, which is the shape a consumer choosing to *run*
the conformance suite already uses::

    pytest --pyargs vendorfake.conformance -p vendorfake.conformance.plugin --conformance-target mypkg:target

Both flags, not either: ``-p`` is what loads the plugin, and therefore what
registers ``--conformance-target`` and wires up the ``conformance_case``
fixture; ``--pyargs vendorfake.conformance`` only *selects* the tests, and
nothing under ``src/vendorfake/conformance/`` auto-loads the plugin when they
are collected (there is no ``conftest.py`` there, deliberately -- see
``conformance/plugin.py``'s "WHY THIS FILE IMPORTS NO PYTEST"). Run the
selection without ``-p`` and pytest rejects ``--conformance-target`` as an
unrecognised argument, or, bare, fails at ``fixture 'conformance_case' not
found``. The README and ``tools/self-test.sh`` show the same pair.

WHY ``vendorfake.testing`` IS IMPORTED INSIDE THE FIXTURES. This module is
loaded into every pytest session on a machine with vendorfake installed, most
of which never touch a unit. ``vendorfake.testing`` pulls in httpx, the vendor
registry and the profile loader (about 37 ms measured on the reference
laptop, see konyklabs/roadmap#68's review); paying that only in the session
that actually requests a fixture is what "inert when unused" means, and the
``pytester`` inertness test would not distinguish the two. Type-only imports
stay at module scope under ``TYPE_CHECKING``.

WHY THE ASYNC FIXTURE IS A SYNCHRONOUS FUNCTION. It yields an object that owns
an ``httpx.AsyncClient``; it is not itself a coroutine. That is what makes it
work under ``pytest-asyncio`` in strict mode, under ``pytest-asyncio`` in auto
mode, and under ``anyio``'s plugin, without this package depending on any of
them or guessing which one is installed. An ``async def`` fixture would need
each of those runners' own decorator -- ``pytest_asyncio.fixture`` in strict
mode, a plain ``pytest.fixture`` under anyio -- and the two are not the same
object, so a module that picked one would break the other's users at collection
time. A unit is built synchronously (``Unit.handle`` is a plain ``def``) and
:attr:`StartedUnit.async_client` binds to no event loop, so there is nothing
the fixture would gain by being awaited.

``import pytest`` below is the real third-party package, not this module: a
bare ``import`` in Python 3 is always absolute, so a module named
``vendorfake/pytest.py`` importing ``pytest`` does not import itself. Pytest
only ever imports this module through its plugin manager, which means pytest
is already the process running -- so, unlike ``vendorfake.conformance``
(``tools/boundary.toml`` holds it to one third-party import, ``httpx``, so its
framework-free CLI never pays for a test runner it does not need), this
module carries no such constraint and imports pytest freely, the way every
``pytest11`` plugin does.
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
    """Register the marker, so ``--strict-markers`` in a consumer suite does
    not reject it. The only hook this module defines besides the fixtures --
    no CLI options, no session hook."""
    config.addinivalue_line("markers", MARKER_LINE)


def _marker_arguments(request: pytest.FixtureRequest, fixture: str) -> tuple[str, dict[str, Any]]:
    """What the marker on this test asked for, as ``unit()`` takes it.

    ``get_closest_marker`` rather than a scan, so a marker on the test wins over
    one on its class or module -- the same precedence pytest gives every other
    marker, which is what a reader will assume without checking.

    An unmarked test is a hard failure and not a skip. A skip would be a green
    run in which the test never happened, and the mistake this catches --
    reaching for the fixture and forgetting the marker -- is one a consumer
    makes once and should be told about once. The same goes for an unknown
    keyword: a typo in ``profile=`` must not become a silent ``full``.
    """
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
        # The same refusal ``unit()`` makes, surfaced as a pytest failure at
        # setup so it names the test: ``unmatched=True`` here is the slip
        # ``Driver.requests(unmatched=...)`` invites, and it used to turn
        # strict mode off silently (konyklabs/roadmap#99, item 1).
        from vendorfake.testing import checked_unmatched

        try:
            checked_unmatched(options["unmatched"])
        except ValueError as exc:
            pytest.fail(f"@pytest.mark.{MARKER}(...) on {request.node.nodeid}: {exc}", pytrace=False)
    return vendor, options


@pytest.fixture
def vendorfake_unit(request: pytest.FixtureRequest) -> Iterator[StartedUnit[Seed]]:
    """A :class:`~vendorfake.testing.StartedUnit` built from this test's
    ``@pytest.mark.vendorfake(...)``, for a synchronous test.

    Function-scoped and built fresh per test, the same grain as
    :func:`vendorfake.testing.unit` itself: a unit's ids are deterministic
    *per unit*, so two tests sharing one would see the second continue the
    first one's id stream and its store. Per test is the only scope under
    which an id assertion means the same thing every run.

    Yielded as ``StartedUnit[Seed]`` -- the structural seed, not a vendor's
    own -- because the vendor arrives here as a runtime marker argument, so
    there is nothing for a checker to narrow on. A test that wants
    ``seed.merchant_id`` to type-check calls :func:`vendorfake.testing.unit`
    with the vendor literal instead; that is what the overloads on it are for.
    """
    from vendorfake.testing import unit

    vendor, options = _marker_arguments(request, "vendorfake_unit")
    with unit(vendor, **options) as started:
        yield started


@pytest.fixture
def vendorfake_async_unit(request: pytest.FixtureRequest) -> Iterator[StartedUnit[Seed]]:
    """The same unit as :func:`vendorfake_unit`, for an ``async def`` test:
    drive it through :attr:`~vendorfake.testing.StartedUnit.async_client`.

    Yields the same :class:`~vendorfake.testing.StartedUnit`, so ``seed``,
    the control-plane helpers (``add_chaos_rule``, ``reset``, ``drain``,
    ``advance_clock``, ``requests``) and the synchronous ``client`` are all
    there beside ``async_client``. Set-up in a synchronous call and the code
    under test on the async one is the ordinary shape, and it is one object.
    See the module docstring for why this fixture is itself synchronous.
    """
    from vendorfake.testing import unit

    vendor, options = _marker_arguments(request, "vendorfake_async_unit")
    with unit(vendor, **options) as started:
        yield started


@pytest.fixture
def vendorfake_webhook_receiver() -> Iterator[WebhookReceiver]:
    """A real HTTP receiver on loopback, function-scoped, for the other half
    of a webhook test -- the same object :func:`vendorfake.testing.webhook_receiver`
    yields, so ``receiver.url`` and ``receiver.received`` need no new
    vocabulary here."""
    from vendorfake.testing import webhook_receiver

    with webhook_receiver() as receiver:
        yield receiver
