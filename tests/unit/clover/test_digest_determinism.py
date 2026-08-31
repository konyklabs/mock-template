"""The state digest sees single-use and rotation, not only their instants.

`refresh_used_at_ms` and `used_at_ms` are volatile -- two units an hour apart
must agree -- but their *presence* is the only record that a refresh token was
rotated or a code spent. The measured hole this closes (konyklabs/roadmap#35):
the digest was byte-identical with `refresh_used_at_ms` present and removed,
so a mutant that stopped marking rotation would not have moved it.
"""

from __future__ import annotations

from tests.unit.clover.harness import harness
from vendorfake.clover.entities import COL

HOUR_MS = 60 * 60 * 1000


def test_a_rotated_refresh_token_moves_the_digest_by_its_mark_alone() -> None:
    for h in harness("full", env={"VENDORFAKE_CLOCK": "virtual"}):
        store = h.unit.context.store
        first = h.exchange()
        assert h.refresh(refresh_token=first["refresh_token"]).status == 200
        record = next(e for e in store.raw(COL.tokens).values() if e.get("access_token") == first["access_token"])
        assert record.get("refresh_used_at_ms") is not None
        with_mark = store.entity_digest()
        record.pop("refresh_used_at_ms")
        assert store.entity_digest() != with_mark


def test_a_spent_code_moves_the_digest_by_its_mark_alone() -> None:
    for h in harness("full", env={"VENDORFAKE_CLOCK": "virtual"}):
        store = h.unit.context.store
        h.exchange()
        spent = next(e for e in store.raw(COL.codes).values() if e.get("used_at_ms") is not None)
        with_mark = store.entity_digest()
        spent.pop("used_at_ms")
        assert store.entity_digest() != with_mark


def test_two_units_rotating_an_hour_apart_digest_alike() -> None:
    """The other half: the instant itself is still ignored."""
    digests = []
    for advance in (0, HOUR_MS):
        for h in harness("full", env={"VENDORFAKE_CLOCK": "virtual"}):
            if advance:
                assert h.api.post("/__unit/clock/advance", {"ms": advance}).status == 200
            first = h.exchange()
            assert h.refresh(refresh_token=first["refresh_token"]).status == 200
            digests.append(h.unit.context.store.entity_digest())
    assert digests[0] == digests[1]
