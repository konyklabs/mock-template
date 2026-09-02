"""``vendorfake-fidelity {pin,run,report}`` -- the fidelity tools, with no test runner in the picture.

FOR: an exit code. ``pin --check`` is what CI runs to know the extract still
matches the upstream document it was cut from; ``run`` is the corpus as a
list of pass/fail lines; ``report`` is the one matrix that joins the contract
leg to the behaviour leg, and it is the page a vendor's fidelity claim is
made on.

WHY THE TARGET IS NAMED AND NEVER GUESSED: the layer rule of
``tools/boundary_check.py``. This package may not import a vendor or the
registry, so every subcommand takes ``--target module:attribute`` (or reads
``VENDORFAKE_FIDELITY_TARGET``) and a missing one is an error that says so.

EXIT CODES. ``0`` clean; ``1`` a finding -- a changed pin under ``--check``, a
failed case, an UNDECLARED route; ``2`` a usage error -- no target, an
unresolvable one, an unknown case id, an unreachable base URL.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from collections.abc import Callable, Sequence
from importlib import resources
from pathlib import Path
from typing import Any

from vendorfake.fidelity.corpus import Case, load_corpus
from vendorfake.fidelity.report import format_cases, format_matrix
from vendorfake.fidelity.runner import (
    TARGET_ENV_VAR,
    ClientFactory,
    FidelityTarget,
    modeled_routes,
    resolve_target,
    run_corpus,
    run_corpus_remote,
    target_from_env,
)
from vendorfake.fidelity.types import FidelityDeclaration, Surface, load_declaration, load_extract

__all__ = ["RefreshFn", "main"]

RefreshFn = Callable[..., Any]
"""``refresh(anchor_dir, declaration, modeled, *, check, fetched)`` from
``vendorfake.fidelity.pin``; injectable so a test never touches the network."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vendorfake-fidelity")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_target(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--target",
            default=target_from_env(),
            metavar="MODULE:ATTR",
            help=f"module:attribute publishing a FidelityTarget (or set {TARGET_ENV_VAR})",
        )

    pin = sub.add_parser("pin", help="re-fetch the upstream document, re-cut the extract, and rewrite the pin")
    with_target(pin)
    pin.add_argument("--check", action="store_true", help="write nothing; exit 1 if upstream or the extract changed")
    pin.add_argument(
        "--offline",
        action="store_true",
        help="implies --check; no fetch -- only that the committed extract and pin agree with each other and the declaration",
    )

    run = sub.add_parser("run", help="run the corpus, one fresh unit per case")
    with_target(run)
    run.add_argument("--base-url", dest="base_url", metavar="URL", help="run against a unit already listening there")
    run.add_argument("--anchor", metavar="PACKAGE", help="with --base-url: the package holding the corpus")
    run.add_argument("--profile", help="run every case on this profile instead of the case's own")
    run.add_argument("--case", action="append", dest="case_ids", metavar="ID", help="repeatable; default is every case")
    run.add_argument(
        "--no-validate", dest="no_validate", action="store_true", help="plain client, no schema validation"
    )

    report = sub.add_parser("report", help="run the corpus with validation and print the route matrix")
    with_target(report)
    report.add_argument("--profile", help="run every case on this profile instead of the case's own")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    refresh: RefreshFn | None = None,
    client_factory: ClientFactory | None = None,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "run" and args.base_url:
        return _run_remote(parser, args)

    if not args.target:
        parser.error(f"--target is required (or set {TARGET_ENV_VAR}); this package never guesses a vendor")
    try:
        target = resolve_target(args.target)
    except (LookupError, ImportError, AttributeError) as exc:
        return _fail(f"cannot resolve --target {args.target}: {exc}")

    if args.command == "pin":
        if args.offline:
            return _pin_offline(target)
        return _pin(target, check=bool(args.check), refresh=refresh)
    try:
        cases = _select(load_corpus(target.anchor), args.case_ids if args.command == "run" else None)
    except (LookupError, ValueError) as exc:
        return _fail(str(exc))
    if args.command == "run":
        report = run_corpus(
            target,
            cases,
            profile_override=args.profile,
            validate=not args.no_validate,
            client_factory=client_factory,
        )
        print(format_cases(report))
        return 0 if report.ok else 1
    return _report(target, cases, profile_override=args.profile, client_factory=client_factory)


def _run_remote(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    anchor = args.anchor
    if not anchor:
        if not args.target:
            parser.error("--base-url needs the corpus: pass --anchor PACKAGE, or --target whose anchor is used")
        try:
            anchor = resolve_target(args.target).anchor
        except (LookupError, ImportError, AttributeError) as exc:
            return _fail(f"cannot resolve --target {args.target}: {exc}")
    if args.profile:
        parser.error("--profile cannot be combined with --base-url: the running unit reports its own profile")
    try:
        cases = _select(load_corpus(anchor), args.case_ids)
        report = run_corpus_remote(args.base_url, anchor, cases)
    except (LookupError, ValueError) as exc:
        return _fail(str(exc))
    print(format_cases(report))
    return 0 if report.ok else 1


def _select(cases: Sequence[Case], ids: Sequence[str] | None) -> tuple[Case, ...]:
    if not ids:
        return tuple(cases)
    by_id = {case.id: case for case in cases}
    unknown = [case_id for case_id in ids if case_id not in by_id]
    if unknown:
        raise LookupError(
            f"no such case(s): {', '.join(unknown)}; the corpus has: {', '.join(sorted(by_id)) or '(none)'}"
        )
    return tuple(by_id[case_id] for case_id in ids)


def _pin(target: FidelityTarget, *, check: bool, refresh: RefreshFn | None) -> int:
    if refresh is None:
        from vendorfake.fidelity.pin import refresh as _refresh

        refresh = _refresh
    declaration: FidelityDeclaration = load_declaration(target.anchor)
    with target.open_unit(target.default_profile) as unit:
        modeled = modeled_routes(unit.routes, declaration)
    anchor_dir = Path(str(resources.files(target.anchor)))
    result = refresh(
        anchor_dir,
        declaration,
        modeled,
        check=check,
        fetched=_dt.date.today().isoformat(),
    )
    changed = bool(result.changed_upstream or result.changed_extract)
    if result.diff_summary:
        print(result.diff_summary)
    if check:
        print("pin: up to date" if not changed else "pin: CHANGED -- run `vendorfake-fidelity pin` and review the diff")
        return 1 if changed else 0
    print("pin: written" if changed else "pin: unchanged")
    return 0


def _pin_offline(target: FidelityTarget) -> int:
    from vendorfake.fidelity.pin import verify

    declaration: FidelityDeclaration = load_declaration(target.anchor)
    result = verify(Path(str(resources.files(target.anchor))), declaration)
    print(result.diff_summary)
    print(
        "pin: consistent (offline)"
        if not result.changed
        else "pin: INCONSISTENT -- run `vendorfake-fidelity pin` and review the diff"
    )
    return 1 if result.changed else 0


def _report(
    target: FidelityTarget,
    cases: Sequence[Case],
    *,
    profile_override: str | None,
    client_factory: ClientFactory | None,
) -> int:
    corpus_report = run_corpus(
        target, cases, profile_override=profile_override, validate=True, client_factory=client_factory
    )
    surface = Surface(load_declaration(target.anchor), load_extract(target.anchor))
    with target.open_unit(target.default_profile) as unit:
        routes = unit.routes
    text = format_matrix(surface, routes, corpus_report.ledger, corpus_report)
    print(text)
    return 0 if text.rstrip().endswith("\nOK") or text.strip() == "OK" else 1


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
