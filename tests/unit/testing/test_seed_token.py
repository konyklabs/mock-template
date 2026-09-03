"""``seed.token``: the stored-credential half of the neutral seed view.

``credentials`` (0.2) neutralised what an application authenticates *as*;
this is what a consumer *stores* per tenant, and it is on the ``Seed``
protocol so a body parametrized over vendors reads it with no ``Any``
(konyklabs/roadmap#101, item 16). The values are asserted against the
vendor-spelled fields, not against literals, so a re-seeded profile cannot
make this pass by coincidence.
"""

from __future__ import annotations

import pytest

from vendorfake import available_vendors
from vendorfake.testing import CloverSeed, Seed, SquareSeed, ToastSeed, Token, unit


@pytest.mark.parametrize("vendor", sorted(available_vendors()))
def test_every_vendor_publishes_a_token_that_agrees_with_its_grant(vendor: str) -> None:
    with unit(vendor) as started:
        seed: Seed = started.seed
        token = seed.token
        assert isinstance(token, Token)
        assert token.access_token and token.tenant_id
        # The one real lifecycle difference, stated twice and agreeing.
        assert (token.refresh_token is None) == (seed.credentials.grant == "client_credentials")
        # It is the token ``auth`` sends, not a second one.
        assert seed.auth["Authorization"] == f"Bearer {token.access_token}"


def test_square_tenant_is_the_seller_not_a_location() -> None:
    with unit("square") as started:
        seed: SquareSeed = started.seed
        assert seed.token == Token(seed.access_token, seed.refresh_token, seed.merchant_id)
        assert seed.token.tenant_id != seed.location_id


def test_clover_tenant_is_the_merchant() -> None:
    with unit("clover") as started:
        seed: CloverSeed = started.seed
        assert seed.token == Token(seed.access_token, seed.refresh_token, seed.merchant_id)


def test_toast_has_no_refresh_token_and_the_restaurant_as_tenant() -> None:
    with unit("toast") as started:
        seed: ToastSeed = started.seed
        assert seed.token == Token(seed.access_token, None, seed.restaurant_guid)


def test_the_token_refreshes_on_a_rotating_vendor() -> None:
    """The neutral view is enough to drive a refresh, which is what a
    consumer's stored row exists for."""
    with unit("clover", "oauth-only") as clover:
        token = clover.seed.token
        answered = clover.client.post(
            "/oauth/v2/refresh",
            json={"client_id": clover.seed.credentials.app_id, "refresh_token": token.refresh_token},
        )
    assert answered.status_code == 200, answered.text
    assert answered.json()["refresh_token"] != token.refresh_token
