"""The seed-overlay merge rule, pinned rule by rule.

Three rules and their interaction, asserted on the pure function rather than
through a built unit, because a merge that is only ever exercised by hydration
is a merge whose corners nobody has seen: ``null`` deletes and an array
replaces are both statements about inputs no shipped seed happens to produce.

The refusal is here too. It is the one thing about an overlay that has no
other symptom -- a mistyped collection merges cleanly and hydrates nothing --
so it is asserted on the message as well as on the exception: naming the key
without listing the ones that exist would send a reader to the seed document,
which is the trip the message exists to save.
"""

from __future__ import annotations

import pytest

from vendorfake.core.config.overlay import (
    DIGEST_PREFIX,
    apply_seed_overlay,
    merge_seed,
    overlay_collections,
    seed_overlay_digest,
    unknown_collections,
)
from vendorfake.core.kernel.types import UnitError, UnitErrorKind

# ---------------------------------------------------------------------------
# Rule 1: objects merge, recursively.
# ---------------------------------------------------------------------------


def test_an_object_merges_key_by_key_and_the_overlay_wins() -> None:
    merged = merge_seed({"merchant": {"id": "M1", "name": "Base"}}, {"merchant": {"name": "Over"}})
    assert merged == {"merchant": {"id": "M1", "name": "Over"}}


def test_the_merge_recurses_to_every_depth() -> None:
    base = {"a": {"b": {"c": {"keep": 1, "change": "before"}}}}
    merged = merge_seed(base, {"a": {"b": {"c": {"change": "after"}}}})
    assert merged == {"a": {"b": {"c": {"keep": 1, "change": "after"}}}}


def test_a_collection_the_overlay_never_mentions_is_untouched() -> None:
    base = {"merchant": {"id": "M1"}, "orders": [{"id": "O1"}]}
    assert merge_seed(base, {"merchant": {"id": "M2"}})["orders"] == [{"id": "O1"}]


def test_neither_argument_is_mutated() -> None:
    """The function is pure, and every caller hands it a document it will read
    again -- ``load_profile`` merges into the seed it just parsed."""
    base = {"merchant": {"id": "M1"}}
    overlay = {"merchant": {"id": "M2"}}
    merge_seed(base, overlay)
    assert base == {"merchant": {"id": "M1"}}
    assert overlay == {"merchant": {"id": "M2"}}


# ---------------------------------------------------------------------------
# Rule 2: null deletes.
# ---------------------------------------------------------------------------


def test_null_removes_the_key_rather_than_setting_it_to_none() -> None:
    """Absent means absent (``core/util/json.py``): a seed carrying an
    explicit ``null`` and a seed carrying no key at all are different
    documents, and "remove it" has to produce the second."""
    merged = merge_seed({"merchant": {"id": "M1"}, "loyalty_program": {"id": "L1"}}, {"loyalty_program": None})
    assert merged == {"merchant": {"id": "M1"}}
    assert "loyalty_program" not in merged


def test_null_deletes_at_depth_too() -> None:
    merged = merge_seed({"merchant": {"id": "M1", "phone": "555"}}, {"merchant": {"phone": None}})
    assert merged == {"merchant": {"id": "M1"}}


def test_null_for_a_key_that_is_already_absent_is_not_an_error() -> None:
    """Deletion is idempotent: an overlay written against a seed that has
    since dropped the key should not start failing for saying so."""
    assert merge_seed({"merchant": {}}, {"loyalty_program": None}) == {"merchant": {}}


# ---------------------------------------------------------------------------
# Rule 3: arrays replace, and so does every scalar.
# ---------------------------------------------------------------------------


def test_an_array_replaces_the_base_array_whole() -> None:
    """Never concatenation, never by index, never by id: an overlay that
    lists two orders means two, exactly as a profile's subscriber list does."""
    merged = merge_seed({"orders": [{"id": "O1"}, {"id": "O2"}]}, {"orders": [{"id": "O9"}]})
    assert merged == {"orders": [{"id": "O9"}]}


def test_an_empty_array_replaces_rather_than_meaning_nothing() -> None:
    assert merge_seed({"orders": [{"id": "O1"}]}, {"orders": []}) == {"orders": []}


def test_a_scalar_replaces_a_scalar() -> None:
    assert merge_seed({"config_modified_ms": 1}, {"config_modified_ms": 2}) == {"config_modified_ms": 2}


def test_a_scalar_replaces_an_object_and_an_object_replaces_a_scalar() -> None:
    """The rules do not compose into "merge whatever is mergeable": only a
    pair of objects recurses, and a type change is a replacement."""
    assert merge_seed({"a": {"deep": 1}}, {"a": "flat"}) == {"a": "flat"}
    assert merge_seed({"a": "flat"}, {"a": {"deep": 1}}) == {"a": {"deep": 1}}


def test_an_object_does_not_merge_into_an_array() -> None:
    assert merge_seed({"orders": [1, 2]}, {"orders": {"id": "O1"}}) == {"orders": {"id": "O1"}}


