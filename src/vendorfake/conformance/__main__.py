"""``python -m vendorfake.conformance`` -- run the contracts against a unit.

FOR: an exit code with no test framework in the picture. An external vendor may
not use pytest, and a container healthcheck wants a number rather than a
report. This is the framework-free façade over the same registry the pytest
layer renders; neither adds an assertion the other does not have.

WHY THE TARGET IS NAMED AND NEVER GUESSED. This package may not import a
vendor, and it may not import the registry that knows vendors exist -- that is
the layer rule ``tools/boundary_check.py`` enforces, and it is what keeps the
suite executable by a consumer whose vendor is not in this distribution. So
the target is a ``module:attribute`` the caller names, and a missing one is an
error that says so rather than a default that quietly tests the wrong thing.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from importlib import import_module

from vendorfake.conformance.registry import CHECKS
from vendorfake.conformance.report import format_report
from vendorfake.conformance.runner import run_conformance
from vendorfake.conformance.types import ConformanceTarget

__all__ = ["main", "resolve_target"]

TARGET_ENV_VAR = "VENDORFAKE_CONFORMANCE_TARGET"


def resolve_target(spec: str) -> ConformanceTarget:
    """``my_package.testing:target`` -> the target itself.

    A zero-argument callable is called, so a vendor may publish either a
    module-level target or a factory; anything else is an error naming what was
    found, because "your target is a string" is a better failure than an
    attribute error thrown from inside the runner.
    """
    module_name, _, attribute = spec.partition(":")
    found = getattr(import_module(module_name), attribute or "target")
    if isinstance(found, ConformanceTarget):
        return found
    if callable(found):
        built = found()
        if isinstance(built, ConformanceTarget):
            return built
    raise SystemExit(
        f"{spec} is {type(found).__name__}, not a ConformanceTarget or a callable returning one. "
        f"Publish a ConformanceTarget -- see vendorfake.conformance.types.ConformanceTarget."
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vendorfake.conformance")
    parser.add_argument(
        "--target",
        default=os.environ.get(TARGET_ENV_VAR),
        help=f"module:attribute publishing a ConformanceTarget (or set {TARGET_ENV_VAR})",
    )
    parser.add_argument("--profile", action="append", dest="profiles", help="repeatable; default is every profile")
    parser.add_argument("--transport", action="append", dest="transports", help="repeatable; default is the target's")
    parser.add_argument("--check", action="append", dest="check_ids", help="repeatable; default is every check")
    parser.add_argument("--strict", action="store_true", help="an undeclared skip is a failure")
    parser.add_argument("--list", action="store_true", help="print the registered contracts and exit")
    args = parser.parse_args(argv)

    if args.list:
        for spec in CHECKS:
            print(f"{spec.id}  {spec.name}\n      {spec.asserts}")
        return 0
    if not args.target:
        parser.error(f"--target is required (or set {TARGET_ENV_VAR}); this package never guesses a vendor")

    report = run_conformance(
        resolve_target(args.target),
        profiles=args.profiles,
        transports=args.transports,
        check_ids=args.check_ids,
        strict=args.strict,
    )
    print(format_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
