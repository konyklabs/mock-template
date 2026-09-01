"""The error table and the documented ErrorMessage envelope."""

from __future__ import annotations

import re

from tests.unit.toast.conftest import fake_ctx
from vendorfake.core.kernel.types import UnitError, UnitErrorKind, UnitRequest
from vendorfake.core.time.clock import Clock
from vendorfake.toast.errors import (
    CATALOGUE_RATE_LIMIT_RESET,
    CATALOGUE_REQUEST_ID,
    CODE_PAYMENT_AMOUNT_EMPTY,
    RATE_LIMIT_RESET_HEADER,
    TOAST_CODE_INFO_KEY,
    TOAST_ERROR_TABLE,
    ToastErrorShaper,
)
from vendorfake.toast.ids import ToastRequestIds

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

DOCUMENTED_KEYS = [
    "status",
    "code",
    "message",
    "messageKey",
    "fieldName",
    "link",
    "requestId",
    "developerMessage",
    "errors",
    "canRetry",
]
"""The keys of the example on apiResponsesAndErrors.html, in the page's order."""


def shaper(**kwargs: object) -> ToastErrorShaper:
    return ToastErrorShaper(request_ids=ToastRequestIds(1), **kwargs)  # type: ignore[arg-type]


def request(path: str = "/orders/v2/orders") -> UnitRequest:
    return UnitRequest(
        id="req_1",
        method="GET",
        path=path,
        query={},
        headers={},
        raw_body=b"",
        transport="inprocess",
        received_at="2026-08-30T00:00:00.000Z",
    )


def test_the_table_is_exhaustive_over_the_twenty_core_kinds() -> None:
    assert set(TOAST_ERROR_TABLE) == set(UnitErrorKind)


def test_the_envelope_is_the_documented_shape_with_its_nulls_in_the_pages_order() -> None:
    body = shaper(sidecar=False).shape(UnitError(UnitErrorKind.NOT_FOUND), fake_ctx()).body
    assert list(body) == DOCUMENTED_KEYS
    assert body["status"] == 404
    assert body["messageKey"] is None and body["fieldName"] is None and body["link"] is None
    assert body["developerMessage"] is None and body["canRetry"] is None
    assert body["errors"] == []
    assert UUID.fullmatch(body["requestId"])


def test_the_documented_example_reproduces_with_the_one_documented_code() -> None:
    """{"status":400,"code":10025,"message":"Payment amount cannot be empty",...}"""
    err = UnitError(
        UnitErrorKind.MISSING_FIELD,
        detail="Payment amount cannot be empty",
        field="amount",
        info={TOAST_CODE_INFO_KEY: CODE_PAYMENT_AMOUNT_EMPTY},
    )
    body = shaper(sidecar=False).shape(err, fake_ctx()).body
    assert body["status"] == 400
    assert body["code"] == 10025
    assert body["message"] == "Payment amount cannot be empty"


def test_the_judgment_codes_never_reuse_10025_and_are_five_digits() -> None:
    codes = [mapping.code for mapping in TOAST_ERROR_TABLE.values()]
    assert len(set(codes)) == len(codes)
    assert CODE_PAYMENT_AMOUNT_EMPTY not in codes
    assert all(10000 <= code <= 19999 for code in codes)


def test_forbidden_scope_is_403_and_distinct_from_the_401_token_kinds() -> None:
    """DOCUMENTED: 403 for a missing scope on POST /prices; 401 for an invalid
    or expired token. No conflation here, unlike Clover."""
    assert TOAST_ERROR_TABLE[UnitErrorKind.FORBIDDEN_SCOPE].status == 403
    assert TOAST_ERROR_TABLE[UnitErrorKind.FORBIDDEN_SCOPE].provenance == "documented"
    for kind in (UnitErrorKind.UNAUTHORIZED, UnitErrorKind.TOKEN_EXPIRED, UnitErrorKind.TOKEN_REVOKED):
        assert TOAST_ERROR_TABLE[kind].status == 401, kind
    assert TOAST_ERROR_TABLE[UnitErrorKind.NOT_FOUND].status == 404
    assert TOAST_ERROR_TABLE[UnitErrorKind.RATE_LIMITED].status == 429


