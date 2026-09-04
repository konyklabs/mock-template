"""A Square unit as a consumer would meet it: a command, a port, and HTTP.

Nothing in this module imports ``vendorfake``. The server is started the way an
operator starts it -- ``python -m vendorfake serve --profile full --port 0`` --
and everything else happens over a socket, which is what makes this evidence
rather than a second reading of the same helpers. A bug in a shared serialiser,
a shared HMAC or a shared JSON convention cannot make both sides agree, because
one side is a subprocess and the other is ``httpx`` plus the standard library.

Four claims, mirroring the reference's own consumer suite:

* the unit describes itself, over the wire;
* the whole OAuth-to-COMPLETED path works end to end with state surviving every
  call boundary;
* a real subscriber on a real socket receives a signed ``order.created``, and a
  rejected delivery is retried with the same signature and a retry reason;
* a disabled capability answers explicitly and a chaos rule injects a
  deterministic 429.

The webhook signature is recomputed here from ``hmac`` and ``hashlib``, written
out literally. Calling the unit's own signer would prove only that it agrees
with itself.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]

APPLICATION_ID = "sandbox-sq0idb-unit-square-application"
APPLICATION_SECRET = "sandbox-sq0csb-unit-square-secret"
REDIRECT_URI = "https://example.test/oauth/callback"
SEED_LOCATION = "18YC4JDH91E1H"
TEA_MUG = "2TZFAOHWGG7PAK2QEXWYPZSP"
SEEDED_TOKEN = "EAAAl-unit-seeded-access-token-full-scopes"
API_VERSION = "2026-08-19"
SIGNATURE_KEY = "integration-signature-key"

AUTH = {"authorization": f"Bearer {SEEDED_TOKEN}"}
STARTUP_TIMEOUT_S = 60.0
LISTENING = re.compile(r"listening on http://([0-9.]+):(\d+)")


# ---------------------------------------------------------------------------
# A subscriber that really answers on a socket.
# ---------------------------------------------------------------------------


class Subscriber:
    """An HTTP endpoint the unit posts to, recording exactly what arrived.

    ``received`` holds ``(headers, raw_body)`` pairs -- the bytes, not a parsed
    object, because the signature is computed over bytes and a re-parse would
    verify a different payload from the one delivered.
    """

    def __init__(self) -> None:
        self.received: list[tuple[dict[str, str], bytes]] = []
        self.respond_with: Callable[[int], int] = lambda index: 200
        self._lock = threading.Lock()
        subscriber = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", "0"))
                body = self.rfile.read(length)
                with subscriber._lock:
                    index = len(subscriber.received)
                    subscriber.received.append(({k.lower(): v for k, v in self.headers.items()}, body))
                status = subscriber.respond_with(index)
                self.send_response(status)
                self.send_header("content-length", "0")
                self.end_headers()

            def log_message(self, fmt: str, *args: object) -> None:
                """Silence: a passing run should print the transcript, not access logs."""

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/hooks"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


# ---------------------------------------------------------------------------
# The server under test.
# ---------------------------------------------------------------------------


def _wait_for_port(process: subprocess.Popen[str]) -> int:
    """Read the CLI's own announcement line, or report why it never came."""
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    assert process.stdout is not None
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if line:
            found = LISTENING.search(line)
            if found is not None:
                return int(found.group(2))
        elif process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(f"the server exited before it bound (code {process.returncode}):\n{stderr}")
        else:
            time.sleep(0.01)
    raise AssertionError("the server did not announce a port within the startup timeout")


@pytest.fixture(scope="module")
def subscriber() -> Iterator[Subscriber]:
    listener = Subscriber()
    try:
        yield listener
    finally:
        listener.close()


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    """``python -m vendorfake serve`` -- the shipped command, not a test double.

    ``--port 0`` means the operating system picks the port and the CLI prints
    it before uvicorn takes a single request, which is the only way a parent
    process can learn it while still holding the pipe open.
    """
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "vendorfake",
            "serve",
            "--vendor",
            "square",
            "--profile",
            "full",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--log-level",
            "error",
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        port = _wait_for_port(process)
        yield f"http://127.0.0.1:{port}"
    finally:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on a hang
            process.kill()
            process.wait(timeout=5)


