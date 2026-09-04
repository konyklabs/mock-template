"""A response-phase fault is paid out on the answers that *raise*, too.

The pipeline draws a chaos decision at step 3 and applies a response-phase
fault at step 9. Four paths never reach step 9: they leave by raising a
``UnitError`` that ``Unit.handle``'s error-shaping block turns into the
vendor's own 401, 403 or 400. Before this was fixed, such a request drew the
fault, spent the rule's ``when.times`` budget, logged ``fault:
connection_reset`` -- and handed the caller a clean error. The budget was gone
and nothing had been faulted.

So the error-shaping block applies the fault to the shaped error, and each
test here asserts the same three things about one raising path:

1. the caller's answer really carries the fault (a dropped connection, here),
2. the request-log row's ``fault``/``rule_id`` match what the caller got, and
3. the budget was spent exactly once, so the next call is clean.

The negative at the bottom is the other half: a *request*-phase fault raises
its own ``UnitError`` at step 4, and must not be applied a second time on the
way out. ``tests/unit/test_idempotency_under_faults.py`` covers the paths that
do reach step 9.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from vendorfake.testing import StartedUnit, unit

IDEMPOTENCY_KEY = "pay-1"


def _reset_rule(rule_id: str = "drop") -> dict[str, Any]:
    """One dropped connection on ``POST /v2/payments`` -- the whole budget."""
    return {
        "id": rule_id,
        "scope": "request",
        "fault": "connection_reset",
        "match": {"route": "POST /v2/payments"},
        "when": {"times": 1},
    }


RATE_LIMIT_RULE: dict[str, Any] = {
    "id": "flaky",
    "scope": "request",
    "fault": "rate_limit",
    "match": {"route": "POST /v2/payments"},
    "when": {"times": 1},
}


def _payment_body(amount: int = 500, key: str | None = IDEMPOTENCY_KEY) -> dict[str, Any]:
    body: dict[str, Any] = {
        "source_id": "EXTERNAL",
        "amount_money": {"amount": amount},
        "external_details": {"type": "OTHER", "source": "Food Delivery Service"},
    }
    if key is not None:
        body["idempotency_key"] = key
    return body


def _pay(
    started: StartedUnit[Any],
    *,
    headers: dict[str, str] | None = None,
    amount: int = 500,
    key: str | None = IDEMPOTENCY_KEY,
) -> httpx.Response:
    return started.client.post(
        "/v2/payments",
        json=_payment_body(amount=amount, key=key),
        headers=dict(started.seed.auth) if headers is None else headers,
    )


def _rows(started: StartedUnit[Any]) -> list[dict[str, Any]]:
    """The payment rows, newest first."""
    return started.requests(route="POST /v2/payments")


def _assert_paid_out_once(started: StartedUnit[Any], *, status: int, rule_id: str = "drop") -> None:
    """The shared tail of every test here: the row the raise produced carries
    the fault the caller was handed, and the budget is now empty."""
    faulted = _rows(started)[0]
    assert (faulted["status"], faulted.get("fault"), faulted.get("rule_id")) == (status, "connection_reset", rule_id)

    clean = _pay(started, key="pay-clean")
    assert clean.status_code == 200, clean.text
    assert "vendorfake-fault" not in clean.headers
    assert _rows(started)[0].get("fault") is None


def test_an_unauthorized_call_is_faulted_and_spends_the_budget_it_drew() -> None:
    """Step 5, the 401 path. The decision was drawn before the token was even
    looked at, so the caller has to be handed the dropped connection the rule
    promised -- not a clean 401 with the budget quietly gone."""
    with unit("square") as started:
        started.add_chaos_rule(_reset_rule())

        with pytest.raises(httpx.RemoteProtocolError):
            _pay(started, headers={"Authorization": "Bearer not-a-real-token"})

        _assert_paid_out_once(started, status=401)


def test_a_forbidden_scope_is_faulted_and_spends_the_budget_it_drew() -> None:
    """Step 5 again, the 403 path: a real token missing ``PAYMENTS_WRITE``.
    The scope check is the kernel's, not the vendor's, and it raises just as
    the credential check does."""
    with unit("square") as started:
        started.add_chaos_rule(_reset_rule())

        with pytest.raises(httpx.RemoteProtocolError):
            _pay(started, headers=dict(started.seed.read_only_auth))

        _assert_paid_out_once(started, status=403)


def test_a_missing_idempotency_key_is_faulted_and_spends_the_budget_it_drew() -> None:
    """Step 7's other exit. ``POST /v2/payments`` declares
    ``required=True``, so a body without ``idempotency_key`` raises before any
    handler runs -- and after the fault was armed."""
    with unit("square") as started:
        started.add_chaos_rule(_reset_rule())

        with pytest.raises(httpx.RemoteProtocolError):
            _pay(started, key=None)

        _assert_paid_out_once(started, status=400)


def test_an_idempotency_conflict_is_faulted_and_spends_the_budget_it_drew() -> None:
    """Step 7's ``on_mismatch="conflict"``: a reused key with a different
    body. ``_replay`` raises rather than returning, so this answer never
    reaches step 9 either -- and a transport fault can hit any answer, a
    refusal included."""
    with unit("square") as started:
        first = _pay(started)
        assert first.status_code == 200, first.text

        started.add_chaos_rule(_reset_rule())
        with pytest.raises(httpx.RemoteProtocolError):
            _pay(started, amount=999)

        _assert_paid_out_once(started, status=400)


def test_a_request_phase_fault_is_not_applied_a_second_time_on_the_way_out() -> None:
    """The negative. ``rate_limit`` raises its own ``UnitError`` at step 4 and
    is not in ``RESPONSE_PHASE_FAULTS``, so the error-shaping block leaves it
    alone: the caller gets one 429 carrying one fault stamp, and the log has
    one row for it. Applying it twice would double-count nothing visible here
    but would put a transport directive on a refusal that never asked for
    one."""
    with unit("square") as started:
        started.add_chaos_rule(RATE_LIMIT_RULE)

        limited = _pay(started)
        assert limited.status_code == 429, limited.text
        assert limited.headers["vendorfake-fault"] == "rate_limit"
        assert limited.headers["vendorfake-rule"] == "flaky"

        rows = _rows(started)
        assert len(rows) == 1, rows
        assert (rows[0]["status"], rows[0].get("fault"), rows[0].get("rule_id")) == (429, "rate_limit", "flaky")

        clean = _pay(started)
        assert clean.status_code == 200, clean.text
        assert "vendorfake-fault" not in clean.headers


# ---------------------------------------------------------------------------
# The payout is attempted once, because attempting it can itself raise.
# ---------------------------------------------------------------------------

BAD_OPS_RULE: dict[str, Any] = {
    "id": "bm",
    "scope": "request",
    "fault": "body_mutation",
    "params": {},
    "match": {"route": "POST /v2/payments"},
    "when": {"times": 1},
}

BAD_MODE_RULE: dict[str, Any] = {
    "id": "mb",
    "scope": "request",
    "fault": "malformed_body",
    "params": {"mode": "nope"},
    "match": {"route": "POST /v2/payments"},
    "when": {"times": 1},
}


def test_a_body_mutation_with_no_ops_answers_the_diagnostic_naming_the_rule() -> None:
    """``params`` are not validated when a rule is added -- ``chaos/rules.py``
    has no route table and no vendor in reach -- so ``body_mutation`` rejects
    an empty ``ops`` at fire time, from inside step 9.

    That raise is a ``UnitError`` like any other and must leave as the vendor's
    400. It would not if the error path tried the payout again: the second
    call raises from inside the ``except`` clause and the exception escapes
    ``handle``, which a served unit turns into a framework 500 -- the one
    document this kernel promises no caller ever receives."""
    with unit("square") as started:
        started.add_chaos_rule(BAD_OPS_RULE)

        answered = _pay(started)
        assert answered.status_code == 400, answered.text
        assert answered.headers["x-unit-error"] == "invalid_value"
        assert "bm" in answered.text


def test_a_malformed_body_with_an_unknown_mode_answers_the_diagnostic_too() -> None:
    """The other fault that rejects its own params at fire time. Same shape,
    same reason: one attempt, and the diagnostic reaches the caller."""
    with unit("square") as started:
        started.add_chaos_rule(BAD_MODE_RULE)

        answered = _pay(started)
        assert answered.status_code == 400, answered.text
        assert answered.headers["x-unit-error"] == "invalid_value"
        assert "mb" in answered.text


# ---------------------------------------------------------------------------
# The payout attempt on a shaped error can fail on its own terms. It answers
# the diagnostic; it never escapes ``handle``.
# ---------------------------------------------------------------------------

BAD_POINTER_RULE: dict[str, Any] = {
    "id": "bm",
    "scope": "request",
    "fault": "body_mutation",
    "params": {"ops": [{"op": "replace", "pointer": "/status", "value": "CORRUPT"}]},
    "match": {"route": "POST /v2/payments"},
    "when": {"times": 1},
}


def test_a_pointer_that_the_error_envelope_lacks_answers_the_diagnostic() -> None:
    """The first attempt is the one that raises here, so the
    already-attempted flag cannot help.

    ``/status`` exists in a payment and not in the 403 envelope the scope
    check produced, so the payout fails inside ``handle``'s ``except
    UnitError`` block. Without a nested ``try`` the raise escapes ``handle``
    entirely -- a served unit answers a framework 500, which is the one
    document this kernel promises no caller ever gets."""
    with unit("square") as started:
        started.add_chaos_rule(BAD_POINTER_RULE)

        answered = _pay(started, headers=dict(started.seed.read_only_auth))
        assert answered.status_code == 400, answered.text
        assert answered.headers["x-unit-error"] == "invalid_value"
        assert "bm" in answered.text
        assert "/status" in answered.text

        row = _rows(started)[0]
        assert (row["status"], row.get("fault"), row.get("rule_id")) == (400, "body_mutation", "bm")


def test_a_bad_mode_on_an_unauthorized_call_answers_the_diagnostic() -> None:
    """The same shape on the 401 path, with the fault that rejects its own
    ``params.mode``."""
    with unit("square") as started:
        started.add_chaos_rule(BAD_MODE_RULE)

        answered = _pay(started, headers={"Authorization": "Bearer not-a-real-token"})
        assert answered.status_code == 400, answered.text
        assert answered.headers["x-unit-error"] == "invalid_value"
        assert "mb" in answered.text


# ---------------------------------------------------------------------------
# A corrupted answer keeps the status it had.
# ---------------------------------------------------------------------------


def _malformed_rule(mode: str, *, status: int | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"mode": mode}
    if status is not None:
        params["status"] = status
    return {
        "id": "mb",
        "scope": "request",
        "fault": "malformed_body",
        "params": params,
        "match": {"route": "POST /v2/payments"},
        "when": {"times": 1},
    }


def test_a_truncated_error_body_keeps_the_errors_own_status() -> None:
    """A 401 whose body is cut in half is still a 401. Reporting 200 would
    tell a consumer's code the call succeeded, which is the opposite of what
    happened -- and the caller cannot read the body to find out, because
    truncating it is the point."""
    with unit("square") as started:
        started.add_chaos_rule(_malformed_rule("truncate"))

        answered = _pay(started, headers={"Authorization": "Bearer not-a-real-token"})
        assert answered.status_code == 401, answered.text
        assert answered.headers["vendorfake-fault"] == "malformed_body"
        with pytest.raises(ValueError):
            answered.json()


def test_a_truncated_success_body_still_answers_two_hundred() -> None:
    """The success case is unchanged: it inherits its own 200."""
    with unit("square") as started:
        started.add_chaos_rule(_malformed_rule("truncate"))

        answered = _pay(started)
        assert answered.status_code == 200, answered.text
        assert answered.headers["vendorfake-fault"] == "malformed_body"
        with pytest.raises(ValueError):
            answered.json()


def test_an_html_error_page_still_defaults_to_five_hundred_and_two() -> None:
    """``html`` keeps its own default whatever it is put in front of: a proxy
    that answers an HTML page does not consult the origin's status."""
    with unit("square") as started:
        started.add_chaos_rule(_malformed_rule("html"))

        answered = _pay(started, headers={"Authorization": "Bearer not-a-real-token"})
        assert answered.status_code == 502, answered.text
        assert answered.headers["content-type"] == "text/html"


def test_an_explicit_status_wins_on_an_error_and_on_a_success() -> None:
    """``params.status`` is still the last word either way."""
    with unit("square") as started:
        started.add_chaos_rule(_malformed_rule("truncate", status=503))
        failed = _pay(started, headers={"Authorization": "Bearer not-a-real-token"})
        assert failed.status_code == 503, failed.text

    with unit("square") as started:
        started.add_chaos_rule(_malformed_rule("truncate", status=503))
        ok = _pay(started)
        assert ok.status_code == 503, ok.text
