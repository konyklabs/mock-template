"""The pipeline's order, pinned where a plausible wrong order would still pass.

Each test names the boundary it defends. A test that merely asserted "a
disabled capability answers 501" would pass under an implementation that
checked auth first; these assert the *other* step did not run.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from tests.fakes import FakeAuth, FakeErrors, FakeVendor, capability, make_unit, route
from vendorfake.core.kernel.reply import json_, no_content
from vendorfake.core.kernel.types import (
    IdempotencySpec,
    MagicTriggerSpec,
    UnitError,
    UnitErrorKind,
)
from vendorfake.core.kernel.unit import REQUEST_ID_HEADER, RouteInfo, Unit, make_request
from vendorfake.core.transport.inprocess import in_process

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _handler(calls: list[str], name: str = "ok", body: object = None):
    def run(args: object) -> object:
        calls.append(name)
        return json_(body if body is not None else {"ok": True})

    return run


def _rule(fault: str, **params: object) -> dict[str, object]:
    document: dict[str, object] = {"id": f"r-{fault}", "scope": "request", "fault": fault}
    if params:
        document["params"] = dict(params)
    return document


# ---------------------------------------------------------------------------
# step 1 -- internal short-circuit
# ---------------------------------------------------------------------------


def test_an_internal_route_skips_the_capability_gate_the_faults_and_auth() -> None:
    """Everything that could make a unit unrecoverable is skipped: a chaos rule
    matching every request and a disabled capability would otherwise lock a
    consumer out of the control plane that switches them back off."""
    calls: list[str] = []
    auth = FakeAuth(raises=UnitError(UnitErrorKind.UNAUTHORIZED))
    vendor = FakeVendor(auth=auth)
    unit = make_unit(
        [route("GET", "/__unit/probe", _handler(calls), capability="ghost", internal=True, auth="bearer")],
        vendor=vendor,
        capabilities=("chaos",),
        chaos_rules=[_rule("server_error")],
    )
    res = in_process(unit).get("/__unit/probe")
    assert res.status == 200
    assert calls == ["ok"]
    assert auth.calls == []


def test_an_internal_route_is_never_decorated() -> None:
    calls: list[str] = []
    vendor = FakeVendor()
    unit = make_unit(
        [route("GET", "/__unit/probe", _handler(calls), capability="ghost", internal=True)],
        vendor=vendor,
    )
    res = in_process(unit).get("/__unit/probe")
    assert "acme-version" not in res.headers
    assert vendor.decorated == []


# ---------------------------------------------------------------------------
# step 2 -- the capability gate runs before auth
# ---------------------------------------------------------------------------


def test_a_disabled_capability_answers_before_auth_is_ever_consulted() -> None:
    """A capability is a property of the deployment, not of the caller, so it
    must answer identically with and without a credential. Answering 401 first
    would send a consumer to fix a token for a route that is switched off."""
    auth = FakeAuth(raises=UnitError(UnitErrorKind.UNAUTHORIZED))
    vendor = FakeVendor(auth=auth)
    unit = make_unit(
        [route("GET", "/v2/orders", _handler([]), auth="bearer")],
        vendor=vendor,
        capabilities=("chaos",),
    )
    res = in_process(unit).get("/v2/orders")
    assert res.status == 501
    assert res.header("x-unit-error") == "capability_disabled"
    assert auth.calls == []


def test_a_disabled_capability_also_answers_before_a_chaos_rule_can_fire() -> None:
    unit = make_unit(
        [route("GET", "/v2/orders", _handler([]))],
        capabilities=("chaos",),
        chaos_rules=[_rule("rate_limit")],
    )
    assert in_process(unit).get("/v2/orders").header("x-unit-error") == "capability_disabled"


# ---------------------------------------------------------------------------
# steps 3 and 4 -- fault selection, then pre-auth application
# ---------------------------------------------------------------------------


def test_a_pre_auth_fault_fires_before_auth_is_consulted() -> None:
    auth = FakeAuth(raises=UnitError(UnitErrorKind.UNAUTHORIZED))
    vendor = FakeVendor(auth=auth)
    unit = make_unit(
        [route("GET", "/v2/orders", _handler([]), auth="bearer")],
        vendor=vendor,
        chaos_rules=[_rule("rate_limit", retry_after_seconds=3)],
    )
    res = in_process(unit).get("/v2/orders")
    assert res.status == 429
    assert auth.calls == []
    assert res.json()["error"]["info"]["retry_after_seconds"] == 3


def test_the_fault_selection_runs_once_per_request_not_once_per_phase() -> None:
    """Two applications, one selection. A rule counted twice would exhaust an
    ``nth``/``times`` budget at half the traffic the author wrote it for."""
    unit = make_unit(
        [route("GET", "/v2/orders", _handler([]), auth="bearer")],
        chaos_rules=[{"id": "r1", "scope": "request", "fault": "server_error", "when": {"times": 1}}],
    )
    api = in_process(unit)
    assert api.get("/v2/orders").status == 500
    status = unit.context.chaos.status()
    assert (status[0].matches, status[0].fires) == (1, 1)


def test_a_disabled_chaos_capability_arms_nothing() -> None:
    unit = make_unit(
        [route("GET", "/v2/orders", _handler([]))],
        capabilities=("orders",),
        chaos_rules=[_rule("server_error")],
    )
    assert in_process(unit).get("/v2/orders").status == 200
    assert unit.context.chaos.status()[0].matches == 0


# ---------------------------------------------------------------------------
# step 5 -- auth and scopes
# ---------------------------------------------------------------------------


def test_the_kernel_checks_scopes_and_names_the_missing_ones() -> None:
    """Checked here rather than in the vendor, because a second place to check
    is a second place to forget."""
    vendor = FakeVendor(auth=FakeAuth(scopes=("orders.read",)))
    unit = make_unit(
        [route("POST", "/v2/orders", _handler([]), auth="bearer", scopes=("orders.read", "orders.write"))],
        vendor=vendor,
    )
    res = in_process(unit).post("/v2/orders", {})
    assert res.status == 403
    assert res.header("x-unit-error") == "forbidden_scope"
    assert res.json()["error"]["info"]["missing"] == ["orders.write"]


def test_a_route_without_auth_never_calls_the_auth_adapter() -> None:
    auth = FakeAuth()
    vendor = FakeVendor(auth=auth)
    unit = make_unit([route("GET", "/v2/orders", _handler([]))], vendor=vendor)
    assert in_process(unit).get("/v2/orders").status == 200
    assert auth.calls == []


def test_the_resolved_principal_reaches_the_handler() -> None:
    seen: list[object] = []

    def run(args):  # type: ignore[no-untyped-def]
        seen.append(args.auth)
        return json_({})

    vendor = FakeVendor(auth=FakeAuth(scopes=("orders.read",)))
    unit = make_unit([route("GET", "/v2/orders", run, auth="bearer")], vendor=vendor)
    in_process(unit).get("/v2/orders")
    assert seen[0].principal_id == "prn_1"


# ---------------------------------------------------------------------------
# step 6 -- token_expiry, after auth, unconditionally
# ---------------------------------------------------------------------------


def test_token_expiry_does_not_pre_empt_a_genuine_auth_failure() -> None:
    """It means "the token expired *while the request was in flight*", so it
    must fire after authentication succeeded. Firing it first would be
    indistinguishable from an ordinary 401 and would prove nothing about a
    consumer's refresh path."""
    auth = FakeAuth(raises=UnitError(UnitErrorKind.UNAUTHORIZED))
    vendor = FakeVendor(auth=auth)
    unit = make_unit(
        [route("GET", "/v2/orders", _handler([]), auth="bearer")],
        vendor=vendor,
        chaos_rules=[_rule("token_expiry")],
    )
    res = in_process(unit).get("/v2/orders")
    assert res.header("x-unit-error") == "unauthorized"
    assert auth.calls == ["bearer"]


