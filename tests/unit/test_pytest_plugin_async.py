"""The async fixture as a consumer meets it: installed, marked, and nothing else.

Every test here runs pytest inside pytest (``pytester``), on a suite written
the way a consumer's would be -- a file, a marker, a fixture name, no
``conftest.py``, no import of ``vendorfake`` at all. That is the claim being
made in the README, and asserting it from inside this repository's own session
would assert something weaker: our ``pyproject.toml`` already registers the
plugin, our fixtures are already on the path, and a consumer has neither.

``pytest_plugins = ["pytester"]`` is declared here and in no other module. It
is not a fixture library we want session-wide: ``pytester`` monkeypatches the
working directory and the plugin manager for the tests that ask for it, and a
suite that enabled it globally would pay for that everywhere.
"""

from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]

#: ``-p no:asyncio`` on every run that does not mean to exercise it. The two
#: async runners are both installed here -- anyio arrives with httpx, and
#: pytest-asyncio is a dev dependency solely so the two tests below can drive a
#: consumer under it -- and leaving both loaded everywhere would make each run
#: prove less than it says: "works under anyio" is only a claim if the other
#: plugin was not there to answer instead.
ANYIO_ONLY = ("-q", "-p", "no:asyncio")
NO_ASYNC_PLUGINS = ("-q", "-p", "no:asyncio", "-p", "no:anyio")
ASYNCIO = ("-q", "-p", "asyncio", "-p", "no:anyio")

#: pytest-asyncio 1.x warns once per session when this is unset. It is a
#: consumer's setting, not ours, and pinning it in the generated suite keeps
#: the evidence -- the run's own output -- free of a warning about neither
#: vendorfake nor the thing under test.
ASYNCIO_INI = "[pytest]\nasyncio_mode = {mode}\nasyncio_default_fixture_loop_scope = function\n"


CONSUMER_ASYNC = """
import pytest


@pytest.mark.anyio
@pytest.mark.vendorfake("square")
async def test_the_consumer_refreshes_a_token(vendorfake_async_unit):
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
    assert answered.json()["access_token"]
"""


def test_a_consumer_gets_the_fixture_from_the_marker_alone(pytester: pytest.Pytester) -> None:
    """No conftest, no import, no registration. The entry point is the wiring."""
    pytester.makepyfile(test_consumer=CONSUMER_ASYNC)
    pytester.runpytest(*ANYIO_ONLY).assert_outcomes(passed=1)


def test_the_marker_is_registered_so_strict_markers_suites_accept_it(pytester: pytest.Pytester) -> None:
    """A consumer running ``--strict-markers`` -- which this repository does,
    and which any careful suite does -- must not have to declare our marker in
    their own ``pyproject.toml``."""
    pytester.makeini("[pytest]\naddopts = --strict-markers\n")
    pytester.makepyfile(test_consumer=CONSUMER_ASYNC)
    pytester.runpytest(*ANYIO_ONLY).assert_outcomes(passed=1)


def test_the_fixture_needs_no_async_plugin_of_any_kind(pytester: pytest.Pytester) -> None:
    """The fixture is a synchronous function that yields an object owning an
    async client, so it does not care which runner drives the test -- or
    whether one drives it at all.

    That is the property that makes "works under pytest-asyncio in strict mode,
    in auto mode, and under anyio" true by construction rather than by three
    compatibility shims. An ``async def`` fixture would need each runner's own
    decorator, and those are different objects.
    """
    pytester.makepyfile(
        test_consumer="""
        import anyio
        import pytest


        @pytest.mark.vendorfake("clover")
        def test_a_plain_synchronous_test_drives_the_async_client(vendorfake_async_unit):
            seed = vendorfake_async_unit.seed

            async def call():
                return await vendorfake_async_unit.async_client.get(
                    seed.path("/items"), headers=seed.auth
                )

            answered = anyio.run(call)
            assert answered.status_code == 200
        """
    )
    pytester.runpytest(*NO_ASYNC_PLUGINS).assert_outcomes(passed=1)


CONSUMER_ASYNCIO = """
import pytest


@pytest.mark.vendorfake("square")
async def test_the_consumer_reads_locations(vendorfake_async_unit):
    answered = await vendorfake_async_unit.async_client.get(
        "/v2/locations", headers=vendorfake_async_unit.seed.auth
    )
    assert answered.status_code == 200
"""


