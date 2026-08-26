"""The manifest is the committed record of what the suite asserts."""

from __future__ import annotations

from vendorfake.conformance import CHECKS, expected_skips, load_manifest
from vendorfake.conformance.registry import manifest_of_registry


def test_the_registry_matches_the_committed_manifest() -> None:
    """Removing or renaming a contract must show up as a diff, not as silence."""
    assert manifest_of_registry() == load_manifest()


def test_check_ids_are_unique_and_ordered() -> None:
    ids = [spec.id for spec in CHECKS]
    assert ids == sorted(set(ids))


def test_every_check_states_what_it_asserts() -> None:
    """A contract nobody can read is a contract nobody can argue with."""
    silent = [spec.id for spec in CHECKS if not spec.asserts.strip()]
    assert not silent


def test_expected_skips_name_registered_checks() -> None:
    registered = {spec.id for spec in CHECKS}
    assert set(expected_skips()) <= registered
