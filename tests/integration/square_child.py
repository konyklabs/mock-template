"""A real Square unit under a real uvicorn, for the out-of-process OAuth test.

FOR: standing the *vendor* up the way ``vendorfake serve`` does, so that the
form-encoded token request the whole build was organised around is proven over
a socket and not only in process.

INVARIANT: **the parent process never imports ``vendorfake``.** It gets a port
and an authorization code on one JSON line and everything else over HTTP. The
code is minted here rather than by the parent for one reason only: it costs the
parent an extra round trip it would otherwise have to make before the thing
under test, and a failure in that round trip would look like a failure in the
thing under test.

Run as ``python tests/integration/square_child.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from vendorfake import create_unit  # noqa: E402
from vendorfake.asgi import create_app, run_server  # noqa: E402
from vendorfake.core.transport.inprocess import in_process  # noqa: E402

APPLICATION_ID = "sandbox-sq0idb-unit-square-application"
APPLICATION_SECRET = "sandbox-sq0csb-unit-square-secret"


def main() -> int:
    unit = create_unit(vendor="square", profile="oauth-only", env={"VENDORFAKE_LOG_LEVEL": "error"})

    # Two codes, minted before the socket exists: one for the urlencoded
    # request and one for the JSON request, because a code is single use.
    client = in_process(unit)
    codes = []
    for label in ("form", "json"):
        response = client.call(
            method="GET",
            path="/oauth2/authorize",
            query={"client_id": APPLICATION_ID, "state": label},
        )
        codes.append(parse_qs(urlsplit(response.headers["location"]).query)["code"][0])

    app = create_app(unit)

    def announce(host: str, port: int) -> None:
        sys.stdout.write(
            json.dumps(
                {
                    "port": port,
                    "host": host,
                    "client_id": APPLICATION_ID,
                    "client_secret": APPLICATION_SECRET,
                    "form_code": codes[0],
                    "json_code": codes[1],
                }
            )
            + "\n"
        )
        sys.stdout.flush()

    try:
        run_server(app, host="127.0.0.1", port=0, log_level="error", on_bound=announce)
    finally:
        unit.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
