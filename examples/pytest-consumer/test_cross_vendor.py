"""One test body, three vendors, no ``isinstance``.

This is the shape an integration reaches for once it supports more than one
POS: the credential handling is the same code, and the only genuine
difference is what the vendor does when the access token expires. That
difference is `seed.credentials.grant`, and it is the only thing this test
branches on.

`unit(vendor)` with a plain `str` -- which is what `pytest.mark.parametrize`
hands you -- yields a `StartedUnit[Seed]`: the fields every vendor's seed has
(`credentials`, `auth`, `read_only_auth`, `event_types`) and nothing that only
some of them do. `unit("square")` with the literal narrows to
`StartedUnit[SquareSeed]` instead, which is what the other files here use.
Neither needs a cast or an `isinstance`.

The paths below are hardcoded per vendor. That is the one thing left that a
consumer should not have to know: `route_for` (stream C) replaces this table,
and when it lands the table goes and the test body stays.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from vendorfake.testing import Credentials, StartedUnit, unit

VENDORS = ["square", "clover", "toast"]


@dataclass(frozen=True)
class Endpoints:
    """Where one vendor's credentials are exchanged.

    Paths only. The request bodies below are shared across vendors, which
    they can be because Square and Clover both ignore keys they do not know
    -- so one body carrying the union of the two documented shapes is
    accepted by each as its own.
    """

    #: Only the refresh-token vendors have one: the page the merchant lands on.
    authorize: str | None
    #: Where the credentials (plus a code, where there is one) are posted.
    exchange: str
    #: Where a refresh token is rotated. ``None`` where there is no refresh.
    refresh: str | None


ENDPOINTS = {
    "square": Endpoints(authorize="/oauth2/authorize", exchange="/oauth2/token", refresh="/oauth2/token"),
    "clover": Endpoints(authorize="/oauth/v2/authorize", exchange="/oauth/v2/token", refresh="/oauth/v2/refresh"),
    "toast": Endpoints(authorize=None, exchange="/authentication/v1/authentication/login", refresh=None),
}


def _authorization_code(started: StartedUnit[Any], authorize: str, credentials: Credentials) -> str:
    """Click "Allow" and read the code off the redirect."""
    landed = started.client.get(authorize, params={"client_id": credentials.app_id})
    assert landed.status_code == 302, landed.text
    return parse_qs(urlsplit(landed.headers["location"]).query)["code"][0]


@pytest.mark.parametrize("vendor", VENDORS)
def test_every_vendor_authenticates_from_the_same_credentials(vendor: str) -> None:
    """Take the app credentials off the seed and get a usable bearer token.

    No vendor name appears below except as a table lookup, and the seed is
    read through the neutral accessor throughout.
    """
    with unit(vendor) as started:
        credentials = started.seed.credentials
        endpoints = ENDPOINTS[vendor]

        if credentials.grant == "refresh_token":
            assert endpoints.authorize is not None
            code = _authorization_code(started, endpoints.authorize, credentials)
            granted = started.client.post(
                endpoints.exchange,
                json={
                    "client_id": credentials.app_id,
                    "client_secret": credentials.app_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                },
            )
            assert granted.status_code == 200, granted.text
            access_token = granted.json()["access_token"]
        else:
            granted = started.client.post(
                endpoints.exchange,
                json={
                    "clientId": credentials.app_id,
                    "clientSecret": credentials.app_secret,
                    "userAccessType": "TOAST_MACHINE_CLIENT",
                },
            )
            assert granted.status_code == 200, granted.text
            access_token = granted.json()["token"]["accessToken"]

        # A freshly minted token, not the seeded one handed out for
        # convenience. `auth` is on the protocol, so reading it to say so
        # needs no vendor branch either.
        assert access_token
        assert f"Bearer {access_token}" != started.seed.auth["Authorization"]


@pytest.mark.parametrize("vendor", VENDORS)
def test_a_refresh_grant_rotates_and_a_client_credentials_grant_logs_in_again(vendor: str) -> None:
    """The one branch that is a real vendor difference rather than a spelling.

    ``grant`` is what a consumer's token store has to switch on: rotate a
    refresh token, or throw the token away and log in again. Both halves are
    driven here so neither can rot.
    """
    with unit(vendor) as started:
        credentials = started.seed.credentials
        endpoints = ENDPOINTS[vendor]

        if credentials.grant == "client_credentials":
            assert endpoints.refresh is None
            first = started.client.post(
                endpoints.exchange,
                json={
                    "clientId": credentials.app_id,
                    "clientSecret": credentials.app_secret,
                    "userAccessType": "TOAST_MACHINE_CLIENT",
                },
            )
            second = started.client.post(
                endpoints.exchange,
                json={
                    "clientId": credentials.app_id,
                    "clientSecret": credentials.app_secret,
                    "userAccessType": "TOAST_MACHINE_CLIENT",
                },
            )
            assert first.status_code == 200, first.text
            assert second.status_code == 200, second.text
            return

        assert endpoints.authorize is not None
        assert endpoints.refresh is not None
        code = _authorization_code(started, endpoints.authorize, credentials)
        granted = started.client.post(
            endpoints.exchange,
            json={
                "client_id": credentials.app_id,
                "client_secret": credentials.app_secret,
                "grant_type": "authorization_code",
                "code": code,
            },
        )
        assert granted.status_code == 200, granted.text

        rotated = started.client.post(
            endpoints.refresh,
            json={
                "client_id": credentials.app_id,
                "client_secret": credentials.app_secret,
                "grant_type": "refresh_token",
                "refresh_token": granted.json()["refresh_token"],
            },
        )
        assert rotated.status_code == 200, rotated.text
        assert rotated.json()["access_token"] != granted.json()["access_token"]