def test_token_expiry_fires_after_a_successful_auth() -> None:
    auth = FakeAuth()
    vendor = FakeVendor(auth=auth)
    calls: list[str] = []
    unit = make_unit(
        [route("GET", "/v2/orders", _handler(calls), auth="bearer")],
        vendor=vendor,
        chaos_rules=[_rule("token_expiry")],
    )
    res = in_process(unit).get("/v2/orders")
    assert res.header("x-unit-error") == "token_expired"
    assert auth.calls == ["bearer"]
    assert calls == []


def test_token_expiry_fires_on_a_route_that_declares_no_auth_at_all() -> None:
    """The phase belongs to the fault, not to the call site. A pipeline that
    only ran the post-auth phase for authenticated routes would be a second,
    divergent copy of the rule that lives in ``chaos/faults.py``."""
    unit = make_unit(
        [route("GET", "/v2/orders", _handler([]))],
        chaos_rules=[_rule("token_expiry")],
    )
    assert in_process(unit).get("/v2/orders").header("x-unit-error") == "token_expired"


def test_a_pre_auth_fault_is_not_applied_a_second_time_after_auth() -> None:
    """Both phases receive the same decision; each fault belongs to exactly
    one of them."""
    calls: list[str] = []
    unit = make_unit(
        [route("GET", "/v2/orders", _handler(calls), auth="bearer")],
        chaos_rules=[_rule("unavailable")],
    )
    assert in_process(unit).get("/v2/orders").status == 503
    assert calls == []


