"""Id shapes, determinism, and the two streams that never touch."""

from __future__ import annotations

import re

from vendorfake.toast.ids import ToastIds, ToastRequestIds

UUID_V4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def test_every_guid_is_a_lowercase_uuid() -> None:
    """The documented shape (apiUnderstandingGuids...); v4 nibbles are JUDGMENT."""
    ids = ToastIds(7)
    for mint in (
        ids.guid,
        ids.order,
        ids.check,
        ids.selection,
        ids.payment,
        ids.applied_discount,
        ids.token_id,
    ):
        value = mint()
        assert UUID_V4.fullmatch(value), value


def test_request_ids_are_uuids_too() -> None:
    assert UUID_V4.fullmatch(ToastRequestIds(7).request_id())


def test_internal_ids_carry_their_prefix() -> None:
    assert re.fullmatch(r"sub_[0-9a-f]{12}", ToastIds(1).internal("sub"))


def test_two_streams_from_one_seed_mint_the_same_ids() -> None:
    a, b = ToastIds(3), ToastIds(3)
    assert [a.order() for _ in range(10)] == [b.order() for _ in range(10)]
    assert [ToastRequestIds(3).request_id() for _ in range(3)] == [ToastRequestIds(3).request_id() for _ in range(3)]


def test_different_seeds_diverge() -> None:
    assert ToastIds(1).order() != ToastIds(2).order()
    assert ToastRequestIds(1).request_id() != ToastRequestIds(2).request_id()


def test_the_request_id_stream_is_not_the_entity_stream() -> None:
    """A refused request draws a requestId; it must not renumber the next
    order guid. Same seed, different salt, and drawing from one leaves the
    other where it was."""
    ids, requests = ToastIds(5), ToastRequestIds(5)
    assert ids.guid() != ToastRequestIds(5).request_id()
    ids.reseed(5)
    first_order = ids.order()
    ids.reseed(5)
    for _ in range(50):
        requests.request_id()
    assert ids.order() == first_order
    assert ids.draw_count == 31  # 30 hex nibbles + the variant nibble, and nothing from the other stream


def test_reseed_restarts_both_streams() -> None:
    ids, requests = ToastIds(5), ToastRequestIds(5)
    first = [ids.order() for _ in range(4)]
    first_requests = [requests.request_id() for _ in range(4)]
    ids.reseed(5)
    requests.reseed(5)
    assert [ids.order() for _ in range(4)] == first
    assert [requests.request_id() for _ in range(4)] == first_requests
