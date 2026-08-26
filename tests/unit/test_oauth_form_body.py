"""The first test written for this implementation, before any implementation existed.

It is here because this exact request shape broke two of the three bake-off
entries that preceded the rebuild. FastAPI needs ``python-multipart`` to read a
body even when that body is plain ``application/x-www-form-urlencoded``: a
``Form(...)`` parameter raises at import time without it, and
``await request.form()`` raises at request time. Either way the content-type
decision ends up at the transport edge, which is precisely the leak that
D-002's framework-free-core invariant forbids.

Writing it first makes that failure impossible to design in. The test blocks
the web framework entirely and still expects a form-encoded token request to
be understood, so the only implementation that can pass it is one where the
core parses the body itself from raw bytes.

It carried ``xfail(strict=True)`` from the moment it was written until the
OAuth surface landed. The marker is gone, which is what ``strict=True`` was
for: it could not be left on by accident.

A note on fidelity, because this test is easy to misread. Square documents
``POST /oauth2/token`` as ``Content-Type: application/json``, with a verbatim
curl example, and publishes nothing about accepting form encoding. Accepting
both is a judgment call made in the consumer's favour, carried here with that
provenance. This is a framework-trap test, not a statement about Square. The
documented JSON shape is asserted separately, and it is the JSON shape that
the conformance suite records as vendor behaviour.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import pytest

BLOCKED = ("fastapi", "starlette", "uvicorn", "python_multipart", "multipart")

APPLICATION_ID = "sandbox-sq0idb-unit-square-application"
APPLICATION_SECRET = "sandbox-sq0csb-unit-square-secret"
"""What ``profiles/oauth-only.json`` configures. Spelled here rather than
imported, because importing the vendor package at module scope would happen
before the fixture blocks the framework and would prove less than it looks."""


class _Silent:
    """A logger that says nothing, so a passing run prints no unit banner."""

    def debug(self, msg: str, fields: object = None) -> None: ...
    def info(self, msg: str, fields: object = None) -> None: ...
    def warn(self, msg: str, fields: object = None) -> None: ...
    def error(self, msg: str, fields: object = None) -> None: ...


SILENT = _Silent()


class _Blocked:
    """A meta-path finder that refuses the web framework, however it is reached."""

    def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
        if fullname.split(".")[0] in BLOCKED:
            raise ImportError(
                f"{fullname} is not importable in this test: the core must parse "
                "form bodies without a web framework present"
            )
        return None


@pytest.fixture
def no_web_framework() -> Iterator[None]:
    finder = _Blocked()
    saved = {name: sys.modules.pop(name, None) for name in BLOCKED}
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        for name, module in saved.items():
            if module is not None:
                sys.modules[name] = module


def test_token_request_with_form_encoded_body(no_web_framework: None) -> None:
    """The whole point, and the marker that used to guard it is gone.

    Two reconciliations against the version written before any implementation
    existed, both because the test assumed a world the implementation
    legitimately contradicts:

    * the credentials are the profile's, not invented ones. ``oauth-only.json``
      configures ``sandbox-sq0idb-unit-square-application``, and a token
      request whose ``client_id`` names a different application is refused with
      ``UNAUTHORIZED`` -- correctly, and before the body is ever looked at,
      which would make this a test of the wrong thing.
    * the authorization code is minted rather than seeded. The shipped scenario
      contains no authorization codes and should not: Square issues one from
      the authorization page and it expires five minutes later, so a code
      baked into a fixture is a code that is either always fresh or always
      stale. ``GET /oauth2/authorize`` is the only way to get one, and it costs
      one line.

    Neither changes what is under test. The assertion that matters is the same:
    the framework is blocked at ``sys.meta_path``, the body is
    ``application/x-www-form-urlencoded``, and the unit understands it.
    """
    from vendorfake import create_unit
    from vendorfake.core.transport.inprocess import in_process

    unit = create_unit(vendor="square", profile="oauth-only", logger=SILENT)
    try:
        api = in_process(unit)
        authorized = api.call(
            method="GET",
            path="/oauth2/authorize",
            query={"client_id": APPLICATION_ID, "state": "form-encoded-body"},
        )
        assert authorized.status == 302, authorized.text
        code = parse_qs(urlsplit(authorized.headers["location"]).query)["code"][0]

        response = api.call(
            method="POST",
            path="/oauth2/token",
            headers={"content-type": "application/x-www-form-urlencoded"},
            raw_body=(
                f"client_id={APPLICATION_ID}"
                f"&client_secret={APPLICATION_SECRET}"
                f"&grant_type=authorization_code"
                f"&code={code}"
            ).encode(),
        )
        assert response.status == 200, response.text
        body = response.json()
        assert body["access_token"]
        assert body["token_type"] == "bearer"
        assert body["merchant_id"] == "MLQW2MYBY81PZ"
    finally:
        unit.stop()


def test_the_documented_json_body_is_the_one_square_publishes(no_web_framework: None) -> None:
    """The JSON path, asserted beside the form path and with equal weight.

    Square documents ``POST /oauth2/token`` as ``Content-Type:
    application/json``, with a verbatim curl example, and publishes nothing
    about form encoding -- so this is the fidelity test and the one above is
    the framework-trap test. The reference had zero tests over its own
    urlencoded branch, which is exactly how a defect in it went unnoticed; both
    paths are covered here, and they must agree on everything but the encoding.
    """
    from vendorfake import create_unit
    from vendorfake.core.transport.inprocess import in_process

    unit = create_unit(vendor="square", profile="oauth-only", logger=SILENT)
    try:
        api = in_process(unit)
        authorized = api.call(
            method="GET",
            path="/oauth2/authorize",
            query={"client_id": APPLICATION_ID, "state": "json-body"},
        )
        code = parse_qs(urlsplit(authorized.headers["location"]).query)["code"][0]
        response = api.post(
            "/oauth2/token",
            {
                "client_id": APPLICATION_ID,
                "client_secret": APPLICATION_SECRET,
                "grant_type": "authorization_code",
                "code": code,
            },
        )
        assert response.status == 200, response.text
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["short_lived"] is False
        # Absent, not null: the code flow's refresh token does not expire.
        assert "refresh_token_expires_at" not in body
    finally:
        unit.stop()
