"""``vendorfake`` -- the command line.

Invariant: the process environment reaches a unit through one function,
``registry.ambient_env()``, which this module, ``unit()`` and ``served()`` all
layer explicit configuration over; ``create_unit``'s ``env`` itself defaults to
an empty mapping.

Invariant: ``vendorfake --help`` imports no web framework. Every first-party
import happens inside a subcommand body, and ``serve`` is the only one that
reaches :mod:`vendorfake.asgi` -- the single named exception in
``tools/boundary.toml``. Module level here is standard library only.

Precedence, in every subcommand: an explicit flag beats a ``VENDORFAKE_*``
variable, which beats the profile document, which beats the built-in default.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at run time
    from vendorfake.core.kernel.unit import Unit

__all__ = ["main"]

PROG = "vendorfake"


# -- shared argument wiring --


def _add_unit_flags(parser: argparse.ArgumentParser) -> None:
    """The two flags every subcommand needs to name a unit. Both default to
    ``None``, so "not given" stays distinguishable from "given the default" all
    the way down to the profile loader."""
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


def _json_flag_parent() -> argparse.ArgumentParser:
    """A parent parser carrying only ``--json``, added to the top-level parser and
    to every describing subcommand, so ``vendorfake --json profiles`` and
    ``vendorfake profiles --json`` are the same request.

    The default is ``argparse.SUPPRESS`` at both parsers, and every reader uses
    ``getattr(args, "json", False)``: subparser dispatch copies a subcommand's
    defaults onto the top-level namespace, so a plain ``False`` would stomp a
    ``--json`` given before the subcommand name. ``serve`` and ``conformance``
    do not carry the flag, having no single document to print.
    """
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Machine output: one JSON document on stdout, nothing else on stdout.",
    )
    return parent


def _wants_json(args: argparse.Namespace) -> bool:
    """``args.json``, true only where a parser declared the flag and it was given."""
    return bool(getattr(args, "json", False))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Run or describe a high-fidelity fake of a third-party vendor API.",
        epilog=(
            "Unofficial. Not affiliated with, endorsed by, or connected to any vendor whose public API "
            "a module here imitates."
        ),
        parents=[_json_flag_parent()],  # `vendorfake --json profiles ...` -- see _json_flag_parent's docstring
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

    # already the only thing each of these prints; --json accepted so a caller need not special-case it
    info = subcommands.add_parser(
        "info", help="Print what a unit would be, as JSON, without serving it.", parents=[_json_flag_parent()]
    )
    _add_unit_flags(info)

    openapi = subcommands.add_parser(
        "openapi", help="Print the OpenAPI 3.1 document for a unit's route table.", parents=[_json_flag_parent()]
    )
    _add_unit_flags(openapi)
    openapi.add_argument(
        "--no-internal",
        action="store_true",
        help="Omit the /__unit/* control plane, describing only the vendor surface.",
    )

    manifest = subcommands.add_parser(
        "manifest",
        help="Print the world-neutral manifest: credentials, webhook keys and entity ids.",
        parents=[_json_flag_parent()],
    )
    _add_unit_flags(manifest)
    manifest.add_argument(
        "--base-url",
        default=None,
        help="The address the unit will be reached at, recorded in the document. Omitted, base_url is null.",
    )

    subcommands.add_parser("vendors", help="List the vendors that would resolve here.", parents=[_json_flag_parent()])

    profiles = subcommands.add_parser(
        "profiles", help="List the profiles a vendor ships.", parents=[_json_flag_parent()]
    )
    profiles.add_argument(
        "--vendor",
        default=None,
        help="Vendor to describe. Defaults to $VENDORFAKE_VENDOR; with exactly one vendor installed that "
        "one is used, otherwise the command refuses and lists them.",
    )

    routes = subcommands.add_parser("routes", help="List a vendor's route table.", parents=[_json_flag_parent()])
    routes.add_argument(
        "--vendor",
        default=None,
        help="Vendor to describe. Defaults to $VENDORFAKE_VENDOR; with exactly one vendor installed that "
        "one is used, otherwise the command refuses and lists them.",
    )
    routes.add_argument(
        "--profile",
        default=None,
        help="Profile to build the table against. The table itself does not vary by profile; see the "
        "docstring of vendorfake.registry.routes. Defaults to $VENDORFAKE_PROFILE, then 'full'.",
    )
    routes.add_argument(
        "--internal",
        action="store_true",
        help="Include the /__unit/* control plane. Omitted by default: this is the vendor surface.",
    )

    subcommands.add_parser("faults", help="List the built-in fault catalogue.", parents=[_json_flag_parent()])

    explain = subcommands.add_parser(
        "explain",
        help="Explain one route, fault, profile, error kind, or Vendorfake-* header.",
        parents=[_json_flag_parent()],
    )
    explain_kinds = explain.add_subparsers(dest="explain_kind", metavar="KIND")

    explain_route = explain_kinds.add_parser("route", help="One route, by operation_id.", parents=[_json_flag_parent()])
    explain_route.add_argument("target", metavar="OPERATION_ID")
    explain_route.add_argument(
        "--vendor",
        default=None,
        help="Vendor to describe. Defaults to $VENDORFAKE_VENDOR; with exactly one vendor installed that "
        "one is used, otherwise the command refuses and lists them.",
    )
    explain_route.add_argument(
        "--profile",
        default=None,
        help="Profile to build the table against. Defaults to $VENDORFAKE_PROFILE, then 'full'.",
    )

    explain_fault = explain_kinds.add_parser("fault", help="One fault, by name.", parents=[_json_flag_parent()])
    explain_fault.add_argument("target", metavar="NAME")

    explain_profile = explain_kinds.add_parser("profile", help="One profile, by name.", parents=[_json_flag_parent()])
    explain_profile.add_argument("target", metavar="NAME")
    explain_profile.add_argument(
        "--vendor",
        default=None,
        help="Vendor to describe. Defaults to $VENDORFAKE_VENDOR; with exactly one vendor installed that "
        "one is used, otherwise the command refuses and lists them.",
    )

    explain_error = explain_kinds.add_parser(
        "error", help="One core error kind, by name.", parents=[_json_flag_parent()]
    )
    explain_error.add_argument("target", metavar="KIND")
    explain_error.add_argument(
        "--vendor",
        default=None,
        help="Vendor to describe. Defaults to $VENDORFAKE_VENDOR; with exactly one vendor installed that "
        "one is used, otherwise the command refuses and lists them.",
    )
    explain_error.add_argument(
        "--profile",
        default=None,
        help="Profile to build the unit against. Defaults to $VENDORFAKE_PROFILE, then 'full'.",
    )

    explain_header = explain_kinds.add_parser(
        "header", help="One Vendorfake-* header, by name.", parents=[_json_flag_parent()]
    )
    explain_header.add_argument("target", metavar="NAME")

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
    """An integer environment variable, or a refusal naming it: falling back on
    ``VENDORFAKE_PORT=eighty`` would bind a port nobody asked for."""
    raw = _env_str(env, name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"{PROG}: {name}={raw!r} is not an integer") from None


def _resolve_vendor_name(args: argparse.Namespace, env: Mapping[str, str]) -> str:
    """``--vendor``, then ``$VENDORFAKE_VENDOR``, then the sole installed vendor:
    ``create_unit``'s own precedence, for a subcommand that builds no unit."""
    from vendorfake.registry import VENDOR_ENV_VAR, available_vendors

    name = args.vendor or _env_str(env, VENDOR_ENV_VAR)
    if name:
        return name
    offered = available_vendors()
    if len(offered) == 1:
        return offered[0]
    listing = ", ".join(offered) if offered else "(none installed)"
    raise SystemExit(f"{PROG}: needs a vendor: pass --vendor, or set {VENDOR_ENV_VAR}. Available: {listing}")


