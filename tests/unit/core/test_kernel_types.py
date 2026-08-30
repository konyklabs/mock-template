"""The contract itself: the parts of it a reviewer could disagree about.

Most of ``kernel/types.py`` is declaration and needs no test. What is tested
here is what carries a decision: that there are exactly twenty error kinds and
which twenty, that a misspelled kind fails at the raise site, that the request
seam is immutable, and that a route key renders in the ``{order_id}`` form the
router, the chaos rules, the capability index and the OpenAPI document all
agree on.
"""

from __future__ import annotations

import dataclasses

import pytest

from vendorfake.core.kernel.types import (
    CapabilityDecl,
    IdempotencySpec,
    JournalEntry,
    Route,
    ShapedError,
    UnitError,
    UnitErrorKind,
    UnitRequest,
    UnitResponse,
)

# Written out rather than derived from the enum: a test that reads the same
# list it is checking proves nothing. This is the list from
# packages/core/src/kernel/types.ts, in its declared order.
REFERENCE_KINDS = [
    "bad_request",
    "invalid_json",
    "missing_field",
    "invalid_value",
    "not_found",
    "method_not_allowed",
    "unauthorized",
    "token_expired",
    "token_revoked",
    "forbidden_scope",
    "capability_disabled",
    "version_conflict",
    "idempotency_conflict",
    "invalid_cursor",
    "invalid_transition",
    "conflict",
    "rate_limited",
    "timeout",
    "unavailable",
    "internal",
]


def a_request(**overrides: object) -> UnitRequest:
    base: dict[str, object] = {
        "id": "req_1",
        "method": "POST",
        "path": "/v2/orders",
        "query": {},
        "headers": {},
        "raw_body": b"",
        "transport": "inprocess",
        "received_at": "2026-01-01T00:00:00.000Z",
    }
    base.update(overrides)
    return UnitRequest(**base)  # type: ignore[arg-type]  # the helper's kwargs are checked by the fields themselves


class TestUnitErrorKind:
    def test_there_are_exactly_twenty(self) -> None:
        assert len(UnitErrorKind) == 20

    def test_they_are_the_reference_kinds_in_the_reference_order(self) -> None:
        assert [k.value for k in UnitErrorKind] == REFERENCE_KINDS

    def test_a_kind_is_its_own_wire_string(self) -> None:
        # The x-unit-error header carries the value directly.
        assert UnitErrorKind.CAPABILITY_DISABLED == "capability_disabled"
        assert f"{UnitErrorKind.NOT_FOUND}" == "not_found"


class TestUnitError:
    def test_carries_kind_detail_field_and_info(self) -> None:
        err = UnitError(
            UnitErrorKind.VERSION_CONFLICT,
            detail="Supplied version 2 does not match the current version 3.",
            field="order.version",
            info={"supplied": 2, "current": 3},
        )
        assert err.kind is UnitErrorKind.VERSION_CONFLICT
        assert err.field == "order.version"
        assert err.info == {"supplied": 2, "current": 3}
        assert str(err) == "Supplied version 2 does not match the current version 3."

    def test_the_message_falls_back_to_the_kind(self) -> None:
        assert str(UnitError(UnitErrorKind.NOT_FOUND)) == "not_found"

    def test_a_plain_string_kind_is_coerced_to_the_enum(self) -> None:
        assert UnitError("missing_field").kind is UnitErrorKind.MISSING_FIELD

    def test_a_misspelled_kind_fails_at_the_raise_site(self) -> None:
        # This is what replaces TypeScript's checking of a literal union: the
        # alternative is a kind that travels to the shaper and falls out of a
        # lookup table as an unrelated status.
        with pytest.raises(ValueError, match="not_a_kind"):
            UnitError("not_a_kind")

    def test_it_is_an_exception(self) -> None:
        with pytest.raises(UnitError):
            raise UnitError(UnitErrorKind.INTERNAL)


