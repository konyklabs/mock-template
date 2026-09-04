"""One behaviour, three bindings: ``unit()`` in process, ``served()`` as a child,
and the bare CLI (``vendorfake serve``) reading only its environment.

Each case asserts the answer a consumer sees is the one the binding contract in
docs/start/bindings.md documents. A binding that diverges from it is a strict
xfail naming the audit finding it tracks (konyklabs/roadmap#116); the fix makes
it an XPASS failure, so the divergence is on record until the mark is dropped.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

import httpx
import pytest

from vendorfake.testing import (
    SERVE_COMMAND,
    _ChildOutput,
    _stop,
    _wait_for_announcement,
    served,
    unit,
)
from vendorfake.testing.transport import UnmatchedRequest

VENDOR = "square"
BINDINGS = ("unit", "served", "cli")
NEAR_MISS_HEADER = "vendorfake-near-miss"


@dataclass(frozen=True)
class Bound:
    """A running unit as the parity cases see it: an HTTP client and nothing else."""

    binding: str
    client: httpx.Client


Opener = Callable[[Mapping[str, str], float | None], Iterator[Bound]]


@contextmanager
def _in_process(explicit: Mapping[str, str], read_timeout_s: float | None, unmatched: str | None) -> Iterator[Bound]:
    with unit(VENDOR, env=explicit, unmatched=unmatched) as started:  # type: ignore[arg-type]
        if read_timeout_s is not None:
            started.client.timeout = httpx.Timeout(read_timeout_s)
        yield Bound("unit", started.client)


@contextmanager
def _served(explicit: Mapping[str, str], read_timeout_s: float | None, unmatched: str | None) -> Iterator[Bound]:
    with served(VENDOR, env=explicit, unmatched=unmatched) as child:  # type: ignore[arg-type]
        if read_timeout_s is not None:
            child.client.timeout = httpx.Timeout(read_timeout_s)
        yield Bound("served", child.client)


@contextmanager
def _cli(explicit: Mapping[str, str], read_timeout_s: float | None, unmatched: str | None) -> Iterator[Bound]:
    argv = [*SERVE_COMMAND, "--vendor", VENDOR, "--host", "127.0.0.1", "--port", "0"]
    process = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env={**os.environ, **explicit}
    )
    output = _ChildOutput(process)
    try:
        base_url = _wait_for_announcement(process, output, 30.0)
        with httpx.Client(base_url=base_url, timeout=httpx.Timeout(read_timeout_s or 10.0)) as client:
            yield Bound("cli", client)
    finally:
        _stop(process)
        output.join()


OPENERS: dict[str, Callable[..., Iterator[Bound]]] = {"unit": _in_process, "served": _served, "cli": _cli}


def over(**diverging: str) -> list[object]:
    """The three bindings as parametrize values; a binding named in ``diverging``
    is a strict xfail carrying the audit finding, so its fix turns into an XPASS
    failure that says to drop the mark."""
    return [
        pytest.param(
            name,
            id=name,
            marks=[pytest.mark.xfail(strict=True, reason=diverging[name])] if name in diverging else [],
        )
        for name in BINDINGS
    ]


ALL = over()


@pytest.fixture
def open_unit(binding: str, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Iterator[Bound]]:
    """``open_unit(ambient=, explicit=, read_timeout_s=)``: a context manager over a
    unit on this binding. ``ambient`` is exported to the process environment the
    way a CI job exports it and nothing else is done with it; ``explicit`` goes
    through each binding's documented configuration path (``unit(env=)``,
    ``served(env=)``, the CLI child's environment)."""

    @contextmanager
    def opener(
        ambient: Mapping[str, str] | None = None,
        explicit: Mapping[str, str] | None = None,
        read_timeout_s: float | None = None,
        unmatched: str | None = None,
    ) -> Iterator[Bound]:
        for key, value in (ambient or {}).items():
            monkeypatch.setenv(key, value)
        with OPENERS[binding](explicit or {}, read_timeout_s, unmatched) as bound:
            yield bound

    return opener


def _auth(bound: Bound) -> dict[str, str]:
    credentials = bound.client.get("/__unit/auth").json()["credentials"]
    token = next(c for c in credentials if c["mode"] == "bearer")
    return dict(token["headers"])


# -- environment resolution (audit E1) ---------------------------------------


@pytest.mark.parametrize("binding", ALL)
def test_an_ambient_profile_variable_is_honoured(binding: str, open_unit: Callable[..., Iterator[Bound]]) -> None:
    """``VENDORFAKE_PROFILE`` exported by the job selects the profile on every
    binding; an explicit argument beats it, nothing else does."""
    with open_unit(ambient={"VENDORFAKE_PROFILE": "no-faults"}) as bound:
        assert bound.client.get("/__unit/info").json()["profile"] == "no-faults"


@pytest.mark.parametrize("binding", ALL)
def test_an_ambient_clock_variable_is_honoured(binding: str, open_unit: Callable[..., Iterator[Bound]]) -> None:
    with open_unit(ambient={"VENDORFAKE_CLOCK": "virtual"}) as bound:
        assert bound.client.get("/__unit/info").json()["clock"]["mode"] == "virtual"


