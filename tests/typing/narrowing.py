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

from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import assert_type

from vendorfake.testing import (
    CloverSeed,
    CloverSeedOverlay,
    Credentials,
    Driver,
    LightspeedSeed,
    LightspeedSeedOverlay,
    Seed,
    ServedUnit,
    SquareSeed,
    SquareSeedOverlay,
    StartedUnit,
    ToastSeed,
    ToastSeedOverlay,
    Token,
    async_unit,
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


# ---------------------------------------------------------------------------
# Seed overlays: the collection names are typed, per vendor.
#
# The positives only. That an overlay naming a collection the vendor does NOT
# have is *rejected* is a negative, and a negative cannot be asserted by a
# type check that passes -- it is asserted by running mypy on
# `tests/typing/negative/square_overlay_unknown_collection.py` from
# `tests/unit/testing/test_seed_typing.py`.
# ---------------------------------------------------------------------------


def a_square_overlay_takes_squares_own_collections() -> None:
    with unit("square", seed_overlay={"catalog": {}, "orders": []}) as started:
        assert_type(started.seed, SquareSeed)


def a_clover_overlay_takes_clovers_own_collections() -> None:
    with unit("clover", seed_overlay={"items": [], "tenders": []}) as started:
        assert_type(started.seed, CloverSeed)


def a_toast_overlay_takes_toasts_own_collections() -> None:
    with unit("toast", seed_overlay={"menu_v3": {}, "dining_options": []}) as started:
        assert_type(started.seed, ToastSeed)


def an_overlay_may_be_a_path() -> None:
    """Both spellings the parameter accepts, on the literal overloads: a
    ``str`` and an ``os.PathLike``, neither of which is a mapping."""
    with unit("square", seed_overlay="overlay.json") as from_str:
        assert_type(from_str.seed, SquareSeed)
    with unit("clover", seed_overlay=Path("overlay.json")) as from_path:
        assert_type(from_path.seed, CloverSeed)


def an_overlay_variable_of_the_declared_type_is_accepted() -> None:
    """The types are usable as annotations and not only as literal contexts,
    which is how a consumer's fixture would hold one."""
    square: SquareSeedOverlay = {"merchant": {"business_name": "Overlaid"}}
    clover: CloverSeedOverlay = {"merchant": {}}
    toast: ToastSeedOverlay = {"restaurant": {}}
    lightspeed: LightspeedSeedOverlay = {"inventory": []}
    with unit("square", seed_overlay=square) as a, unit("clover", seed_overlay=clover) as b:
        assert_type(a.seed, SquareSeed)
        assert_type(b.seed, CloverSeed)
    with unit("toast", seed_overlay=toast) as c:
        assert_type(c.seed, ToastSeed)
    with unit("lightspeed", seed_overlay=lightspeed) as d:
        assert_type(d.seed, LightspeedSeed)


def a_vendor_that_is_not_a_literal_takes_any_object_as_an_overlay(vendor: str) -> None:
    """``SeedOverlay`` is ``Mapping[str, Any]``: the honest answer when the
    call site does not know which vendor's collections apply."""
    with unit(vendor, seed_overlay={"whatever": 1}) as started:
        assert_type(started, StartedUnit[Seed])


def served_and_async_unit_carry_the_same_overlay_types() -> None:
    with served("toast", seed_overlay={"orders": []}) as child:
        assert_type(child, ServedUnit[ToastSeed])
    holder: AbstractAsyncContextManager[StartedUnit[SquareSeed]] = async_unit("square", seed_overlay={"locations": []})
    del holder
