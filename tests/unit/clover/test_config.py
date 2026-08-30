"""The configuration defaults, their documented values, and the merge rule."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from vendorfake.clover.config import DEFAULT_PERMISSIONS, CloverConfig, resolve_clover_config


def test_the_documented_and_judgment_ttls() -> None:
    config = CloverConfig()
    # DOCUMENTED: "OAuth access_tokens expire in 30 minutes."
    assert config.access_token_ttl == timedelta(minutes=30)
    # JUDGMENT: read off the documented example's ~366-day spread.
    assert config.refresh_token_ttl == timedelta(days=365)
    # JUDGMENT: Clover documents no code expiry at all.
    assert config.authorization_code_ttl == timedelta(minutes=10)


def test_the_fake_credentials_look_fake_and_the_app_id_keeps_the_13_char_shape() -> None:
    config = CloverConfig()
    assert len(config.client_id) == 13
    assert config.client_id.isupper()
    assert "unit" in config.client_id.lower()
    assert "unit" in config.client_secret


def test_the_permission_set_defaults_and_is_inherited_from_config() -> None:
    assert CloverConfig().permissions == DEFAULT_PERMISSIONS
    assert "ORDERS_R" in DEFAULT_PERMISSIONS
    assert "MERCHANT_R" in DEFAULT_PERMISSIONS


def test_an_unknown_key_in_the_vendor_block_is_refused_naming_it() -> None:
    """extra="forbid": {"cleint_id": ...} must be a startup failure, never a
    default silently left in place."""
    with pytest.raises(ValidationError) as caught:
        resolve_clover_config({"cleint_id": "TYPO"})
    assert "cleint_id" in str(caught.value)


def test_merged_with_lays_the_profile_over_the_base_and_still_refuses_junk() -> None:
    base = CloverConfig()
    merged = base.merged_with({"client_id": "OTHERAPP12345", "access_token_ttl_ms": 60_000})
    assert merged.client_id == "OTHERAPP12345"
    assert merged.access_token_ttl == timedelta(minutes=1)
    assert merged.client_secret == base.client_secret  # untouched fields survive
    with pytest.raises(ValidationError):
        base.merged_with({"nonsense": True})


def test_ttls_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        resolve_clover_config({"access_token_ttl_ms": 0})