# ---------------------------------------------------------------------------
# step 7 -- idempotency
# ---------------------------------------------------------------------------

_IDEM = IdempotencySpec(key_path="idempotency_key", scope="orders.create")


def test_a_repeated_key_replays_the_stored_response_without_running_the_handler() -> None:
    calls: list[str] = []
    unit = make_unit([route("POST", "/v2/orders", _handler(calls), idempotency=_IDEM)])
    api = in_process(unit)
    first = api.post("/v2/orders", {"idempotency_key": "k1", "a": 1})
    second = api.post("/v2/orders", {"idempotency_key": "k1", "a": 1})
    assert calls == ["ok"]
    assert second.status == first.status
    assert second.body == first.body
    assert second.header("x-unit-idempotent-replay") == "true"
    assert first.header("x-unit-idempotent-replay") is None


def test_a_reused_key_with_a_different_body_conflicts_by_default() -> None:
    unit = make_unit([route("POST", "/v2/orders", _handler([]), idempotency=_IDEM)])
    api = in_process(unit)
    api.post("/v2/orders", {"idempotency_key": "k1", "a": 1})
    res = api.post("/v2/orders", {"idempotency_key": "k1", "a": 2})
    assert res.status == 409
    assert res.header("x-unit-error") == "idempotency_conflict"


def test_on_mismatch_replay_returns_the_stored_body_and_says_so() -> None:
    """Documented vendor behaviour: "you get a 200 response but the returned
    order doesn't reflect any of your updates". The header is the only way a
    consumer can observe that their update was discarded."""
    spec = IdempotencySpec(key_path="idempotency_key", scope="orders.update", on_mismatch="replay")
    calls: list[str] = []
    unit = make_unit([route("POST", "/v2/orders", _handler(calls), idempotency=spec)])
    api = in_process(unit)
    first = api.post("/v2/orders", {"idempotency_key": "k1", "a": 1})
    second = api.post("/v2/orders", {"idempotency_key": "k1", "a": 2})
    assert calls == ["ok"]
    assert second.body == first.body
    assert second.header("x-unit-idempotent-ignored-body") == "true"


def test_the_digest_ignores_key_order_but_not_values() -> None:
    unit = make_unit([route("POST", "/v2/orders", _handler([]), idempotency=_IDEM)])
    api = in_process(unit)
    api.post("/v2/orders", {"idempotency_key": "k1", "a": 1, "b": 2})
    assert api.post("/v2/orders", {"b": 2, "a": 1, "idempotency_key": "k1"}).status == 200


def test_a_missing_key_is_ignored_unless_the_route_requires_one() -> None:
    calls: list[str] = []
    unit = make_unit([route("POST", "/v2/orders", _handler(calls), idempotency=_IDEM)])
    assert in_process(unit).post("/v2/orders", {"a": 1}).status == 200

    required = IdempotencySpec(key_path="idempotency_key", scope="s", required=True)
    unit2 = make_unit([route("POST", "/v2/x", _handler(calls), idempotency=required)])
    res = in_process(unit2).post("/v2/x", {"a": 1})
    assert res.status == 400
    assert res.header("x-unit-error") == "missing_field"
    assert res.json()["error"]["field"] == "idempotency_key"


def test_an_empty_string_key_is_treated_as_absent() -> None:
    required = IdempotencySpec(key_path="idempotency_key", scope="s", required=True)
    unit = make_unit([route("POST", "/v2/x", _handler([]), idempotency=required)])
    assert in_process(unit).post("/v2/x", {"idempotency_key": ""}).header("x-unit-error") == "missing_field"


def test_the_key_path_may_use_the_bracket_grammar() -> None:
    """One path resolver, not two. The reference used a bracket-free reducer
    here and ``dotGet`` everywhere else; unifying them is a behaviour change
    for any key path containing brackets, and this is it."""
    spec = IdempotencySpec(key_path="meta.keys[0]", scope="s", required=True)
    calls: list[str] = []
    unit = make_unit([route("POST", "/v2/x", _handler(calls), idempotency=spec)])
    api = in_process(unit)
    assert api.post("/v2/x", {"meta": {"keys": ["k9"]}}).status == 200
    assert api.post("/v2/x", {"meta": {"keys": ["k9"]}}).header("x-unit-idempotent-replay") == "true"
    assert calls == ["ok"]


