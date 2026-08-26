"""A real uvicorn on a real socket, driven from a process that knows nothing.

Nothing in this module imports ``vendorfake``. The only thing it shares with
the code under test is HTTP, which is what makes it evidence rather than a
second reading of the same helpers: a bug in a shared serialiser cannot make
both sides agree, because one side is a subprocess and the other is httpx.

The three properties this proves, and cannot be proved anywhere else:

* the form-encoded body arrives intact **over a socket** -- constraint 2's
  "the exact shape that broke two of three implementations" includes the
  socket, and an in-process test never touches uvicorn's h11 parser;
* HTTP and in-process answers are byte-identical, with the difference having
  survived a process boundary in each direction;
* ``framework_answered`` is 0 **as read over HTTP**, which is the only place a
  parent process can read it.
"""

from __future__ import annotations

import base64
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
CHILD = REPO_ROOT / "tests" / "integration" / "server_child.py"

#: Headers that cannot match and are excluded from the comparison, named here
#: rather than waved at. ``x-unit-request-id`` is minted per binding;
#: ``content-length`` is added by Starlette; ``date``, ``server`` and
#: ``connection`` are uvicorn's. Everything the unit set must survive.
EXCLUDED_HEADERS = frozenset({"x-unit-request-id", "content-length", "date", "server", "connection"})

STARTUP_TIMEOUT_S = 30.0


class Server:
    """A child process serving on a port it chose, plus what it expects."""

    def __init__(self, process: subprocess.Popen[str], handshake: dict[str, object]) -> None:
        self.process = process
        self.port = int(handshake["port"])  # type: ignore[arg-type]
        self.probes: list[dict[str, object]] = list(handshake["probes"])  # type: ignore[arg-type]
        self.base_url = f"http://127.0.0.1:{self.port}"


def _read_handshake(process: subprocess.Popen[str]) -> dict[str, object]:
    """One JSON line, written before uvicorn took the socket.

    A deadline rather than a blocking read: if the child dies during startup,
    ``readline`` would block until the pipe closed and the failure would be a
    timeout with no output. This reports the child's own stderr instead.
    """
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    assert process.stdout is not None
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if line:
            return dict(json.loads(line))
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(f"the server exited before it bound (code {process.returncode}):\n{stderr}")
        time.sleep(0.01)
    raise AssertionError("the server did not announce a port within the startup timeout")


@pytest.fixture(scope="module")
def server() -> Iterator[Server]:
    process = subprocess.Popen(
        [sys.executable, str(CHILD)],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        handshake = _read_handshake(process)
        yield Server(process, handshake)
    finally:
        # SIGINT rather than SIGKILL: uvicorn installs a handler for it and
        # runs its graceful-shutdown path, so this also exercises the shutdown
        # the CLI relies on rather than only the startup.
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on a hang
            process.kill()
            process.wait(timeout=5)


@pytest.fixture(scope="module")
def client(server: Server) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=server.base_url, timeout=15.0) as opened:
        yield opened


def test_the_server_bound_a_port_it_chose(server: Server) -> None:
    """``--port 0`` has to report the number it got, before serving.

    A server that only learns its port once it is answering cannot tell the
    process that started it, because that process is blocked reading. Binding
    first and announcing second is what makes the whole test possible.
    """
    assert server.port > 0


def test_health_is_answered_over_the_socket(client: httpx.Client) -> None:
    response = client.get("/__unit/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_a_form_encoded_body_survives_the_socket(client: httpx.Client) -> None:
    """The exact shape that broke two of three earlier implementations.

    ``python-multipart`` is not a dependency of this distribution, so a
    ``Form(...)`` parameter would have raised at import and
    ``await request.form()`` would raise right here, in the child, and this
    request would come back a 500. It comes back parsed, which means the
    adapter read bytes and the core decided what they were.
    """
    body = "grant_type=authorization_code&code=sq0cgb-probe&scope=one&scope=two"
    response = client.post(
        "/__unit/echo",
        content=body.encode(),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200
    parsed = response.json()
    assert parsed["content_type"] == "application/x-www-form-urlencoded"
    assert parsed["raw_len"] == len(body)
    assert parsed["fields"]["grant_type"] == "authorization_code"
    assert parsed["fields_multi"]["scope"] == ["one", "two"]


def test_an_unknown_path_gets_the_vendor_envelope_not_starlette_s(client: httpx.Client) -> None:
    """Raw body asserted, because the shape is the assertion.

    ``{"detail": "Not Found"}`` is what a framework 404 looks like. A consumer
    who wrote their error handling against that would have written it against
    Starlette.
    """
    response = client.get("/no/such/path")
    assert response.status_code == 404
    assert response.content == b'{"error":{"code":"no_route","path":"/no/such/path"}}'


def test_a_malformed_json_body_is_never_422(client: httpx.Client) -> None:
    response = client.post("/__unit/echo", content=b"{not json", headers={"content-type": "application/json"})
    assert response.status_code == 400
    assert response.headers["x-unit-error"] == "invalid_json"


def test_percent_escapes_survive_the_server_s_own_decoding(client: httpx.Client) -> None:
    """uvicorn hands the application an already-decoded path.

    ``/v2/orders/a%2Fb`` arrives in ``scope["path"]`` as ``/v2/orders/a/b`` --
    three segments where the consumer sent two. Reading ``raw_path`` instead is
    what keeps the segmentation the consumer's; here there is no such route
    either way, so what is asserted is that the path the unit reports back is
    the one that was sent.
    """
    response = client.get("/v2/orders/a%2Fb")
    assert response.status_code == 404
    assert response.json()["error"]["path"] == "/v2/orders/a%2Fb"


def test_http_and_in_process_answers_are_byte_identical(client: httpx.Client, server: Server) -> None:
    """The comparison the whole transport design exists to satisfy.

    The expected bytes were produced by the in-process binding inside the child
    process, carried out as base64, and are compared here against bytes that
    came back over TCP. Any re-serialisation anywhere on the HTTP path -- a
    ``JSONResponse``, a compression middleware, a helpful header rewrite --
    shows up as a diff.
    """
    for probe in server.probes:
        method = str(probe["method"])
        path = str(probe["path"])
        content_type = probe["content_type"]
        body = probe["body"]
        headers = {} if content_type is None else {"content-type": str(content_type)}
        content = None if body is None else str(body).encode()

        response = client.request(method, path, content=content, headers=headers)
        expected_body = base64.b64decode(str(probe["expected_body_b64"]))

        where = f"{method} {path} ({content_type})"
        assert response.status_code == probe["status"], where
        assert len(response.content) == len(expected_body), f"{where}: {len(response.content)} != {len(expected_body)}"
        assert response.content == expected_body, where

        expected_headers = {
            name: value
            for name, value in dict(probe["expected_headers"]).items()  # type: ignore[arg-type]
            if name not in EXCLUDED_HEADERS
        }
        got_headers = {name: value for name, value in response.headers.items() if name not in EXCLUDED_HEADERS}
        assert got_headers == expected_headers, where


def test_the_framework_answered_nothing_over_http(client: httpx.Client) -> None:
    """Read last, over the wire, after every other test in this module.

    The counter lives in the child process, so this is the only way to read it
    at all -- which is precisely why it is reported at ``/__unit/health``
    instead of kept in a list the parent could never see. Its correct value is
    0: every request above reached the unit, and none was answered by Starlette
    on the way.
    """
    assert client.get("/__unit/health").json()["framework_answered"] == 0