def test_a_handlers_own_detail_wins_over_the_tables_message() -> None:
    body = shaper().shape(UnitError(UnitErrorKind.BAD_REQUEST, detail="The GUID was malformed"), fake_ctx()).body
    assert body["message"] == "The GUID was malformed"
    bare = shaper().shape(UnitError(UnitErrorKind.BAD_REQUEST), fake_ctx()).body
    assert bare["message"] == "Bad request."


def test_the_sidecar_carries_kind_provenance_and_field_and_switches_off() -> None:
    err = UnitError(UnitErrorKind.MISSING_FIELD, field="checks", info={"extra": "kept", TOAST_CODE_INFO_KEY: 10025})
    with_sidecar = shaper(sidecar=True).shape(err, fake_ctx()).body
    # The sidecar is the core's (kernel/shaping.py) and spreads the whole
    # info document, the toast_code override included -- debug data, on purpose.
    assert with_sidecar["unit_error"] == {
        "extra": "kept",
        TOAST_CODE_INFO_KEY: 10025,
        "kind": "missing_field",
        "status_provenance": "judgment",
        "field": "checks",
    }
    assert with_sidecar["fieldName"] is None  # reserved for future use; the field travels in the sidecar
    assert "unit_error" not in shaper(sidecar=False).shape(err, fake_ctx()).body