@pytest.fixture(scope="module")
def client(base_url: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=base_url, timeout=30.0, follow_redirects=False) as opened:
        yield opened


def square_signature(signature_key: str, notification_url: str, raw_body: bytes) -> str:
    """base64(HMAC-SHA256(key, notification_url + raw_body)).

    https://developer.squareup.com/docs/webhooks/step3validate
    Written out here rather than imported: this module's whole value is that it
    shares nothing with the code under test except the protocol.
    """
    payload = notification_url.encode("utf-8") + raw_body
    return base64.b64encode(hmac.new(signature_key.encode("utf-8"), payload, hashlib.sha256).digest()).decode()


# ---------------------------------------------------------------------------
# 1. The unit describes itself.
# ---------------------------------------------------------------------------


def test_the_unit_reports_healthy_and_describes_itself(client: httpx.Client) -> None:
    health = client.get("/__unit/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["vendor"] == "square"
    assert body["profile"] == "full"

    info = client.get("/__unit/info").json()
    assert info["vendor"]["api_version"] == API_VERSION
    assert [row["name"] for row in info["capabilities"]] == [
        "oauth",
        "order-lifecycle",
        "merchant-directory",
        "payments",
        "inventory",
        "loyalty",
        "webhooks",
        "chaos",
        "webhooks.chaos",
    ]
    assert client.get("/__unit/routes").json()["routes"]


# ---------------------------------------------------------------------------
# 2. OAuth to COMPLETED, over the wire.
# ---------------------------------------------------------------------------


def test_the_oauth_flow_yields_a_token_that_drives_an_order_to_completed(client: httpx.Client) -> None:
    authorize = client.get(
        "/oauth2/authorize",
        params={
            "client_id": APPLICATION_ID,
            "scope": "ORDERS_READ ORDERS_WRITE PAYMENTS_WRITE MERCHANT_PROFILE_READ ITEMS_READ",
            "state": "integration",
            "redirect_uri": REDIRECT_URI,
        },
    )
    assert authorize.status_code == 302
    # A redirect carries no body at all, and no content type: a truthiness test
    # on the reply builder would have sent `b"{}"` here with a JSON header.
    assert authorize.content == b""
    assert "content-type" not in authorize.headers

    code = parse_qs(urlsplit(authorize.headers["location"]).query)["code"][0]
    assert code.startswith("sq0cgb-")

    token = client.post(
        "/oauth2/token",
        json={
            "client_id": APPLICATION_ID,
            "client_secret": APPLICATION_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            # Required because the authorize request supplied one: "the
            # redirect_uri, if provided in the authorization URL".
            # https://developer.squareup.com/reference/square/oauth-api/obtain-token
            "redirect_uri": REDIRECT_URI,
        },
    )
    assert token.status_code == 200, token.text
    granted = token.json()
    assert granted["token_type"] == "bearer"
    auth = {"authorization": f"Bearer {granted['access_token']}"}

    locations = client.get("/v2/locations", headers=auth).json()["locations"]
    assert SEED_LOCATION in [row["id"] for row in locations]

    created = client.post(
        "/v2/orders",
        headers=auth,
        json={
            "idempotency_key": "integration-order-1",
            "order": {"location_id": SEED_LOCATION, "line_items": [{"catalog_object_id": TEA_MUG, "quantity": "2"}]},
        },
    )
    assert created.status_code == 200
    order = created.json()["order"]
    assert order["state"] == "OPEN"
    assert order["total_money"] == {"amount": 300, "currency": "USD"}

    paid = client.post(
        f"/v2/orders/{order['id']}/pay",
        headers=auth,
        json={"idempotency_key": "integration-pay-1", "order_version": 1, "payment_ids": ["PAY_INTEGRATION"]},
    )
    assert paid.status_code == 200
    assert paid.json()["order"]["state"] == "COMPLETED"

    # State survives the call boundary: a fresh request sees the mutation.
    fetched = client.get(f"/v2/orders/{order['id']}", headers=auth).json()["order"]
    assert fetched["state"] == "COMPLETED"
    assert fetched["version"] == 2

    # A second payment on a COMPLETED order is refused rather than re-paying
    # it: the self-transition the reference permitted, closed.
    again = client.post(
        f"/v2/orders/{order['id']}/pay",
        headers=auth,
        json={"idempotency_key": "integration-pay-2", "payment_ids": ["PAY_AGAIN"]},
    )
    assert again.status_code == 400
    assert again.headers["x-unit-error"] == "invalid_transition"
    assert again.headers["square-version"] == API_VERSION


# ---------------------------------------------------------------------------
# 3. A signed delivery to a real subscriber, and a retry that crosses the wire.
# ---------------------------------------------------------------------------


def test_a_signed_order_created_reaches_a_real_subscriber_and_is_retried(
    client: httpx.Client, subscriber: Subscriber
) -> None:
    registered = client.post(
        "/__unit/webhooks/subscriptions",
        json={
            "id": "wbhk_integration",
            "notification_url": subscriber.url,
            "event_types": ["order.created"],
            "signature_key": SIGNATURE_KEY,
        },
    )
    assert registered.status_code == 201

    # Reject the first delivery so the retry actually crosses the network.
    subscriber.received.clear()
    subscriber.respond_with = lambda index: 500 if index == 0 else 200

    created = client.post(
        "/v2/orders",
        headers=AUTH,
        json={
            "idempotency_key": "integration-webhook-1",
            "order": {"location_id": SEED_LOCATION, "line_items": [{"catalog_object_id": TEA_MUG, "quantity": "1"}]},
        },
    )
    assert created.status_code == 200
    assert client.post("/__unit/webhooks/drain", json={}).status_code == 200

    assert len(subscriber.received) == 2
    (first_headers, first_body), (retry_headers, retry_body) = subscriber.received

    for headers, raw in ((first_headers, first_body), (retry_headers, retry_body)):
        expected = square_signature(SIGNATURE_KEY, subscriber.url, raw)
        assert headers["x-square-hmacsha256-signature"] == expected
        assert headers["square-environment"] == "Sandbox"
        assert headers["content-type"] == "application/json"

    event = json.loads(first_body)
    assert event["type"] == "order.created"
    assert event["data"]["type"] == "order_created"
    assert event["data"]["object"]["order_created"]["state"] == "OPEN"

    # At-least-once: the retry is the same event, so a consumer dedupes on
    # event_id rather than on arrival.
    assert json.loads(retry_body)["event_id"] == event["event_id"]
    assert retry_headers["square-retry-number"] == "1"
    assert retry_headers["square-retry-reason"] == "http_error"
    # The signature is not bound to the attempt: identical bytes, identical
    # signature, which is what lets a subscriber verify a retry at all.
    assert retry_headers["x-square-hmacsha256-signature"] == first_headers["x-square-hmacsha256-signature"]


# ---------------------------------------------------------------------------
# 4. A disabled capability, and a deterministic 429.
# ---------------------------------------------------------------------------


def test_a_disabled_capability_answers_explicitly_over_http(client: httpx.Client) -> None:
    assert client.post("/__unit/capabilities", json={"disable": ["merchant-directory"]}).status_code == 200
    try:
        disabled = client.get("/v2/locations", headers=AUTH)
        assert disabled.status_code == 501
        assert disabled.headers["x-unit-error"] == "capability_disabled"
        assert disabled.headers["x-unit-capability"] == "merchant-directory"
        assert disabled.json()["errors"][0]["code"] == "NOT_IMPLEMENTED"
    finally:
        assert client.post("/__unit/capabilities", json={"enable": ["merchant-directory"]}).status_code == 200
    assert client.get("/v2/locations", headers=AUTH).status_code == 200


def test_a_chaos_rule_injects_a_deterministic_429_over_http(client: httpx.Client) -> None:
    added = client.post(
        "/__unit/chaos/rules",
        json={
            "id": "integration-429",
            "scope": "request",
            "fault": "rate_limit",
            "match": {"route": "GET /v2/locations"},
            "when": {"nth": [2]},
        },
    )
    assert added.status_code == 200
    try:
        statuses = [client.get("/v2/locations", headers=AUTH) for _ in range(3)]
        assert [response.status_code for response in statuses] == [200, 429, 200]
        assert statuses[1].json()["errors"][0]["code"] == "RATE_LIMITED"
        assert statuses[1].headers["retry-after"] == "1"
    finally:
        assert client.post("/__unit/chaos/reset", json={}).status_code == 200
