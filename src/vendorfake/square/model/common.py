"""Turning a Pydantic validation failure into the error this vendor publishes.

FOR: letting a surface state its request shape as a model and still answer with
the vendor's own error vocabulary, so that "grant_type is required" is a
``missing_field`` naming ``grant_type`` rather than a Pydantic report a
consumer has never seen.

INVARIANT: **an absent field and an empty field are the same failure.** The
reference's ``requireString`` rejects both with ``missing_field`` -- ``typeof v
!== 'string' || v.length === 0`` -- and a form-encoded body reaches that branch
constantly, because ``client_id=`` parses to the empty string rather than to a
missing key. Every request model here therefore spells a required string as
``min_length=1``, and this module maps Pydantic's ``missing`` and
``string_too_short`` onto the one kind, so the two spellings of "you did not
send it" cannot drift apart.

Everything else -- a wrong type, a value out of range -- is ``invalid_value``,
which is the same split the reference draws by hand.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = ["MISSING_ERROR_TYPES", "unit_error_from_validation", "validate_body"]

_M = TypeVar("_M", bound=BaseModel)

MISSING_ERROR_TYPES = frozenset({"missing", "string_too_short"})
"""Pydantic error types that mean "you did not send this field".

``string_too_short`` is in the set because a required string is declared with
``min_length=1``; see the module docstring.
"""


def _field_path(loc: tuple[int | str, ...]) -> str | None:
    """Pydantic's location tuple as the dot path Square puts in ``field``."""
    if not loc:
        return None
    return ".".join(str(part) for part in loc)


def unit_error_from_validation(exc: ValidationError) -> UnitError:
    """The first validation failure, as this vendor's error.

    The *first*, not all of them: Square's ``errors`` array can carry several,
    but the reference raises on the first failing field and a consumer fixing
    one field at a time sees the same sequence either way. Reporting only the
    first also keeps the error body identical whichever content type carried
    the request, which a form-encoded body would otherwise perturb by ordering
    keys differently.
    """
    first = exc.errors()[0]
    field = _field_path(tuple(first.get("loc", ())))
    if first.get("type") in MISSING_ERROR_TYPES:
        return UnitError(
            UnitErrorKind.MISSING_FIELD,
            detail=f"{field} is required." if field else "A required field is missing.",
            field=field,
        )
    return UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f"{field}: {first.get('msg', 'is not valid')}." if field else str(first.get("msg", "")),
        field=field,
    )


def validate_body(model: type[_M], body: Any) -> _M:
    """Validate ``body`` against ``model``, raising the vendor's error instead.

    The one call site pattern for every request model in this package, so that
    no surface has to remember to catch :class:`ValidationError`.
    """
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise unit_error_from_validation(exc) from exc
