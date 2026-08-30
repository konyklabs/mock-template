"""The chassis every vendor id stream is built on."""

from __future__ import annotations

import re

from vendorfake.core.rand.ids import HEX, MIXED_ALNUM, UPPER_ALNUM, IdStream
from vendorfake.core.rand.rng import ID_SEED_SALT, Rng


class Ids(IdStream):
    """The smallest vendor: one shape, plus what the chassis gives it."""

    __slots__ = ()

    def thing(self) -> str:
        return f"T{self._pick(MIXED_ALNUM, 9)}"


def test_the_alphabets_are_what_their_names_say() -> None:
    assert UPPER_ALNUM == "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    assert MIXED_ALNUM == "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    assert HEX == "0123456789abcdef"


def test_pick_draws_one_character_per_position_from_the_alphabet() -> None:
    ids = Ids(7)
    value = ids.thing()
    assert re.fullmatch(r"T[A-Za-z0-9]{9}", value), value
    assert ids.draw_count == 9


def test_internal_ids_carry_their_prefix_and_twelve_hex_characters() -> None:
    assert re.fullmatch(r"tok_[0-9a-f]{12}", Ids(1).internal("tok"))


def test_two_streams_from_one_seed_mint_the_same_ids() -> None:
    a, b = Ids(3), Ids(3)
    assert [a.thing() for _ in range(10)] == [b.thing() for _ in range(10)]


def test_different_seeds_diverge() -> None:
    assert Ids(1).thing() != Ids(2).thing()


def test_reseed_restarts_the_stream() -> None:
    """The hydrate contract: a unit that re-hydrates on POST /__unit/state/reset
    must mint the ids it minted the first time."""
    ids = Ids(5)
    first_run = [ids.thing() for _ in range(4)]
    ids.reseed(5)
    assert [ids.thing() for _ in range(4)] == first_run
    assert ids.draw_count == 36


def test_reseed_with_another_seed_is_a_different_stream() -> None:
    ids = Ids(5)
    first = ids.thing()
    ids.reseed(6)
    assert ids.thing() != first


def test_the_stream_is_salted_away_from_the_units_own_stream() -> None:
    """Adding a probability rule to a profile must not renumber every id, so
    the same algorithm on an unsalted stream of the same seed produces a
    different id -- and the salted one is exactly reproducible by hand."""
    unsalted = Rng(11)
    from_unit_stream = "T" + "".join(MIXED_ALNUM[unsalted.int(len(MIXED_ALNUM))] for _ in range(9))
    assert Ids(11).thing() != from_unit_stream

    salted = Rng(11 ^ ID_SEED_SALT)
    from_salted_stream = "T" + "".join(MIXED_ALNUM[salted.int(len(MIXED_ALNUM))] for _ in range(9))
    assert Ids(11).thing() == from_salted_stream


def test_a_slotted_subclass_stays_dict_free() -> None:
    """The chassis declares __slots__ so a vendor that does too pays for no
    instance dict; the vendors in this repository both do."""
    assert not hasattr(Ids(1), "__dict__")
