"""The pytest plugin: a started unit per test, named by a marker.

FOR: the consumer who wants a fake in their suite and not a fixture library to
learn. ``pip install vendorfake`` registers this module through the ``pytest11``
entry point, so the fixtures below exist in their suite with nothing imported
and no ``conftest.py`` written::

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

STREAM NOTE: this module currently holds only the **async** fixture. The
synchronous one -- ``vendorfake_unit`` -- lands with the sync-fixture stream and
belongs in this same module, under the same marker; the marker registration and
:func:`_marker_arguments` below are written to serve both and should not be
duplicated when it arrives.

WHY THIS FILE IMPORTS PYTEST, WHEN ``vendorfake.conformance.plugin`` DOES NOT.
That module is a plugin written without importing pytest on purpose:
``tools/boundary.toml`` allows the conformance package exactly one third-party
dependency, because the whole claim of the suite is that a contract talks to a
control plane and nothing else, so it must stay runnable by a consumer with no
test runner installed. None of that applies here. This module *is* the pytest
integration -- a fixture cannot be declared without ``@pytest.fixture``, and
anyone importing it has pytest by definition. The precedent is followed where
its reason holds and not where it does not.

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
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vendorfake.testing import StartedUnit

__all__ = ["MARKER", "vendorfake_async_unit"]

MARKER = "vendorfake"
"""The marker every fixture in this module reads.

One marker for the whole plugin, not one per fixture: a test that switches from
the synchronous fixture to the async one is switching how it calls the unit,
not which unit it wants, and a second marker name would make that a two-line
edit with one line easy to forget.
"""

_MARKER_HELP = (
    "vendorfake(vendor, *, profile='full', env=None, seed=None): build a vendorfake unit for this "
    "test and hand it to the vendorfake_* fixtures. Same arguments as vendorfake.testing.unit()."
)

_UNMARKED = (
    "the {fixture!r} fixture needs a @pytest.mark.{marker}(...) on the test saying which vendor to "
    "build, e.g. @pytest.mark.{marker}('square'). The marker takes the same arguments as "
    "vendorfake.testing.unit(): vendor, profile, env, seed."
)


def pytest_configure(config: Any) -> None:
    """Register the marker so ``--strict-markers`` suites accept it.

    ``config`` is untyped for the same reason the conformance plugin's hooks
    are: pytest's hook objects are not part of a stable public type surface,
    and annotating them against a version would date this file faster than the
    behaviour changes.
    """
    config.addinivalue_line("markers", _MARKER_HELP)


def _marker_arguments(request: Any, fixture: str) -> tuple[str, dict[str, Any]]:
    """What the marker on this test asked for, as ``unit()`` takes it.

    ``get_closest_marker`` rather than a scan, so a marker on the test wins over
    one on its class or module -- the same precedence pytest gives every other
    marker, which is what a reader will assume without checking.

    An unmarked test is a hard failure and not a skip. A skip would be a green
    run in which the test never happened, and the mistake this catches --
    reaching for the fixture and forgetting the marker -- is one a consumer
    makes once and should be told about once.
    """
    marker = request.node.get_closest_marker(MARKER)
    if marker is None:
        pytest.fail(_UNMARKED.format(fixture=fixture, marker=MARKER), pytrace=False)
    arguments = list(marker.args)
    options = dict(marker.kwargs)
    if arguments:
        vendor = str(arguments[0])
        if len(arguments) > 1:
            options.setdefault("profile", str(arguments[1]))
    else:
        vendor = str(options.pop("vendor", ""))
    if not vendor:
        pytest.fail(
            f"@pytest.mark.{MARKER} needs a vendor: @pytest.mark.{MARKER}('square').",
            pytrace=False,
        )
    return vendor, options


@pytest.fixture
def vendorfake_async_unit(request: Any) -> Iterator[StartedUnit]:
    """A started unit for this test, driven through ``async_client``.

    Function-scoped, and that is a contract rather than a default: a unit's ids
    are deterministic *per unit*, so two tests sharing one would see the second
    continue the first one's id stream and its store. Per test is the only
    scope under which an id assertion means the same thing every run.

    Yields :class:`vendorfake.testing.StartedUnit`, so ``seed``, the
    control-plane helpers (``add_chaos_rule``, ``reset``, ``drain``,
    ``advance_clock``) and the synchronous ``client`` are all there beside
    ``async_client``. Set-up in a synchronous call and the code under test on
    the async one is the ordinary shape, and it is one object.

    ``vendorfake.testing`` is imported here rather than at module scope. This
    module is loaded by every pytest session on a machine where vendorfake is
    installed, including the suites that never use it, and the import pulls in
    ``httpx``, the vendor registry and the profile loader. The same argument
    ``pyproject.toml`` records for keeping FastAPI out of ``vendorfake --help``.
    """
    from vendorfake.testing import unit

    vendor, options = _marker_arguments(request, "vendorfake_async_unit")
    with unit(vendor, **options) as started:
        yield started
