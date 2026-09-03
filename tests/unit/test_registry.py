"""Vendor selection, and the environment rule ``create_unit`` exists to enforce."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import vendorfake
from tests.fakes import FakeVendor, capability
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


def test_with_two_vendors_and_no_selector_the_error_names_them_all() -> None:
    """The multi-vendor vendorless path, run for real. Two vendors ship in this
    distribution, so "exactly one installed" no longer applies and the
    registry must refuse rather than pick -- naming every candidate and the
    environment variable that would have chosen one. A hidden default here
    would be the silent misconfiguration the registry's invariant forbids."""
    offered = available_vendors()
    assert {"clover", "square"} <= set(offered), offered
    with pytest.raises(ValueError) as caught:
        create_unit(profile="full")
    message = str(caught.value)
    assert VENDOR_ENV_VAR in message
    for name in offered:
        assert name in message


def test_with_no_vendor_and_none_installed_the_error_says_how_to_supply_one(tmp_path: Path) -> None:
    installed = available_vendors()
    if installed:
        pytest.skip(f"vendors installed: {installed}; the none-installed branch is unreachable here")
    with pytest.raises(ValueError) as caught:
        create_unit(profile="test")
    assert VENDOR_ENV_VAR in str(caught.value)


def test_the_vendor_env_var_is_not_in_the_profile_loader_s_table() -> None:
    """It decides which module to import, which happens before a profile
    exists, so it belongs to the registry rather than to configuration."""
    from vendorfake.core.config.profile import env_names

    assert VENDOR_ENV_VAR not in env_names()


# ---------------------------------------------------------------------------
# Discovery: profiles, routes, and capabilities=.
# ---------------------------------------------------------------------------


def test_available_profiles_names_match_the_packaged_files_on_disk() -> None:
    """Read independently through ``importlib.resources`` rather than the
    ``Path.glob`` ``available_profiles`` itself uses, so this would still
    catch a discovery bug that pointed at the wrong directory and happened to
    glob it consistently with itself."""
    from importlib import resources

    from vendorfake.registry import available_profiles

    found = available_profiles("toast")
    assert len(found) == 6
    on_disk = {
        entry.name.removesuffix(".json")
        for entry in (resources.files("vendorfake.toast") / "profiles").iterdir()
        if entry.name.endswith(".json")
    }
    assert {row.name for row in found} == on_disk
    assert all(row.vendor == "toast" for row in found)
    assert list(found) == sorted(found, key=lambda row: row.name)


def test_available_profiles_reads_the_same_schema_load_profile_validates_against() -> None:
    """Every field a profile document may carry is present or ``None`` --
    never absent -- because a caller reads a dataclass, not a dict that might
    be missing a key on a profile that never set one."""
    from vendorfake.registry import available_profiles

    row = next(p for p in available_profiles("square") if p.name == "oauth-only")
    assert row.capabilities == ("oauth", "chaos")
    assert row.seed == "seed/default.seed.json"
    assert "OAuth" in row.summary


def test_every_shipped_profiles_own_name_field_equals_its_file_stem() -> None:
    """``ProfileInfo.name`` is the file's stem, not the document's own
    optional ``name`` field (see ``_profiles_of``'s docstring) -- and this
    pins the data, not just that code path: every shipped profile's own
    ``name`` field agrees with its filename too, across all three vendors,
    so a document that quietly drifted from its filename would be caught
    here even though ``available_profiles`` itself can no longer surface it
    (it reports the stem regardless of what the document says)."""
    from importlib import resources

    for vendor_name in ("square", "clover", "toast"):
        package = resources.files(f"vendorfake.{vendor_name}") / "profiles"
        checked = 0
        for entry in package.iterdir():
            if not entry.name.endswith(".json"):
                continue
            document = json.loads(entry.read_text(encoding="utf-8"))
            stem = entry.name.removesuffix(".json")
            assert document.get("name") == stem, (
                f"{vendor_name}/profiles/{entry.name}: document's own 'name' field is "
                f"{document.get('name')!r}, not {stem!r}"
            )
            checked += 1
        assert checked == 6, f"{vendor_name}: expected 6 shipped profiles, found {checked}"


def test_available_profiles_refuses_an_unknown_vendor_the_same_way_resolve_vendor_does() -> None:
    from vendorfake.registry import available_profiles

    with pytest.raises(ValueError) as caught:
        available_profiles("nosuchvendor")
    assert "no vendor named 'nosuchvendor'" in str(caught.value)


