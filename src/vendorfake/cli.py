"""``vendorfake`` -- the command line, and the one place that reads ``os.environ``.

FOR: turning a shell invocation into a running unit, a description of one, or a
report about one, without any other module in the distribution needing to know
that a process environment exists.

INVARIANT: **this is the only module that touches ``os.environ``.**
``create_unit``'s ``env`` parameter defaults to an empty mapping, deliberately,
so that a variable set by one test cannot change the profile a unit built by
another test resolves to -- a whole class of order-dependent flake that simply
cannot occur when the environment is passed in rather than read. The privilege
of reading the real environment belongs to the process boundary, and this is
it.

SECOND INVARIANT: **``vendorfake --help`` imports no web framework.** Every
first-party import below happens inside a subcommand body, and the ``serve``
subcommand is the only one that reaches :mod:`vendorfake.asgi`. That import is
the single named exception in ``tools/boundary.toml``, and it is named there
rather than waived because the point of the exception is that a reviewer sees
it. Module level here is standard library only.

Precedence, applied the same way by every subcommand: an explicit flag beats a
``VENDORFAKE_*`` environment variable, which beats what the profile document
says, which beats the built-in default. Flags win because a flag is the most
specific thing a caller can say, and the environment wins over the profile
because that is the layer an operator has when the profile is baked into an
image.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at run time
    from vendorfake.core.kernel.unit import Unit

__all__ = ["main"]

PROG = "vendorfake"


# ---------------------------------------------------------------------------
# Shared argument wiring.
# ---------------------------------------------------------------------------


def _add_unit_flags(parser: argparse.ArgumentParser) -> None:
    """The two flags every subcommand needs to name a unit.

    Both default to ``None`` rather than to a value, so that "not given" stays
    distinguishable from "given the default" all the way down to the profile
    loader, which is the only thing that knows what the environment and the
    profile document have to say about it.
    """
    parser.add_argument(
        "--vendor",
        default=None,
        help=(
            "Vendor to serve (see `vendorfake vendors`). Defaults to $VENDORFAKE_VENDOR; with exactly one "
            "vendor installed that one is used, otherwise the command refuses and lists them."
        ),
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Profile name or path. Defaults to $VENDORFAKE_PROFILE, then to the vendor's default profile.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Run or describe a high-fidelity fake of a third-party vendor API.",
        epilog=(
            "Unofficial. Not affiliated with, endorsed by, or connected to any vendor whose public API "
            "a module here imitates."
        ),
    )
    parser.add_argument("--version", action="store_true", help="Print the distribution version and exit.")
    subcommands = parser.add_subparsers(dest="command", metavar="COMMAND")

    serve = subcommands.add_parser("serve", help="Serve a unit over HTTP.")
    _add_unit_flags(serve)
    serve.add_argument("--host", default=None, help="Interface to bind. Defaults to $VENDORFAKE_HOST, then loopback.")
    serve.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind; 0 picks a free one and prints it. Defaults to $VENDORFAKE_PORT, then 8080.",
    )
    serve.add_argument(
        "--log-level",
        default=None,
        help="uvicorn log level. Defaults to $VENDORFAKE_LOG_LEVEL, then the profile's.",
    )

    info = subcommands.add_parser("info", help="Print what a unit would be, as JSON, without serving it.")
    _add_unit_flags(info)

    openapi = subcommands.add_parser("openapi", help="Print the OpenAPI 3.1 document for a unit's route table.")
    _add_unit_flags(openapi)
    openapi.add_argument(
        "--no-internal",
        action="store_true",
        help="Omit the /__unit/* control plane, describing only the vendor surface.",
    )

    subcommands.add_parser("vendors", help="List the vendors that would resolve here.")

    conformance = subcommands.add_parser("conformance", help="Run the conformance contracts against a unit.")
    conformance.add_argument("rest", nargs=argparse.REMAINDER, help="Arguments forwarded to the conformance runner.")

    return parser


# ---------------------------------------------------------------------------
# The environment layer. Read once, here, and passed down as plain data.
# ---------------------------------------------------------------------------


def _env_str(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    return value if value else None


def _env_int(env: Mapping[str, str], name: str) -> int | None:
    """An integer environment variable, or a refusal that says which one.

    Silently falling back to the default on ``VENDORFAKE_PORT=eighty`` would
    bind a port the operator did not ask for and report success.
    """
    raw = _env_str(env, name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"{PROG}: {name}={raw!r} is not an integer") from None


def _make_unit(
    args: argparse.Namespace,
    env: Mapping[str, str],
    *,
    framework_answered: Callable[[], int] | None = None,
) -> Unit:
    """Resolve the vendor and build the unit, turning a bad name into a refusal.

    ``resolve_vendor`` raises ``ValueError`` naming every available vendor,
    because it runs before a unit exists and therefore before there is a vendor
    to shape an error with. At this boundary that becomes a message and a
    non-zero exit, which is what a typo in a container's environment should
    look like -- not a server that starts and 404s everything.
    """
    from vendorfake.registry import create_unit

    try:
        return create_unit(
            vendor=args.vendor,
            profile=args.profile,
            env=env,
            framework_answered=framework_answered,
        )
    except ValueError as exc:
        raise SystemExit(f"{PROG}: {exc}") from None


# ---------------------------------------------------------------------------
# Subcommands.
# ---------------------------------------------------------------------------


def _serve(args: argparse.Namespace, env: Mapping[str, str], out: TextIO) -> int:
    """Build a unit, put the ASGI adapter in front of it, and listen.

    The import is inside the function body on purpose: it is the only reach
    into :mod:`vendorfake.asgi`, and therefore the only import of a web
    framework anywhere outside that package. ``vendorfake --help`` must not pay
    for it, and no other subcommand should be able to.

    The tripwire is created *before* the unit, because the unit's control plane
    closes over the callable that reads it -- that is what puts
    ``framework_answered`` in ``GET /__unit/health``, where a parent process can
    read it over HTTP.
    """
    from vendorfake.asgi import DEFAULT_HOST, DEFAULT_PORT, FrameworkTripwire, create_app, run_server

    tripwire = FrameworkTripwire()
    unit = _make_unit(args, env, framework_answered=tripwire.get)
    transport = unit.context.config.transport

    host = args.host or _env_str(env, "VENDORFAKE_HOST") or transport.host or DEFAULT_HOST
    port = args.port if args.port is not None else _env_int(env, "VENDORFAKE_PORT")
    if port is None:
        port = transport.port if transport.port else DEFAULT_PORT
    log_level = args.log_level or _env_str(env, "VENDORFAKE_LOG_LEVEL") or unit.context.config.log_level

    app = create_app(unit, tripwire=tripwire)

    def announce(bound_host: str, bound_port: int) -> None:
        # One line, flushed, before a single request can arrive. `--port 0` is
        # only usable if the number reaches the parent process while it is
        # still reading, rather than after the server begins answering.
        print(f"{PROG}: listening on http://{bound_host}:{bound_port} (vendor={unit.name})", file=out, flush=True)

    try:
        run_server(app, host=host, port=port, log_level=log_level, on_bound=announce)
    except KeyboardInterrupt:  # pragma: no cover - uvicorn normally absorbs this
        pass
    finally:
        unit.stop()
    return 0


def _info(args: argparse.Namespace, env: Mapping[str, str], out: TextIO) -> int:
    """Print ``GET /__unit/info`` without a server.

    The same bytes the control plane serves, produced by driving the unit
    through the in-process binding. Going through the binding rather than
    reaching into the unit is the point: what this prints is exactly what a
    consumer would read over HTTP, so the two can never drift.
    """
    from vendorfake.core.transport.inprocess import in_process

    unit = _make_unit(args, env)
    try:
        response = in_process(unit).get("/__unit/info")
        print(response.text, file=out)
        return 0 if response.status == 200 else 1
    finally:
        unit.stop()


def _openapi(args: argparse.Namespace, env: Mapping[str, str], out: TextIO) -> int:
    """Print the OpenAPI 3.1 document generated from the unit's route table.

    No server, and no web framework: the generator lives in the core precisely
    so this subcommand can exist. It is the same document the adapter serves at
    ``/__unit/openapi.json``, from the same function and the same route rows.
    """
    from vendorfake.core.control.openapi import document_for_unit
    from vendorfake.core.util.json import dump_json

    unit = _make_unit(args, env)
    try:
        document = document_for_unit(unit, include_internal=not args.no_internal)
        print(dump_json(document).decode("utf-8"), file=out)
        return 0
    finally:
        unit.stop()


def _vendors(out: TextIO) -> int:
    """List what would actually resolve, not what is declared.

    ``available_vendors`` filters through an importability check, so a name
    printed here is a name that will start. A list that advertised a vendor
    which then failed to import would be worse than no list.
    """
    from vendorfake.registry import available_vendors

    found = available_vendors()
    if not found:
        print(f"{PROG}: no vendors installed", file=sys.stderr)
        return 1
    for name in found:
        print(name, file=out)
    return 0


def _conformance(args: argparse.Namespace) -> int:
    """Hand off to the conformance runner, which owns its own arguments.

    Forwarded rather than re-declared: the suite is the specification, and a
    second copy of its flags here would be a second thing to keep in step with
    it. ``vendorfake-conformance`` is the same entry point under its own name.
    """
    from vendorfake.conformance.__main__ import main as conformance_main

    rest: list[str] = [arg for arg in args.rest if arg != "--"]
    return conformance_main(rest)


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Parse, dispatch, and return an exit code.

    ``os.environ`` is read here and nowhere else, and it is copied into a plain
    ``dict`` before it goes any further: everything downstream takes a
    ``Mapping[str, str]``, so nothing can mutate the process environment by
    accident and a test can substitute one without monkeypatching a global.
    """
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    env: Mapping[str, str] = dict(os.environ)
    out = sys.stdout

    if args.version:
        from vendorfake import __version__

        print(__version__, file=out)
        return 0

    if args.command is None:
        parser.print_help(out)
        return 2

    if args.command == "serve":
        return _serve(args, env, out)
    if args.command == "info":
        return _info(args, env, out)
    if args.command == "openapi":
        return _openapi(args, env, out)
    if args.command == "vendors":
        return _vendors(out)
    if args.command == "conformance":
        return _conformance(args)

    # argparse rejects an unknown subcommand before this is reachable; the
    # branch exists so that adding a parser without adding a dispatch arm is a
    # loud failure rather than a silent exit 0.
    raise SystemExit(f"{PROG}: no handler for subcommand {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