def test_idempotency_is_checked_after_auth() -> None:
    """A stored response must never reach a caller who guessed a key and holds
    no credential."""
    calls: list[str] = []
    good = FakeAuth()
    vendor = FakeVendor(auth=good)
    unit = make_unit([route("POST", "/v2/orders", _handler(calls), auth="bearer", idempotency=_IDEM)], vendor=vendor)
    api = in_process(unit)
    api.post("/v2/orders", {"idempotency_key": "k1"})
    good.raises = UnitError(UnitErrorKind.UNAUTHORIZED)
    res = api.post("/v2/orders", {"idempotency_key": "k1"})
    assert res.header("x-unit-error") == "unauthorized"
    assert res.header("x-unit-idempotent-replay") is None


def test_a_fault_fires_before_the_key_is_consumed() -> None:
    """An injected failure must not burn an idempotency key: the consumer's
    retry with the same key has to be able to succeed."""
    calls: list[str] = []
    unit = make_unit(
        [route("POST", "/v2/orders", _handler(calls), idempotency=_IDEM)],
        chaos_rules=[{"id": "r1", "scope": "request", "fault": "server_error", "when": {"times": 1}}],
    )
    api = in_process(unit)
    assert api.post("/v2/orders", {"idempotency_key": "k1"}).status == 500
    assert api.post("/v2/orders", {"idempotency_key": "k1"}).status == 200
    assert calls == ["ok"]


def test_a_response_phase_fault_is_not_stored_against_the_key() -> None:
    """``connection_reset`` (and the other four response-phase faults) leaves
    the handler's real body untouched and only attaches a
    ``UnitResponse.transport`` directive plus the ``vendorfake-fault`` header
    (``core/chaos/faults.py``'s ``_directive``). ``IdempotencyRecord`` has
    nowhere to put a transport directive, so storing that response would make
    a replay a clean 200 that still claimed ``vendorfake-fault`` -- worse than
    either "clean" or "faulted" on its own, and it would silently switch off
    ``is_transport_fault`` for a validator on every later replay of the key.
    The fault must not burn the key at all: a replay re-runs the handler, the
    same guarantee ``test_a_fault_fires_before_the_key_is_consumed`` gives a
    request-scope fault.
    """
    calls: list[str] = []
    unit = make_unit(
        [route("POST", "/v2/orders", _handler(calls), idempotency=_IDEM)],
        chaos_rules=[
            {
                "id": "r1",
                "scope": "request",
                "fault": "connection_reset",
                "match": {"route": "POST /v2/orders"},
                "when": {"times": 1},
            }
        ],
    )
    api = in_process(unit)
    first = api.post("/v2/orders", {"idempotency_key": "k1"})
    assert first.status == 200
    assert first.header("vendorfake-fault") == "connection_reset"
    second = api.post("/v2/orders", {"idempotency_key": "k1"})
    assert second.status == 200
    assert second.header("vendorfake-fault") is None
    assert second.header("x-unit-idempotent-replay") is None
    assert calls == ["ok", "ok"]


def test_a_non_2xx_response_is_not_stored_against_the_key() -> None:
    calls: list[str] = []

    def run(args):  # type: ignore[no-untyped-def]
        calls.append("ok")
        return json_({"n": len(calls)}, 400)

    unit = make_unit([route("POST", "/v2/orders", run, idempotency=_IDEM)])
    api = in_process(unit)
    api.post("/v2/orders", {"idempotency_key": "k1"})
    second = api.post("/v2/orders", {"idempotency_key": "k1"})
    assert calls == ["ok", "ok"]
    assert second.header("x-unit-idempotent-replay") is None


def test_the_replayed_body_is_the_exact_stored_bytes() -> None:
    def run(args):  # type: ignore[no-untyped-def]
        return json_({"name": "café", "n": 1})

    unit = make_unit([route("POST", "/v2/orders", run, idempotency=_IDEM)])
    api = in_process(unit)
    first = api.post("/v2/orders", {"idempotency_key": "k1"})
    second = api.post("/v2/orders", {"idempotency_key": "k1"})
    assert second.body == first.body == '{"name":"café","n":1}'.encode()


