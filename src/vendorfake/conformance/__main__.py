"""``python -m vendorfake.conformance`` — run the contracts against a unit."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Placeholder until the check registry lands in phase 5."""
    del argv
    print("vendorfake conformance: the check registry lands in phase 5", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
