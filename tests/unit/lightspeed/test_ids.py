"""The two id streams: the shapes, the determinism, and the salt between them."""

from __future__ import annotations

import re

from vendorfake.lightspeed.ids import CREDENTIAL_SALT, LightspeedCredentialIds, LightspeedIds

#: The lowercase-UUID layout every ``id`` in the specification's examples has.
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-1[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def test_every_entity_id_is_a_lowercase_uuid() -> None:
    ids = LightspeedIds(7)
    for minted in (ids.uuid(), ids.outlet(), ids.register(), ids.register_closure(), ids.payment_type()):
        assert UUID.match(minted), minted


def test_the_same_seed_mints_the_same_sequence() -> None:
    assert [LightspeedIds(3).uuid() for _ in range(4)] == [LightspeedIds(3).uuid() for _ in range(4)]


def test_different_seeds_diverge() -> None:
    assert LightspeedIds(3).uuid() != LightspeedIds(4).uuid()


def test_reseeding_restarts_the_stream() -> None:
    """Called at hydrate, so ``POST /__unit/state/reset`` reproduces the ids
    rather than continuing from wherever the previous scenario left off."""
    ids = LightspeedIds(5)
    first = [ids.uuid() for _ in range(3)]
    ids.reseed(5)
    assert [ids.uuid() for _ in range(3)] == first


def test_tokens_and_codes_are_not_uuid_shaped() -> None:
    """The authorization page states no token format, so a consumer must treat
    one as opaque -- and a UUID-shaped token would invite the parsing this
    guards against."""
    credentials = LightspeedCredentialIds(1)
    for minted in (credentials.access_token(), credentials.refresh_token(), credentials.authorization_code()):
        assert not UUID.match(minted)
        assert "-" not in minted
        assert minted.isalnum()


def test_the_credential_stream_never_moves_the_entity_stream() -> None:
    """A rejected token exchange draws from the credential stream; a scenario's
    outlet ids must not renumber because of it."""
    entities = LightspeedIds(11)
    baseline = [entities.uuid() for _ in range(3)]

    entities = LightspeedIds(11)
    credentials = LightspeedCredentialIds(11)
    for _ in range(5):
        credentials.access_token()
    assert [entities.uuid() for _ in range(3)] == baseline


def test_the_salt_separates_the_two_streams() -> None:
    assert CREDENTIAL_SALT != 0
    assert LightspeedIds(1)._pick("0123456789abcdef", 8) != LightspeedCredentialIds(1)._pick("0123456789abcdef", 8)


def test_the_draw_count_reports_how_far_the_stream_advanced() -> None:
    ids = LightspeedIds(1)
    before = ids.draw_count
    ids.uuid()
    assert ids.draw_count > before
