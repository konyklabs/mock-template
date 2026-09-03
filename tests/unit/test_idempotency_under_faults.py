"""What an idempotency key promises when the answer never reaches the caller.

The whole point of an idempotency key is that a retry after a lost response
does not charge the customer twice. A response-phase fault --
``connection_reset``, ``malformed_body`` and the other three -- rehearses
exactly that: the vendor *committed*, and the caller never found out. So the
retry must replay the committed answer, and the store must hold one payment,
not two.

A request-scope fault is the opposite case and is checked here too: it raises
before the handler runs, so nothing was committed, the key was not consumed,
and the retry is what creates the payment -- once.

Square's ``POST /v2/payments`` is the route the whole question is about: it
declares ``IdempotencySpec(key_path="idempotency_key", scope="payments.create",
required=True)`` (``square/surface/payments.py``), and "the handler ran twice"
means a second real payment with a new id. The kernel-level counterpart of the
same claim is ``tests/unit/core/test_kernel_unit.py``'s
``test_a_response_phase_fault_records_the_handlers_clean_answer``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from vendorfake.core.state.store import IdempotencyRecord
from vendorfake.core.util.b64 import b64url_decode
from vendorfake.testing import StartedUnit, unit

IDEMPOTENCY_KEY = "pay-1"

RESET_RULE: dict[str, Any] = {
    "id": "drop",
    "scope": "request",
    "fault": "connection_reset",
    "match": {"route": "POST /v2/payments"},
    "when": {"times": 1},
}

GARBAGE_RULE: dict[str, Any] = {
    "id": "garbage",
    "scope": "request",
    "fault": "malformed_body",
    "match": {"route": "POST /v2/payments"},
    "params": {"mode": "invalid_json"},
    "when": {"times": 1},
}

RESET_TWICE_RULE: dict[str, Any] = {
    "id": "drop",
    "scope": "request",
    "fault": "connection_reset",
    "match": {"route": "POST /v2/payments"},
    "when": {"times": 2},
}

SERVER_ERROR_RULE: dict[str, Any] = {
    "id": "five-hundred",
    "scope": "request",
    "fault": "server_error",
    "match": {"route": "POST /v2/payments"},
    "when": {"times": 1},
}


def _auth(started: StartedUnit[Any]) -> dict[str, str]:
    return {"authorization": f"Bearer {started.seed.access_token}"}


def _payment_body(amount: int = 500, key: str = IDEMPOTENCY_KEY) -> dict[str, Any]:
    return {
        "idempotency_key": key,
        "source_id": "EXTERNAL",
        "amount_money": {"amount": amount},
        "external_details": {"type": "OTHER", "source": "Food Delivery Service"},
    }


def _pay(started: StartedUnit[Any], key: str = IDEMPOTENCY_KEY) -> httpx.Response:
    return started.client.post("/v2/payments", json=_payment_body(key=key), headers=_auth(started))


def _stored(started: StartedUnit[Any], key: str = IDEMPOTENCY_KEY) -> IdempotencyRecord | None:
    """The idempotency record the kernel holds for ``key`` -- read straight
    from the store, because what a replay *carries* and what the store *holds*
    are the two different things this file has to keep apart."""
    return started.unit.context.store.get_idempotent("payments.create", key)


def _payments_held(started: StartedUnit[Any]) -> int:
    """How many payments the store actually holds, read from
    ``GET /__unit/state``'s per-collection counts."""
    entities = started.client.get("/__unit/state").json()["entities"]
    return int(entities.get("payments", 0))


# ---------------------------------------------------------------------------
# Response-phase faults: the handler committed, so the key carries the commit.
# ---------------------------------------------------------------------------


def test_a_dropped_connection_replays_the_payment_the_vendor_already_took() -> None:
    """``connection_reset`` is the fault whose entire purpose is to rehearse a
    retry across a dropped connection. The first call creates and journals the
    payment and then dies on the wire; the retry with the same key must answer
    with that same payment, not take a second one."""
    with unit("square") as started:
        started.add_chaos_rule(RESET_RULE)

        with pytest.raises(httpx.RemoteProtocolError):
            _pay(started)

        retry = _pay(started)
        assert retry.status_code == 200, retry.text
        assert "vendorfake-fault" not in retry.headers
        assert "vendorfake-rule" not in retry.headers
        assert retry.headers.get("x-unit-idempotent-replay") == "true"

        payment = retry.json()["payment"]
        assert _payments_held(started) == 1
        held = started.client.get(f"/v2/payments/{payment['id']}", headers=_auth(started))
        assert held.status_code == 200, held.text
        assert held.json()["payment"]["id"] == payment["id"]


def test_a_garbage_body_replays_the_clean_committed_payment() -> None:
    """``malformed_body`` reaches the caller -- it is a real 200 whose body is
    not JSON -- so the caller cannot tell whether the vendor took the payment.
    The retry answers the clean, committed one, and there is still exactly one
    payment."""
    with unit("square") as started:
        started.add_chaos_rule(GARBAGE_RULE)

        garbage = _pay(started)
        assert garbage.status_code == 200
        assert garbage.headers["vendorfake-fault"] == "malformed_body"
        with pytest.raises(ValueError):
            garbage.json()

        retry = _pay(started)
        assert retry.status_code == 200, retry.text
        assert "vendorfake-fault" not in retry.headers
        assert retry.headers.get("x-unit-idempotent-replay") == "true"

        payment = retry.json()["payment"]
        assert payment["status"] == "COMPLETED"
        assert _payments_held(started) == 1
        held = started.client.get(f"/v2/payments/{payment['id']}", headers=_auth(started))
        assert held.json()["payment"]["id"] == payment["id"]


