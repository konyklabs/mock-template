"""The six consumer cases from the feedback item, from the consumer's side.

"The vendor returned garbage" cannot be rehearsed by anything the reference
build offers -- no fault kind produces a body that violates the vendor's own
schema -- so a consumer keeps a second, hand-rolled mocking mechanism forever
just for this. These six are the cases the feedback item named: an HTML error
page behind a 502, malformed JSON, a 200 missing its token, a 200 with an
empty token, a 200 missing its expiry, and a documented-string field retyped
to a string from something that was not one.

Five run against Square's ``ObtainToken`` (``POST /oauth2/token``); the sixth
runs against Clover's refresh endpoint instead, because Square's own
``expires_at`` is *already* a string and retyping a string to a string would
not exercise the fault. Clover's ``access_token_expiration`` is documented as
a number (Unix seconds), so it is the one that shows the retype actually
happening. See ``core/chaos/faults.py`` and the "Design notes" section this
stream's report keeps for the rest of the reasoning.

Every case is asserted twice, sync and async, over the same transport (see
``test_async_seam.py``): the claim under test is about the fault, not about
which client asked for it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from vendorfake.testing import CloverSeed, SquareSeed, StartedUnit, unit


@dataclass(frozen=True)
class Case:
    id: str
    vendor: str
    path: str
    rule: Mapping[str, Any]
    body: Callable[[Any], Mapping[str, Any]]
    assertion: Callable[[httpx.Response], None]


def _assert_html_error_page(response: httpx.Response) -> None:
    assert response.status_code == 502
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["vendorfake-fault"] == "malformed_body"
    assert response.headers["vendorfake-rule"] == "html-error-page"


def _assert_body_is_not_json(response: httpx.Response) -> None:
    assert response.status_code == 200
    with pytest.raises(ValueError):
        response.json()
    assert response.headers["vendorfake-fault"] == "malformed_body"


def _assert_no_access_token(response: httpx.Response) -> None:
    assert response.status_code == 200
    document = response.json()
    assert "access_token" not in document
    assert response.headers["vendorfake-fault"] == "body_mutation"


def _assert_empty_access_token(response: httpx.Response) -> None:
    assert response.status_code == 200
    assert response.json()["access_token"] == ""


def _assert_no_expiry(response: httpx.Response) -> None:
    assert response.status_code == 200
    assert "expires_at" not in response.json()


def _assert_expiration_retyped_to_string(response: httpx.Response) -> None:
    assert response.status_code == 200
    value = response.json()["access_token_expiration"]
    assert isinstance(value, str)
    assert value.isdigit(), value


CASES: tuple[Case, ...] = (
    Case(
        id="html-behind-502",
        vendor="square",
        path="/oauth2/token",
        rule={
            "id": "html-error-page",
            "scope": "request",
            "fault": "malformed_body",
            "match": {"route": "POST /oauth2/token"},
            "params": {"mode": "html"},
        },
        body=lambda seed: {
            "client_id": seed.application_id,
            "client_secret": seed.application_secret,
            "grant_type": "refresh_token",
            "refresh_token": seed.refresh_token,
        },
        assertion=_assert_html_error_page,
    ),
    Case(
        id="invalid-json",
        vendor="square",
        path="/oauth2/token",
        rule={
            "id": "bad-json",
            "scope": "request",
            "fault": "malformed_body",
            "match": {"route": "POST /oauth2/token"},
            "params": {"mode": "invalid_json"},
        },
        body=lambda seed: {
            "client_id": seed.application_id,
            "client_secret": seed.application_secret,
            "grant_type": "refresh_token",
            "refresh_token": seed.refresh_token,
        },
        assertion=_assert_body_is_not_json,
    ),
    Case(
        id="200-without-access-token",
        vendor="square",
        path="/oauth2/token",
        rule={
            "id": "no-access-token",
            "scope": "request",
            "fault": "body_mutation",
            "match": {"route": "POST /oauth2/token"},
            "params": {"ops": [{"op": "remove", "pointer": "/access_token"}]},
        },
        body=lambda seed: {
            "client_id": seed.application_id,
            "client_secret": seed.application_secret,
            "grant_type": "refresh_token",
            "refresh_token": seed.refresh_token,
        },
        assertion=_assert_no_access_token,
    ),
    Case(
        id="200-with-empty-access-token",
        vendor="square",
        path="/oauth2/token",
        rule={
            "id": "empty-access-token",
            "scope": "request",
            "fault": "body_mutation",
            "match": {"route": "POST /oauth2/token"},
            "params": {"ops": [{"op": "replace", "pointer": "/access_token", "value": ""}]},
        },
        body=lambda seed: {
            "client_id": seed.application_id,
            "client_secret": seed.application_secret,
            "grant_type": "refresh_token",
            "refresh_token": seed.refresh_token,
        },
        assertion=_assert_empty_access_token,
    ),
    Case(
        id="200-with-expiry-removed",
        vendor="square",
        path="/oauth2/token",
        rule={
            "id": "no-expiry",
            "scope": "request",
            "fault": "body_mutation",
            "match": {"route": "POST /oauth2/token"},
            "params": {"ops": [{"op": "remove", "pointer": "/expires_at"}]},
        },
        body=lambda seed: {
            "client_id": seed.application_id,
            "client_secret": seed.application_secret,
            "grant_type": "refresh_token",
            "refresh_token": seed.refresh_token,
        },
        assertion=_assert_no_expiry,
    ),
    Case(
        id="numeric-field-retyped-to-string",
        vendor="clover",
        path="/oauth/v2/refresh",
        rule={
            "id": "retype-expiration",
            "scope": "request",
            "fault": "body_mutation",
            "match": {"route": "POST /oauth/v2/refresh"},
            "params": {"ops": [{"op": "retype", "pointer": "/access_token_expiration"}]},
        },
        body=lambda seed: {"client_id": seed.client_id, "refresh_token": seed.refresh_token},
        assertion=_assert_expiration_retyped_to_string,
    ),
)

IDS = [case.id for case in CASES]


def _seed(started: StartedUnit) -> SquareSeed | CloverSeed:
    assert started.seed is not None
    return started.seed


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_sync(case: Case) -> None:
    with unit(case.vendor) as started:
        started.add_chaos_rule(case.rule)
        response = started.client.post(case.path, json=dict(case.body(_seed(started))))
        case.assertion(response)


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_async(case: Case) -> None:
    with unit(case.vendor) as started:
        started.add_chaos_rule(case.rule)
        response = await started.async_client.post(case.path, json=dict(case.body(_seed(started))))
        case.assertion(response)
