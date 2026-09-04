"""``python -m vendorfake.conformance`` -- run the contracts against a unit, and return an exit code with no test framework in the picture.

The target is always named, never guessed: this package may not import a vendor or the registry that knows vendors exist, so ``--target module:attribute`` (or ``--base-url``) is required, and a missing one is an error rather than a default that quietly tests the wrong thing.

``--target module:attribute`` is the matrix run: a fresh unit per contract, so the whole profile list can be swept and "every contract passed somewhere" is meaningful. ``--base-url http://host:port`` is the container run: the unit is already running and reached over a socket, single-profile by construction, so the aggregate rule is switched off and :data:`~vendorfake.conformance.runner.REMOTE_CAVEAT` is printed. This package never starts a server itself.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from vendorfake.conformance.registry import CHECKS
from vendorfake.conformance.report import format_report
from vendorfake.conformance.runner import (
    REMOTE_CAVEAT,
    TARGET_ENV_VAR,
    remote_target,
    resolve_target,
    run_conformance,
)
from vendorfake.conformance.types import ConformanceTarget

__all__ = ["main"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m vendorfake.conformance")
    parser.add_argument(
        "--target",
        default=os.environ.get(TARGET_ENV_VAR),
        help=f"module:attribute publishing a ConformanceTarget (or set {TARGET_ENV_VAR})",
    )
    parser.add_argument(
        "--base-url",
        dest="base_url",
        metavar="URL",
        help="run against a unit already listening there; discovers its profile and overrides --target",
    )
    parser.add_argument("--profile", action="append", dest="profiles", help="repeatable; default is every profile")
    parser.add_argument("--transport", action="append", dest="transports", help="repeatable; default is the target's")
    parser.add_argument("--check", action="append", dest="check_ids", help="repeatable; default is every check")
    parser.add_argument("--strict", action="store_true", help="an undeclared skip is a failure")
    parser.add_argument("--list", action="store_true", help="print the registered contracts and exit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if args.list:
        for spec in CHECKS:
            print(f"{spec.id}  {spec.name}\n      {spec.asserts}")
        return 0

    target: ConformanceTarget
    cross_profile: bool | None = None
    if args.base_url:
        if args.profiles:
            # The container knows which profile it loaded; a disagreeing flag would only mislabel the run.
            parser.error("--profile cannot be combined with --base-url: the running unit reports its own profile")
        try:
            target = remote_target(args.base_url)
        except LookupError as exc:
            return _fail(str(exc))
        cross_profile = False
        print(f"note: {REMOTE_CAVEAT}\n")
    elif args.target:
        try:
            target = resolve_target(args.target)
        except LookupError as exc:
            return _fail(str(exc))
    else:
        parser.error(
            f"one of --target or --base-url is required (or set {TARGET_ENV_VAR}); this package never guesses a vendor"
        )

    report = run_conformance(
        target,
        profiles=args.profiles,
        transports=args.transports,
        check_ids=args.check_ids,
        strict=args.strict,
        cross_profile=cross_profile,
    )
    print(format_report(report))
    return 0 if report.ok else 1


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