def _table(rows: Sequence[Mapping[str, object]], columns: Sequence[str]) -> str:
    """A minimal aligned table. No dependency earns its place for four columns."""
    widths = [len(col) for col in columns]
    for row in rows:
        for index, col in enumerate(columns):
            widths[index] = max(widths[index], len(str(row.get(col, ""))))

    def line(values: Sequence[object]) -> str:
        return "  ".join(str(value).ljust(width) for value, width in zip(values, widths, strict=True))

    lines = [line(columns), line(["-" * width for width in widths])]
    lines.extend(line([row.get(col, "") for col in columns]) for row in rows)
    return "\n".join(lines)


def _make_unit(args: argparse.Namespace, env: Mapping[str, str]) -> Unit:
    """Resolve the vendor and build the unit, turning a bad name into a refusal.

    ``ValueError`` from ``resolve_vendor`` and any ``UnitError`` raised while the
    unit is built both become a message and a non-zero exit, so a typo in a
    container's environment reads the same whichever flag carried it, rather than
    a traceback or a server that starts and 404s everything.
    """
    from vendorfake.core.kernel.types import UnitError
    from vendorfake.registry import create_unit

    try:
        return create_unit(
            vendor=args.vendor,
            profile=args.profile,
            env=env,
        )
    except (ValueError, UnitError) as exc:
        raise SystemExit(f"{PROG}: {exc}") from None


