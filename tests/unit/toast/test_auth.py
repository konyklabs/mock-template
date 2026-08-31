"""The auth adapter: the bearer, the restaurant header, and the two modes."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from tests.unit.toast.harness import RESTAURANT, Harness, harness
from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.toast.entities import COL, TokenEntity
from vendorfake.toast.errors import TOAST_ERROR_TABLE
from vendorfake.toast.seed.constants import (
    SEED_ACCESS_TOKEN,
    SEED_MANAGEMENT_GROUP_GUID,
    SEED_PARTNER_GUID,
    SEED_READ_ONLY_ACCESS_TOKEN,
    SEED_READ_ONLY_SCOPES,
    SEED_SCOPES,
)
from vendorfake.toast.surface.common import BEARER_AUTH, RESTAURANT_AUTH, RESTAURANT_HEADER, RESTAURANT_META_KEY


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def _args(h: Harness, headers: dict[str, str]):  # type: ignore[no-untyped-def]
    lowered = {k.lower(): v for k, v in headers.items()}
    return SimpleNamespace(ctx=h.unit.context, header=lambda name: lowered.get(name.lower()))


def test_the_seeded_token_resolves_in_bearer_mode_without_a_restaurant(h: Harness) -> None:
    result = h.unit.context.vendor.auth.resolve(_args(h, {"Authorization": f"Bearer {SEED_ACCESS_TOKEN}"}), BEARER_AUTH)
    assert result.principal_id == SEED_PARTNER_GUID
    assert tuple(result.scopes) == SEED_SCOPES
    assert result.meta is not None and RESTAURANT_META_KEY not in result.meta


def test_restaurant_mode_records_the_header_s_restaurant(h: Harness) -> None:
    result = h.unit.context.vendor.auth.resolve(
        _args(h, {"Authorization": f"Bearer {SEED_ACCESS_TOKEN}", RESTAURANT_HEADER: f" {RESTAURANT} "}),
        RESTAURANT_AUTH,
    )
    assert result.meta is not None and result.meta[RESTAURANT_META_KEY] == RESTAURANT


@pytest.mark.parametrize(
    ("headers", "expected_kind"),
    [
        ({}, UnitErrorKind.UNAUTHORIZED),
        ({"Authorization": "Basic dXNlcg=="}, UnitErrorKind.UNAUTHORIZED),
        ({"Authorization": "Bearer "}, UnitErrorKind.UNAUTHORIZED),
        ({"Authorization": "Bearer never-minted"}, UnitErrorKind.UNAUTHORIZED),
        ({"Authorization": "conformance-not-a-real-credential"}, UnitErrorKind.UNAUTHORIZED),
    ],
)
def test_bad_bearers_are_401_before_the_restaurant_header_is_looked_at(
    h: Harness, headers: dict[str, str], expected_kind: UnitErrorKind
) -> None:
    with pytest.raises(UnitError) as caught:
        h.unit.context.vendor.auth.resolve(_args(h, headers), RESTAURANT_AUTH)
    assert caught.value.kind is expected_kind
    assert TOAST_ERROR_TABLE[caught.value.kind].status == 401


def test_a_missing_restaurant_header_is_400_and_an_unknown_one_404(h: Harness) -> None:
    """JUDGMENT statuses, labelled in auth.py; the group-guid refusal quotes
    the documented sentence."""
    auth = h.unit.context.vendor.auth
    bearer = {"Authorization": f"Bearer {SEED_ACCESS_TOKEN}"}
    with pytest.raises(UnitError) as missing:
        auth.resolve(_args(h, bearer), RESTAURANT_AUTH)
    assert missing.value.kind is UnitErrorKind.BAD_REQUEST
    assert missing.value.field == RESTAURANT_HEADER
    with pytest.raises(UnitError) as unknown:
        auth.resolve(_args(h, {**bearer, RESTAURANT_HEADER: "e6a4a8d2-0000-4000-8000-0000000000ff"}), RESTAURANT_AUTH)
    assert unknown.value.kind is UnitErrorKind.NOT_FOUND
    with pytest.raises(UnitError) as group:
        auth.resolve(_args(h, {**bearer, RESTAURANT_HEADER: SEED_MANAGEMENT_GROUP_GUID}), RESTAURANT_AUTH)
    assert group.value.kind is UnitErrorKind.NOT_FOUND
    assert "restaurant group" in str(group.value)
    assert group.value.info is not None and group.value.info["reason"] == "restaurant_group_guid"


def test_an_expired_token_is_token_expired_which_is_401(h: Harness) -> None:
    entity = TokenEntity(
        id="tok_expired",
        access_token="expired-token",
        client_id="c",
        partner_guid=SEED_PARTNER_GUID,
        expires_at_ms=1,
        scopes=("orders:read",),
    )
    h.unit.context.store.collection(COL.tokens).insert(entity.to_entity(), {"seed": True, "operation_id": "T"})
    with pytest.raises(UnitError) as caught:
        h.unit.context.vendor.auth.resolve(_args(h, {"Authorization": "Bearer expired-token"}), BEARER_AUTH)
    assert caught.value.kind is UnitErrorKind.TOKEN_EXPIRED
    assert TOAST_ERROR_TABLE[UnitErrorKind.TOKEN_EXPIRED].status == 401


def test_credentials_publishes_both_modes_for_every_live_token_and_drops_expired_ones(h: Harness) -> None:
    entity = TokenEntity(
        id="tok_expired", access_token="expired-token", client_id="c", partner_guid="p", expires_at_ms=1
    )
    h.unit.context.store.collection(COL.tokens).insert(entity.to_entity(), {"seed": True, "operation_id": "T"})
    offered = h.unit.context.vendor.auth.credentials(h.unit.context)
    by_mode = {mode: [c for c in offered if c.mode == mode] for mode in (BEARER_AUTH, RESTAURANT_AUTH)}
    assert len(by_mode[BEARER_AUTH]) == 2 and len(by_mode[RESTAURANT_AUTH]) == 2
    assert all(set(c.headers) == {"authorization"} for c in by_mode[BEARER_AUTH])
    assert all(set(c.headers) == {"authorization", RESTAURANT_HEADER.lower()} for c in by_mode[RESTAURANT_AUTH])
    assert all(c.headers[RESTAURANT_HEADER.lower()] == RESTAURANT for c in by_mode[RESTAURANT_AUTH])
    tokens = {c.headers["authorization"] for c in offered}
    assert tokens == {f"Bearer {SEED_ACCESS_TOKEN}", f"Bearer {SEED_READ_ONLY_ACCESS_TOKEN}"}
    # The read-only credential is the under-scoped one the conformance suite
    # needs for its forbidden_scope clause.
    read_only = next(c for c in by_mode[RESTAURANT_AUTH] if SEED_READ_ONLY_ACCESS_TOKEN in c.headers["authorization"])
    assert tuple(read_only.scopes) == SEED_READ_ONLY_SCOPES


def test_describe_names_both_headers_and_the_four_refusals(h: Harness) -> None:
    described = h.unit.context.vendor.auth.describe()
    assert "Bearer" in described["bearer"]
    assert RESTAURANT_HEADER in described["restaurant"]
    assert "403" in described["refusals"] and "401" in described["refusals"]
