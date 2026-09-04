"""``python -m vendorfake`` -- the same entry point as the ``vendorfake`` script,
reaching the same :func:`vendorfake.cli.main` and adding nothing."""

from __future__ import annotations

from vendorfake.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