# ---------------------------------------------------------------------------
# The armed decision is paid out on whatever answer the pipeline produces --
# a replay included, because a vendor's network cannot tell a retry from a
# first attempt.
# ---------------------------------------------------------------------------


def test_a_response_fault_budget_counts_only_faults_the_caller_observed() -> None:
    """``times: 2`` means two dropped connections the caller has to survive,
    not "two requests, one of which quietly replayed".

    The fault is chosen before the idempotency lookup and is applied after it,
    so the replay is reset just as the first answer was. The budget is spent
    only on answers that reached the caller as faults, which is why the third
    call -- the one the rule no longer covers -- is a clean replay and a call
    on a fresh key afterwards is a clean creation."""
    with unit("square") as started:
        started.add_chaos_rule(RESET_TWICE_RULE)

        with pytest.raises(httpx.RemoteProtocolError):
            _pay(started)
        with pytest.raises(httpx.RemoteProtocolError):
            _pay(started)

        replay = _pay(started)
        assert replay.status_code == 200, replay.text
        assert replay.headers.get("x-unit-idempotent-replay") == "true"
        assert "vendorfake-fault" not in replay.headers
        assert "vendorfake-rule" not in replay.headers

        payment = replay.json()["payment"]
        assert payment["status"] == "COMPLETED"
        # Three calls on one key, one payment: the two faulted answers were the
        # same commit as this clean one.
        assert _payments_held(started) == 1
        held = started.client.get(f"/v2/payments/{payment['id']}", headers=_auth(started))
        assert held.status_code == 200, held.text
        assert held.json()["payment"]["id"] == payment["id"]

        fresh = _pay(started, key="pay-2")
        assert fresh.status_code == 200, fresh.text
        assert "vendorfake-fault" not in fresh.headers
        assert "x-unit-idempotent-replay" not in fresh.headers
        assert fresh.json()["payment"]["id"] != payment["id"]
        assert _payments_held(started) == 2


def test_the_request_log_fault_column_matches_the_response_on_a_replay() -> None:
    """A row claiming ``fault: connection_reset`` for a call the caller got a
    clean 200 on is the request log lying about the one thing it exists to
    answer. The two faulted calls carry the fault and the rule that armed it;
    the clean replay carries neither."""
    with unit("square") as started:
        started.add_chaos_rule(RESET_TWICE_RULE)

        with pytest.raises(httpx.RemoteProtocolError):
            _pay(started)
        with pytest.raises(httpx.RemoteProtocolError):
            _pay(started)
        replay = _pay(started)
        assert replay.status_code == 200, replay.text

        rows = started.requests(route="POST /v2/payments")
        assert len(rows) == 3, rows
        newest, second, first = rows  # newest first
        assert (first["fault"], first["rule_id"]) == ("connection_reset", "drop")
        assert (second["fault"], second["rule_id"]) == ("connection_reset", "drop")
        # ``as_json`` drops nulls, so "no fault" is an absent key, not a
        # present ``None`` -- asserted through ``get`` for exactly that reason.
        assert (newest.get("fault"), newest.get("rule_id"), newest["status"]) == (None, None, 200)


def test_a_faulted_replay_never_rewrites_the_stored_answer() -> None:
    """Faulting a replay must not feed the fault back into the store. Step 8 --
    handler plus ``put_idempotent`` -- does not run at all on a replay, so the
    record stays the clean answer the first call committed: no
    ``vendorfake-fault``/``vendorfake-rule`` header, and no transport directive
    (:class:`IdempotencyRecord` has nowhere to put one, which is the defect
    storing a faulted response would re-create)."""
    with unit("square") as started:
        started.add_chaos_rule(RESET_TWICE_RULE)

        with pytest.raises(httpx.RemoteProtocolError):
            _pay(started)
        committed = _stored(started)
        assert committed is not None

        with pytest.raises(httpx.RemoteProtocolError):
            _pay(started)
        after_replay = _stored(started)
        assert after_replay == committed

        assert after_replay is not None
        assert after_replay.status == 200
        assert "vendorfake-fault" not in after_replay.headers
        assert "vendorfake-rule" not in after_replay.headers
        assert "x-unit-idempotent-replay" not in after_replay.headers
        body = json.loads(b64url_decode(after_replay.body_b64))
        assert body["payment"]["status"] == "COMPLETED"


# ---------------------------------------------------------------------------
# A request-scope fault is the other case: nothing was committed.
# ---------------------------------------------------------------------------


def test_a_request_scope_fault_leaves_the_key_unconsumed_and_the_retry_pays_once() -> None:
    """``server_error`` raises before ``route.handler(args)`` runs, so no
    payment was created and nothing is recorded against the key. The retry is
    the call that creates the payment -- and creates it exactly once."""
    with unit("square") as started:
        started.add_chaos_rule(SERVER_ERROR_RULE)

        failed = _pay(started)
        assert failed.status_code == 500
        assert _payments_held(started) == 0

        retry = _pay(started)
        assert retry.status_code == 200, retry.text
        assert "x-unit-idempotent-replay" not in retry.headers
        assert _payments_held(started) == 1

        again = _pay(started)
        assert again.status_code == 200, again.text
        assert again.headers.get("x-unit-idempotent-replay") == "true"
        assert again.json()["payment"]["id"] == retry.json()["payment"]["id"]
        assert _payments_held(started) == 1