# ---------------------------------------------------------------------------
# Subcommands.
# ---------------------------------------------------------------------------


def _serve(args: argparse.Namespace, env: Mapping[str, str], out: TextIO) -> int:
    """Build a unit, put the ASGI adapter in front of it, and listen. The import is
    inside the body because it is the only reach into :mod:`vendorfake.asgi`, and
    ``vendorfake --help`` must not pay for it."""
    from vendorfake.asgi import DEFAULT_HOST, DEFAULT_PORT, create_app, run_server

    unit = _make_unit(args, env)
    transport = unit.context.config.transport

    host = args.host or _env_str(env, "VENDORFAKE_HOST") or transport.host or DEFAULT_HOST
    port = args.port if args.port is not None else _env_int(env, "VENDORFAKE_PORT")
    if port is None:
        port = transport.port if transport.port else DEFAULT_PORT
    log_level = args.log_level or _env_str(env, "VENDORFAKE_LOG_LEVEL") or unit.context.config.log_level

    app = create_app(unit)

    def announce(bound_host: str, bound_port: int) -> None:
        # One line, flushed, before a single request can arrive, so `--port 0`
        # reaches the parent while it is still reading.
        print(f"{PROG}: listening on http://{bound_host}:{bound_port} (vendor={unit.name})", file=out, flush=True)

    try:
        run_server(app, host=host, port=port, log_level=log_level, on_bound=announce)
    except KeyboardInterrupt:  # pragma: no cover - uvicorn normally absorbs this
        pass
    finally:
        unit.stop()
    return 0


def _info(args: argparse.Namespace, env: Mapping[str, str], out: TextIO) -> int:
    """Print ``GET /__unit/info`` without a server: the same bytes the control
    plane serves, produced through the in-process binding so the two cannot
    drift."""
    from vendorfake.core.transport.inprocess import in_process

    unit = _make_unit(args, env)
    try:
        response = in_process(unit).get("/__unit/info")
        print(response.text, file=out)
        return 0 if response.status == 200 else 1
    finally:
        unit.stop()


def _manifest(args: argparse.Namespace, env: Mapping[str, str], out: TextIO) -> int:
    """Print the document ``GET /__unit/manifest`` serves, without a server.

    The same function builds both, so the two cannot drift. ``--json`` is
    implicit -- the output *is* the document, and nothing else goes to stdout --
    and ``--base-url`` is the one thing a process with no request cannot infer:
    a unit reached over a container port mapping does not know its own address.
    """
    from vendorfake.core.control.plane import manifest_document
    from vendorfake.core.util.json import dump_json

    unit = _make_unit(args, env)
    try:
        document = manifest_document(unit.context, base_url=args.base_url)
        print(dump_json(document).decode("utf-8"), file=out)
        return 0
    finally:
        unit.stop()


