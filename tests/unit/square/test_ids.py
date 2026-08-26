"""Id shapes, and the two properties that make a transcript diffable."""

from __future__ import annotations

import re

from vendorfake.core.rand.rng import ID_SEED_SALT, Rng
from vendorfake.square.ids import SquareIds


def test_the_documented_shapes() -> None:
    ids = SquareIds(7)
    order = ids.order()
    assert re.fullmatch(r"CAIS[A-Za-z0-9]{23}", order), order
    assert len(order) == 27
    assert re.fullmatch(r"sq0cgb-[A-Za-z0-9_-]{22}", ids.authorization_code())
    assert re.fullmatch(r"wbhk_[0-9a-f]{32}", ids.subscription())
    assert re.fullmatch(r"EAAA[A-Za-z0-9_-]{60}", ids.access_token())
    assert re.fullmatch(r"EQAA[A-Za-z0-9_-]{60}", ids.refresh_token())
    assert re.fullmatch(r"[A-Z0-9]{13}", ids.location())
    assert re.fullmatch(r"[A-Z0-9]{13}", ids.merchant())
    assert re.fullmatch(r"[A-Z0-9]{24}", ids.catalog_object())
    assert re.fullmatch(r"[A-Za-z0-9]{22}", ids.line_item_uid())
    assert re.fullmatch(r"[A-Za-z0-9]{27}", ids.tender())
    assert re.fullmatch(r"tok_[0-9a-f]{12}", ids.internal("tok"))


def test_two_streams_from_one_seed_mint_the_same_ids() -> None:
    """The whole reason the stream is seeded: a webhook transcript is diffable
    evidence between runs rather than noise."""
    a, b = SquareIds(3), SquareIds(3)
    assert [a.order() for _ in range(10)] == [b.order() for _ in range(10)]


def test_different_seeds_diverge() -> None:
    assert SquareIds(1).order() != SquareIds(2).order()


def test_reseed_restarts_the_stream() -> None:
    """A unit that re-hydrates on POST /__unit/state/reset must mint the ids it
    minted the first time, not continue from where the last scenario stopped."""
    ids = SquareIds(5)
    first_run = [ids.order() for _ in range(4)]
    ids.reseed(5)
    assert [ids.order() for _ in range(4)] == first_run


def test_the_id_stream_is_salted_away_from_the_units_own_stream() -> None:
    """Adding a probability rule to a profile must not renumber every id.

    Minting the same id from the *unit's* stream is what the salt prevents, so
    the check is that the same algorithm on an unsalted stream of the same seed
    produces a different id -- and that the salted one is reproducible.
    """
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    unsalted = Rng(11)
    from_unit_stream = "CAIS" + "".join(alphabet[unsalted.int(len(alphabet))] for _ in range(23))

    assert SquareIds(11).order() != from_unit_stream
    assert SquareIds(11).order() == SquareIds(11).order()

    salted = Rng(11 ^ ID_SEED_SALT)
    from_salted_stream = "CAIS" + "".join(alphabet[salted.int(len(alphabet))] for _ in range(23))
    assert SquareIds(11).order() == from_salted_stream


def test_draw_count_reports_that_the_stream_advanced() -> None:
    ids = SquareIds(1)
    assert ids.draw_count == 0
    ids.order()
    assert ids.draw_count == 23