def test_a_consumer_on_pytest_asyncio_in_auto_mode(pytester: pytest.Pytester) -> None:
    """Auto mode marks every ``async def`` test itself, so the consumer's test
    carries only our marker."""
    pytester.makeini(ASYNCIO_INI.format(mode="auto"))
    pytester.makepyfile(test_consumer=CONSUMER_ASYNCIO)
    pytester.runpytest(*ASYNCIO).assert_outcomes(passed=1)


def test_a_consumer_on_pytest_asyncio_in_strict_mode(pytester: pytest.Pytester) -> None:
    """Strict mode is the default and the harder case: the runner's own marker
    is required on the test, and an ``async def`` *fixture* would additionally
    need ``pytest_asyncio.fixture`` rather than ``pytest.fixture``. Ours is
    synchronous, so there is nothing for the consumer to decorate."""
    pytester.makeini(ASYNCIO_INI.format(mode="strict"))
    pytester.makepyfile(
        test_consumer=CONSUMER_ASYNCIO.replace(
            '@pytest.mark.vendorfake("square")',
            '@pytest.mark.asyncio\n@pytest.mark.vendorfake("square")',
        )
    )
    pytester.runpytest(*ASYNCIO).assert_outcomes(passed=1)


def test_the_marker_carries_profile_env_and_seed_through_to_the_unit(pytester: pytest.Pytester) -> None:
    """The marker's arguments are ``unit()``'s arguments. Anything else would be
    a second vocabulary for the same three settings."""
    pytester.makepyfile(
        test_consumer="""
        import pytest


        @pytest.mark.vendorfake("square", profile="oauth-only", env={"VENDORFAKE_CLOCK": "virtual"}, seed=11)
        def test_the_unit_was_built_as_the_marker_said(vendorfake_async_unit):
            info = vendorfake_async_unit.client.get("/__unit/info").json()
            assert vendorfake_async_unit.profile == "oauth-only"
            assert info["clock"]["mode"] == "virtual"
            assert info["chaos"]["seed"] == 11
        """
    )
    pytester.runpytest(*ANYIO_ONLY).assert_outcomes(passed=1)


def test_the_profile_may_also_be_the_second_positional_argument(pytester: pytest.Pytester) -> None:
    """``unit("square", "oauth-only")`` reads naturally, so the marker takes it
    the same way; a consumer should not have to remember which of the two
    spellings this one accepts."""
    pytester.makepyfile(
        test_consumer="""
        import pytest


        @pytest.mark.vendorfake("square", "oauth-only")
        def test_positional(vendorfake_async_unit):
            assert vendorfake_async_unit.profile == "oauth-only"
        """
    )
    pytester.runpytest(*ANYIO_ONLY).assert_outcomes(passed=1)


def test_a_unit_is_built_per_test_and_not_shared(pytester: pytest.Pytester) -> None:
    """Function scope is a contract, not a default: ids are deterministic *per
    unit*, so two tests sharing one would see the second continue the first's
    id stream and its store."""
    pytester.makepyfile(
        test_consumer="""
        import pytest

        seen = []


        @pytest.mark.vendorfake("square")
        def test_one(vendorfake_async_unit):
            seen.append(vendorfake_async_unit.unit)


        @pytest.mark.vendorfake("square")
        def test_two(vendorfake_async_unit):
            seen.append(vendorfake_async_unit.unit)
            assert seen[0] is not seen[1]
        """
    )
    pytester.runpytest(*ANYIO_ONLY).assert_outcomes(passed=2)


def test_forgetting_the_marker_fails_with_the_instruction(pytester: pytest.Pytester) -> None:
    """A failure and not a skip. A skip would be a green run in which the test
    never happened, and the message has to say what to add, because "fixture
    error" tells a consumer nothing about a marker they have never seen."""
    pytester.makepyfile(
        test_consumer="""
        def test_without_a_marker(vendorfake_async_unit):
            assert False, "unreachable: the fixture fails first"
        """
    )
    result = pytester.runpytest(*ANYIO_ONLY)
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*@pytest.mark.vendorfake*"])
