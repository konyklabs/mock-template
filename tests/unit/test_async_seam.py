"""The async half of the in-process seam, and who owns a deliberate delay.

Two claims are under test here and they are separate.

**One transport, both protocols.** An async consumer -- a service that injects
an ``httpx.AsyncClient`` -- gets the same unit, the same base URL and the same
transport instance as a synchronous one, with no ASGI wiring of their own and
no reach into ``vendorfake.asgi``. The proof is that the same flow, asserted by
the same function, passes on ``client`` and on ``async_client`` for every
vendor.

**The binding owns the wait.** The kernel no longer sleeps for a ``timeout``
fault; it reports ``UnitResponse.delay_ms`` and each binding decides what to do
with it. In process that means the client's own read timeout is consulted: a
delay longer than it raises ``httpx.ReadTimeout`` immediately, so a consumer
rehearsing their retry path waits a millisecond rather than the five seconds
they told the fault to take; a delay shorter than it is carried out for real,
so a test asserting "my backoff waited" still measures elapsed time.

The async tests are marked ``anyio`` rather than ``asyncio``. ``anyio``'s pytest
plugin arrives with ``httpx`` and is therefore already installed for every
consumer of this distribution, so the suite adds no test-runner dependency of
its own -- and the transport's own wait is ``anyio.sleep`` for the same reason.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from vendorfake.testing import CloverSeed, SquareSeed, StartedUnit, ToastSeed, UnitTransport, async_unit, unit
from vendorfake.toast.surface.auth import LOGIN_PATH as TOAST_LOGIN_PATH

Seed = SquareSeed | CloverSeed | ToastSeed


@dataclass(frozen=True)
class Renewal:
    """One vendor's "get me a working credential" call.

    Every vendor has one and no two spell it alike: Square refreshes an OAuth
    token, Clover rotates a single-use refresh token, Toast has no refresh at
    all and logs a machine client in. Naming the differences as data is what
    lets the flow itself be asserted once, which is the point of the test --
    the claim is about the transport, not about OAuth.
    """

    vendor: str
    path: str
    #: The request body, built from that vendor's seed.
    body: Callable[[Any], dict[str, Any]]
    #: The credential dug out of the answer, wherever that vendor puts it.
    credential: Callable[[Any], str]


RENEWALS: tuple[Renewal, ...] = (
    Renewal(
        vendor="square",
        path="/oauth2/token",
        body=lambda seed: {
            "client_id": seed.application_id,
            "client_secret": seed.application_secret,
            "grant_type": "refresh_token",
            "refresh_token": seed.refresh_token,
        },
        credential=lambda answer: str(answer["access_token"]),
    ),
    Renewal(
        vendor="clover",
        path="/oauth/v2/refresh",
        body=lambda seed: {"client_id": seed.client_id, "refresh_token": seed.refresh_token},
        credential=lambda answer: str(answer["access_token"]),
    ),
    Renewal(
        vendor="toast",
        path=TOAST_LOGIN_PATH,
        body=lambda seed: {
            "clientId": seed.client_id,
            "clientSecret": seed.client_secret,
            "userAccessType": "TOAST_MACHINE_CLIENT",
        },
        credential=lambda answer: str(answer["token"]["accessToken"]),
    ),
)

IDS = [case.vendor for case in RENEWALS]

MATCH_EVERYTHING: Mapping[str, Any] = {"id": "slow", "scope": "request", "fault": "timeout"}
"""A rule with no ``match`` fires on every non-internal route.

