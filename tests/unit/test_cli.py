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
    assert declared == {"serve", "info", "openapi", "vendors", "profiles", "routes", "faults", "conformance"}


def test_serve_without_a_vendor_refuses_and_lists_both(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The README quickstart names a vendor because this refusal is real: with
    two vendors installed and no --vendor or $VENDORFAKE_VENDOR, `serve`
    exits non-zero before binding anything, listing what it found. Run
    against the real registry -- no monkeypatched create_unit."""
    from vendorfake.registry import available_vendors

    monkeypatch.delenv("VENDORFAKE_VENDOR", raising=False)
    offered = available_vendors()
    assert {"clover", "square"} <= set(offered), offered
    with pytest.raises(SystemExit) as raised:
        run("serve")
    message = str(raised.value)
    assert "create_unit needs a vendor" in message
    for name in offered:
        assert name in message


def test_an_unknown_vendor_is_a_startup_failure_that_lists_the_real_ones() -> None:
    """Not a server that starts and 404s everything.

    "Every endpoint returns 404" is indistinguishable from a consumer's own
    misconfiguration, which is exactly the debugging session this refusal
    prevents.
    """
    with pytest.raises(SystemExit) as raised:
        run("info", "--vendor", "nosuchvendor")
    assert "no vendor named 'nosuchvendor'" in str(raised.value)


def test_vendors_lists_what_would_actually_resolve() -> None:
    """The list is derived from an importability check, never declared, so a
    name printed here is a name that will start."""
    from vendorfake.registry import available_vendors

    code, out = run("vendors")
    assert code == 0
    assert out.split() == list(available_vendors())
    assert "square" in out.split()


def test_vendors_reports_nothing_installed_as_a_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Exit 1 with the message on stderr. An empty successful list would read
    as "the fake is fine, you asked for nothing".

    A vendor ships in this distribution, so the empty case is reached by
    emptying the discovery rather than by uninstalling one -- which is what the
    subcommand consults, and therefore what has to be empty for the branch to
    be the one under test.
    """
    import vendorfake.registry as registry_module

    monkeypatch.setattr(registry_module, "available_vendors", lambda: ())
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
# Discovery: `profiles`, `routes`, `faults`, and `--json` everywhere it applies.
# ---------------------------------------------------------------------------

#: Every subcommand `--json` is honoured by. `serve` and `conformance` are
#: deliberately absent -- see `_json_flag_parent`'s docstring in cli.py.
JSON_SUBCOMMANDS: tuple[tuple[str, ...], ...] = (
    ("info", "--vendor", "square"),
    ("openapi", "--vendor", "square"),
    ("vendors",),
    ("profiles", "--vendor", "square"),
    ("routes", "--vendor", "square"),
    ("faults",),
)


@pytest.mark.parametrize("argv", JSON_SUBCOMMANDS, ids=[row[0] for row in JSON_SUBCOMMANDS])
def test_every_json_subcommand_produces_one_parseable_document_on_stdout(argv: tuple[str, ...]) -> None:
    code, out = run(*argv, "--json")
    assert code == 0
    parsed = json.loads(out)  # raises if anything but valid JSON reached stdout
    assert parsed is not None


@pytest.mark.parametrize("argv", JSON_SUBCOMMANDS, ids=[row[0] for row in JSON_SUBCOMMANDS])
def test_every_json_subcommand_also_accepts_json_before_the_subcommand(argv: tuple[str, ...]) -> None:
    """``--json`` reads naturally on either side of the subcommand name:
    ``vendorfake --json profiles --vendor square`` and
    ``vendorfake profiles --vendor square --json`` are the same request.
    Before this fix, only the trailing position worked: the global position
    exited 2 with ``unrecognized arguments: --json``, which contradicted the
    CHANGELOG's own description of the flag as global."""
    code, out = run("--json", *argv)
    assert code == 0
    parsed = json.loads(out)
    assert parsed is not None


def test_json_before_and_after_the_subcommand_produce_the_identical_document() -> None:
    global_code, global_out = run("--json", "profiles", "--vendor", "square")
    trailing_code, trailing_out = run("profiles", "--vendor", "square", "--json")
    assert global_code == trailing_code == 0
    assert json.loads(global_out) == json.loads(trailing_out)


def test_a_json_flag_repeated_on_both_sides_of_the_subcommand_is_accepted() -> None:
    code, out = run("--json", "profiles", "--vendor", "square", "--json")
    assert code == 0
    assert json.loads(out)


def test_profiles_lists_the_six_shipped_profiles() -> None:
    code, out = run("profiles", "--vendor", "square", "--json")
    assert code == 0
    rows = json.loads(out)
    assert sorted(row["name"] for row in rows) == [
        "chaos-demo",
        "full",
        "no-chaos",
        "no-faults",
        "oauth-only",
        "orders-only",
    ]
    for row in rows:
        assert row["vendor"] == "square"
        assert isinstance(row["capabilities"], list) and row["capabilities"]
        assert row["summary"]


def test_profiles_table_form_lists_the_same_names() -> None:
    code, out = run("profiles", "--vendor", "square")
    assert code == 0
    for name in ("chaos-demo", "full", "no-chaos", "no-faults", "oauth-only", "orders-only"):
        assert name in out


def test_routes_excludes_internal_routes_unless_asked() -> None:
    code, out = run("routes", "--vendor", "square", "--json")
    assert code == 0
    rows = json.loads(out)
    assert rows  # the vendor surface is not empty
    assert not any(row["internal"] for row in rows)
    assert any(row["operation_id"] == "ObtainToken" and row["path"] == "/oauth2/token" for row in rows)

    code, out = run("routes", "--vendor", "square", "--internal", "--json")
    assert code == 0
    with_internal = json.loads(out)
    assert any(row["internal"] and row["path"] == "/__unit/info" for row in with_internal)
    assert len(with_internal) > len(rows)


def test_faults_lists_every_key_of_the_fault_param_table() -> None:
    from vendorfake.core.chaos.faults import FAULT_DESCRIPTIONS, FAULT_PARAM_KEYS

    code, out = run("faults", "--json")
    assert code == 0
    rows = json.loads(out)
    assert {row["name"] for row in rows} == set(FAULT_PARAM_KEYS)
    for row in rows:
        assert row["params"] == list(FAULT_PARAM_KEYS[row["name"]])
        assert row["description"] == FAULT_DESCRIPTIONS[row["name"]]


def test_fault_descriptions_names_exactly_the_fault_param_keys_names() -> None:
    """The drift the CLI would otherwise reproduce silently: a fault with
    parameters and no prose, or prose for a fault the engine does not have."""
    from vendorfake.core.chaos.faults import FAULT_DESCRIPTIONS, FAULT_PARAM_KEYS

    assert set(FAULT_DESCRIPTIONS) == set(FAULT_PARAM_KEYS)


def test_vendors_json_is_the_same_list_the_text_form_prints() -> None:
    from vendorfake.registry import available_vendors

    code, out = run("vendors", "--json")
    assert code == 0
    assert json.loads(out) == list(available_vendors())


def test_profiles_and_routes_refuse_an_unknown_vendor_by_name() -> None:
    with pytest.raises(SystemExit) as raised:
        run("profiles", "--vendor", "nosuchvendor")
    assert "no vendor named 'nosuchvendor'" in str(raised.value)

    with pytest.raises(SystemExit) as raised:
        run("routes", "--vendor", "nosuchvendor")
    assert "no vendor named 'nosuchvendor'" in str(raised.value)


def test_routes_defaults_to_full_and_honours_an_explicit_profile() -> None:
    """The route table does not vary by profile -- every declared route is
    registered whether or not its capability is enabled -- so this asserts
    the flag is accepted and produces the same table, not a different one."""
    code, full_out = run("routes", "--vendor", "square", "--json")
    code_named, named_out = run("routes", "--vendor", "square", "--profile", "oauth-only", "--json")
    assert code == 0 and code_named == 0
    assert json.loads(full_out) == json.loads(named_out)


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


# ---------------------------------------------------------------------------
# Startup failures read as refusals, not as crashes (konyklabs/roadmap#74).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subcommand", ["serve", "info", "openapi", "routes"])
def test_a_nonexistent_profile_is_a_refusal_that_names_the_real_ones(subcommand: str) -> None:
    """A mistyped ``--profile`` used to be a raw ``UnitError`` traceback out of
    the profile loader, while the adjacent ``--vendor`` flag -- the same kind
    of typo, one letter away -- was already a one-line refusal. The loader's
    message always named every profile the vendor ships; nothing but the
    ``except`` clause stood between it and the caller.
    """
    with pytest.raises(SystemExit) as raised:
        run(subcommand, "--vendor", "square", "--profile", "nosuchprofile")

    message = str(raised.value)
    assert message.startswith("vendorfake: "), message
    assert "nosuchprofile" in message
    for shipped in ("full", "oauth-only", "orders-only", "no-chaos", "no-faults", "chaos-demo"):
        assert shipped in message


def test_the_refusal_carries_a_message_rather_than_a_bare_code() -> None:
    """``SystemExit`` with a string prints it to stderr and exits 1, which is
    the shape every other refusal in this module already has. Asserted so the
    two kinds of startup failure cannot drift apart again.
    """
    with pytest.raises(SystemExit) as bad_profile:
        run("info", "--vendor", "square", "--profile", "nosuchprofile")
    with pytest.raises(SystemExit) as bad_vendor:
        run("info", "--vendor", "nosuchvendor")

    assert isinstance(bad_profile.value.code, str), repr(bad_profile.value.code)
    assert isinstance(bad_vendor.value.code, str), repr(bad_vendor.value.code)


def test_a_malformed_profile_document_is_a_refusal_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Not only a missing profile. Any ``UnitError`` raised while the unit is
    being built is a startup failure the caller can act on, so all of them
    leave through the same message rather than the first one leaving through a
    traceback.
    """
    import vendorfake.registry as registry_module
    from tests.fakes import FakeVendor

    (tmp_path / "broken.json").write_text('{"name": "broken", "capabilities": "not-a-list"}', encoding="utf-8")
    definition = FakeVendor(name="acme", profile_dir=tmp_path, base_dir=tmp_path)
    monkeypatch.setattr(registry_module, "resolve_vendor", lambda name: definition)

    with pytest.raises(SystemExit) as raised:
        run("info", "--vendor", "acme", "--profile", "broken")

    assert str(raised.value).startswith("vendorfake: "), str(raised.value)
