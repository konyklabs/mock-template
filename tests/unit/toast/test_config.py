"""Configuration: documented values, labelled judgments, strict keys."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vendorfake.toast.config import (
    DEFAULT_SCOPES,
    DOCUMENTED_SCOPES,
    JUDGMENT_SCOPES,
    READ_ONLY_SCOPES,
    ToastConfig,
    resolve_toast_config,
)


def test_the_documented_token_lifetime_example_is_the_default() -> None:
    """`"expiresIn": 19168` -- the one number the login page prints."""
    config = ToastConfig()
    assert config.access_token_ttl_s == 19168
    assert config.access_token_ttl_ms == 19168 * 1000
    assert config.access_token_ttl.total_seconds() == 19168


def test_the_scopes_split_into_documented_and_judgment_and_the_default_is_both() -> None:
    assert set(DOCUMENTED_SCOPES) == {
        "orders:read",
        "orders.channel:read",
        "orders.payments:write",
        "guest.pi:read",
        "delivery_info.address:read",
    }
    assert not set(DOCUMENTED_SCOPES) & set(JUDGMENT_SCOPES)
    assert set(DEFAULT_SCOPES) == set(DOCUMENTED_SCOPES) | set(JUDGMENT_SCOPES)
    assert set(READ_ONLY_SCOPES) < set(DEFAULT_SCOPES)
    assert not any(scope.endswith(":write") for scope in READ_ONLY_SCOPES)
    assert "guest.pi:read" not in READ_ONLY_SCOPES


def test_the_switches_default_on_and_insecure_callbacks_off() -> None:
    config = ToastConfig()
    assert config.error_sidecar is True
    assert config.retry_after_header is True
    assert config.allow_insecure_callbacks is False
    assert config.low_quantity_threshold == 5.0


def test_an_unknown_key_is_refused_at_resolve_and_at_merge() -> None:
    with pytest.raises(ValidationError):
        resolve_toast_config({"client_idd": "x"})
    with pytest.raises(ValidationError):
        ToastConfig().merged_with({"nope": 1})


def test_merge_lets_the_profile_win_and_keeps_the_rest() -> None:
    merged = ToastConfig().merged_with({"client_id": "other", "access_token_ttl_s": 60})
    assert merged.client_id == "other"
    assert merged.access_token_ttl_s == 60
    assert merged.client_secret == ToastConfig().client_secret


def test_a_non_positive_ttl_is_refused() -> None:
    with pytest.raises(ValidationError):
        ToastConfig(access_token_ttl_s=0)
