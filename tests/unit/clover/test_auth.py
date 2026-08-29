"""The auth adapter, and the documented 401 conflation asserted at its level.

"The API does not distinguish between an unauthorized error (401 -
expired/invalid token) and a permissions error (403 - token has insufficient
permissions) and returns a 401 Unauthorized in either case."
https://docs.clover.com/dev/docs/401-unauthorized
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from tests.unit.clover.harness import Harness, harness
from vendorfake.clover.entities import COL, TokenEntity
from vendorfake.clover.errors import CLOVER_ERROR_TABLE, CloverErrorShaper
from vendorfake.core.kernel.types import UnitError, UnitErrorKind


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def _args_with(h: Harness, authorization: str | None):  # type: ignore[no-untyped-def]
    headers = {} if authorization is None else {"authorization": authorization}
    return SimpleNamespace(
        ctx=h.unit.context,
        header=lambda name: headers.get(name.lower()),
    )


def _insert_token(h: Harness, *, access_expiry_ms: int, permissions: tuple[str, ...] = ("ORDERS_R",)) -> TokenEntity:
    entity = TokenEntity(
        id="tok_manual00001",
        access_token="aaaaaaaa-1111-4222-8333-444444444444",
        refresh_token="bbbbbbbb-1111-4222-8333-444444444444",
        client_id="UNITCLOVERAPP",
        merchant_id="HRVSTRYE12345",
        access_token_expiration_ms=access_expiry_ms,
        refresh_token_expiration_ms=access_expiry_ms,
        permissions=permissions,
    )
    h.unit.context.store.collection(COL.tokens).insert(entity.to_entity(), {"operation_id": "TestSeed", "seed": True})
    return entity


def test_a_minted_token_resolves_with_its_app_inherited_permissions(h: Harness) -> None:
    body = h.exchange()
    auth = h.unit.context.vendor.auth
    result = auth.resolve(_args_with(h, f"Bearer {body['access_token']}"), "bearer")
    assert result.principal_id == "HRVSTRYE12345"
    # The app's fixed set, inherited at mint -- Clover has no per-token scopes.
    assert set(result.scopes) == {"ORDERS_R", "ORDERS_W", "INVENTORY_R", "INVENTORY_W", "MERCHANT_R"}
    assert result.token_id is not None


@pytest.mark.parametrize(
    ("header", "expected_kind"),
    [
        (None, UnitErrorKind.UNAUTHORIZED),
        ("Basic dXNlcg==", UnitErrorKind.UNAUTHORIZED),
        ("Bearer never-minted", UnitErrorKind.UNAUTHORIZED),
    ],
)
def test_bad_credentials_raise_kinds_whose_status_is_401(
    h: Harness, header: str | None, expected_kind: UnitErrorKind
) -> None:
    auth = h.unit.context.vendor.auth
    with pytest.raises(UnitError) as caught:
        auth.resolve(_args_with(h, header), "bearer")
    assert caught.value.kind is expected_kind
    assert CLOVER_ERROR_TABLE[caught.value.kind].status == 401


def test_an_expired_token_raises_token_expired_which_is_also_401(h: Harness) -> None:
    token = _insert_token(h, access_expiry_ms=1)
    auth = h.unit.context.vendor.auth
    with pytest.raises(UnitError) as caught:
        auth.resolve(_args_with(h, f"Bearer {token.access_token}"), "bearer")
    assert caught.value.kind is UnitErrorKind.TOKEN_EXPIRED
    assert CLOVER_ERROR_TABLE[UnitErrorKind.TOKEN_EXPIRED].status == 401


def test_the_conflation_makes_every_auth_failure_identical_on_the_wire(h: Harness) -> None:
    """The adapter raises without a detail on purpose, so bad-token, expired
    and insufficient-permission failures shape to byte-identical Clover
    envelopes -- only the debugging sidecar differs. That is the documented
    behaviour a Square-habituated consumer (expecting a 403) must meet."""
    token = _insert_token(h, access_expiry_ms=1)
    auth = h.unit.context.vendor.auth
    shaper = CloverErrorShaper(sidecar=False)
    ctx = h.unit.context

    failures: list[UnitError] = []
    for header in (None, "Bearer never-minted", f"Bearer {token.access_token}"):
        with pytest.raises(UnitError) as caught:
            auth.resolve(_args_with(h, header), "bearer")
        failures.append(caught.value)
    # The kernel's own permission check raises this kind for an
    # under-permitted token; it belongs in the same conflated set.
    failures.append(UnitError(UnitErrorKind.FORBIDDEN_SCOPE))

    shaped = [shaper.shape(err, ctx) for err in failures]
    assert {s.status for s in shaped} == {401}
    bodies = [s.body for s in shaped]
    assert all(body == {"message": "401 Unauthorized"} for body in bodies), bodies


def test_a_rotated_refresh_does_not_end_the_access_token_here_either(h: Harness) -> None:
    """The adapter never consults refresh_used_at_ms; only the clock ends a
    token. JUDGMENT labelled in surface/oauth.py."""
    first = h.exchange()
    assert h.refresh(refresh_token=first["refresh_token"]).status == 200
    auth = h.unit.context.vendor.auth
    result = auth.resolve(_args_with(h, f"Bearer {first['access_token']}"), "bearer")
    assert result.principal_id == "HRVSTRYE12345"


def test_credentials_offers_live_tokens_and_filters_expired_ones(h: Harness) -> None:
    body = h.exchange()
    _insert_token(h, access_expiry_ms=1)  # expired; must not be offered
    offered = h.unit.context.vendor.auth.credentials(h.unit.context)
    headers = {credential.headers["authorization"] for credential in offered}
    assert f"Bearer {body['access_token']}" in headers
    assert "Bearer aaaaaaaa-1111-4222-8333-444444444444" not in headers
    for credential in offered:
        assert credential.mode == "bearer"
        assert credential.scopes  # the app's permission set, discoverable


def test_describe_names_the_scheme_and_the_conflation(h: Harness) -> None:
    described = h.unit.context.vendor.auth.describe()
    assert "Bearer" in described["bearer"]
    assert "401" in described["conflation"]
