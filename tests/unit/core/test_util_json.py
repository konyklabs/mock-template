"""What the two JSON encoders must produce, byte for byte.

Three separate contracts hash through ``canonical_json`` -- the entity digest,
the idempotency request fingerprint and the cursor query fingerprint -- so if
its output moves, all three drift at once and every stored cursor and every
recorded digest becomes wrong together. The literals asserted below are the
tripwire: they are meant to make that change impossible to land without
appearing in a diff.
"""

from __future__ import annotations

import hashlib

import pytest

from vendorfake.core.util.json import (
    canonical_json,
    compact,
    digest_of,
    dump_json,
    sha256_hex,
)


class TestCanonicalJson:
    def test_pins_the_exact_byte_form(self) -> None:
        value = {
            "zebra": 1,
            "alpha": {"b": [3, 2, 1], "a": None},
            "Mixed": True,
        }
        assert canonical_json(value) == '{"Mixed":true,"alpha":{"a":null,"b":[3,2,1]},"zebra":1}'

    def test_sorts_keys_at_every_depth(self) -> None:
        assert canonical_json({"b": {"d": 1, "c": 2}, "a": 3}) == '{"a":3,"b":{"c":2,"d":1}}'

    def test_preserves_array_order_because_array_order_is_data(self) -> None:
        assert canonical_json(["c", "a", "b"]) == '["c","a","b"]'
        assert canonical_json({"k": [{"z": 1, "a": 2}]}) == '{"k":[{"a":2,"z":1}]}'

    def test_sorts_uppercase_before_lowercase_by_code_point(self) -> None:
        # ICU collation (JavaScript's localeCompare) puts "a" before "B".
        # Code point order, which is the cross-language contract here, does not.
        assert canonical_json({"a": 1, "B": 2}) == '{"B":2,"a":1}'

    def test_emits_non_ascii_as_utf8_not_escapes(self) -> None:
        assert canonical_json({"name": "Café"}) == '{"name":"Café"}'

    def test_refuses_non_finite_floats_rather_than_emitting_invalid_json(self) -> None:
        with pytest.raises(ValueError):
            canonical_json({"amount": float("nan")})
        with pytest.raises(ValueError):
            canonical_json({"amount": float("inf")})


class TestDumpJson:
    def test_pins_the_exact_wire_bytes(self) -> None:
        body = {"merchant_id": "MLQW2MYBY81PZ", "type": "order.created", "n": 1}
        assert dump_json(body) == b'{"merchant_id":"MLQW2MYBY81PZ","type":"order.created","n":1}'

    def test_keeps_the_producing_order_because_that_order_is_the_vendor_shape(self) -> None:
        assert dump_json({"z": 1, "a": 2}) == b'{"z":1,"a":2}'

    def test_non_ascii_stays_utf8_because_the_signature_covers_these_bytes(self) -> None:
        assert dump_json({"name": "Café"}) == b'{"name":"Caf\xc3\xa9"}'

    def test_integral_floats_keep_their_python_form(self) -> None:
        # JavaScript emits `1`; Python emits `1.0`. Named here so a vendor
        # projection that must match a documented example knows to send an int.
        assert dump_json({"n": 1.0}) == b'{"n":1.0}'
        assert dump_json({"n": 1}) == b'{"n":1}'

    def test_refuses_non_finite_floats(self) -> None:
        with pytest.raises(ValueError):
            dump_json({"amount": float("nan")})


class TestHashes:
    def test_sha256_hex_of_a_string_hashes_its_utf8_bytes(self) -> None:
        assert sha256_hex("café") == sha256_hex("café".encode())

    def test_sha256_hex_pins_a_literal(self) -> None:
        assert sha256_hex("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_digest_of_is_sha256_of_the_canonical_form(self) -> None:
        value = {"b": 1, "a": 2}
        assert digest_of(value) == hashlib.sha256(b'{"a":2,"b":1}').hexdigest()

    def test_key_order_cannot_change_a_digest(self) -> None:
        assert digest_of({"a": 1, "b": 2}) == digest_of({"b": 2, "a": 1})

    def test_array_order_does_change_a_digest(self) -> None:
        assert digest_of([1, 2]) != digest_of([2, 1])


class TestCompact:
    def test_drops_none_valued_keys(self) -> None:
        projected = compact({"id": "CAIS1", "reference_id": None, "state": "OPEN"})
        assert projected == {"id": "CAIS1", "state": "OPEN"}
        assert "reference_id" not in projected

    def test_a_compacted_projection_emits_no_null_keys(self) -> None:
        # This is the whole point: without compact() every optional vendor
        # field would appear as an explicit null and the wire format would
        # diverge from the vendor's own examples on every response.
        assert dump_json(compact({"id": "CAIS1", "closed_at": None})) == b'{"id":"CAIS1"}'

    def test_keeps_falsy_values_that_are_not_none(self) -> None:
        assert compact({"a": 0, "b": "", "c": False, "d": [], "e": None}) == {
            "a": 0,
            "b": "",
            "c": False,
            "d": [],
        }

    def test_preserves_insertion_order(self) -> None:
        assert list(compact({"z": 1, "m": None, "a": 2})) == ["z", "a"]

    def test_is_shallow_and_does_not_mutate_its_argument(self) -> None:
        source: dict[str, object] = {"a": None, "nested": {"b": None}}
        result = compact(source)
        assert result == {"nested": {"b": None}}
        assert source == {"a": None, "nested": {"b": None}}
        assert result is not source
