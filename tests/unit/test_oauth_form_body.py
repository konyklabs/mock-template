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

import pytest

BLOCKED = ("fastapi", "starlette", "uvicorn", "python_multipart", "multipart")


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


@pytest.mark.xfail(
    strict=True,
    reason="the OAuth surface lands in phase 4; this marker is removed there, and strict=True means it cannot be left on by accident",
)
def test_token_request_with_form_encoded_body(no_web_framework: None) -> None:
    from vendorfake import create_unit
    from vendorfake.core.transport.inprocess import in_process

    # The failure shape is asserted here rather than left to the marker,
    # because pytest reports the marker's `reason` and not the underlying
    # exception -- so "the failure moved" is otherwise unreadable from a run.
    # Where it stands today: the kernel, the pipeline and this binding all
    # exist and are reachable with the web framework blocked; the one thing
    # missing is the vendor module. When the vendor lands, this block is what
    # goes red first, and it is deleted along with the marker below.
    with pytest.raises(ValueError, match="no vendor named 'square'"):
        create_unit(vendor="square", profile="oauth-only")

    unit = create_unit(vendor="square", profile="oauth-only")
    try:
        api = in_process(unit)
        response = api.call(
            method="POST",
            path="/oauth2/token",
            headers={"content-type": "application/x-www-form-urlencoded"},
            raw_body=(
                b"client_id=sandbox-app-id"
                b"&client_secret=sandbox-app-secret"
                b"&grant_type=authorization_code"
                b"&code=sq0cgb-seeded-authorization-code"
            ),
        )
        assert response.status == 200, response.text
        body = response.json()
        assert body["access_token"]
        assert body["token_type"] == "bearer"
    finally:
        unit.stop()
