"""``vendorfake`` command line entry point."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Placeholder until the transport adapter lands in phase 3."""
    del argv
    print("vendorfake: the server lands in phase 3", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
