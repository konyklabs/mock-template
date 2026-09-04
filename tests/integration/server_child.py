"""The child process for the out-of-process test: a real uvicorn on a real port.

FOR: standing up the same application the CLI's ``serve`` subcommand does,
against a vendor-neutral fake, and telling the parent process two things it
cannot work out for itself -- which port it got, and what the *in-process*
binding answers for a fixed set of probes.

INVARIANT: **the parent never imports ``vendorfake``.** That is the whole value
of this test. The reference implementation got independent verification for
free by being TypeScript with a Python consumer suite -- a second language, a
second HTTP client, a second HMAC -- and both sides are Python here, so a
shared helper bug could pass on both. Keeping the parent to nothing but
``httpx`` and ``json`` is the strongest form of that separation available
without a second language: the only thing the two processes share is HTTP.

So the byte-for-byte comparison the transport must satisfy is arranged in two
halves. This process computes the in-process answers and hands them over as
base64 in the handshake line; the parent replays the same requests over the
socket and compares. Neither half can be right by accident: the bytes crossed a
process boundary and a TCP connection between being produced and being
compared.

Run as ``python tests/integration/server_child.py``. The handshake is one JSON
line on stdout, flushed before uvicorn takes the socket, so the parent can read
it while the server is still starting.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tests.fakes import make_unit, route  # noqa: E402
from vendorfake.asgi import create_app, run_server  # noqa: E402
from vendorfake.core.control.plane import control_plane_routes  # noqa: E402
from vendorfake.core.kernel.reply import json_, text  # noqa: E402
from vendorfake.core.transport.inprocess import in_process  # noqa: E402

#: Every probe the parent replays over HTTP. Method, path, content type, body.
#: A tuple rather than a fixture so that both halves of the comparison are
#: driven from one list and cannot drift apart.
PROBES: tuple[tuple[str, str, str | None, str | None], ...] = (
    ("GET", "/v2/stable", None, None),
    ("GET", "/v2/plain", None, None),
    ("GET", "/__unit/routes", None, None),
    ("GET", "/__unit/errors", None, None),
    ("GET", "/no/such/path", None, None),
    ("DELETE", "/v2/stable", None, None),
    (
        "POST",
        "/__unit/echo",
        "application/x-www-form-urlencoded",
        "grant_type=authorization_code&code=sq0cgb-probe&scope=one&scope=two",
    ),
    ("POST", "/__unit/echo", "application/json", '{"grant_type":"authorization_code"}'),
    ("POST", "/__unit/echo", "application/json", "{not json"),
)


def _stable(args: Any) -> Any:
    return json_({"stable": True, "items": [1, 2, 3], "note": "smørrebrød"})


def _plain(args: Any) -> Any:
    return text("plain body")


def main() -> int:
    unit = make_unit(
        [
            route("GET", "/v2/stable", _stable),
            route("GET", "/v2/plain", _plain),
        ],
        control_routes=control_plane_routes,
        log_level="error",
    )

    client = in_process(unit)
    expected: list[dict[str, Any]] = []
    for method, path, content_type, body in PROBES:
        headers = {} if content_type is None else {"content-type": content_type}
        response = client.call(method=method, path=path, headers=headers, raw_body=body)
        expected.append(
            {
                "method": method,
                "path": path,
                "content_type": content_type,
                "body": body,
                "status": response.status,
                # base64 because the handshake is a JSON line and a body may be
                # any bytes at all; the parent decodes and compares bytes, never
                # a string that a decoder somewhere might have normalised.
                "expected_body_b64": base64.b64encode(response.body).decode("ascii"),
                "expected_headers": dict(response.headers),
            }
        )

    app = create_app(unit)

    def announce(host: str, port: int) -> None:
        sys.stdout.write(json.dumps({"port": port, "host": host, "probes": expected}) + "\n")
        sys.stdout.flush()

    try:
        run_server(app, host="127.0.0.1", port=0, log_level="error", on_bound=announce)
    finally:
        unit.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
