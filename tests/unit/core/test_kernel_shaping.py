"""The vendor-neutral tail every error table shares."""

from __future__ import annotations

import pytest

from vendorfake.core.kernel.shaping import (
    DEFAULT_RETRY_AFTER,
    assert_error_table_total,
    mechanism_headers,
    unit_error_sidecar,
)
from vendorfake.core.kernel.types import UnitError, UnitErrorKind

# ---------------------------------------------------------------------------
# exhaustiveness
# ---------------------------------------------------------------------------


def test_a_total_table_passes_silently() -> None:
    assert_error_table_total(dict.fromkeys(UnitErrorKind, object()), name="T")


def test_a_missing_row_raises_naming_the_table_and_the_kind() -> None:
    table = {kind: object() for kind in UnitErrorKind if kind is not UnitErrorKind.TIMEOUT}
    with pytest.raises(RuntimeError, match=r"MY_TABLE.*missing: \['timeout'\].*unknown: none"):
        assert_error_table_total(table, name="MY_TABLE")


def test_an_unknown_row_raises_too() -> None:
    table: dict[object, object] = dict.fromkeys(UnitErrorKind, object())
    table["not_a_kind"] = object()
    with pytest.raises(RuntimeError, match=r"missing: none.*unknown: \['not_a_kind'\]"):
        assert_error_table_total(table, name="T")  # type: ignore[arg-type]


def test_the_check_is_a_raise_and_not_an_assert() -> None:
    """`python -O` strips assert statements; a raise survives it. The test
    reads the source rather than trusting the docstring."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path("src/vendorfake/core/kernel/shaping.py").read_text(encoding="utf-8"))
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Assert)]


# ---------------------------------------------------------------------------
# the sidecar
# ---------------------------------------------------------------------------


def test_the_sidecar_carries_info_kind_and_provenance() -> None:
    err = UnitError(UnitErrorKind.VERSION_CONFLICT, info={"expected": 3, "actual": 4})
    assert unit_error_sidecar(err, "judgment") == {
        "expected": 3,
        "actual": 4,
        "kind": "version_conflict",
        "status_provenance": "judgment",
    }


def test_reserved_keys_win_over_an_info_document_that_carries_them() -> None:
    err = UnitError(UnitErrorKind.NOT_FOUND, info={"kind": "lie", "status_provenance": "lie"})
    sidecar = unit_error_sidecar(err, "documented")
    assert sidecar["kind"] == "not_found"
    assert sidecar["status_provenance"] == "documented"


def test_vendor_extras_are_appended_and_none_is_dropped() -> None:
    err = UnitError(UnitErrorKind.FORBIDDEN_SCOPE, detail="missing ORDERS_W")
    sidecar = unit_error_sidecar(err, "documented", detail=err.detail, field=None)
    assert sidecar == {"kind": "forbidden_scope", "status_provenance": "documented", "detail": "missing ORDERS_W"}


# ---------------------------------------------------------------------------
# the headers
# ---------------------------------------------------------------------------


def test_retry_after_follows_the_rule_and_falls_back_to_one_second() -> None:
    with_interval = UnitError(UnitErrorKind.RATE_LIMITED, info={"retry_after_seconds": 7})
    assert mechanism_headers(with_interval, retry_after_header=True) == {"retry-after": "7"}
    bare = UnitError(UnitErrorKind.RATE_LIMITED)
    assert mechanism_headers(bare, retry_after_header=True) == {"retry-after": DEFAULT_RETRY_AFTER}
    assert DEFAULT_RETRY_AFTER == "1"


def test_retry_after_is_switchable() -> None:
    err = UnitError(UnitErrorKind.RATE_LIMITED, info={"retry_after_seconds": 7})
    assert mechanism_headers(err, retry_after_header=False) == {}


def test_a_disabled_capability_names_itself_and_an_unnamed_one_is_empty() -> None:
    named = UnitError(UnitErrorKind.CAPABILITY_DISABLED, info={"capability": "webhooks"})
    assert mechanism_headers(named, retry_after_header=True) == {"x-unit-capability": "webhooks"}
    unnamed = UnitError(UnitErrorKind.CAPABILITY_DISABLED)
    assert mechanism_headers(unnamed, retry_after_header=True) == {"x-unit-capability": ""}


def test_every_other_kind_gets_no_mechanism_header() -> None:
    for kind in UnitErrorKind:
        if kind in (UnitErrorKind.RATE_LIMITED, UnitErrorKind.CAPABILITY_DISABLED):
            continue
        assert mechanism_headers(UnitError(kind), retry_after_header=True) == {}, kind
