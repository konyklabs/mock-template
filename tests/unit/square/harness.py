"""One started Square unit, driven in process, for the behaviour suites.

Everything here talks to the unit through the in-process binding -- no socket,
no container -- which is what makes a vendor's behavioural suite fast enough to
run on every save. The out-of-process half is proven separately in
``tests/integration``.

The harness is deliberately thin. It knows the profile's credentials and how to
walk the authorize redirect, because every OAuth test needs both and a test
that re-derives them is a test that can disagree with the profile.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from vendorfake import create_unit
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.transport.inprocess import InProcessClient, InProcessResponse, in_process
from vendorfake.square.seed.constants import SEED_ACCESS_TOKEN, SEED_READ_ONLY_ACCESS_TOKEN

APPLICATION_ID = "sandbox-sq0idb-unit-square-application"
APPLICATION_SECRET = "sandbox-sq0csb-unit-square-secret"
CONFIGURED_REDIRECT_URI = "https://example.test/oauth/callback"
"""The three values ``profiles/oauth-only.json`` and ``profiles/full.json`` set."""

FORM = {"content-type": "application/x-www-form-urlencoded"}


class Silent:
    """A logger that says nothing, so a passing run prints no unit banner."""

    def debug(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def info(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def warn(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def error(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...


@dataclass(frozen=True, slots=True)
class Harness:
    """A started unit and the client that drives it."""

    unit: Unit
    api: InProcessClient

    @property
    def auth(self) -> dict[str, str]:
        """The seeded full-scope token, as an ``Authorization`` header."""
        return {"authorization": f"Bearer {SEED_ACCESS_TOKEN}"}

    @property
    def read_auth(self) -> dict[str, str]:
        """The seeded token that cannot write."""
        return {"authorization": f"Bearer {SEED_READ_ONLY_ACCESS_TOKEN}"}

    @property
    def client_auth(self) -> dict[str, str]:
        """``Authorization: Client {APPLICATION_SECRET}``, for revocation."""
        return {"authorization": f"Client {APPLICATION_SECRET}"}

    def authorize(self, **query: str) -> InProcessResponse:
        """``GET /oauth2/authorize`` with ``client_id`` already filled in."""
        return self.api.call(
            method="GET",
            path="/oauth2/authorize",
            query={"client_id": APPLICATION_ID, **query},
        )

    def code(self, **query: str) -> str:
        """Walk the authorize redirect and return the authorization code."""
        response = self.authorize(**query)
        location = response.headers["location"]
        return parse_qs(urlsplit(location).query)["code"][0]

    def token(self, **fields: Any) -> InProcessResponse:
        """``POST /oauth2/token`` as JSON -- the documented content type."""
        return self.api.post("/oauth2/token", {"client_id": APPLICATION_ID, **fields})

    def token_form(self, **fields: Any) -> InProcessResponse:
        """The same request, urlencoded. A judgment call in the consumer's
        favour, and the path the reference shipped with no tests at all."""
        pairs = {"client_id": APPLICATION_ID, **fields}
        body = "&".join(f"{name}={_form_value(value)}" for name, value in pairs.items())
        return self.api.call(
            method="POST",
            path="/oauth2/token",
            headers=FORM,
            raw_body=body.encode("utf-8"),
        )


def _form_value(value: Any) -> str:
    """Render a value the way a form-encoded client would.

    Booleans go out as ``true``/``false`` rather than Python's ``True``: the
    point of the urlencoded tests is what a real client sends, and a real
    client sends the JSON spelling.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def harness(profile: str = "oauth-only", **kwargs: Any) -> Iterator[Harness]:
    """Start a unit, yield it, and stop it however the test ends.

    Defaults ``VENDORFAKE_ERROR_SIDECAR=both`` unless the caller's own ``env``
    already names it: this suite reads `unit_error` out of the body to assert
    on the *content* of a refusal (which field, which reason), a concern the
    sidecar's wire placement (konyklabs/roadmap#71; default ``headers`` since)
    does not change. A test of the placement itself builds its own unit --
    with :func:`vendorfake.testing.unit` or ``create_unit`` directly -- rather
    than through this harness.
    """
    env = {"VENDORFAKE_ERROR_SIDECAR": "both", **kwargs.pop("env", {})}
    unit = create_unit(vendor="square", profile=profile, logger=Silent(), env=env, **kwargs)
    try:
        yield Harness(unit=unit, api=in_process(unit))
    finally:
        unit.stop()


def errors_of(response: InProcessResponse) -> list[dict[str, Any]]:
    """The ``errors`` array from a shaped failure."""
    body = response.json()
    return list(body["errors"])


def first_error(response: InProcessResponse) -> dict[str, Any]:
    return errors_of(response)[0]