def test_a_form_encoded_body_can_carry_an_idempotency_key() -> None:
    """The reference read JSON only here, so a form-encoded request was never
    idempotent. Unifying on the content-type-general reader is recorded as a
    judgment; this is what it buys."""
    calls: list[str] = []
    unit = make_unit([route("POST", "/v2/orders", _handler(calls), idempotency=_IDEM)])
    api = in_process(unit)
    for _ in range(2):
        res = api.call(
            method="POST",
            path="/v2/orders",
            headers={"content-type": "application/x-www-form-urlencoded"},
            raw_body=b"idempotency_key=k1&a=1",
        )
    assert calls == ["ok"]
    assert res.header("x-unit-idempotent-replay") == "true"


# ---------------------------------------------------------------------------
# outside the numbering -- router match, finish, decorate
# ---------------------------------------------------------------------------


def test_a_path_that_matches_nothing_uses_the_vendor_s_own_not_found_body() -> None:
    vendor = FakeVendor()
    unit = make_unit([route("GET", "/v2/orders", _handler([]))], vendor=vendor)
    res = in_process(unit).get("/v2/nope")
    assert res.status == 404
    assert res.json()["error"]["code"] == "no_route"
    # The 404 is still a shaped error: every failure carries x-unit-error, so a
    # conformance check can say "this failed, and for this reason" without
    # knowing anything about the vendor's body.
    assert res.header("x-unit-error") == "not_found"


def test_the_404_path_is_never_decorated_because_no_route_matched() -> None:
    vendor = FakeVendor()
    unit = make_unit([route("GET", "/v2/orders", _handler([]))], vendor=vendor)
    res = in_process(unit).get("/v2/nope")
    assert "acme-version" not in res.headers
    assert vendor.decorated == []


def test_decorate_runs_on_a_shaped_error_for_a_matched_route() -> None:
    """Decorating only successes is a one-line loss that a single test catches:
    the reference's own transport test asserts the vendor header on a 400."""
    vendor = FakeVendor(auth=FakeAuth(raises=UnitError(UnitErrorKind.UNAUTHORIZED)))
    unit = make_unit([route("GET", "/v2/orders", _handler([]), auth="bearer")], vendor=vendor)
    res = in_process(unit).get("/v2/orders")
    assert res.status == 401
    assert res.header("acme-version") == "2024-01-01"
    assert vendor.decorated == ["GET /v2/orders"]


def test_the_wrong_verb_is_405_and_names_the_allowed_ones() -> None:
    unit = make_unit(
        [
            route("POST", "/v2/orders", _handler([])),
            route("GET", "/v2/orders", _handler([])),
        ]
    )
    res = in_process(unit).delete("/v2/orders")
    assert res.status == 405
    assert res.header("x-unit-error") == "method_not_allowed"
    assert res.json()["error"]["info"]["allowed"] == ["GET", "POST"]


def test_every_response_carries_the_request_id() -> None:
    unit = make_unit([route("GET", "/v2/orders", _handler([]))])
    api = in_process(unit)
    assert api.get("/v2/orders").header(REQUEST_ID_HEADER)
    assert api.get("/v2/nope").header(REQUEST_ID_HEADER)


def test_an_inbound_request_id_is_echoed_rather_than_replaced() -> None:
    """Minting a fresh id for a request whose caller already supplied one gives
    the same logical call two identities depending on which binding carried it."""
    unit = make_unit([route("GET", "/v2/orders", _handler([]))])
    res = in_process(unit).get("/v2/orders", headers={"X-Unit-Request-Id": "corr-1"})
    assert res.header(REQUEST_ID_HEADER) == "corr-1"


def test_a_handler_that_raises_something_other_than_a_unit_error_becomes_a_shaped_500() -> None:
    def boom(args):  # type: ignore[no-untyped-def]
        raise RuntimeError("handler bug")

    unit = make_unit([route("GET", "/v2/orders", boom)])
    res = in_process(unit).get("/v2/orders")
    assert res.status == 500
    assert res.header("x-unit-error") == "internal"
    assert res.json()["error"]["detail"] == "handler bug"


def test_a_handler_may_return_a_bare_reply_init_or_a_unit_response() -> None:
    unit = make_unit(
        [
            route("GET", "/v2/a", lambda args: no_content()),
            route("GET", "/v2/b", lambda args: json_({"x": 1}, 201)),
        ]
    )
    api = in_process(unit)
    assert api.get("/v2/a").status == 204
    assert api.get("/v2/b").json() == {"x": 1}


