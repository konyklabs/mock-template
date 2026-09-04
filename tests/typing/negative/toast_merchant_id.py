"""A module that MUST fail type checking. Nothing here runs, and mypy's
ordinary run does not look at it -- ``pyproject.toml`` excludes this directory.

FOR: proving the narrowing is load-bearing rather than merely present. That
``unit("toast")`` gives a ``ToastSeed`` is asserted next door in
``narrowing.py``; that it *rejects* a field belonging to another vendor is a
negative, and a negative cannot be asserted by a passing type check. So it is
asserted by running mypy on this file from a test and reading the error:
``tests/unit/testing/test_seed_typing.py``.

``merchant_id`` is the field to reach for: Square and Clover both have one and
Toast does not -- it scopes by restaurant guid -- so it is exactly the mistake
the union used to let through silently.
"""

from __future__ import annotations

from vendorfake.testing import unit


def toast_has_no_merchant_id() -> None:
    with unit("toast") as started:
        # The single expected error:
        #   error: "ToastSeed" has no attribute "merchant_id"  [attr-defined]
        # Bound to a typed local rather than returned, so the run reports that
        # one error and nothing about `Any` on top of it.
        tenant: str = started.seed.merchant_id
        del tenant