# -- unmatched path (audit E2) -------------------------------------------------
# The contract: on the wire every binding answers 404 with the near-miss header;
# a Python driver (unit(), served(), serve_in_thread()) raises UnmatchedRequest
# by default and answers the 404 under unmatched="vendor-404".


@pytest.mark.parametrize("binding", ALL)
def test_an_unmatched_path_answers_404_with_the_near_miss_header_on_the_wire(
    binding: str, open_unit: Callable[..., Iterator[Bound]]
) -> None:
    with open_unit(unmatched="vendor-404") as bound:
        response = bound.client.get("/v2/locationz", headers=_auth(bound))
        assert response.status_code == 404
        assert "ListLocations" in response.headers[NEAR_MISS_HEADER]


@pytest.mark.parametrize("binding", ALL)
def test_a_python_driver_raises_on_an_unmatched_path_by_default(
    binding: str, open_unit: Callable[..., Iterator[Bound]]
) -> None:
    if binding == "cli":
        pytest.skip("the bare CLI has no Python driver; the wire answer is the case above")
    with open_unit() as bound, pytest.raises(UnmatchedRequest, match="ListLocations"):
        bound.client.get("/v2/locationz", headers=_auth(bound))


# -- timeout fault (audit E4) --------------------------------------------------


def _arm_timeout(bound: Bound, delay_ms: int) -> None:
    response = bound.client.post(
        "/__unit/chaos/rules",
        json={
            "id": "slow",
            "scope": "request",
            "fault": "timeout",
            "params": {"delay_ms": delay_ms},
            "match": {"route": "GET /v2/locations"},
        },
    )
    assert response.status_code in (200, 201), response.text


@pytest.mark.parametrize("binding", ALL)
def test_a_timeout_fault_past_the_read_timeout_is_a_read_timeout_on_a_real_clock(
    binding: str, open_unit: Callable[..., Iterator[Bound]]
) -> None:
    with open_unit(read_timeout_s=0.5) as bound:
        _arm_timeout(bound, delay_ms=5_000)
        with pytest.raises(httpx.ReadTimeout):
            bound.client.get("/v2/locations", headers=_auth(bound))


@pytest.mark.parametrize("binding", ALL)
def test_a_timeout_fault_on_a_virtual_clock_follows_the_documented_matrix(
    binding: str, open_unit: Callable[..., Iterator[Bound]]
) -> None:
    """A virtual clock does not sleep. In process the client's own read timeout is
    compared with the asked delay and ReadTimeout is raised at once; over a socket
    the unit cannot know the client's timeout, so it answers 504 at once with the
    asked delay in Vendorfake-Delay-Ms (docs/start/bindings.md)."""
    with open_unit(ambient={"VENDORFAKE_CLOCK": "virtual"}, read_timeout_s=0.5) as bound:
        _arm_timeout(bound, delay_ms=5_000)
        if binding == "unit":
            with pytest.raises(httpx.ReadTimeout):
                bound.client.get("/v2/locations", headers=_auth(bound))
        else:
            response = bound.client.get("/v2/locations", headers=_auth(bound))
            assert response.status_code == 504
            assert response.headers["vendorfake-delay-ms"] == "5000"


# -- seeds, chaos rules, reset --------------------------------------------------


@pytest.mark.parametrize("binding", ALL)
def test_the_seeded_scenario_is_identical(binding: str, open_unit: Callable[..., Iterator[Bound]]) -> None:
    with open_unit() as bound:
        locations = bound.client.get("/v2/locations", headers=_auth(bound)).json()["locations"]
        assert [location["id"] for location in locations] == ["18YC4JDH91E1H", "057P5VYJ4A5X1"]


@pytest.mark.parametrize("binding", ALL)
def test_a_chaos_rule_fires_the_same(binding: str, open_unit: Callable[..., Iterator[Bound]]) -> None:
    with open_unit() as bound:
        armed = bound.client.post(
            "/__unit/chaos/rules",
            json={"id": "boom", "scope": "request", "fault": "server_error", "match": {"route": "GET /v2/locations"}},
        )
        assert armed.status_code in (200, 201), armed.text
        response = bound.client.get("/v2/locations", headers=_auth(bound))
        assert response.status_code == 500
        assert response.headers["vendorfake-fault"] == "server_error"


@pytest.mark.parametrize("binding", ALL)
def test_a_reset_returns_the_unit_to_its_seed(binding: str, open_unit: Callable[..., Iterator[Bound]]) -> None:
    with open_unit() as bound:
        auth = _auth(bound)
        created = bound.client.post(
            "/v2/orders",
            json={"idempotency_key": "parity-1", "order": {"location_id": "18YC4JDH91E1H"}},
            headers=auth,
        )
        assert created.status_code == 200, created.text
        order_id = created.json()["order"]["id"]
        assert bound.client.get(f"/v2/orders/{order_id}", headers=auth).status_code == 200
        assert bound.client.post("/__unit/state/reset", json={}).status_code == 200
        assert bound.client.get(f"/v2/orders/{order_id}", headers=auth).status_code == 404