# ---------------------------------------------------------------------------
# the request lock
# ---------------------------------------------------------------------------


def _blocking_route(path: str, entered: threading.Event, release: threading.Event, *, serialized: bool):
    def run(args):  # type: ignore[no-untyped-def]
        entered.set()
        release.wait(timeout=5)
        return json_({"path": path})

    return route("GET", path, run, serialized=serialized)


def test_a_serialized_route_holds_the_unit_and_an_unserialized_one_does_not() -> None:
    """The lock is what makes id minting and journal ordering deterministic.
    The exemption exists because a handler that blocks on machinery *another
    request must feed* -- draining a webhook queue, advancing a virtual clock --
    would otherwise hold the whole unit for the full delivery timeout."""
    entered, release = threading.Event(), threading.Event()
    unit = make_unit(
        [
            _blocking_route("/v2/slow", entered, release, serialized=True),
            route("GET", "/v2/fast", lambda args: json_({"fast": True})),
            route("GET", "/v2/free", lambda args: json_({"free": True}), serialized=False),
        ]
    )
    api = in_process(unit)
    holder = threading.Thread(target=lambda: api.get("/v2/slow"), daemon=True)
    holder.start()
    try:
        assert entered.wait(timeout=5)

        free_done = threading.Event()
        threading.Thread(target=lambda: (api.get("/v2/free"), free_done.set()), daemon=True).start()
        # An unserialized route must not wait for the request lock.
        assert free_done.wait(timeout=2)

        blocked_done = threading.Event()
        threading.Thread(target=lambda: (api.get("/v2/fast"), blocked_done.set()), daemon=True).start()
        # A serialized route must wait for the request lock.
        assert not blocked_done.wait(timeout=0.3)
    finally:
        release.set()
        holder.join(timeout=5)


# ---------------------------------------------------------------------------
# construction
# ---------------------------------------------------------------------------


def test_an_incomplete_capability_declaration_is_a_startup_failure() -> None:
    vendor = FakeVendor(capabilities=(capability("orders"),), not_supported={})
    with pytest.raises(UnitError) as caught:
        make_unit([route("GET", "/v2/orders", _handler([]))], vendor=vendor)
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert "chaos" in str(caught.value)


def test_declaring_webhooks_without_a_retry_schedule_is_a_startup_failure() -> None:
    """An unmerged vendor default would otherwise present as instant
    exhaustion -- "the subscriber is unreachable" -- rather than as a
    configuration mistake."""
    vendor = FakeVendor(
        capabilities=(
            capability("orders"),
            capability("chaos", kind="behavior"),
            capability("webhooks"),
            capability("webhooks.chaos", kind="behavior", requires=("webhooks", "chaos")),
        ),
        not_supported={},
    )
    with pytest.raises(UnitError) as caught:
        make_unit([route("GET", "/v2/orders", _handler([]))], vendor=vendor)
    assert caught.value.field == "webhooks.retry.schedule_ms"


def test_declaring_webhooks_with_a_schedule_starts() -> None:
    vendor = FakeVendor(
        capabilities=(
            capability("orders"),
            capability("chaos", kind="behavior"),
            capability("webhooks"),
            capability("webhooks.chaos", kind="behavior", requires=("webhooks", "chaos")),
        ),
        not_supported={},
    )
    unit = make_unit(
        [route("GET", "/v2/orders", _handler([]))],
        vendor=vendor,
        schedule_ms=(10, 20),
        capabilities=("orders", "chaos", "webhooks", "webhooks.chaos"),
    )
    assert unit.name == "acme"


class _NoDescribe:
    shape = FakeErrors.shape
    not_found = FakeErrors.not_found


class _ShortDescribe(FakeErrors):
    def describe(self) -> dict[str, dict[str, Any]]:
        rows = super().describe()
        del rows["timeout"]
        return rows


class _UnlabelledDescribe(FakeErrors):
    def describe(self) -> dict[str, dict[str, Any]]:
        rows = super().describe()
        rows["timeout"] = {"status": 504}
        return rows


def test_a_shaper_without_describe_is_a_startup_failure() -> None:
    """Otherwise the first GET /__unit/errors 500s with a leaked
    `'NoneType' object is not callable`."""
    with pytest.raises(UnitError) as caught:
        make_unit([route("GET", "/v2/orders", _handler([]))], vendor=FakeVendor(errors=_NoDescribe()))  # type: ignore[arg-type]
    assert caught.value.field == "vendor.errors.describe"
    assert "no describe()" in str(caught.value)


