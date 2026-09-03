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

from typing import Any

import httpx
import pytest

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

SERVER_ERROR_RULE: dict[str, Any] = {
    "id": "five-hundred",
    "scope": "request",
    "fault": "server_error",
    "match": {"route": "POST /v2/payments"},
    "when": {"times": 1},
}


def _auth(started: StartedUnit[Any]) -> dict[str, str]:
    return {"authorization": f"Bearer {started.seed.access_token}"}


def _payment_body(amount: int = 500) -> dict[str, Any]:
    return {
        "idempotency_key": IDEMPOTENCY_KEY,
        "source_id": "EXTERNAL",
        "amount_money": {"amount": amount},
        "external_details": {"type": "OTHER", "source": "Food Delivery Service"},
    }


def _pay(started: StartedUnit[Any]) -> httpx.Response:
    return started.client.post("/v2/payments", json=_payment_body(), headers=_auth(started))


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