def test_routes_contains_the_documented_operation_and_agrees_with_path_for() -> None:
    from vendorfake.registry import routes
    from vendorfake.testing import unit as start_unit

    table = routes("square")
    row = next(r for r in table if r.operation_id == "ObtainToken")
    assert row.path == "/oauth2/token"
    assert row.method == "POST"
    assert row.capability == "oauth"
    assert row.internal is False

    with start_unit("square") as driver:
        assert driver.path_for("ObtainToken") == "/oauth2/token"
        assert driver.route_for("ObtainToken").capability == "oauth"


def test_route_for_an_unknown_operation_id_lists_the_ones_that_exist() -> None:
    from vendorfake.testing import unit as start_unit

    with start_unit("square") as driver:
        with pytest.raises(KeyError) as caught:
            driver.route_for("NoSuchOperation")
        assert "ObtainToken" in str(caught.value)


def test_capabilities_and_profile_together_is_a_value_error() -> None:
    with pytest.raises(ValueError) as caught:
        create_unit(vendor="square", profile="full", capabilities=["oauth"])
    assert "capabilities" in str(caught.value)
    assert "profile" in str(caught.value)


def test_an_empty_capabilities_list_is_a_value_error_not_the_narrowest_profile() -> None:
    """An empty set is a subset of every profile's capabilities, so resolving
    it the way a non-empty request resolves would silently pick the smallest
    shipped profile -- almost certainly not what an empty list was meant to
    ask for. Verified against the wrong behaviour first: before this guard,
    ``create_unit(vendor="square", capabilities=[])`` started on 'oauth-only'
    with capabilities ('oauth', 'chaos')."""
    with pytest.raises(ValueError) as caught:
        create_unit(vendor="square", capabilities=[])
    assert "capabilities" in str(caught.value)
    assert "empty" in str(caught.value) or "[]" in str(caught.value)


def test_capabilities_translates_a_role_name_into_this_vendors_own_and_picks_the_narrowest_profile() -> None:
    unit = create_unit(vendor="toast", capabilities=["auth"])
    try:
        assert unit.context.config.profile == "oauth-only"
        assert unit.context.config.requested_capabilities == ("auth",)
    finally:
        unit.stop()


def test_capabilities_picks_the_narrowest_shipped_superset_profile() -> None:
    unit = create_unit(vendor="square", capabilities=["oauth", "payments"])
    try:
        # Square ships no profile naming exactly {oauth, payments}; `no-faults`
        # is the smallest shipped profile that is a superset of it (`no-chaos`
        # and `full` are supersets too, but larger), which is what the DoD's
        # "narrowest superset profile" example names.
        assert unit.context.config.profile == "no-faults"
        assert set(unit.context.config.capabilities) >= {"oauth", "payments"}
        assert unit.context.config.requested_capabilities == ("oauth", "payments")
    finally:
        unit.stop()


def test_capabilities_falls_back_to_full_and_an_absolute_list_when_nothing_shipped_matches(tmp_path: Path) -> None:
    """The other half of the DoD's "or": a vendor whose shipped profiles do
    not include one that is a superset of the request at all -- unreachable
    with the three built-in vendors, whose ``full`` profile enables every
    capability by definition and is therefore always a superset of any valid
    request. A fixture vendor with a smaller `full` demonstrates the branch
    directly rather than leaving it merely plausible.
    """
    directory = tmp_path / "profiles"
    directory.mkdir()
    (directory / "full.json").write_text(json.dumps({"capabilities": ["orders"]}), encoding="utf-8")
    (directory / "narrow.json").write_text(json.dumps({"capabilities": ["chaos"]}), encoding="utf-8")
    vendor = FakeVendor(
        profile_dir=directory,
        base_dir=tmp_path,
        capabilities=(capability("orders"), capability("chaos", kind="behavior")),
        roles={"auth": "orders", "orders": "orders", "webhooks": "webhooks", "chaos": "chaos"},
    )
    unit = create_unit(vendor=vendor, capabilities=["orders", "chaos"])
    try:
        # Neither `full` ({orders}) nor `narrow` ({chaos}) is a superset of
        # {orders, chaos}, so resolution falls back to `full` plus the
        # absolute list through VENDORFAKE_CAPABILITIES.
        assert unit.context.config.profile == "full"
        assert set(unit.context.config.capabilities) == {"orders", "chaos"}
        assert unit.context.config.requested_capabilities == ("orders", "chaos")
    finally:
        unit.stop()


def test_a_unit_started_by_profile_reports_no_requested_capabilities() -> None:
    unit = create_unit(vendor="square", profile="full")
    try:
        assert unit.context.config.requested_capabilities is None
    finally:
        unit.stop()
