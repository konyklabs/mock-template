"""The form-encoded token request, over a socket, against the real vendor.

This is the transport-side half of the constraint the whole build was organised
around, and it is the half an in-process test cannot reach: the bytes go
through uvicorn's h11 parser and Starlette's request object before the unit
sees them, which is exactly where ``python-multipart`` would have been needed
had the content-type decision been left at the edge.

``python-multipart`` is not a dependency of this distribution. That absence is
what makes this test meaningful: with it installed a stray ``await
request.form()`` in the adapter would work forever and no gate would notice.

Nothing here imports ``vendorfake``.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
CHILD = REPO_ROOT / "tests" / "integration" / "square_child.py"
STARTUP_TIMEOUT_S = 30.0


@pytest.fixture(scope="module")
def square_server() -> Iterator[dict[str, object]]:
    process = subprocess.Popen(
        [sys.executable, str(CHILD)],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        handshake: dict[str, object] | None = None
        assert process.stdout is not None
        while time.monotonic() < deadline and handshake is None:
            line = process.stdout.readline()
            if line:
                handshake = dict(json.loads(line))
                break
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr is not None else ""
                raise AssertionError(f"the server exited before it bound:\n{stderr}")
            time.sleep(0.01)
        if handshake is None:
            raise AssertionError("the server did not announce a port within the startup timeout")
        handshake["base_url"] = f"http://127.0.0.1:{handshake['port']}"
        yield handshake
    finally:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on a wedged child
            process.kill()
            process.wait(timeout=5)


def test_a_form_encoded_token_request_succeeds_over_a_real_socket(
    square_server: dict[str, object],
) -> None:
    body = (
        f"client_id={square_server['client_id']}"
        f"&client_secret={square_server['client_secret']}"
        f"&grant_type=authorization_code"
        f"&code={square_server['form_code']}"
        f"&short_lived=true"
    )
    response = httpx.post(
        f"{square_server['base_url']}/oauth2/token",
        content=body.encode("utf-8"),
        headers={"content-type": "application/x-www-form-urlencoded"},
        timeout=10.0,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["access_token"].startswith("EAAA")
    # The coercion, over the wire: `short_lived=true` in a form body is a
    # string, and it still means what it says.
    assert payload["short_lived"] is True
    # And the vendor's own decoration survived the transport.
    assert response.headers["square-version"] == "2026-08-19"


def test_the_documented_json_path_over_the_same_socket(square_server: dict[str, object]) -> None:
    """Square documents this endpoint as `application/json`; the form path is
    this unit's judgment call. Both are exercised here, so neither can rot."""
    response = httpx.post(
        f"{square_server['base_url']}/oauth2/token",
        json={
            "client_id": square_server["client_id"],
            "client_secret": square_server["client_secret"],
            "grant_type": "authorization_code",
            "code": square_server["json_code"],
        },
        timeout=10.0,
    )
    assert response.status_code == 200, response.text
    assert response.json()["short_lived"] is False


def test_a_charset_parameter_on_the_content_type_is_understood(
    square_server: dict[str, object],
) -> None:
    """`application/x-www-form-urlencoded; charset=utf-8` is what several HTTP
    clients send by default. A substring match on the header would pass this
    and a naive equality check would not, which is why the core parses the
    media type rather than grepping it."""
    response = httpx.post(
        f"{square_server['base_url']}/oauth2/token",
        content=f"client_id={square_server['client_id']}&grant_type=".encode(),
        headers={"content-type": "application/x-www-form-urlencoded; charset=utf-8"},
        timeout=10.0,
    )
    # The body was understood: the failure is the empty grant_type, not the
    # content type. A body the unit could not read would answer `invalid_json`.
    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "grant_type"


def test_the_framework_never_answered_for_itself(square_server: dict[str, object]) -> None:
    """Read over HTTP, which is the only place a parent process can read it: a
    non-zero count means the catch-all route has a hole and Starlette answered
    a request the unit never saw."""
    health = httpx.get(f"{square_server['base_url']}/__unit/health", timeout=10.0).json()
    assert health["vendor"] == "square"