def test_rate_limited_carries_the_four_documented_headers_and_the_switch_removes_retry_after() -> None:
    clock = Clock("virtual", "2026-08-30T12:00:00.000Z")
    err = UnitError(UnitErrorKind.RATE_LIMITED, info={"retry_after_seconds": 7})
    headers = shaper().shape(err, fake_ctx(clock=clock)).headers
    assert headers["X-Toast-RateLimit-By"] == "ENDPOINT"
    assert headers["X-Toast-RateLimit-Remaining"] == "0"
    assert headers["X-Toast-RateLimit-Reset"] == str(int(clock.now() // 1000) + 7)
    assert headers["Retry-After"] == "7"
    assert shaper().shape(UnitError(UnitErrorKind.RATE_LIMITED), fake_ctx()).headers["Retry-After"] == "1"
    without = shaper(retry_after_header=False).shape(err, fake_ctx(clock=clock)).headers
    assert "Retry-After" not in without
    assert without["X-Toast-RateLimit-By"] == "ENDPOINT"
    assert "X-Toast-RateLimit-By" not in shaper().shape(UnitError(UnitErrorKind.CONFLICT), fake_ctx()).headers


def test_capability_disabled_names_the_capability_in_a_header() -> None:
    err = UnitError(UnitErrorKind.CAPABILITY_DISABLED, info={"capability": "orders"})
    assert shaper().shape(err, fake_ctx()).headers["x-unit-capability"] == "orders"


def test_request_ids_are_deterministic_per_seed_and_advance() -> None:
    """Two shapers on one seed answer the same requestId sequence -- which is
    what lets two units answer byte-identical error bodies (conformance C10)."""
    a, b = shaper(sidecar=False), shaper(sidecar=False)
    first = [a.shape(UnitError(UnitErrorKind.NOT_FOUND), fake_ctx()).body["requestId"] for _ in range(3)]
    second = [b.shape(UnitError(UnitErrorKind.NOT_FOUND), fake_ctx()).body["requestId"] for _ in range(3)]
    assert first == second
    assert len(set(first)) == 3


def test_not_found_names_the_route_listing() -> None:
    shaped = shaper().not_found(request(), fake_ctx(profile="full"))
    assert shaped.status == 404
    assert "/__unit/routes" in shaped.body["message"]
    assert shaped.body["unit_error"]["profile"] == "full"


def test_every_row_has_an_error_status_a_code_and_a_provenance() -> None:
    for kind, mapping in TOAST_ERROR_TABLE.items():
        assert 400 <= mapping.status <= 599, kind
        assert mapping.provenance in ("documented", "judgment"), kind
        assert mapping.message, kind


def test_describe_publishes_all_twenty_rows_with_codes() -> None:
    described = shaper().describe()
    assert set(described) == {kind.value for kind in UnitErrorKind}
    assert described["forbidden_scope"]["status"] == 403
    assert described["forbidden_scope"]["code"] == TOAST_ERROR_TABLE[UnitErrorKind.FORBIDDEN_SCOPE].code


# ---------------------------------------------------------------------------
# Describing is not refusing: GET /__unit/errors must consume nothing.
# ---------------------------------------------------------------------------


def test_a_described_error_takes_the_placeholder_id_and_draws_nothing() -> None:
    """The defect this pins: shaping a catalogue row drew a real request id,
    so a read-only GET advanced the stream twenty-one times and renumbered
    every id in the caller's remaining scenario."""
    ids = ToastRequestIds(1)
    subject = ToastErrorShaper(request_ids=ids, sidecar=False)  # type: ignore[arg-type]
    before = ids.draw_count
    described = subject.shape(UnitError(UnitErrorKind.NOT_FOUND), fake_ctx(), describing=True)
    assert described.body["requestId"] == CATALOGUE_REQUEST_ID
    assert ids.draw_count == before, f"describing drew {ids.draw_count - before} times from the request-id stream"


def test_a_real_refusal_still_draws_a_fresh_unique_id() -> None:
    """The half that must NOT change: a refusal that happened is identified."""
    ids = ToastRequestIds(1)
    subject = ToastErrorShaper(request_ids=ids, sidecar=False)  # type: ignore[arg-type]
    drawn = [subject.shape(UnitError(UnitErrorKind.NOT_FOUND), fake_ctx()).body["requestId"] for _ in range(3)]
    assert len(set(drawn)) == 3
    assert CATALOGUE_REQUEST_ID not in drawn
    assert all(UUID.fullmatch(value) for value in drawn)
    assert ids.draw_count > 0


def test_a_described_rate_limit_does_not_carry_the_live_clock() -> None:
    """The half that actually turned CI red, and the rarer one: the 429 row's
    ``X-Toast-RateLimit-Reset`` is ``floor(now/1000) + retry_after``, so two
    renderings of the *same* catalogue disagreed whenever they straddled a
    wall-clock second -- which a loaded runner does and a laptop does not.
    Same byte length either way, which is why it read as an id problem."""
    subject = shaper(sidecar=False)
    noon = fake_ctx(clock=Clock("virtual", "2026-08-30T12:00:00.000Z"))
    later = fake_ctx(clock=Clock("virtual", "2027-01-01T00:00:00.000Z"))
    described = [
        subject.shape(UnitError(UnitErrorKind.RATE_LIMITED), ctx, describing=True).headers[RATE_LIMIT_RESET_HEADER]
        for ctx in (noon, later)
    ]
    assert described == [CATALOGUE_RATE_LIMIT_RESET, CATALOGUE_RATE_LIMIT_RESET]
    # A real 429 still reports when its window resets, from the clock it ran on.
    live = subject.shape(UnitError(UnitErrorKind.RATE_LIMITED), noon).headers[RATE_LIMIT_RESET_HEADER]
    assert live != CATALOGUE_RATE_LIMIT_RESET
    assert int(live) == int(noon.clock.now() / 1000) + 1


def test_not_found_propagates_describing_to_the_body_it_shapes() -> None:
    """The no-route row is the one catalogue body that does not come from the
    table, so it needs the flag passed on or it keeps drawing alone."""
    ids = ToastRequestIds(1)
    subject = ToastErrorShaper(request_ids=ids, sidecar=False)  # type: ignore[arg-type]
    described = subject.not_found(request(), fake_ctx(profile="full"), describing=True)
    assert described.body["requestId"] == CATALOGUE_REQUEST_ID
    assert ids.draw_count == 0
    # Still the useful body, not a stub.
    assert "/__unit/routes" in described.body["message"]
    live = subject.not_found(request(), fake_ctx(profile="full"))
    assert live.body["requestId"] != CATALOGUE_REQUEST_ID
