"""The narrowing, asserted statically. Nothing here runs.

FOR: proving that ``unit("clover")`` gives a type checker a ``CloverSeed`` and
not a union, which is a claim only a type checker can settle. ``mypy`` is
pointed at this directory by ``pyproject.toml``; ``assert_type`` fails the run
if an inferred type drifts, so a regression in the overloads shows up as a red
type-check rather than as a consumer's ``isinstance`` coming back.

Not collected by pytest -- the file is not named ``test_*`` and this directory
is not a package -- because there is nothing here to execute. The one dynamic
half of the evidence, that the *negative* case really is rejected, is
``tests/typing/negative/`` driven from
``tests/unit/testing/test_seed_typing.py``.

``served`` is checked alongside ``unit`` because it carries the same overloads
for the same reason, and an overload set that is never exercised rots.
"""

from __future__ import annotations

from typing import assert_type

from vendorfake.testing import (
    CloverSeed,
    Credentials,
    Driver,
    Seed,
    ServedUnit,
    SquareSeed,
    StartedUnit,
    ToastSeed,
    Token,
    serve_in_thread,
    served,
    unit,
)


def _credentials_of(seed: Seed) -> Credentials:
    """The structural use the protocol exists for: no ``isinstance``, no
    vendor branch, and it has to accept all three seeds."""
    return seed.credentials


def _stored_row(seed: Seed) -> tuple[str, str | None, str]:
    """The other half (konyklabs/roadmap#101 item 16): a consumer's stored
    credential row, built from ``Seed`` alone with no ``Any`` escape."""
    token: Token = seed.token
    assert_type(token.refresh_token, str | None)
    return (token.access_token, token.refresh_token, token.tenant_id)


def square_narrows() -> None:
    with unit("square") as started:
        assert_type(started, StartedUnit[SquareSeed])
        assert_type(started.seed, SquareSeed)
        assert_type(started.seed.credentials.app_id, str)
        assert_type(started.seed.merchant_id, str)
        _credentials_of(started.seed)
        _stored_row(started.seed)


def clover_narrows() -> None:
    with unit("clover") as started:
        assert_type(started.seed, CloverSeed)
        assert_type(started.seed.credentials.app_id, str)
        assert_type(started.seed.path("/orders"), str)
        _credentials_of(started.seed)


def toast_narrows() -> None:
    with unit("toast") as started:
        assert_type(started.seed, ToastSeed)
        assert_type(started.seed.credentials.app_secret, str)
        assert_type(started.seed.restaurant_guid, str)
        _credentials_of(started.seed)


def a_vendor_that_is_not_a_literal_falls_back_to_the_protocol(vendor: str) -> None:
    """What a parametrized test gets. ``Seed`` is the honest answer: the
    fields every vendor has, and nothing that only two of them do.

    ``vendor`` is a parameter and not a local assigned a string literal: a
    checker that narrows on assignment (pyright does, mypy does not) would
    take a local straight back to ``Literal["square"]`` and pick the first
    overload, and the fallback would go untested under one of the two.
    """
    with unit(vendor) as started:
        assert_type(started, StartedUnit[Seed])
        assert_type(started.seed.credentials, Credentials)
        assert_type(started.seed.event_types, tuple[str, ...])
        _credentials_of(started.seed)


def a_thread_served_driver_keeps_the_seed_type() -> None:
    """``serve_in_thread`` is a second driver onto the same unit, so it
    carries the same seed and must not widen it back to a union."""
    with unit("toast") as started, serve_in_thread(started) as over_http:
        assert_type(over_http, Driver[ToastSeed])
        assert_type(over_http.seed.restaurant_header_name, str)


def a_child_process_narrows_too() -> None:
    with served("clover") as child:
        assert_type(child, ServedUnit[CloverSeed])
        assert_type(child.seed.merchant_id, str)