def _openapi(args: argparse.Namespace, env: Mapping[str, str], out: TextIO) -> int:
    """Print the OpenAPI 3.1 document generated from the unit's route table. No
    server and no web framework: the generator lives in the core, and this is the
    same document the adapter serves at ``/__unit/openapi.json``."""
    from vendorfake.core.control.openapi import document_for_unit
    from vendorfake.core.util.json import dump_json

    unit = _make_unit(args, env)
    try:
        document = document_for_unit(unit, include_internal=not args.no_internal)
        print(dump_json(document).decode("utf-8"), file=out)
        return 0
    finally:
        unit.stop()


def _vendors(args: argparse.Namespace, out: TextIO) -> int:
    """List what would actually resolve, not what is declared: ``available_vendors``
    filters through an importability check, so a name printed here will start."""
    from vendorfake.core.util.json import dump_json
    from vendorfake.registry import available_vendors

    found = available_vendors()
    if not found:
        print(f"{PROG}: no vendors installed", file=sys.stderr)
        return 1
    if _wants_json(args):
        print(dump_json(list(found)).decode("utf-8"), file=out)
        return 0
    for name in found:
        print(name, file=out)
    return 0


def _profiles(args: argparse.Namespace, env: Mapping[str, str], out: TextIO) -> int:
    """``vendorfake.registry.available_profiles`` over the command line.
    ``UnitError`` is caught alongside ``ValueError`` for the reason
    :func:`_make_unit` gives: reading profiles can fail on a document rather than
    on the vendor's name."""
    from vendorfake.core.kernel.types import UnitError
    from vendorfake.core.util.json import dump_json
    from vendorfake.registry import available_profiles

    name = _resolve_vendor_name(args, env)
    try:
        found = available_profiles(name)
    except (ValueError, UnitError) as exc:
        raise SystemExit(f"{PROG}: {exc}") from None
    if _wants_json(args):
        payload = [
            {
                "vendor": row.vendor,
                "name": row.name,
                "summary": row.summary,
                "capabilities": list(row.capabilities),
                "seed": row.seed,
            }
            for row in found
        ]
        print(dump_json(payload).decode("utf-8"), file=out)
        return 0
    print(
        _table(
            [{"name": row.name, "capabilities": ", ".join(row.capabilities), "summary": row.summary} for row in found],
            ("name", "capabilities", "summary"),
        ),
        file=out,
    )
    return 0


def _routes_cmd(args: argparse.Namespace, env: Mapping[str, str], out: TextIO) -> int:
    """``vendorfake.registry.routes`` over the command line; internal routes are
    omitted unless ``--internal`` is given. ``UnitError`` is caught alongside
    ``ValueError`` for the reason :func:`_make_unit` gives."""
    from vendorfake.core.kernel.types import UnitError
    from vendorfake.core.util.json import dump_json
    from vendorfake.registry import routes as list_routes

    name = _resolve_vendor_name(args, env)
    profile = args.profile or _env_str(env, "VENDORFAKE_PROFILE") or "full"
    try:
        found = list_routes(name, profile)
    except (ValueError, UnitError) as exc:
        raise SystemExit(f"{PROG}: {exc}") from None
    if not args.internal:
        found = tuple(row for row in found if not row.internal)
    if _wants_json(args):
        payload = [
            {
                "method": row.method,
                "path": row.path,
                "operation_id": row.operation_id,
                "capability": row.capability,
                "summary": row.summary,
                "internal": row.internal,
            }
            for row in found
        ]
        print(dump_json(payload).decode("utf-8"), file=out)
        return 0
    print(
        _table(
            [
                {
                    "method": row.method,
                    "path": row.path,
                    "operation_id": row.operation_id or "",
                    "capability": row.capability,
                }
                for row in found
            ],
            ("method", "path", "operation_id", "capability"),
        ),
        file=out,
    )
    return 0