def test_a_describe_table_short_of_a_kind_is_a_startup_failure_naming_it() -> None:
    with pytest.raises(UnitError) as caught:
        make_unit([route("GET", "/v2/orders", _handler([]))], vendor=FakeVendor(errors=_ShortDescribe()))
    assert caught.value.field == "vendor.errors.describe"
    assert "missing: ['timeout']" in str(caught.value)


def test_a_describe_row_without_provenance_is_a_startup_failure_naming_it() -> None:
    with pytest.raises(UnitError) as caught:
        make_unit([route("GET", "/v2/orders", _handler([]))], vendor=FakeVendor(errors=_UnlabelledDescribe()))
    assert caught.value.field == "vendor.errors.describe"
    assert caught.value.info == {"kinds": ["timeout"]}


def test_a_vendor_route_under_the_control_plane_prefix_refuses_to_start() -> None:
    with pytest.raises(UnitError):
        make_unit([route("GET", "/__unit/sneaky", _handler([]))])


def test_start_hydrates_and_the_control_binding_can_hydrate_again() -> None:
    vendor = FakeVendor()
    unit = make_unit([route("GET", "/v2/orders", _handler([]))], vendor=vendor)
    assert vendor.hydrated == 1
    unit.control.hydrate()
    assert vendor.hydrated == 2


def test_the_control_binding_lists_every_route_including_the_internal_ones() -> None:
    def control(binding):  # type: ignore[no-untyped-def]
        return (route("GET", "/__unit/routes", lambda args: json_({}), capability="__control", internal=True),)

    unit = make_unit(
        [route("GET", "/v2/orders", _handler([]), operation_id="ListOrders")],
        control_routes=control,
    )
    rows = unit.control.list_routes()
    assert [r.path for r in rows] == ["/v2/orders", "/__unit/routes"]
    assert rows[0].as_json() == {
        "method": "GET",
        "path": "/v2/orders",
        "capability": "orders",
        "internal": False,
        "serialized": True,
        "operation_id": "ListOrders",
    }
    assert rows[1].internal is True


def test_the_context_exposes_the_subsystems_a_handler_may_touch_and_no_more() -> None:
    """What is absent is as much the design as what is present: re-seeding the
    store and enumerating the router are reachable only through the typed
    control binding."""
    unit = make_unit([route("GET", "/v2/orders", _handler([]))])
    ctx = unit.context
    for name in ("vendor", "config", "store", "capabilities", "chaos", "clock", "rng", "log"):
        assert hasattr(ctx, name), name
    assert not hasattr(ctx, "hydrate")
    assert not hasattr(ctx, "list_routes")


