"""Response-scope faults: what they do to a real ``UnitResponse``, in isolation
from any vendor or transport.

FOR: the mechanics ``apply_response_fault`` owns -- malformed bodies, JSON
pointer mutation, and the transport directives -- pinned against hand-built
responses so a failure here points at the fault engine and not at a vendor's
surface. The consumer-visible shape of these five faults (against Square and
Clover) is ``tests/unit/test_transport_faults_consumer.py``.
"""

from __future__ import annotations

import json

import pytest

from vendorfake.core.chaos.engine import ChaosDecision
from vendorfake.core.chaos.faults import (
    FAULT_DESCRIPTIONS,
    FAULT_PARAM_KEYS,
    RESPONSE_PHASE_FAULTS,
    apply_request_fault,
    apply_response_fault,
    is_transport_fault,
)
from vendorfake.core.chaos.rules import BUILTIN_FAULTS
from vendorfake.core.kernel.types import TransportDirective, UnitError, UnitResponse
from vendorfake.core.logging import SilentLogger
from vendorfake.core.time.clock import Clock

LOG = SilentLogger()

TOKEN_BODY = json.dumps({"access_token": "tok_abc", "expires_at": "2024-01-01T00:00:00Z", "merchant_id": "M1"}).encode(
    "utf-8"
)


def _response(*, status: int = 200, headers: dict[str, str] | None = None, body: bytes = TOKEN_BODY) -> UnitResponse:
    return UnitResponse(status=status, headers=headers or {"content-type": "application/json"}, body=body)


def _decision(fault: str, rule_id: str = "r1", **params: object) -> ChaosDecision:
    return ChaosDecision(rule_id=rule_id, fault=fault, params=dict(params), occurrence=1)


def _apply(fault: str, **params: object) -> UnitResponse:
    return apply_response_fault(_decision(fault, **params), _response(), log=LOG)


# ---------------------------------------------------------------------------
# apply_request_fault must not touch these -- apply_response_fault owns them.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fault", sorted(RESPONSE_PHASE_FAULTS))
@pytest.mark.parametrize("phase", ["pre", "post_auth"])
def test_apply_request_fault_does_nothing_for_a_response_scope_fault(fault: str, phase: str) -> None:
    """No exception, and no "unknown fault" warning either -- these are faults
    this core knows well; they just do not fire at this call site."""
    warned: list[object] = []

    class Recording(SilentLogger):
        def warn(self, msg, fields=None):  # type: ignore[no-untyped-def]
            warned.append((msg, fields))

    apply_request_fault(_decision(fault), phase, clock=Clock("real"), log=Recording())  # type: ignore[arg-type]
    assert warned == []


def test_the_five_response_scope_faults_are_the_catalogue_entries() -> None:
    assert {spec.name for spec in BUILTIN_FAULTS if spec.provenance == "transport"} == RESPONSE_PHASE_FAULTS


def test_apply_response_fault_is_a_no_op_for_a_request_scope_fault() -> None:
    """The dispatcher only acts on its own five; anything else is handed back
    unchanged, which is what lets ``kernel/unit.py`` call it unconditionally
    whenever a decision exists."""
    original = _response()
    assert apply_response_fault(_decision("rate_limit"), original, log=LOG) is original


# ---------------------------------------------------------------------------
# malformed_body
# ---------------------------------------------------------------------------


def test_malformed_body_html_is_a_502_by_default() -> None:
    answered = _apply("malformed_body", mode="html")
    assert answered.status == 502
    assert answered.headers["content-type"] == "text/html"
    assert b"html" in answered.body.lower()


def test_malformed_body_html_status_is_overridable() -> None:
    answered = _apply("malformed_body", mode="html", status=503)
    assert answered.status == 503


def test_malformed_body_empty_is_zero_bytes_with_json_content_type() -> None:
    answered = _apply("malformed_body", mode="empty")
    assert answered.status == 200
    assert answered.body == b""
    assert answered.headers["content-type"] == "application/json"


