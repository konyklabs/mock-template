"""Vendor selection, and the environment rule ``create_unit`` exists to enforce."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import vendorfake
from tests.fakes import FakeVendor
from vendorfake.registry import VENDOR_ENV_VAR, available_vendors, create_unit, resolve_vendor


def _profile_dir(tmp_path: Path, document: dict[str, object], name: str = "test") -> Path:
    directory = tmp_path / "profiles"
    directory.mkdir()
    (directory / f"{name}.json").write_text(json.dumps(document), encoding="utf-8")
    return directory


def _vendor(tmp_path: Path, document: dict[str, object] | None = None) -> FakeVendor:
    return FakeVendor(
        profile_dir=_profile_dir(tmp_path, document or {"capabilities": ["orders", "chaos"]}),
        base_dir=tmp_path,
    )


def test_create_unit_is_exported_from_the_package_root() -> None:
    assert vendorfake.create_unit is create_unit
    assert set(vendorfake.__all__) >= {"create_unit", "resolve_vendor", "available_vendors"}


def test_an_unknown_vendor_name_lists_the_real_ones() -> None:
    with pytest.raises(ValueError) as caught:
        resolve_vendor("sqaure")
    assert "no vendor named 'sqaure'" in str(caught.value)
    assert "Available:" in str(caught.value)


def test_available_vendors_never_advertises_a_name_that_would_not_load() -> None:
    """An error message listing a vendor that does not exist is worse than no
    message. Every declared target is filtered through an importability check,
    so this is a true statement about this checkout and not a static list."""
    import importlib.util

    for name in available_vendors():
        assert importlib.util.find_spec(f"vendorfake.{name}") is not None


def test_a_vendor_definition_may_be_passed_directly(tmp_path: Path) -> None:
    unit = create_unit(vendor=_vendor(tmp_path), profile="test")
    assert unit.name == "acme"
    assert unit.context.config.profile == "test"


def test_the_process_environment_is_never_read(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """The reference spread ``process.env`` into every unit it built, so a
    variable set by one test changed the profile of a unit built by another.
    ``env`` defaults to ``{}`` and only the CLI passes the real mapping."""
    monkeypatch.setenv("VENDORFAKE_CAPABILITIES", "-orders")
    monkeypatch.setenv("VENDORFAKE_LOG_LEVEL", "debug")
    monkeypatch.setenv("VENDORFAKE_CHAOS_SEED", "424242")
    unit = create_unit(vendor=_vendor(tmp_path), profile="test")
    assert unit.context.config.capabilities == ("orders", "chaos")
    assert unit.context.config.log_level == "info"
    assert unit.context.config.chaos.seed == 1


def test_an_explicit_env_mapping_is_honoured(tmp_path: Path) -> None:
    unit = create_unit(
        vendor=_vendor(tmp_path),
        profile="test",
        env={"VENDORFAKE_CAPABILITIES": "-orders", "VENDORFAKE_LOG_LEVEL": "error"},
    )
    assert unit.context.config.capabilities == ("chaos",)
    assert unit.context.config.log_level == "error"


def test_the_vendor_document_is_merged_under_the_profile(tmp_path: Path) -> None:
    """``retry_defaults`` supplies the schedule the core deliberately does not
    ship; the profile overrides it, and the environment overrides both."""
    from vendorfake.core.config.models import ProfileDocument

    vendor = _vendor(tmp_path, {"capabilities": ["orders", "chaos"], "webhooks": {"retry": {"timeout_ms": 250}}})
    vendor.retry_defaults = ProfileDocument.model_validate(
        {"webhooks": {"retry": {"schedule_ms": [10, 20], "timeout_ms": 9999, "time_scale": 0.5}}}
    )
    unit = create_unit(vendor=vendor, profile="test")
    retry = unit.context.config.webhooks.retry
    assert retry.schedule_ms == (10, 20)  # only the vendor supplied it
    assert retry.timeout_ms == 250  # the profile won
    assert retry.time_scale == 0.5  # the vendor's, untouched


def test_a_profile_name_that_does_not_exist_lists_what_does(tmp_path: Path) -> None:
    from vendorfake.core.kernel.types import UnitError

    with pytest.raises(UnitError) as caught:
        create_unit(vendor=_vendor(tmp_path), profile="missing")
    assert "test" in str(caught.value)


def test_create_unit_starts_the_unit(tmp_path: Path) -> None:
    vendor = _vendor(tmp_path)
    create_unit(vendor=vendor, profile="test")
    assert vendor.hydrated == 1


def test_with_no_vendor_and_none_installed_the_error_says_how_to_supply_one(tmp_path: Path) -> None:
    if available_vendors():
        pytest.skip("a vendor is installed; the single-vendor default applies instead")
    with pytest.raises(ValueError) as caught:
        create_unit(profile="test")
    assert VENDOR_ENV_VAR in str(caught.value)


def test_the_vendor_env_var_is_not_in_the_profile_loader_s_table() -> None:
    """It decides which module to import, which happens before a profile
    exists, so it belongs to the registry rather than to configuration."""
    from vendorfake.core.config.profile import env_names

    assert VENDOR_ENV_VAR not in env_names()