class TestTheSeam:
    def test_a_request_is_frozen(self) -> None:
        req = a_request()
        with pytest.raises(dataclasses.FrozenInstanceError):
            req.path = "/elsewhere"  # type: ignore[misc]

    def test_a_response_is_frozen(self) -> None:
        res = UnitResponse(status=200, headers={}, body=b"{}")
        with pytest.raises(dataclasses.FrozenInstanceError):
            res.status = 500  # type: ignore[misc]

    def test_raw_body_is_bytes_and_is_kept_exactly(self) -> None:
        # A signature is computed over these bytes; a re-serialisation would
        # silently change what is under test.
        raw = b'{"a": 1,  "b":   2}'
        assert a_request(raw_body=raw).raw_body == raw

    def test_the_nine_request_fields_are_the_contract(self) -> None:
        assert [f.name for f in dataclasses.fields(UnitRequest)] == [
            "id",
            "method",
            "path",
            "query",
            "headers",
            "raw_body",
            "transport",
            "received_at",
            "query_all",
        ]

    def test_query_all_defaults_to_the_single_valued_view_of_query(self) -> None:
        # The invariant ``query[k] == query_all[k][-1]`` must hold for a request
        # built by hand as much as for one a binding built.
        req = a_request(query={"a": "1", "b": ""})
        assert req.query_all == {"a": ("1",), "b": ("",)}
        assert a_request().query_all == {}

    def test_an_explicit_query_all_is_kept(self) -> None:
        req = a_request(query={"a": "2"}, query_all={"a": ("1", "2")})
        assert req.query_all == {"a": ("1", "2")}

    @pytest.mark.parametrize(
        ("query", "query_all"),
        [
            ({"a": "1"}, {"b": ("2",)}),
            ({"a": "1"}, {"a": ("1", "2")}),
            ({"a": "1", "b": "2"}, {"a": ("1",)}),
            ({"a": "1"}, {"a": ()}),
        ],
    )
    def test_two_views_that_disagree_are_refused_at_construction(
        self, query: dict[str, str], query_all: dict[str, tuple[str, ...]]
    ) -> None:
        # Silently keeping both would let `args.query(k)` and `args.query_all(k)`
        # answer different questions about the same request.
        with pytest.raises(ValueError, match="query_all"):
            a_request(query=query, query_all=query_all)

    def test_the_three_response_fields_are_the_contract(self) -> None:
        assert [f.name for f in dataclasses.fields(UnitResponse)] == ["status", "headers", "body"]


class TestRoute:
    def _route(self, **overrides: object) -> Route:
        base: dict[str, object] = {
            "method": "post",
            "path": "/v2/orders/{order_id}/pay",
            "capability": "order-lifecycle",
            "handler": lambda args: None,
        }
        base.update(overrides)
        return Route(**base)  # type: ignore[arg-type]

    def test_the_key_is_uppercase_method_space_brace_path(self) -> None:
        # The same string a chaos rule's match.route carries and the same one
        # the capability-to-routes index publishes.
        assert self._route().key == "POST /v2/orders/{order_id}/pay"

    def test_defaults_are_the_unauthenticated_non_idempotent_vendor_route(self) -> None:
        route = self._route()
        assert route.auth is None
        assert route.scopes == ()
        assert route.idempotency is None
        assert route.internal is False

    def test_a_route_is_frozen_because_routes_are_data(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            self._route().capability = "elsewhere"  # type: ignore[misc]


class TestSmallContracts:
    def test_idempotency_defaults_to_optional_and_conflict(self) -> None:
        spec = IdempotencySpec(key_path="idempotency_key", scope="CreateOrder")
        assert spec.required is False
        assert spec.on_mismatch == "conflict"

    def test_a_capability_defaults_to_surface_with_no_prerequisites(self) -> None:
        decl = CapabilityDecl(name="webhooks", summary="Signed event delivery.")
        assert decl.kind == "surface"
        assert decl.requires == ()

    def test_a_shaped_error_defaults_to_no_extra_headers(self) -> None:
        assert ShapedError(status=404, body={"errors": []}).headers == {}

    def test_a_journal_entry_records_both_versions_and_the_changed_keys(self) -> None:
        entry = JournalEntry(
            seq=1,
            at="2026-01-01T00:00:00.000Z",
            collection="orders",
            id="CAIS1",
            op="update",
            from_version=1,
            to_version=2,
            changed=["state", "total_money"],
        )
        assert entry.from_version == 1
        assert entry.to_version == 2
        assert entry.meta is None