def test_a_supplied_logger_is_used_verbatim_and_the_environment_is_not_read(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("VENDORFAKE_LOG_LEVEL", "debug")
    lines: list[str] = []

    class Capturing:
        def debug(self, msg, fields=None):  # type: ignore[no-untyped-def]
            lines.append(f"debug:{msg}")

        def info(self, msg, fields=None):  # type: ignore[no-untyped-def]
            lines.append(f"info:{msg}")

        def warn(self, msg, fields=None):  # type: ignore[no-untyped-def]
            lines.append(f"warn:{msg}")

        def error(self, msg, fields=None):  # type: ignore[no-untyped-def]
            lines.append(f"error:{msg}")

    from tests.fakes import make_config

    vendor = FakeVendor(routes=(route("GET", "/v2/orders", _handler([])),))
    unit = Unit(vendor=vendor, config=make_config(), logger=Capturing())
    unit.start()
    in_process(unit).get("/v2/orders")
    assert "info:unit started" in lines
    assert "debug:request" in lines


# ---------------------------------------------------------------------------
# make_request
# ---------------------------------------------------------------------------


def test_make_request_lowercases_headers_and_defaults_the_method_to_upper() -> None:
    req = make_request(method="post", path="v2/orders", headers={"Content-Type": "application/json"})
    assert req.method == "POST"
    assert req.path == "/v2/orders"
    assert req.headers == {"content-type": "application/json"}


def test_make_request_prefers_raw_body_over_body_and_sets_no_content_type_for_it() -> None:
    req = make_request(method="POST", path="/x", body={"a": 1}, raw_body=b"a=1")
    assert req.raw_body == b"a=1"
    assert "content-type" not in req.headers


def test_make_request_serialises_a_body_compactly_and_defaults_the_content_type() -> None:
    req = make_request(method="POST", path="/x", body={"name": "café"})
    assert req.raw_body == '{"name":"café"}'.encode()
    assert req.headers["content-type"] == "application/json"


def test_make_request_leaves_an_explicit_content_type_alone() -> None:
    req = make_request(method="POST", path="/x", body="a=1", headers={"content-type": "text/plain"})
    assert req.headers["content-type"] == "text/plain"
    assert req.raw_body == b"a=1"


def test_make_request_keeps_every_query_value_and_the_last_one_wins_the_scalar_view() -> None:
    """Pairs are what Starlette's ``multi_items()`` and ``parse_qsl`` both hand
    over; a mapping still works for every existing caller."""
    req = make_request(method="GET", path="/x", query=[("k", "a"), ("k", "b"), ("n", "1")])
    assert req.query == {"k": "b", "n": "1"}
    assert req.query_all == {"k": ("a", "b"), "n": ("1",)}
    assert make_request(method="GET", path="/x", query={"k": "a"}).query_all == {"k": ("a",)}


def test_make_request_splits_a_query_string_off_the_path_and_keeps_blank_values() -> None:
    req = make_request(method="GET", path="/x?k=a&k=b&flag", query={"n": "1"})
    assert req.path == "/x"
    assert req.query == {"k": "b", "flag": "", "n": "1"}
    assert req.query_all == {"k": ("a", "b"), "flag": ("",), "n": ("1",)}


def test_make_request_takes_its_id_from_the_inbound_header_when_present() -> None:
    assert make_request(method="GET", path="/x", headers={"x-unit-request-id": "abc"}).id == "abc"
    assert make_request(method="GET", path="/x", request_id="explicit").id == "explicit"
    assert len(make_request(method="GET", path="/x").id) == 36


def test_the_magic_spec_reaches_fault_selection_through_the_general_body() -> None:
    """The reference fed magic extraction from a JSON-only reader, so every
    declared body path was unreachable on a form-encoded request."""
    vendor = FakeVendor(magic=MagicTriggerSpec(prefix="chaos:", body_paths=("reference_id",)))
    unit = make_unit([route("POST", "/v2/orders", _handler([]))], vendor=vendor)
    res = in_process(unit).call(
        method="POST",
        path="/v2/orders",
        headers={"content-type": "application/x-www-form-urlencoded"},
        raw_body=b"reference_id=chaos:rate_limit",
    )
    assert res.status == 429


def test_a_magic_value_does_not_advance_a_standing_rule_s_counters() -> None:
    """The overlay short-circuits; it does not merge. A rule configured to fire
    on its second match must still fire on its second match."""
    vendor = FakeVendor(magic=MagicTriggerSpec(prefix="chaos:", body_paths=("reference_id",)))
    unit = make_unit(
        [route("POST", "/v2/orders", _handler([]))],
        vendor=vendor,
        chaos_rules=[{"id": "r1", "scope": "request", "fault": "unavailable", "when": {"nth": [2]}}],
    )
    api = in_process(unit)
    assert api.post("/v2/orders", {"reference_id": "chaos:rate_limit"}).status == 429
    status = unit.context.chaos.status()
    assert (status[0].matches, status[0].fires) == (0, 0)
    assert api.post("/v2/orders", {}).status == 200
    assert api.post("/v2/orders", {}).status == 503


def test_an_unparseable_body_does_not_break_magic_extraction() -> None:
    vendor = FakeVendor(magic=MagicTriggerSpec(prefix="chaos:", body_paths=("reference_id",)))
    unit = make_unit([route("POST", "/v2/orders", _handler([]))], vendor=vendor)
    res = in_process(unit).call(
        method="POST",
        path="/v2/orders",
        headers={"content-type": "application/json"},
        raw_body=b"{not json",
    )
    assert res.status == 200


def test_route_info_omits_absent_optional_keys_rather_than_nulling_them() -> None:
    info = RouteInfo.of(route("GET", "/v2/x", _handler([])))
    assert "auth" not in info.as_json()
    assert "summary" not in info.as_json()


def test_a_malformed_percent_escape_reaches_the_caller_as_a_shaped_400() -> None:
    """The router raises rather than passing a garbage parameter through, and
    the pipeline's error path shapes it like any other bad request."""
    unit = make_unit([route("GET", "/v2/orders/{order_id}", _handler([]))])
    res = in_process(unit).get("/v2/orders/%zz")
    assert res.status == 400
    assert res.header("x-unit-error") == "invalid_value"
    assert res.json()["error"]["field"] == "path"