def _faults(args: argparse.Namespace, out: TextIO) -> int:
    """The built-in fault catalogue: name, provenance, phase, parameters and a
    one-line description, read from the same mappings ``GET /__unit/chaos``
    publishes each rule against, so the two cannot disagree."""
    from vendorfake.core.chaos.faults import FAULT_DESCRIPTIONS, FAULT_PARAM_KEYS, FAULT_PHASE, FAULT_PROVENANCE
    from vendorfake.core.util.json import dump_json

    names = sorted(FAULT_PARAM_KEYS)
    if _wants_json(args):
        payload = [
            {
                "name": name,
                "provenance": FAULT_PROVENANCE[name],
                "phase": FAULT_PHASE[name],
                "params": list(FAULT_PARAM_KEYS[name]),
                "description": FAULT_DESCRIPTIONS[name],
            }
            for name in names
        ]
        print(dump_json(payload).decode("utf-8"), file=out)
        return 0
    print(
        _table(
            [
                {
                    "name": name,
                    "provenance": FAULT_PROVENANCE[name],
                    "phase": FAULT_PHASE[name],
                    "params": ", ".join(FAULT_PARAM_KEYS[name]),
                    "description": FAULT_DESCRIPTIONS[name],
                }
                for name in names
            ],
            ("name", "provenance", "phase", "params", "description"),
        ),
        file=out,
    )
    return 0


def _explain(args: argparse.Namespace, env: Mapping[str, str], out: TextIO) -> int:
    """Look up one route, fault, profile, error kind or header, and print it. Every
    lookup lives in :mod:`vendorfake.agent.explain`; this picks which one, resolves
    the vendor and profile flags, and chooses text or ``--json``. ``UnitError`` is
    caught alongside ``ValueError`` for the reason :func:`_make_unit` gives."""
    from vendorfake.agent import explain as explainer
    from vendorfake.core.kernel.types import UnitError
    from vendorfake.core.util.json import dump_json

    kind = args.explain_kind
    if kind is None:
        raise SystemExit(f"{PROG}: explain needs a kind: route, fault, profile, error, or header")

    try:
        if kind == "route":
            vendor = _resolve_vendor_name(args, env)
            profile = args.profile or _env_str(env, "VENDORFAKE_PROFILE") or "full"
            data = explainer.explain_route(vendor, profile, args.target)
            text = explainer.render_route(data)
        elif kind == "fault":
            data = explainer.explain_fault(args.target)
            text = explainer.render_fault(data)
        elif kind == "profile":
            vendor = _resolve_vendor_name(args, env)
            data = explainer.explain_profile(vendor, args.target)
            text = explainer.render_profile(data)
        elif kind == "error":
            vendor = _resolve_vendor_name(args, env)
            profile = args.profile or _env_str(env, "VENDORFAKE_PROFILE") or "full"
            data = explainer.explain_error(vendor, profile, args.target)
            text = explainer.render_error(data)
        elif kind == "header":
            data = explainer.explain_header(args.target)
            text = explainer.render_header(data)
        else:  # pragma: no cover - argparse restricts explain_kind to the five arms above
            raise SystemExit(f"{PROG}: no handler for explain kind {kind!r}")
    except (ValueError, UnitError) as exc:
        raise SystemExit(f"{PROG}: {exc}") from None

    if _wants_json(args):
        print(dump_json(data).decode("utf-8"), file=out)
    else:
        print(text, file=out)
    return 0


def _conformance(args: argparse.Namespace) -> int:
    """Hand off to the conformance runner, which owns its own arguments. Forwarded
    rather than re-declared, a second copy of its flags being a second thing to
    keep in step; ``vendorfake-conformance`` is the same entry point."""
    from vendorfake.conformance.__main__ import main as conformance_main

    rest: list[str] = [arg for arg in args.rest if arg != "--"]
    return conformance_main(rest)


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Parse, dispatch, and return an exit code. The environment is copied into a
    plain ``dict`` so nothing downstream can mutate it and a test can substitute one."""
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
    if args.command == "manifest":
        return _manifest(args, env, out)
    if args.command == "openapi":
        return _openapi(args, env, out)
    if args.command == "vendors":
        return _vendors(args, out)
    if args.command == "profiles":
        return _profiles(args, env, out)
    if args.command == "routes":
        return _routes_cmd(args, env, out)
    if args.command == "faults":
        return _faults(args, out)
    if args.command == "explain":
        return _explain(args, env, out)
    if args.command == "conformance":
        return _conformance(args)

    # Unreachable via argparse; a parser added without a dispatch arm fails loud.
    raise SystemExit(f"{PROG}: no handler for subcommand {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
