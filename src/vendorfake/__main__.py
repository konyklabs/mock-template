"""``python -m vendorfake`` -- the same entry point as the ``vendorfake`` script.

FOR: making the command reachable without the console script on ``PATH``, which
is the normal case inside a container, in a CI step that has only ``uv run``,
and in an editable checkout.

INVARIANT: it adds nothing. Both routes reach exactly the same
:func:`vendorfake.cli.main`, so behaviour cannot differ between "the installed
command" and "the module", which is a difference nobody would think to test.
"""

from __future__ import annotations

from vendorfake.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
