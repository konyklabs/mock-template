"""What the seeded stream guarantees.

The pinned stream exists so a CPython upgrade that changed the generator shows
up as a diff rather than as quietly different ids in every recorded webhook
transcript. If this test fails after an interpreter upgrade, the answer is to
decide whether ids may move, not to update the literals reflexively.
"""

from __future__ import annotations

from vendorfake.core.rand.rng import ID_SEED_SALT, Rng, salted_seed


class TestDeterminism:
    def test_two_generators_with_one_seed_produce_one_stream(self) -> None:
        a = [Rng(7).int(1000) for _ in range(5)]
        b = [Rng(7).int(1000) for _ in range(5)]
        assert a == b

    def test_different_seeds_produce_different_streams(self) -> None:
        assert [Rng(1).int(1000) for _ in range(5)] != [Rng(2).int(1000) for _ in range(5)]

    def test_the_stream_is_pinned_for_a_fixed_seed(self) -> None:
        rng = Rng(1)
        assert [rng.int(256) for _ in range(10)] == [34, 216, 195, 65, 126, 115, 166, 201, 24, 7]

    def test_hex_is_pinned_for_a_fixed_seed(self) -> None:
        assert Rng(1).hex(16) == "22d8c3417e73a6c91807d56ec30072b8"

    def test_reset_rewinds_to_the_seed(self) -> None:
        rng = Rng(42)
        first = [rng.next() for _ in range(3)]
        rng.reset()
        assert [rng.next() for _ in range(3)] == first


class TestInterface:
    def test_next_stays_in_the_unit_interval(self) -> None:
        rng = Rng(3)
        for _ in range(200):
            value = rng.next()
            assert 0.0 <= value < 1.0

    def test_int_stays_below_the_exclusive_bound(self) -> None:
        rng = Rng(3)
        for _ in range(200):
            assert 0 <= rng.int(37) < 37

    def test_hex_is_lowercase_and_two_characters_per_byte(self) -> None:
        value = Rng(9).hex(16)
        assert len(value) == 32
        assert value == value.lower()
        assert all(c in "0123456789abcdef" for c in value)

    def test_hex_of_zero_bytes_is_empty(self) -> None:
        assert Rng(9).hex(0) == ""

    def test_draw_count_reports_every_draw_and_only_draws(self) -> None:
        rng = Rng(5)
        assert rng.draw_count == 0
        rng.next()
        assert rng.draw_count == 1
        rng.int(10)
        assert rng.draw_count == 2
        rng.hex(4)
        assert rng.draw_count == 6
        rng.reset()
        assert rng.draw_count == 0

    def test_a_run_that_never_draws_reports_zero(self) -> None:
        # Chaos triggering is counter-based; a deterministic scenario must be
        # able to show it consumed no randomness at all.
        assert Rng(1).draw_count == 0


class TestSalt:
    def test_the_salt_is_the_reference_constant(self) -> None:
        assert ID_SEED_SALT == 0x51754152

    def test_salting_separates_the_id_stream_from_the_chaos_stream(self) -> None:
        seed = 1
        chaos = [Rng(seed).int(1000) for _ in range(5)]
        ids = [Rng(salted_seed(seed)).int(1000) for _ in range(5)]
        assert chaos != ids

    def test_salted_seed_stays_a_uint32(self) -> None:
        # Reproduces JavaScript's `>>> 0`; Python's ints do not wrap.
        for seed in (0, 1, 0xFFFFFFFF, 0x7FFFFFFF, ID_SEED_SALT):
            assert 0 <= salted_seed(seed) <= 0xFFFFFFFF

    def test_salting_is_its_own_inverse(self) -> None:
        assert salted_seed(salted_seed(12345)) == 12345

    def test_the_seed_is_normalised_to_a_uint32(self) -> None:
        assert Rng(-1).seed == 0xFFFFFFFF
        assert Rng(0x1_0000_0001).seed == 1