def test_malformed_body_invalid_json_does_not_parse() -> None:
    answered = _apply("malformed_body", mode="invalid_json")
    assert answered.status == 200
    with pytest.raises(ValueError):
        json.loads(answered.body)


def test_malformed_body_truncate_is_the_first_half_of_the_real_bytes() -> None:
    real = _response()
    answered = apply_response_fault(_decision("malformed_body", mode="truncate"), real, log=LOG)
    assert answered.body == real.body[: len(real.body) // 2]
    assert len(answered.body) < len(real.body)


def test_malformed_body_rejects_an_unknown_mode() -> None:
    with pytest.raises(UnitError):
        _apply("malformed_body", mode="shredded")


@pytest.mark.parametrize("mode", ["invalid_json", "html", "empty", "truncate"])
def test_every_malformed_body_mode_is_stamped(mode: str) -> None:
    answered = _apply("malformed_body", mode=mode, rule_id="the-rule")
    assert answered.headers["vendorfake-fault"] == "malformed_body"


# ---------------------------------------------------------------------------
# body_mutation
# ---------------------------------------------------------------------------


def test_body_mutation_remove_drops_the_key() -> None:
    answered = _apply("body_mutation", ops=[{"op": "remove", "pointer": "/access_token"}])
    assert "access_token" not in json.loads(answered.body)


def test_body_mutation_replace_sets_the_value() -> None:
    answered = _apply("body_mutation", ops=[{"op": "replace", "pointer": "/access_token", "value": ""}])
    assert json.loads(answered.body)["access_token"] == ""


def test_body_mutation_retype_number_to_string() -> None:
    body = json.dumps({"access_token_expiration": 1677875430}).encode("utf-8")
    answered = apply_response_fault(
        _decision("body_mutation", ops=[{"op": "retype", "pointer": "/access_token_expiration"}]),
        _response(body=body),
        log=LOG,
    )
    document = json.loads(answered.body)
    assert document["access_token_expiration"] == "1677875430"
    assert isinstance(document["access_token_expiration"], str)


def test_body_mutation_retype_string_to_number_when_parseable() -> None:
    body = json.dumps({"expires_at": "42"}).encode("utf-8")
    answered = apply_response_fault(
        _decision("body_mutation", ops=[{"op": "retype", "pointer": "/expires_at"}]),
        _response(body=body),
        log=LOG,
    )
    document = json.loads(answered.body)
    assert document["expires_at"] == 42


def test_body_mutation_retype_unparseable_string_is_an_error() -> None:
    # No leading numeric prefix at all -- unlike "2024-01-01...", which
    # `js_parse_float` would happily read as `2024.0` (see its docstring).
    body = json.dumps({"expires_at": "not-a-number"}).encode("utf-8")
    with pytest.raises(UnitError):
        apply_response_fault(
            _decision("body_mutation", ops=[{"op": "retype", "pointer": "/expires_at"}]),
            _response(body=body),
            log=LOG,
        )


def test_body_mutation_retype_honours_an_explicit_target() -> None:
    body = json.dumps({"access_token_expiration": 1677875430}).encode("utf-8")
    answered = apply_response_fault(
        _decision("body_mutation", ops=[{"op": "retype", "pointer": "/access_token_expiration", "as": "null"}]),
        _response(body=body),
        log=LOG,
    )
    assert json.loads(answered.body)["access_token_expiration"] is None


def test_body_mutation_requires_a_non_empty_ops_list() -> None:
    with pytest.raises(UnitError):
        _apply("body_mutation", ops=[])


def test_body_mutation_on_a_route_whose_response_is_not_json_is_a_clear_error() -> None:
    """Fire-time, not rule-add time: see ``core/chaos/faults.py``'s docstring
    on ``_body_mutation`` for why."""
    not_json = _response(body=b"plain text", headers={"content-type": "text/plain"})
    with pytest.raises(UnitError, match="is not JSON"):
        apply_response_fault(
            _decision("body_mutation", ops=[{"op": "remove", "pointer": "/access_token"}]), not_json, log=LOG
        )


def test_body_mutation_a_pointer_that_does_not_exist_is_a_clear_error() -> None:
    with pytest.raises(UnitError):
        _apply("body_mutation", ops=[{"op": "remove", "pointer": "/nonexistent"}])


def test_body_mutation_applies_every_op_in_order() -> None:
    answered = _apply(
        "body_mutation",
        ops=[
            {"op": "remove", "pointer": "/expires_at"},
            {"op": "replace", "pointer": "/access_token", "value": "replaced"},
        ],
    )
    document = json.loads(answered.body)
    assert "expires_at" not in document
    assert document["access_token"] == "replaced"


def test_body_mutation_is_stamped() -> None:
    answered = _apply("body_mutation", ops=[{"op": "remove", "pointer": "/access_token"}], rule_id="mutate-me")
    assert answered.headers["vendorfake-fault"] == "body_mutation"
    assert answered.headers["vendorfake-rule"] == "mutate-me"


def test_body_mutation_preserves_status_and_content_type() -> None:
    real = _response(status=200, headers={"content-type": "application/json", "x-unit-request-id": "abc"})
    answered = apply_response_fault(
        _decision("body_mutation", ops=[{"op": "remove", "pointer": "/access_token"}]), real, log=LOG
    )
    assert answered.status == 200
    assert answered.headers["x-unit-request-id"] == "abc"


# ---------------------------------------------------------------------------
# directives: connection_reset, empty_response, slow_body
# ---------------------------------------------------------------------------


def test_connection_reset_attaches_the_directive_and_leaves_the_body_alone() -> None:
    real = _response()
    answered = apply_response_fault(_decision("connection_reset"), real, log=LOG)
    assert answered.transport == TransportDirective(kind="connection_reset")
    assert answered.body == real.body
    assert answered.status == real.status


def test_empty_response_attaches_the_directive() -> None:
    answered = apply_response_fault(_decision("empty_response"), _response(), log=LOG)
    assert answered.transport == TransportDirective(kind="empty_response")


def test_slow_body_attaches_the_directive_with_its_parameters() -> None:
    answered = _apply("slow_body", chunk_bytes=16, chunk_delay_ms=250)
    assert answered.transport == TransportDirective(kind="slow_body", chunk_bytes=16, chunk_delay_ms=250)


def test_slow_body_defaults_match_the_catalogue() -> None:
    answered = _apply("slow_body")
    assert answered.transport == TransportDirective(kind="slow_body", chunk_bytes=64, chunk_delay_ms=100)


@pytest.mark.parametrize("fault", ["connection_reset", "empty_response", "slow_body"])
def test_every_directive_is_stamped(fault: str) -> None:
    answered = apply_response_fault(_decision(fault, rule_id="directive-rule"), _response(), log=LOG)
    assert answered.headers["vendorfake-fault"] == fault
    assert answered.headers["vendorfake-rule"] == "directive-rule"


# ---------------------------------------------------------------------------
# is_transport_fault
# ---------------------------------------------------------------------------


def test_is_transport_fault_reads_the_stamp() -> None:
    assert is_transport_fault(_apply("malformed_body", mode="empty")) is True
    assert is_transport_fault(_response()) is False


# ---------------------------------------------------------------------------
# the catalogue: FAULT_PARAM_KEYS / FAULT_DESCRIPTIONS stay in lock-step.
# ---------------------------------------------------------------------------


def test_fault_param_keys_covers_the_five_new_kinds() -> None:
    for name in RESPONSE_PHASE_FAULTS:
        assert name in FAULT_PARAM_KEYS, name


def test_fault_descriptions_covers_every_catalogued_fault() -> None:
    assert set(FAULT_DESCRIPTIONS) == {spec.name for spec in BUILTIN_FAULTS}


def test_fault_descriptions_is_derived_from_the_catalogue_not_retyped() -> None:
    for spec in BUILTIN_FAULTS:
        assert FAULT_DESCRIPTIONS[spec.name] == spec.summary