def test_the_rules_compose_in_one_overlay() -> None:
    """All three at once, which is the shape a real overlay has."""
    base = {
        "merchant": {"id": "M1", "name": "Base", "phone": "555"},
        "orders": [{"id": "O1"}, {"id": "O2"}],
        "loyalty_program": {"id": "L1"},
        "tokens": {"access": "t1"},
    }
    merged = merge_seed(
        base,
        {"merchant": {"name": "Over", "phone": None}, "orders": [{"id": "O9"}], "loyalty_program": None},
    )
    assert merged == {
        "merchant": {"id": "M1", "name": "Over"},
        "orders": [{"id": "O9"}],
        "tokens": {"access": "t1"},
    }


# ---------------------------------------------------------------------------
# The digest.
# ---------------------------------------------------------------------------


def test_the_digest_is_prefixed_hex_over_canonical_json() -> None:
    digest = seed_overlay_digest({"b": 1, "a": 2})
    assert digest.startswith(DIGEST_PREFIX)
    assert len(digest) == len(DIGEST_PREFIX) + 64


def test_key_order_does_not_change_the_digest() -> None:
    """Canonical, so two callers who wrote the same overlay differently
    produce one value -- the only way the digest is comparable at all."""
    assert seed_overlay_digest({"a": 1, "b": {"y": 2, "x": 3}}) == seed_overlay_digest({"b": {"x": 3, "y": 2}, "a": 1})


def test_a_different_overlay_digests_differently() -> None:
    assert seed_overlay_digest({"a": 1}) != seed_overlay_digest({"a": 2})


# ---------------------------------------------------------------------------
# The refusal.
# ---------------------------------------------------------------------------


def test_an_unknown_collection_is_refused_naming_it_and_the_valid_ones() -> None:
    base = {"_comment": ["ignore me"], "merchant": {}, "orders": []}
    with pytest.raises(UnitError) as refused:
        apply_seed_overlay(base, {"order": []}, profile="full")

    error = refused.value
    assert error.kind is UnitErrorKind.INVALID_VALUE
    assert error.field == "seed_overlay"
    message = str(error)
    assert "'order'" in message
    assert "Valid collections: merchant, orders." in message
    assert error.info["unknown"] == ["order"]
    assert error.info["available"] == ["merchant", "orders"]


def test_the_annotation_key_is_accepted_but_never_offered() -> None:
    """``_comment`` is a real key of every shipped seed, so an overlay naming
    it is not a typo -- but it is not a collection either, and offering it to
    a reader hunting for something to override would be the wrong answer."""
    assert overlay_collections({"_comment": [], "merchant": {}}) == ("merchant",)
    assert apply_seed_overlay({"_comment": ["x"], "merchant": {}}, {"_comment": ["y"]}, profile="full") == {
        "_comment": ["y"],
        "merchant": {},
    }


def test_every_unknown_key_is_named_not_just_the_first() -> None:
    with pytest.raises(UnitError) as refused:
        apply_seed_overlay({"merchant": {}}, {"zeta": 1, "alpha": 2}, profile="full")
    message = str(refused.value)
    assert "'alpha'" in message
    assert "'zeta'" in message


def test_a_profile_with_no_seed_document_says_so_rather_than_listing_nothing() -> None:
    with pytest.raises(UnitError) as refused:
        apply_seed_overlay(None, {"merchant": {}}, profile="seedless")
    message = str(refused.value)
    assert "loads no seed document at all" in message
    assert "'seedless'" in message


def test_a_seed_document_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(UnitError) as refused:
        apply_seed_overlay([1, 2, 3], {"merchant": {}}, profile="full")
    assert "not a JSON object" in str(refused.value)


def test_an_empty_overlay_is_accepted_and_changes_nothing() -> None:
    """The trivial case is legal on purpose: it is what a caller writes to
    prove the mechanism is wired before filling it in, and it is what the
    conformance clause uses as its positive control."""
    assert apply_seed_overlay({"merchant": {"id": "M1"}}, {}, profile="full") == {"merchant": {"id": "M1"}}


def test_an_empty_overlay_on_a_seedless_profile_leaves_the_seed_absent() -> None:
    """``None`` in, ``None`` out -- not ``{}``.

    The one combination the refusal above cannot catch: no seed document and
    an overlay that names nothing, so there is no offending key to report.
    Returning the merge result would hand the vendor ``{}`` where it had
    ``None``, and those are different things to every hydrator here -- ``None``
    means "load nothing, legal" and ``{}`` is a document missing its required
    collections. A helper writing ``seed_overlay=overlay or {}`` would then
    fail a legal seedless profile with a message about a document nobody
    wrote.
    """
    assert apply_seed_overlay(None, {}, profile="seedless") is None


def test_unknown_collections_reports_in_sorted_order() -> None:
    assert unknown_collections({"a": 1}, {"z": 1, "b": 2, "a": 3}) == ("b", "z")