Safe to leave armed: the pipeline short-circuits control-plane routes before
fault selection runs, so the driver can still reset, read and disarm through a
unit that is refusing every vendor call.
"""


def assert_renewed(case: Renewal, status: int, answer: Any) -> str:
    """The whole assertion, so that "the same test body" is literally true.

    Both the synchronous and the asynchronous test call this with what their
    client returned. If the two ever diverge it will be because one of them
    stopped calling this, which is visible in the diff.
    """
    assert status == 200
    credential = case.credential(answer)
    assert credential
    return credential


# ---------------------------------------------------------------------------
# One flow, two clients.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", RENEWALS, ids=IDS)
def test_the_renewal_flow_runs_on_the_synchronous_client(case: Renewal) -> None:
    with unit(case.vendor) as started:
        answered = started.client.post(case.path, json=case.body(started.seed))
        assert_renewed(case, answered.status_code, answered.json())


@pytest.mark.anyio
@pytest.mark.parametrize("case", RENEWALS, ids=IDS)
async def test_the_renewal_flow_runs_on_the_async_client(case: Renewal) -> None:
    with unit(case.vendor) as started:
        answered = await started.async_client.post(case.path, json=case.body(started.seed))
        assert_renewed(case, answered.status_code, answered.json())


@pytest.mark.anyio
async def test_both_clients_address_one_unit_and_one_transport() -> None:
    """Not two units that happen to agree: one unit, reached two ways.

    Asserted through state written by the control plane on the synchronous
    client and observed by the vendor surface on the asynchronous one, because
    a shared *object* proves nothing on its own -- the fixture could hold two
    clients onto two units and every read would still look right until one of
    them was written to.
    """
    with unit("square") as started:
        assert started.async_client.base_url == started.client.base_url
        started.add_chaos_rule({**MATCH_EVERYTHING, "params": {"delay_ms": 0}})

        refused = await started.async_client.get("/v2/locations", headers=started.seed.auth)
        assert refused.status_code == 504
        assert refused.headers["x-unit-error"] == "timeout"

        started.reset_chaos()
        allowed = await started.async_client.get("/v2/locations", headers=started.seed.auth)
        assert allowed.status_code == 200


@pytest.mark.anyio
async def test_the_async_client_is_built_once_and_reused() -> None:
    """Lazy, and then stable. A property that rebuilt the client per access
    would leave a test holding one object while the fixture closed another."""
    with unit("square") as started:
        first = started.async_client
        assert started.async_client is first


# ---------------------------------------------------------------------------
# async_unit(): the same object, from an async context manager.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_async_unit_yields_the_same_type_as_unit() -> None:
    async with async_unit("square") as started:
        assert isinstance(started, StartedUnit)
        assert started.vendor == "square"
        assert started.profile == "full"
        answered = await started.async_client.get("/__unit/health")
        assert answered.json()["status"] == "ok"


@pytest.mark.anyio
async def test_async_unit_closes_the_async_client_on_the_way_out() -> None:
    """The difference between the two entry points, and the only one.

    ``unit()`` is synchronous, so its ``__exit__`` cannot await; this is where
    the close is real. Nothing leaks either way -- the transport owns no socket
    -- but a client left open is a client a later ``await`` would still use, on
    a unit that has already stopped.
    """
    async with async_unit("square") as started:
        client = started.async_client
        assert not client.is_closed
    assert client.is_closed


@pytest.mark.anyio
async def test_async_unit_takes_the_same_arguments_as_unit() -> None:
    """One code path, two entry points: ``async_unit`` delegates, so profile,
    env and seed cannot mean something different here."""
    async with async_unit("square", "oauth-only", env={"VENDORFAKE_CLOCK": "virtual"}, seed=7) as started:
        info = (await started.async_client.get("/__unit/info")).json()
        assert started.profile == "oauth-only"
        assert info["clock"]["mode"] == "virtual"
        assert info["chaos"]["seed"] == 7


# ---------------------------------------------------------------------------
# The delay: whose clock, and whose timeout.
# ---------------------------------------------------------------------------


def _armed(started: StartedUnit, delay_ms: int) -> None:
    started.add_chaos_rule({**MATCH_EVERYTHING, "params": {"delay_ms": delay_ms}})


@pytest.mark.parametrize("case", RENEWALS, ids=IDS)
def test_a_delay_longer_than_the_read_timeout_raises_at_once_synchronously(case: Renewal) -> None:
    """Five seconds asked for, nothing waited, ``ReadTimeout`` raised.

    This is the whole reason the delay moved out of the kernel. A consumer
    rehearsing "my client times out and my retry runs" used to need a real
    socket; now it costs a millisecond and the assertion on elapsed time is
    what proves nothing slept.
    """
    with unit(case.vendor) as started:
        _armed(started, 5000)
        with httpx.Client(
            transport=UnitTransport(started.unit),
            base_url=started.base_url,
            timeout=httpx.Timeout(0.2),
        ) as client:
            begun = time.monotonic()
            with pytest.raises(httpx.ReadTimeout):
                client.post(case.path, json=case.body(started.seed))
            elapsed_ms = (time.monotonic() - begun) * 1000
    assert elapsed_ms < 100, f"waited {elapsed_ms:.1f}ms for a timeout that should not have waited at all"


@pytest.mark.anyio
@pytest.mark.parametrize("case", RENEWALS, ids=IDS)
async def test_a_delay_longer_than_the_read_timeout_raises_at_once_asynchronously(case: Renewal) -> None:
    with unit(case.vendor) as started:
        _armed(started, 5000)
        async with httpx.AsyncClient(
            transport=UnitTransport(started.unit),
            base_url=started.base_url,
            timeout=httpx.Timeout(0.2),
        ) as client:
            begun = time.monotonic()
            with pytest.raises(httpx.ReadTimeout):
                await client.post(case.path, json=case.body(started.seed))
            elapsed_ms = (time.monotonic() - begun) * 1000
    assert elapsed_ms < 100, f"waited {elapsed_ms:.1f}ms for a timeout that should not have waited at all"


#: Short on purpose. The rule is the same rule; the magnitude is not what is
#: under test, and a five-second wait per client per vendor would put half a
#: minute of sleeping into a unit suite that currently runs in two.
PATIENT_DELAY_MS = 250


def test_a_delay_inside_the_read_timeout_is_really_waited_synchronously() -> None:
    """The other branch, and it must not be optimised away: a consumer
    asserting that their backoff waited needs elapsed time to move."""
    with unit("square") as started:
        _armed(started, PATIENT_DELAY_MS)
        with httpx.Client(
            transport=UnitTransport(started.unit),
            base_url=started.base_url,
            timeout=httpx.Timeout(10.0),
        ) as client:
            begun = time.monotonic()
            answered = client.get("/v2/locations", headers=started.seed.auth)
            elapsed_ms = (time.monotonic() - begun) * 1000
    assert answered.status_code == 504
    assert answered.headers["x-unit-error"] == "timeout"
    assert elapsed_ms >= PATIENT_DELAY_MS, f"answered after {elapsed_ms:.1f}ms, short of {PATIENT_DELAY_MS}ms"


@pytest.mark.anyio
async def test_a_delay_inside_the_read_timeout_is_really_waited_asynchronously() -> None:
    with unit("square") as started:
        _armed(started, PATIENT_DELAY_MS)
        async with httpx.AsyncClient(
            transport=UnitTransport(started.unit),
            base_url=started.base_url,
            timeout=httpx.Timeout(10.0),
        ) as client:
            begun = time.monotonic()
            answered = await client.get("/v2/locations", headers=started.seed.auth)
            elapsed_ms = (time.monotonic() - begun) * 1000
    assert answered.status_code == 504
    assert answered.headers["x-unit-error"] == "timeout"
    assert elapsed_ms >= PATIENT_DELAY_MS, f"answered after {elapsed_ms:.1f}ms, short of {PATIENT_DELAY_MS}ms"


def test_a_client_with_no_read_timeout_waits_rather_than_raising() -> None:
    """``timeout=None`` is a setting, not a missing one. Treating it as zero
    would turn every delayed call into a ``ReadTimeout`` for a caller who
    explicitly said they would wait for anything."""
    with unit("square") as started:
        _armed(started, 20)
        with httpx.Client(transport=UnitTransport(started.unit), base_url=started.base_url, timeout=None) as client:
            answered = client.get("/v2/locations", headers=started.seed.auth)
    assert answered.status_code == 504


@pytest.mark.anyio
async def test_a_virtual_clock_delay_answers_at_once_on_both_clients() -> None:
    """Virtual mode moves scenario time instead of asking a binding to wait, so
    the response carries no delay and neither client sleeps -- which is what
    makes an uncompressed schedule drivable at all."""
    with unit("square", env={"VENDORFAKE_CLOCK": "virtual"}) as started:
        _armed(started, 5000)
        before = (await started.async_client.get("/__unit/info")).json()["clock"]["now"]

        begun = time.monotonic()
        answered = await started.async_client.get("/v2/locations", headers=started.seed.auth)
        elapsed_ms = (time.monotonic() - begun) * 1000

        after = (await started.async_client.get("/__unit/info")).json()["clock"]["now"]
        also = started.client.get("/v2/locations", headers=started.seed.auth)

    assert answered.status_code == 504
    assert also.status_code == 504
    assert before != after
    assert elapsed_ms < 500, f"waited {elapsed_ms:.1f}ms on a virtual clock, which never owes a binding time"


def test_a_synchronous_block_closes_the_async_client_it_built() -> None:
    """No event loop is running here, so ``unit()``'s exit can finish the close
    and does. The asymmetric case is the next test."""
    with unit("square") as started:
        client = started.async_client
        assert not client.is_closed
    assert client.is_closed


@pytest.mark.anyio
async def test_a_synchronous_block_inside_a_loop_leaves_the_client_open() -> None:
    """Documented, not accidental.

    A synchronous ``__exit__`` cannot await inside a live loop, and both ways
    round it are worse than doing nothing: ``asyncio.run`` raises there, and a
    task scheduled from an exiting block may be collected before the loop runs
    it. Nothing leaks -- this transport owns no socket, no pool and no thread,
    so ``aclose`` only sets a flag -- and the unit itself is stopped either way.
    ``async_unit()`` is where the close is real.
    """
    with unit("square") as started:
        client = started.async_client
        assert started.unit.context.store.stats()
    assert not client.is_closed
