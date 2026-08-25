"""Launch the unit the way a Python consumer would, and hand back a base URL.

Mirror of tests/support/launch.ts. Two backends behind one contract:

    docker   Testcontainers starts the published image. This is the real
             packaging story and what CI runs.
    process  The built server is spawned directly. Same entry point, same
             environment contract, no container runtime.

``auto`` (the default) picks docker when a container runtime is reachable and
falls back to process otherwise. Every run prints which backend it used, so a
green result is never ambiguous about what it proved.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ENTRY = REPO_ROOT / "packages" / "square" / "dist" / "bin" / "serve.js"
DEFAULT_IMAGE = "vendor-unit-square:test"


@dataclass
class UnitHandle:
    base_url: str
    backend: str
    describe: str
    _stop: Callable[[], None] = field(repr=False)
    _host_url: Callable[[int], str] = field(repr=False)

    def host_url(self, port: int) -> str:
        """URL the UNIT can use to reach a server listening on the test host."""
        return self._host_url(port)

    def stop(self) -> None:
        self._stop()


def detect_backend() -> str:
    """Find a container runtime, and configure Testcontainers for it.

    Not every developer's Docker lives at /var/run/docker.sock: Colima, Rancher
    Desktop and Podman each put it somewhere else. Asking the docker CLI which
    context is active turns "works on my machine" into "works on the machine
    that has docker installed".
    """
    forced = os.environ.get("UNIT_TEST_BACKEND")
    if forced == "process":
        return "process"
    if os.environ.get("DOCKER_HOST"):
        return "docker"

    candidates = [
        "/var/run/docker.sock",
        str(Path.home() / ".docker" / "run" / "docker.sock"),
        "/run/podman/podman.sock",
    ]
    if any(Path(p).exists() for p in candidates):
        return "docker"

    endpoint = _active_docker_endpoint()
    if endpoint and endpoint.startswith("unix://") and Path(endpoint[len("unix://") :]).exists():
        os.environ["DOCKER_HOST"] = endpoint
        # Ryuk mounts the socket from inside the runtime's VM, where it is
        # always at the canonical path even when the host sees it elsewhere.
        os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")
        return "docker"
    # Forcing docker must still fail loudly rather than silently downgrading.
    return "docker" if forced == "docker" else "process"


def _active_docker_endpoint() -> str | None:
    try:
        result = subprocess.run(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def launch_unit(profile: str = "full", backend: str | None = None, env: Mapping[str, str] | None = None) -> UnitHandle:
    chosen = backend or detect_backend()
    merged = {"UNIT_PROFILE": profile, "UNIT_LOG_LEVEL": "warn", **dict(env or {})}
    return _start_container(merged) if chosen == "docker" else _start_process(merged)


def _start_container(env: dict[str, str]) -> UnitHandle:
    # Resolving the socket is a precondition of using a container, not a step in
    # deciding whether to: UNIT_TEST_BACKEND=docker must still find it.
    detect_backend()

    from testcontainers.core.container import DockerContainer  # imported lazily

    image = os.environ.get("UNIT_IMAGE")
    if not image:
        image = DEFAULT_IMAGE
        subprocess.run(["docker", "build", "-t", image, "."], cwd=REPO_ROOT, check=True)

    container = DockerContainer(image).with_exposed_ports(8080)
    for key, value in env.items():
        container = container.with_env(key, value)
    # Lets the unit reach a subscriber running on the test host. Docker Desktop
    # provides this alias already; the mapping makes it work on Linux too.
    container = container.with_kwargs(extra_hosts={"host.docker.internal": "host-gateway"})
    container.start()

    base_url = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8080)}"
    _wait_for_health(base_url, timeout=120)
    return UnitHandle(
        base_url=base_url,
        backend="docker",
        describe=f"Testcontainers, image {image}",
        _stop=container.stop,
        _host_url=lambda port: f"http://host.docker.internal:{port}",
    )


def _start_process(env: dict[str, str]) -> UnitHandle:
    if not SERVER_ENTRY.exists():
        raise RuntimeError(f"{SERVER_ENTRY} is missing — run `npm run build` first")
    port = _free_port()
    child_env = {**os.environ, **env, "UNIT_PORT": str(port), "UNIT_HOST": "127.0.0.1"}
    process = subprocess.Popen(
        ["node", str(SERVER_ENTRY)],
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base_url, timeout=20)
    except Exception:
        process.kill()
        _, stderr = process.communicate(timeout=5)
        raise RuntimeError(f"unit did not become healthy:\n{stderr.decode('utf-8', 'replace')}") from None

    def stop() -> None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    return UnitHandle(
        base_url=base_url,
        backend="process",
        describe=f"spawned {SERVER_ENTRY}",
        _stop=stop,
        _host_url=lambda p: f"http://127.0.0.1:{p}",
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_health(base_url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            status, _, _ = call(base_url, "GET", "/__unit/health")
            if status == 200:
                return
            last = f"status {status}"
        except Exception as exc:  # noqa: BLE001 - the server may not be up yet
            last = exc
        time.sleep(0.1)
    raise TimeoutError(f"timed out waiting for {base_url}/__unit/health (last: {last})")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A consumer testing an OAuth redirect needs to see the 302, not follow it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, D102
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def call(
    base_url: str,
    method: str,
    path: str,
    body: Any = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, str], Any]:
    """Minimal JSON client: returns (status, headers, parsed body)."""
    data = None
    request_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request_headers.setdefault("content-type", "application/json")

    request = urllib.request.Request(f"{base_url}{path}", data=data, method=method.upper(), headers=request_headers)
    try:
        with _opener.open(request, timeout=30) as response:
            status = response.status
            response_headers = {k.lower(): v for k, v in response.headers.items()}
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        response_headers = {k.lower(): v for k, v in error.headers.items()}
        text = error.read().decode("utf-8")

    try:
        parsed = json.loads(text) if text else None
    except json.JSONDecodeError:
        parsed = text
    return status, response_headers, parsed


def announce(handle: UnitHandle, extra: Iterable[str] = ()) -> None:
    parts = [f"backend={handle.backend}", f"({handle.describe})", f"baseUrl={handle.base_url}", *extra]
    print(f"\n[integration] {' '.join(parts)}\n", file=sys.stderr)
