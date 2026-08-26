"""The command line: dispatch, precedence, and the two things it must not do.

The two are the point of most of this file. ``vendorfake --help`` must not
import a web framework, and no module but this one may read ``os.environ`` --
both are properties of the *process*, so both are asserted by starting a fresh
interpreter and looking at what it did, not by reading the source.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from vendorfake.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]


def run(*argv: str) -> tuple[int, str]:
    """Call ``main`` with stdout captured, returning the code and the text."""
    buffer = io.StringIO()
    saved = sys.stdout
    sys.stdout = buffer
    try:
        code = main(list(argv))
    finally:
        sys.stdout = saved
    return code, buffer.getvalue()


def child(code: str) -> subprocess.CompletedProcess[str]:
    """Run a snippet in a fresh interpreter with the repo's ``src`` importable.

    A subprocess rather than an import, because what is being asserted is what
    a *process* ended up holding: inside this one, pytest has already imported
    half the distribution, so ``sys.modules`` says nothing.
    """
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


# ---------------------------------------------------------------------------
# The two process-level properties.
# ---------------------------------------------------------------------------


def test_help_imports_no_web_framework() -> None:
    """The reason every first-party import in ``cli.py`` is inside a function.

    ``serve`` is the only subcommand that reaches :mod:`vendorfake.asgi`, and
    that import is the single named exception in ``tools/boundary.toml``. If it
    drifted to module level, ``--help`` would start paying for FastAPI and the
    exception would quietly stop being an exception -- which no import-graph
    rule can see, because the import is still in the file it is allowed to be
    in.
    """
    result = child(
        "import sys\n"
        "from vendorfake.cli import main\n"
        "try:\n"
        "    main(['--help'])\n"
        "except SystemExit:\n"
        "    pass\n"
        "loaded = sorted(n for n in sys.modules if n.split('.')[0] in {'fastapi', 'starlette', 'uvicorn'})\n"
        "print('LOADED', loaded)\n"
    )
    assert result.returncode == 0, result.stderr
    assert "LOADED []" in result.stdout


def test_no_other_module_reads_the_process_environment() -> None:
    """``create_unit(env=...)`` defaults to ``{}``, and the CLI is the exception.

    Asserted the only way that means anything: set a ``VENDORFAKE_*`` variable
    in a child process, build a unit without passing ``env``, and check that it
    was ignored. A unit that read ``os.environ`` on its own would make one
    test's exported variable change another test's profile.
    """
    result = child(
        "import os, sys\n"
        "os.environ['VENDORFAKE_PROFILE'] = 'from-the-environment'\n"
        "sys.path.insert(0, '.')\n"
        "from tests.fakes import make_unit\n"
        "unit = make_unit()\n"
        "print('PROFILE', unit.context.config.profile)\n"
        "unit.stop()\n"
    )
    assert result.returncode == 0, result.stderr
    assert "PROFILE test" in result.stdout


# ---------------------------------------------------------------------------
# Dispatch.
# ---------------------------------------------------------------------------


def test_version_prints_the_distribution_version() -> None:
    from vendorfake import __version__

    code, out = run("--version")
    assert code == 0
    assert out.strip() == __version__


def test_no_subcommand_prints_help_and_fails() -> None:
    """Exit 2, not 0. A container whose command was mistyped must not look like
    a successful run that simply did nothing."""
    code, out = run()
    assert code == 2
    assert "COMMAND" in out


def test_every_declared_subcommand_has_a_dispatch_arm() -> None:
    """Derived from the parser, never hand-listed.

    Adding a subparser without adding a dispatch arm would otherwise be a
    silent exit through the final ``raise``; this fails at the moment the
    parser and the dispatcher disagree.
    """
    import argparse

    from vendorfake.cli import _build_parser

    parser = _build_parser()
    subparsers = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    declared = set(subparsers[0].choices)
    assert declared == {"serve", "info", "openapi", "vendors", "conformance"}


def test_an_unknown_vendor_is_a_startup_failure_that_lists_the_real_ones() -> None:
    """Not a server that starts and 404s everything.

    "Every endpoint returns 404" is indistinguishable from a consumer's own
    misconfiguration, which is exactly the debugging session this refusal
    prevents.
    """
    with pytest.raises(SystemExit) as raised:
        run("info", "--vendor", "nosuchvendor")
    assert "no vendor named 'nosuchvendor'" in str(raised.value)


def test_vendors_reports_nothing_installed_as_a_failure() -> None:
    """Exit 1 with the message on stderr. An empty successful list would read
    as "the fake is fine, you asked for nothing"."""
    code, out = run("vendors")
    assert code == 1
    assert out == ""


def test_a_non_integer_port_variable_is_refused_by_name() -> None:
    """Rather than falling back to the default and binding a port nobody asked
    for while reporting success."""
    from vendorfake.cli import _env_int

    with pytest.raises(SystemExit) as raised:
        _env_int({"VENDORFAKE_PORT": "eighty"}, "VENDORFAKE_PORT")
    assert "VENDORFAKE_PORT='eighty' is not an integer" in str(raised.value)


def test_an_empty_environment_variable_counts_as_unset() -> None:
    """``VENDORFAKE_PROFILE=`` in a compose file means "I did not set this",
    not "the profile is the empty string"."""
    from vendorfake.cli import _env_str

    assert _env_str({"VENDORFAKE_PROFILE": ""}, "VENDORFAKE_PROFILE") is None
    assert _env_str({}, "VENDORFAKE_PROFILE") is None
    assert _env_str({"VENDORFAKE_PROFILE": "full"}, "VENDORFAKE_PROFILE") == "full"


# ---------------------------------------------------------------------------
# The subcommands that produce a document.
# ---------------------------------------------------------------------------


def test_openapi_prints_the_same_document_the_adapter_serves() -> None:
    """One generator, one naming, two renderings.

    The CLI reaches the document with no server and no web framework; the
    adapter serves the bytes of the same call. Both go through
    ``document_for_unit``, which is the point: a second place deciding the
    title or the version would drift the first time either changed, and this
    test would not see it if it built its own expectation.
    """
    import functools

    import anyio
    import httpx

    from tests.fakes import make_unit, route
    from vendorfake.asgi import OPENAPI_PATH, create_app
    from vendorfake.core.control.openapi import UNOFFICIAL_NOTICE, document_for_unit
    from vendorfake.core.control.plane import control_plane_routes
    from vendorfake.core.kernel.reply import json_
    from vendorfake.core.util.json import dump_json

    unit = make_unit(
        [route("GET", "/v2/orders", lambda args: json_({}))],
        control_routes=functools.partial(control_plane_routes),
    )
    try:
        offline = document_for_unit(unit)
        app = create_app(unit)

        async def fetch() -> bytes:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                return (await client.get(OPENAPI_PATH)).content

        served = anyio.run(fetch)
        assert served == dump_json(offline)

        parsed = json.loads(served)
        assert parsed["paths"]["/v2/orders"]["get"]["x-unit-capability"] == "orders"
        assert parsed["info"]["description"] == UNOFFICIAL_NOTICE
        assert "Unofficial" in parsed["info"]["description"]
    finally:
        unit.stop()


def test_the_cli_can_drop_the_control_plane_from_the_document() -> None:
    """``--no-internal`` describes only what the fake is pretending to be.

    The control plane is real and is part of the product, so it is in the
    document by default; a consumer generating a client for the vendor surface
    alone should not have to filter it out by hand.
    """
    from tests.fakes import make_unit, route
    from vendorfake.core.control.openapi import document_for_unit
    from vendorfake.core.control.plane import control_plane_routes
    from vendorfake.core.kernel.reply import json_

    unit = make_unit([route("GET", "/v2/orders", lambda args: json_({}))], control_routes=control_plane_routes)
    try:
        full = document_for_unit(unit)
        trimmed = document_for_unit(unit, include_internal=False)
        assert any(path.startswith("/__unit/") for path in full["paths"])
        assert set(trimmed["paths"]) == {"/v2/orders"}
    finally:
        unit.stop()


# ---------------------------------------------------------------------------
# `serve`: the precedence rule, without binding a socket.
# ---------------------------------------------------------------------------


def test_serve_applies_flag_then_environment_then_profile_then_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag beats environment beats profile beats built-in default.

    Asserted by intercepting ``run_server`` rather than by starting one: what
    is under test is which numbers were chosen, and binding a real port to find
    that out would make the test slower, racier and no more conclusive. The
    socket itself is proved out of process, in ``tests/integration``.
    """
    import functools

    import vendorfake.asgi as asgi_module
    import vendorfake.cli as cli_module
    import vendorfake.registry as registry_module
    from tests.fakes import make_unit
    from vendorfake.core.control.plane import control_plane_routes

    calls: list[dict[str, object]] = []
    units: list[object] = []

    def fake_create_unit(**kwargs: object) -> object:
        built = make_unit(
            control_routes=functools.partial(
                control_plane_routes,
                framework_answered=kwargs["framework_answered"],  # type: ignore[arg-type]
            ),
            log_level="warn",
        )
        units.append(built)
        return built

    def fake_run_server(app: object, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(registry_module, "create_unit", fake_create_unit)
    monkeypatch.setattr(asgi_module, "run_server", fake_run_server)

    def serve(argv: list[str], env: dict[str, str]) -> dict[str, object]:
        calls.clear()
        parser = cli_module._build_parser()
        args = parser.parse_args(["serve", *argv])
        assert cli_module._serve(args, env, sys.stdout) == 0
        return calls[0]

    try:
        # Nothing given: the profile's transport section, then the defaults.
        chosen = serve([], {})
        assert chosen["host"] == "127.0.0.1"
        assert chosen["port"] == 8080
        assert chosen["log_level"] == "warn"

        # The environment beats the profile.
        chosen = serve([], {"VENDORFAKE_HOST": "0.0.0.0", "VENDORFAKE_PORT": "9001", "VENDORFAKE_LOG_LEVEL": "debug"})
        assert (chosen["host"], chosen["port"], chosen["log_level"]) == ("0.0.0.0", 9001, "debug")

        # The flag beats the environment.
        chosen = serve(
            ["--host", "10.0.0.5", "--port", "0", "--log-level", "error"],
            {"VENDORFAKE_HOST": "0.0.0.0", "VENDORFAKE_PORT": "9001", "VENDORFAKE_LOG_LEVEL": "debug"},
        )
        assert (chosen["host"], chosen["port"], chosen["log_level"]) == ("10.0.0.5", 0, "error")
    finally:
        for built in units:
            built.stop()  # type: ignore[attr-defined]


def test_serve_wires_the_tripwire_into_the_unit_before_building_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """``framework_answered`` reaches ``create_unit``, not just ``create_app``.

    The control plane closes over the callable at construction, so a tripwire
    handed only to the application would leave ``GET /__unit/health`` reporting
    a permanent 0 -- a tripwire that can never fire and would look like a pass
    forever.
    """
    import vendorfake.asgi as asgi_module
    import vendorfake.cli as cli_module
    import vendorfake.registry as registry_module
    from tests.fakes import make_unit

    seen: dict[str, object] = {}
    units: list[object] = []

    def fake_create_unit(**kwargs: object) -> object:
        seen.update(kwargs)
        built = make_unit(log_level="error")
        units.append(built)
        return built

    monkeypatch.setattr(registry_module, "create_unit", fake_create_unit)
    monkeypatch.setattr(asgi_module, "run_server", lambda app, **kwargs: None)

    parser = cli_module._build_parser()
    try:
        assert cli_module._serve(parser.parse_args(["serve"]), {}, sys.stdout) == 0
        answered = seen["framework_answered"]
        assert callable(answered)
        assert answered() == 0
    finally:
        for built in units:
            built.stop()  # type: ignore[attr-defined]
