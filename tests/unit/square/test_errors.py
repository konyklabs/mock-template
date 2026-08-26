"""The twenty-row error table, its provenance labels, and the envelope.

The table is a deliverable in its own right: a public-docs audit verified it
row by row, so these tests pin the parts that audit checked -- the exact set of
rows Square documents, the two rows carrying a further caveat, and the
envelope's byte shape against the example Square publishes.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.unit.square.conftest import fake_ctx
from vendorfake.core.kernel.types import UnitError, UnitErrorKind, UnitRequest
from vendorfake.core.util.json import dump_json
from vendorfake.square.errors import (
    PUBLISHED_ERROR_CODES,
    SQUARE_ERROR_TABLE,
    UNPUBLISHED_CODES,
    UNREACHABLE_CODES,
    ErrorCategory,
    ErrorCode,
    SquareErrorShaper,
)

#: The seven kinds whose HTTP status Square actually publishes. Written out
#: rather than derived from the table, so that relabelling a row is a failing
#: test and not a silent upgrade from "our reading" to "documented".
DOCUMENTED_KINDS = {
    UnitErrorKind.UNAUTHORIZED,
    UnitErrorKind.TOKEN_EXPIRED,
    UnitErrorKind.TOKEN_REVOKED,
    UnitErrorKind.FORBIDDEN_SCOPE,
    UnitErrorKind.IDEMPOTENCY_CONFLICT,
    UnitErrorKind.RATE_LIMITED,
}


def request(method: str = "GET", path: str = "/v2/nope") -> UnitRequest:
    return UnitRequest(
        id="req_1",
        method=method,
        path=path,
        query={},
        headers={},
        raw_body=b"",
        transport="inprocess",
        received_at="2026-08-25T00:00:00.000Z",
    )


def test_the_table_has_exactly_twenty_rows_one_per_kind() -> None:
    assert len(SQUARE_ERROR_TABLE) == 20
    assert set(SQUARE_ERROR_TABLE) == set(UnitErrorKind)


def test_every_row_is_a_4xx_or_5xx() -> None:
    for kind, mapping in SQUARE_ERROR_TABLE.items():
        assert 400 <= mapping.status <= 599, kind
        assert mapping.detail.strip()


def test_the_documented_rows_are_exactly_the_ones_square_publishes() -> None:
    labelled = {kind for kind, m in SQUARE_ERROR_TABLE.items() if m.provenance == "documented"}
    assert labelled == DOCUMENTED_KINDS
    assert len(labelled) == 6
    assert len(SQUARE_ERROR_TABLE) - len(labelled) == 14


def test_version_mismatch_is_labelled_beyond_judgment() -> None:
    """It is real, but it is not in the published enum and its category is not
    verified anywhere -- which "judgment" alone would understate."""
    row = SQUARE_ERROR_TABLE[UnitErrorKind.VERSION_CONFLICT]
    assert row.code is ErrorCode.VERSION_MISMATCH
    assert row.provenance == "judgment"
    assert row.note is not None
    assert "NOT VERIFIED" in row.note
    assert ErrorCode.VERSION_MISMATCH in UNPUBLISHED_CODES
    assert ErrorCode.VERSION_MISMATCH not in PUBLISHED_ERROR_CODES


def test_the_category_enum_carries_all_eight_documented_members() -> None:
    assert len(ErrorCategory) == 8
    assert {c.value for c in ErrorCategory} >= {"PAYMENT_METHOD_ERROR", "REFUND_ERROR", "EXTERNAL_VENDOR_ERROR"}


def test_two_documented_codes_are_recorded_as_unreachable() -> None:
    """Square publishes 401 CLIENT_DISABLED and a general 403 FORBIDDEN; no
    core kind maps onto either, and that is written down rather than noticed."""
    assert {ErrorCode.CLIENT_DISABLED, ErrorCode.FORBIDDEN} == UNREACHABLE_CODES
    mapped = {m.code for m in SQUARE_ERROR_TABLE.values()}
    assert not (mapped & UNREACHABLE_CODES)


def test_the_envelope_matches_squares_published_example() -> None:
    shaped = SquareErrorShaper(sidecar=False).shape(UnitError(UnitErrorKind.UNAUTHORIZED), fake_ctx())
    assert shaped.status == 401
    assert dump_json(shaped.body) == (
        b'{"errors":[{"category":"AUTHENTICATION_ERROR","code":"UNAUTHORIZED",'
        b'"detail":"This request could not be authorized."}]}'
    )


def test_an_absent_field_pointer_emits_no_key() -> None:
    """Square's Error object marks `field` optional. A null pointer would be
    worse than no key, and is what a naive dict build produces."""
    body = SquareErrorShaper(sidecar=False).shape(UnitError(UnitErrorKind.NOT_FOUND), fake_ctx()).body
    assert isinstance(body, dict)
    assert "field" not in body["errors"][0]
    assert b"null" not in dump_json(body)


def test_a_field_pointer_is_threaded_through() -> None:
    shaped = SquareErrorShaper().shape(
        UnitError(UnitErrorKind.MISSING_FIELD, detail="order.version is required", field="order.version"),
        fake_ctx(),
    )
    assert isinstance(shaped.body, dict)
    assert shaped.body["errors"][0] == {
        "category": "INVALID_REQUEST_ERROR",
        "code": "MISSING_REQUIRED_PARAMETER",
        "detail": "order.version is required",
        "field": "order.version",
    }


def test_the_sidecar_carries_the_kind_and_the_status_provenance() -> None:
    shaped = SquareErrorShaper().shape(
        UnitError(UnitErrorKind.VERSION_CONFLICT, info={"expected": 3, "actual": 4}),
        fake_ctx(),
    )
    assert isinstance(shaped.body, dict)
    assert shaped.body["unit_error"] == {
        "kind": "version_conflict",
        "status_provenance": "judgment",
        "expected": 3,
        "actual": 4,
    }


def test_the_sidecar_switches_off() -> None:
    shaped = SquareErrorShaper(sidecar=False).shape(UnitError(UnitErrorKind.INTERNAL), fake_ctx())
    assert isinstance(shaped.body, dict)
    assert "unit_error" not in shaped.body


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ({"retry_after_seconds": 3}, "3"),
        # The chaos engine coerces through as_int, but a rule that reached the
        # shaper with a float must not produce "3.0": the reference asserts "3"
        # and a client library parsing Retry-After rejects the decimal.
        ({"retry_after_seconds": 3.0}, "3"),
        ({}, "1"),
        (None, "1"),
    ],
)
def test_retry_after_is_an_integral_string(info: dict[str, object] | None, expected: str) -> None:
    shaped = SquareErrorShaper().shape(UnitError(UnitErrorKind.RATE_LIMITED, info=info), fake_ctx())
    assert shaped.status == 429
    assert shaped.headers["retry-after"] == expected


def test_retry_after_switches_off_because_square_does_not_document_it() -> None:
    shaped = SquareErrorShaper(retry_after_header=False).shape(
        UnitError(UnitErrorKind.RATE_LIMITED, info={"retry_after_seconds": 3}), fake_ctx()
    )
    assert "retry-after" not in shaped.headers
    assert shaped.status == 429


def test_a_disabled_capability_names_itself_in_a_header() -> None:
    shaped = SquareErrorShaper().shape(
        UnitError(UnitErrorKind.CAPABILITY_DISABLED, info={"capability": "order-lifecycle"}), fake_ctx()
    )
    assert shaped.status == 501
    assert shaped.headers["x-unit-capability"] == "order-lifecycle"


def test_no_route_names_the_control_route_that_lists_the_surface() -> None:
    shaped = SquareErrorShaper().not_found(request("POST", "/v2/payments"), fake_ctx(profile="orders-only"))
    assert shaped.status == 404
    assert isinstance(shaped.body, dict)
    detail = shaped.body["errors"][0]["detail"]
    assert "POST /v2/payments" in detail
    assert "GET /__unit/routes" in detail
    assert shaped.body["unit_error"]["profile"] == "orders-only"


def test_describe_publishes_every_row_with_its_provenance() -> None:
    described = SquareErrorShaper().describe()
    assert len(described) == 20
    assert described["unauthorized"]["provenance"] == "documented"
    assert "note" not in described["unauthorized"]
    assert "note" in described["version_conflict"]


def test_exhaustiveness_is_a_raise_and_not_an_assert() -> None:
    """`python -O` strips assert statements. A table that lost a row under -O
    would answer one kind with a KeyError-turned-500 and the other nineteen
    normally, which is the worst failure mode this project has."""
    source = Path("src/vendorfake/square/errors.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Assert)]
    guards = [
        node
        for node in tree.body
        if isinstance(node, ast.If) and any(isinstance(stmt, ast.Raise) for stmt in ast.walk(node))
    ]
    assert guards, "the module-level exhaustiveness guard is missing"


def test_the_shaper_is_total_over_the_twenty_kinds() -> None:
    """No `?? internal` fallback exists here, so a missing row would raise --
    which is why the guard above matters and why this walks every kind."""
    shaper = SquareErrorShaper()
    for kind in UnitErrorKind:
        shaped = shaper.shape(UnitError(kind), fake_ctx())
        assert 400 <= shaped.status <= 599
        assert dump_json(shaped.body)


def test_the_error_code_enum_rejects_an_invented_code() -> None:
    with pytest.raises(ValueError):
        ErrorCode("NOT_A_SQUARE_CODE")
    with pytest.raises((ValidationError, ValueError)):
        ErrorCategory("NOT_A_CATEGORY")
