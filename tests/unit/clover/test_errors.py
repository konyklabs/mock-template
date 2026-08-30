"""The error table: the documented 401 conflation above all."""

from __future__ import annotations

from tests.unit.clover.conftest import fake_ctx
from vendorfake.clover.errors import (
    CLOVER_ERROR_TABLE,
    CONFLATED_401_KINDS,
    DETAIL_SUPPRESSED_KINDS,
    CloverErrorShaper,
)
from vendorfake.core.kernel.types import UnitError, UnitErrorKind, UnitRequest


def request(path: str = "/v3/merchants/M/orders") -> UnitRequest:
    return UnitRequest(
        id="req_1",
        method="GET",
        path=path,
        query={},
        headers={},
        raw_body=b"",
        transport="inprocess",
        received_at="2026-08-29T00:00:00.000Z",
    )


def test_the_table_is_exhaustive_over_the_twenty_core_kinds() -> None:
    assert set(CLOVER_ERROR_TABLE) == set(UnitErrorKind)


def test_authorization_failures_all_conflate_to_401_and_none_to_403() -> None:
    """DOCUMENTED: "The API does not distinguish between an unauthorized error
    (401 - expired/invalid token) and a permissions error (403 - token has
    insufficient permissions) and returns a 401 Unauthorized in either case."
    https://docs.clover.com/dev/docs/401-unauthorized

    A Square-habituated consumer expects 403 on forbidden_scope; surfacing that
    Clover sends 401 is the product."""
    for kind in (
        UnitErrorKind.UNAUTHORIZED,
        UnitErrorKind.TOKEN_EXPIRED,
        UnitErrorKind.TOKEN_REVOKED,
        UnitErrorKind.FORBIDDEN_SCOPE,
    ):
        assert CLOVER_ERROR_TABLE[kind].status == 401, kind
        assert CLOVER_ERROR_TABLE[kind].provenance == "documented", kind
    assert all(mapping.status != 403 for mapping in CLOVER_ERROR_TABLE.values())


def test_forbidden_scope_shapes_to_401_on_the_wire() -> None:
    shaped = CloverErrorShaper().shape(UnitError(UnitErrorKind.FORBIDDEN_SCOPE), fake_ctx())
    assert shaped.status == 401
    assert shaped.body["message"] == "401 Unauthorized"


def test_the_bearer_path_rows_never_put_the_errors_detail_on_the_wire() -> None:
    """The kernel's forbidden_scope names the missing permission in its detail
    and the chaos engine's token_expired carries one too; a Clover that said
    either would distinguish what it documents itself as not distinguishing.
    The table wins on those rows; the detail reaches the sidecar only."""
    leaky = "The access token is missing the required permission(s): ORDERS_W."
    assert {
        UnitErrorKind.TOKEN_EXPIRED,
        UnitErrorKind.TOKEN_REVOKED,
        UnitErrorKind.FORBIDDEN_SCOPE,
    } == DETAIL_SUPPRESSED_KINDS
    assert DETAIL_SUPPRESSED_KINDS < CONFLATED_401_KINDS
    for kind in DETAIL_SUPPRESSED_KINDS:
        shaped = CloverErrorShaper(sidecar=False).shape(UnitError(kind, detail=leaky), fake_ctx())
        assert shaped.body == {"message": "401 Unauthorized"}, kind
        with_sidecar = CloverErrorShaper(sidecar=True).shape(UnitError(kind, detail=leaky), fake_ctx())
        assert with_sidecar.body["message"] == "401 Unauthorized"
        assert with_sidecar.body["unit_error"]["detail"] == leaky
    # `unauthorized` is conflated in status but keeps a handler's detail: the
    # bearer adapter never attaches one (the byte-identity test in test_auth
    # drives that through the kernel), and the OAuth endpoints' own refusal
    # bodies are a labelled JUDGMENT the 401 sentence does not cover.
    oauth = CloverErrorShaper().shape(
        UnitError(UnitErrorKind.UNAUTHORIZED, detail="Failed to validate authentication code"), fake_ctx()
    )
    assert oauth.status == 401
    assert oauth.body["message"] == "Failed to validate authentication code"
    bare = CloverErrorShaper(sidecar=False).shape(UnitError(UnitErrorKind.UNAUTHORIZED), fake_ctx())
    assert bare.body == {"message": "401 Unauthorized"}
    # Every other row still prefers the handler's own wording, and carries no
    # sidecar `detail` key because nothing was suppressed.
    other = CloverErrorShaper().shape(UnitError(UnitErrorKind.NOT_FOUND, detail="no such order"), fake_ctx())
    assert other.body["message"] == "no such order"
    assert "detail" not in other.body["unit_error"]


def test_the_envelope_is_message_plus_optional_type() -> None:
    """JUDGMENT shape: {"message": ...}; "type" only where a documented value
    exists (RESOURCE_CONFLICT, the status-code reference's one example)."""
    ctx = fake_ctx()
    shaper = CloverErrorShaper()
    not_found = shaper.shape(UnitError(UnitErrorKind.NOT_FOUND), ctx)
    assert not_found.status == 404
    assert not_found.body["message"] == "Not found."
    assert "type" not in not_found.body
    conflict = shaper.shape(UnitError(UnitErrorKind.CONFLICT), ctx)
    assert conflict.status == 409
    assert conflict.body["type"] == "RESOURCE_CONFLICT"
    assert all(m.type is None for k, m in CLOVER_ERROR_TABLE.items() if k is not UnitErrorKind.CONFLICT)


