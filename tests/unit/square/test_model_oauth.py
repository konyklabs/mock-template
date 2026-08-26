"""The OAuth request models, and the validation-to-error mapping under them.

Unit-level rather than through a running unit, because the interesting cases
are the ones a surface test cannot reach cheaply: what an empty string does,
what a wrong type does, and which of the two error kinds each becomes.
"""

from __future__ import annotations

import pytest

from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.square.model.common import unit_error_from_validation, validate_body
from vendorfake.square.model.oauth import (
    SQUARE_GRANT_TYPES,
    SUPPORTED_GRANT_TYPES,
    AuthorizationCodeGrant,
    ObtainTokenEnvelope,
    RefreshTokenGrant,
    RevokeTokenRequest,
    TokenResponse,
)


def caught(model: type, body: object) -> UnitError:
    with pytest.raises(UnitError) as raised:
        validate_body(model, body)  # type: ignore[arg-type]
    return raised.value


def test_a_missing_field_names_itself() -> None:
    error = caught(ObtainTokenEnvelope, {"client_id": "app"})
    assert error.kind is UnitErrorKind.MISSING_FIELD
    assert error.field == "grant_type"
    assert str(error) == "grant_type is required."


def test_an_empty_string_is_the_same_failure_as_an_absent_one() -> None:
    """A urlencoded `client_id=` parses to the empty string, and the
    reference's `requireString` rejects both with the same error. The two
    spellings of "you did not send it" must not drift apart."""
    absent = caught(ObtainTokenEnvelope, {"grant_type": "refresh_token"})
    empty = caught(ObtainTokenEnvelope, {"client_id": "", "grant_type": "refresh_token"})
    assert absent.kind is empty.kind is UnitErrorKind.MISSING_FIELD
    assert absent.field == empty.field == "client_id"


def test_a_wrong_type_is_an_invalid_value_not_a_missing_field() -> None:
    error = caught(ObtainTokenEnvelope, {"client_id": ["app"], "grant_type": "refresh_token"})
    assert error.kind is UnitErrorKind.INVALID_VALUE
    assert error.field == "client_id"


def test_booleans_coerce_because_a_form_body_carries_only_strings() -> None:
    """The deliberate exception to this build's strict-model rule, and the
    reason it is an exception: under strict validation `short_lived=true` over
    `application/x-www-form-urlencoded` could only ever be a type error."""
    assert validate_body(RefreshTokenGrant, {"refresh_token": "EQAAx", "short_lived": "true"}).short_lived
    assert validate_body(RefreshTokenGrant, {"refresh_token": "EQAAx", "short_lived": "1"}).short_lived
    assert not validate_body(RefreshTokenGrant, {"refresh_token": "EQAAx", "short_lived": "false"}).short_lived
    assert not validate_body(RefreshTokenGrant, {"refresh_token": "EQAAx"}).short_lived


def test_a_boolean_that_means_nothing_is_still_refused() -> None:
    """Coercion is not "accept anything": `short_lived=maybe` is a mistake, and
    reading it as false would be the reference's defect with a new spelling."""
    error = caught(RefreshTokenGrant, {"refresh_token": "EQAAx", "short_lived": "maybe"})
    assert error.kind is UnitErrorKind.INVALID_VALUE
    assert error.field == "short_lived"


def test_scopes_keeps_an_array_and_ignores_anything_else() -> None:
    assert validate_body(RefreshTokenGrant, {"refresh_token": "x", "scopes": ["A", "B"]}).scopes == ["A", "B"]
    assert validate_body(RefreshTokenGrant, {"refresh_token": "x", "scopes": "A B"}).scopes is None
    assert validate_body(RefreshTokenGrant, {"refresh_token": "x", "scopes": 7}).scopes is None


def test_unmodelled_parameters_are_ignored_rather_than_refused() -> None:
    """A consumer's OAuth client sends parameters this unit does not model.
    Refusing over one would fail on the encoding rather than on the thing under
    test, which is the same reasoning as accepting a form body at all."""
    grant = validate_body(AuthorizationCodeGrant, {"code": "sq0cgb-x", "session": "1", "state": "abc"})
    assert grant.code == "sq0cgb-x"


def test_the_revoke_selectors_are_optional_at_the_model_level() -> None:
    """Both rules -- one required, not both -- live in the surface, because each
    has its own documented error field and a model-level check would report the
    whole model rather than the field a consumer must fix."""
    request = validate_body(RevokeTokenRequest, {"client_id": "app"})
    assert request.access_token is None
    assert request.merchant_id is None
    assert request.revoke_only_access_token is False


def test_the_supported_grants_are_a_strict_subset_of_the_documented_ones() -> None:
    assert set(SUPPORTED_GRANT_TYPES) < set(SQUARE_GRANT_TYPES)
    assert "migration_token" not in SUPPORTED_GRANT_TYPES


def test_the_token_response_omits_an_absent_refresh_expiry() -> None:
    """ "Refresh tokens obtained using the code flow don't expire", so the key
    is absent rather than null -- a consumer writing `if "..." in body` takes
    the right branch."""
    code_flow = TokenResponse(
        access_token="EAAAx",
        expires_at="2026-09-24T00:00:00Z",
        merchant_id="MLQW2MYBY81PZ",
        refresh_token="EQAAx",
        short_lived=False,
    ).wire()
    assert "refresh_token_expires_at" not in code_flow
    assert code_flow["short_lived"] is False, "a false boolean is data, not absence"
    assert list(code_flow) == [
        "access_token",
        "token_type",
        "expires_at",
        "merchant_id",
        "refresh_token",
        "short_lived",
    ]

    pkce = TokenResponse(
        access_token="EAAAx",
        expires_at="2026-09-24T00:00:00Z",
        merchant_id="MLQW2MYBY81PZ",
        refresh_token="EQAAx",
        short_lived=False,
        refresh_token_expires_at="2026-11-23T00:00:00Z",
    ).wire()
    assert pkce["refresh_token_expires_at"] == "2026-11-23T00:00:00Z"


def test_the_response_model_is_strict() -> None:
    """A value on its way out of this unit is produced here, so a wrong type is
    a defect in this package and coercing it would hide one."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TokenResponse(
            access_token="EAAAx",
            expires_at="2026-09-24T00:00:00Z",
            merchant_id="M",
            refresh_token="EQAAx",
            short_lived="yes",  # type: ignore[arg-type]
        )


def test_the_mapping_reports_the_first_failure_only() -> None:
    """Reporting one keeps the error body identical whichever content type
    carried the request; a form body orders its keys differently."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as raised:
        ObtainTokenEnvelope.model_validate({})
    error = unit_error_from_validation(raised.value)
    assert error.field in {"client_id", "grant_type"}
    assert error.kind is UnitErrorKind.MISSING_FIELD
