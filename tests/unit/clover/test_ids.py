"""Id shapes, and the two properties that make a transcript diffable."""

from __future__ import annotations

import re

from vendorfake.clover.ids import CloverIds

ENTITY = re.compile(r"[A-Z0-9]{13}")
UUID_V4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def test_entity_ids_are_13_uppercase_alphanumerics() -> None:
    """The one shape every official Clover example uses (DRKVJT2ZRRRSC,
    KFRPRVCZ73JHM, ...) -- PARTIAL provenance, stated nowhere as a rule."""
    ids = CloverIds(7)
    for mint in (ids.merchant, ids.order, ids.line_item, ids.item, ids.order_type):
        value = mint()
        assert ENTITY.fullmatch(value), value


def test_tokens_and_codes_are_uuid_format() -> None:
    """JUDGMENT shape: v2 docs show placeholders only; legacy examples and the
    documented webhook codes are UUID-shaped."""
    ids = CloverIds(7)
    for mint in (
        ids.access_token,
        ids.refresh_token,
        ids.authorization_code,
        ids.verification_code,
        ids.webhook_auth_code,
    ):
        value = mint()
        assert UUID_V4.fullmatch(value), value


def test_internal_ids_carry_their_prefix() -> None:
    assert re.fullmatch(r"tok_[0-9a-f]{12}", CloverIds(1).internal("tok"))


def test_two_streams_from_one_seed_mint_the_same_ids() -> None:
    """The whole reason the stream is seeded: a transcript is diffable evidence
    between runs rather than noise."""
    a, b = CloverIds(3), CloverIds(3)
    assert [a.order() for _ in range(10)] == [b.order() for _ in range(10)]
    assert [a.access_token() for _ in range(5)] == [b.access_token() for _ in range(5)]


def test_different_seeds_diverge() -> None:
    assert CloverIds(1).order() != CloverIds(2).order()


def test_reseed_restarts_the_stream() -> None:
    """A unit that re-hydrates on POST /__unit/state/reset must mint the ids it
    minted the first time, not continue from where the last scenario stopped."""
    ids = CloverIds(5)
    first_run = [ids.order() for _ in range(4)]
    ids.reseed(5)
    assert [ids.order() for _ in range(4)] == first_run


def test_draw_count_reports_that_the_stream_advanced() -> None:
    ids = CloverIds(1)
    assert ids.draw_count == 0
    ids.order()
    assert ids.draw_count == 13
