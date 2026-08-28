"""A unit in a process of its own, for the contracts that are about processes.

FOR: making "the seed scenario is deterministic" a claim that can actually be
falsified. Two units built in one interpreter share everything an interpreter
has -- the pid, ``PYTHONHASHSEED``, every import-time counter, every module
global -- so a hydrate that drew an id from any of them would give both units
the *same* wrong answer and the comparison would be green. A determinism
contract is a statement about separate runs, and separate runs are separate
processes.

The parent starts this with ``--profile`` and, optionally, ``--mutant``, reads
one JSON line naming the port, and then talks to it over HTTP like any other
served unit. ``--mutant`` exists because the same argument applies to the
mutant that proves the contract can fail: a per-process defect served from a
thread in the parent would be invisible to the very check written to catch it.

Run as ``python -m tests.conformance.unit_child --profile full``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - only in the child
    sys.path.insert(0, str(REPO_ROOT))

from vendorfake.asgi import create_app, run_server  # noqa: E402
from vendorfake.core.kernel.unit import Unit  # noqa: E402


def _build(profile: str, mutant_id: str | None) -> Unit:
    if mutant_id is None:
        from tests.conformance.harness import build_unit

        return build_unit(profile)
    # Imported here so that an ordinary child never pays for the mutant
    # registry, and so that a mutant child builds the unit through exactly the
    # same factory an in-process mutant run uses.
    from tests.conformance.mutants import MUTANTS
    from tests.conformance.mutants.model import build_unit as build_mutant_unit

    mutant = next(entry for entry in MUTANTS if entry.id == mutant_id)
    return build_mutant_unit(mutant, profile)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve one unit for the out-of-process contracts.")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--mutant", default=None)
    args = parser.parse_args(argv)

    unit = _build(args.profile, args.mutant)
    app = create_app(unit)

    def announce(host: str, port: int) -> None:
        sys.stdout.write(json.dumps({"host": host, "port": port}) + "\n")
        sys.stdout.flush()

    try:
        run_server(app, host="127.0.0.1", port=0, log_level="error", on_bound=announce)
    finally:
        unit.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover - a child process entry point
    raise SystemExit(main())
