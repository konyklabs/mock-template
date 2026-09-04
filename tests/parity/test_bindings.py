"""One behaviour, three bindings: ``unit()`` in process, ``served()`` as a child,
and the bare CLI (``vendorfake serve``) reading only its environment.

Each case asserts the answer a consumer sees on the wire is the same whichever
binding stands in for the vendor. A case that diverges today is marked
``xfail(strict=True)`` with the audit finding it tracks (konyklabs/roadmap#116);
the fix flips the mark, so the divergence is on record until then.
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
def _in_process(ambient: Mapping[str, str], read_timeout_s: float | None) -> Iterator[Bound]:
    # unit() reads no environment: its unmatched policy is a parameter, mapped here from the variable's value.
    policy = ambient.get("VENDORFAKE_UNMATCHED")
    with unit(VENDOR, unmatched=policy) as started:  # type: ignore[arg-type]
        if read_timeout_s is not None:
            started.client.timeout = httpx.Timeout(read_timeout_s)
        yield Bound("unit", started.client)


@contextmanager
def _served(ambient: Mapping[str, str], read_timeout_s: float | None) -> Iterator[Bound]:
    with served(VENDOR) as child:
        if read_timeout_s is not None:
            child.client.timeout = httpx.Timeout(read_timeout_s)
        yield Bound("served", child.client)


@contextmanager
def _cli(ambient: Mapping[str, str], read_timeout_s: float | None) -> Iterator[Bound]:
    argv = [*SERVE_COMMAND, "--vendor", VENDOR, "--host", "127.0.0.1", "--port", "0"]
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ))
    output = _ChildOutput(process)
    try:
        base_url = _wait_for_announcement(process, output, 30.0)
        with httpx.Client(base_url=base_url, timeout=httpx.Timeout(read_timeout_s or 10.0)) as client:
            yield Bound("cli", client)
    finally:
        process.terminate()
        process.wait(timeout=10)


OPENERS: dict[str, Callable[..., Iterator[Bound]]] = {"unit": _in_process, "served": _served, "cli": _cli}


@pytest.fixture(params=BINDINGS)
def binding(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def open_unit(binding: str, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Iterator[Bound]]:
    """``open_unit(ambient={...}, read_timeout_s=None)``: a context manager over a
    unit on this binding, with ``ambient`` exported to the process environment
    the way a CI job exports it."""

    @contextmanager
    def opener(ambient: Mapping[str, str] | None = None, read_timeout_s: float | None = None) -> Iterator[Bound]:
        for key, value in (ambient or {}).items():
            monkeypatch.setenv(key, value)
        with OPENERS[binding](ambient or {}, read_timeout_s) as bound:
            yield bound

    return opener


def _auth(bound: Bound) -> dict[str, str]:
    credentials = bound.client.get("/__unit/auth").json()["credentials"]
    token = next(c for c in credentials if c["mode"] == "bearer")
    return dict(token["headers"])


def _xfail_on(binding: str, *diverging: str, reason: str) -> None:
    if binding in diverging:
        pytest.xfail(reason)


# -- environment resolution (audit E1) ---------------------------------------


def test_an_ambient_profile_variable_is_honoured(binding: str, open_unit: Callable[..., Iterator[Bound]]) -> None:
    """``VENDORFAKE_PROFILE`` exported by the job selects the profile on every
    binding; an explicit argument beats it, nothing else does."""
    _xfail_on(
        binding,
        "unit",
        "served",
        reason="E1: unit() ignores the ambient environment; served() passes the profile as a flag that beats it",
    )
    with open_unit(ambient={"VENDORFAKE_PROFILE": "no-faults"}) as bound:
        assert bound.client.get("/__unit/info").json()["profile"] == "no-faults"


def test_an_ambient_clock_variable_is_honoured(binding: str, open_unit: Callable[..., Iterator[Bound]]) -> None:
    _xfail_on(binding, "unit", reason="E1: unit() ignores the ambient environment")
    with open_unit(ambient={"VENDORFAKE_CLOCK": "virtual"}) as bound:
        assert bound.client.get("/__unit/info").json()["clock"]["mode"] == "virtual"


# -- unmatched path (audit E2) -------------------------------------------------


def test_an_unmatched_path_answers_404_with_the_near_miss_header_under_vendor_404(
    binding: str, open_unit: Callable[..., Iterator[Bound]]
) -> None:
    with open_unit(ambient={"VENDORFAKE_UNMATCHED": "vendor-404"}) as bound:
        response = bound.client.get("/v2/locationz", headers=_auth(bound))
        assert response.status_code == 404
        assert "ListLocations" in response.headers[NEAR_MISS_HEADER]


def test_the_default_unmatched_policy_is_the_same_on_every_binding(
    binding: str, open_unit: Callable[..., Iterator[Bound]]
) -> None:
    """With nothing configured, the wire answer to a mistyped path is one thing."""
    _xfail_on(
        binding, "unit", reason="E2: the in-process default raises UnmatchedRequest; served and the CLI answer 404"
    )
    with open_unit() as bound:
        try:
            response = bound.client.get("/v2/locationz", headers=_auth(bound))
        except UnmatchedRequest:
            pytest.fail("the in-process binding raised where the others answer 404")
        assert response.status_code == 404
        assert NEAR_MISS_HEADER in response.headers


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


def test_a_timeout_fault_past_the_read_timeout_is_a_read_timeout_on_a_real_clock(
    binding: str, open_unit: Callable[..., Iterator[Bound]]
) -> None:
    with open_unit(read_timeout_s=0.5) as bound:
        _arm_timeout(bound, delay_ms=5_000)
        with pytest.raises(httpx.ReadTimeout):
            bound.client.get("/v2/locations", headers=_auth(bound))


def test_a_timeout_fault_on_a_virtual_clock_answers_the_same_on_every_binding(
    binding: str, open_unit: Callable[..., Iterator[Bound]]
) -> None:
    """A virtual clock does not sleep; the answer must still be one thing."""
    _xfail_on(
        binding,
        "unit",
        "served",
        "cli",
        reason="E4: in-process raises ReadTimeout at once, served answers 504 at once; the contract is Phase 2's",
    )
    with open_unit(ambient={"VENDORFAKE_CLOCK": "virtual"}, read_timeout_s=0.5) as bound:
        _arm_timeout(bound, delay_ms=5_000)
        with pytest.raises(httpx.ReadTimeout):
            bound.client.get("/v2/locations", headers=_auth(bound))


# -- seeds, chaos rules, reset --------------------------------------------------


def test_the_seeded_scenario_is_identical(binding: str, open_unit: Callable[..., Iterator[Bound]]) -> None:
    with open_unit() as bound:
        locations = bound.client.get("/v2/locations", headers=_auth(bound)).json()["locations"]
        assert [location["id"] for location in locations] == ["18YC4JDH91E1H", "057P5VYJ4A5X1"]


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