def test_a_handlers_own_detail_wins_over_the_tables_generic_message() -> None:
    shaped = CloverErrorShaper().shape(
        UnitError(UnitErrorKind.INVALID_VALUE, detail="unitQty must be an integer (fixed-point x1000)."),
        fake_ctx(),
    )
    assert shaped.body["message"] == "unitQty must be an integer (fixed-point x1000)."


def test_the_sidecar_carries_kind_and_provenance_and_switches_off() -> None:
    err = UnitError(UnitErrorKind.RATE_LIMITED, info={"retry_after_seconds": 7})
    with_sidecar = CloverErrorShaper(sidecar=True).shape(err, fake_ctx())
    assert with_sidecar.body["unit_error"]["kind"] == "rate_limited"
    assert with_sidecar.body["unit_error"]["status_provenance"] == "documented"
    without = CloverErrorShaper(sidecar=False).shape(err, fake_ctx())
    assert "unit_error" not in without.body


def test_the_sidecar_reports_the_field_the_error_names() -> None:
    err = UnitError(UnitErrorKind.MISSING_FIELD, field="price")
    shaped = CloverErrorShaper().shape(err, fake_ctx())
    assert shaped.body["unit_error"]["field"] == "price"
    # No field named -> no key, per the absence-is-absence rule.
    bare = CloverErrorShaper().shape(UnitError(UnitErrorKind.MISSING_FIELD), fake_ctx())
    assert "field" not in bare.body["unit_error"]


def test_an_info_document_cannot_clobber_the_sidecars_reserved_keys() -> None:
    """Reserved keys are merged last: an err.info carrying its own `kind` or
    `status_provenance` must not overwrite what the sidecar exists to report."""
    err = UnitError(
        UnitErrorKind.CONFLICT,
        info={"kind": "spoofed", "status_provenance": "spoofed", "extra": "kept"},
    )
    sidecar = CloverErrorShaper().shape(err, fake_ctx()).body["unit_error"]
    assert sidecar["kind"] == "conflict"
    assert sidecar["status_provenance"] == "documented"
    assert sidecar["extra"] == "kept"


def test_rate_limited_carries_retry_after_and_the_switch_removes_it() -> None:
    err = UnitError(UnitErrorKind.RATE_LIMITED, info={"retry_after_seconds": 7})
    assert CloverErrorShaper().shape(err, fake_ctx()).headers["retry-after"] == "7"
    assert CloverErrorShaper().shape(UnitError(UnitErrorKind.RATE_LIMITED), fake_ctx()).headers["retry-after"] == "1"
    assert "retry-after" not in CloverErrorShaper(retry_after_header=False).shape(err, fake_ctx()).headers


def test_rate_limited_stamps_the_four_documented_rate_limit_headers() -> None:
    """Headers and numbers documented (api-usage-rate-limits: 16/s per token,
    50/s per app, 5 and 10 concurrent); stamping all four on a chaos-injected
    429 is the labelled JUDGMENT. The retry-after switch does not remove them."""
    for shaper in (CloverErrorShaper(), CloverErrorShaper(retry_after_header=False)):
        headers = shaper.shape(UnitError(UnitErrorKind.RATE_LIMITED), fake_ctx()).headers
        assert headers["x-ratelimit-tokenlimit"] == "16"
        assert headers["x-ratelimit-crosstokenlimit"] == "50"
        assert headers["x-ratelimit-tokenconcurrentlimit"] == "5"
        assert headers["x-ratelimit-crosstokenconcurrentlimit"] == "10"
    # And no other kind carries them.
    assert (
        "x-ratelimit-tokenlimit" not in CloverErrorShaper().shape(UnitError(UnitErrorKind.CONFLICT), fake_ctx()).headers
    )


def test_not_found_names_the_route_listing() -> None:
    shaped = CloverErrorShaper().not_found(request(), fake_ctx(profile="full"))
    assert shaped.status == 404
    assert "/__unit/routes" in shaped.body["message"]
    assert shaped.body["unit_error"]["profile"] == "full"


def test_every_row_has_an_error_status_and_a_provenance() -> None:
    for kind, mapping in CLOVER_ERROR_TABLE.items():
        assert 400 <= mapping.status <= 599, kind
        assert mapping.provenance in ("documented", "judgment"), kind
        assert mapping.message, kind


def test_describe_publishes_all_twenty_rows() -> None:
    described = CloverErrorShaper().describe()
    assert set(described) == {kind.value for kind in UnitErrorKind}
    assert described["forbidden_scope"]["status"] == 401


def test_the_control_plane_publishes_each_rows_provenance_from_describe() -> None:
    """`GET /__unit/errors` carries the table's provenance per row -- the
    promise the module docstring makes -- with the sidecar off, the case in
    which nothing else on the wire could carry it."""
    from tests.unit.clover.harness import harness

    for h in harness("full", env={"VENDORFAKE_VENDOR_ERROR_SIDECAR": "false"}):
        rows = {row["kind"]: row for row in h.api.get("/__unit/errors").json()["kinds"]}
        assert rows["forbidden_scope"]["provenance"] == "documented"
        assert rows["not_found"]["provenance"] == "judgment"
        assert "unit_error" not in rows["not_found"]["body"]
        for kind, mapping in CLOVER_ERROR_TABLE.items():
            assert rows[kind.value]["provenance"] == mapping.provenance, kind
