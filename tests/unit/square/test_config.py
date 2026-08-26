"""The vendor config: documented defaults, and two refusals the reference lacks."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from vendorfake.square.config import DEFAULT_SCOPES, SQUARE_API_VERSION, SquareConfig, resolve_square_config


def test_the_documented_ttls() -> None:
    config = SquareConfig()
    assert config.access_token_ttl == timedelta(days=30)
    assert config.short_lived_ttl == timedelta(hours=24)
    assert config.pkce_refresh_ttl == timedelta(days=90)
    assert config.authorization_code_ttl == timedelta(minutes=5)


def test_ttls_stay_millisecond_integers_on_the_wire() -> None:
    """So a profile document remains diff-comparable, while code reads the
    timedelta properties where the unit is unmistakable."""
    assert SquareConfig().access_token_ttl_ms == 2_592_000_000


def test_the_documented_default_scope_set() -> None:
    assert DEFAULT_SCOPES == (
        "MERCHANT_PROFILE_READ",
        "PAYMENTS_READ",
        "SETTLEMENTS_READ",
        "BANK_ACCOUNTS_READ",
    )
    assert SquareConfig().default_scopes == DEFAULT_SCOPES


def test_the_api_version_is_the_one_the_unit_claims() -> None:
    assert SquareConfig().api_version == SQUARE_API_VERSION


def test_an_unknown_key_is_refused_by_name() -> None:
    with pytest.raises(ValidationError) as caught:
        resolve_square_config({"aplication_id": "typo"})
    assert "aplication_id" in str(caught.value)


def test_a_misspelled_environment_is_refused_rather_than_becoming_sandbox() -> None:
    """The reference writes `x === 'Production' ? 'Production' : 'Sandbox'`, so
    the spelling every shell script uses quietly means the opposite of what the
    operator asked for -- and the square-environment delivery header then lies."""
    with pytest.raises(ValidationError):
        resolve_square_config({"environment": "production"})
    assert resolve_square_config({"environment": "Production"}).environment == "Production"


def test_a_non_positive_ttl_is_refused() -> None:
    with pytest.raises(ValidationError):
        resolve_square_config({"access_token_ttl_ms": 0})


def test_merged_with_lets_the_profile_win_and_still_validates() -> None:
    base = resolve_square_config({"api_version": "2020-01-01", "error_sidecar": False})
    merged = base.merged_with({"api_version": "2030-12-31"})
    assert merged.api_version == "2030-12-31"
    assert merged.error_sidecar is False
    with pytest.raises(ValidationError):
        base.merged_with({"nope": 1})
