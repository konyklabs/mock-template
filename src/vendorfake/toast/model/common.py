"""Turning a Pydantic validation failure into the error this vendor publishes.

FOR: letting a surface state its request shape as a model and still answer
with the vendor's own error vocabulary, so that "clientId is required" is a
``missing_field`` naming ``clientId`` rather than a Pydantic report.

INVARIANT: **an absent field and an empty field are the same failure.** Every
required string in a request model is spelled ``min_length=1`` and this module
maps Pydantic's ``missing`` and ``string_too_short`` onto the one
``missing_field`` kind. Everything else is ``invalid_value``.

CHASSIS: this module is byte-for-byte the Clover package's ``model/common.py``
with the vendor name changed. The cross-vendor extraction into ``core`` is
konyklabs/roadmap#35; when it lands, this file goes and the import moves.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = ["MISSING_ERROR_TYPES", "unit_error_from_validation", "validate_body"]

_M = TypeVar("_M", bound=BaseModel)

MISSING_ERROR_TYPES = frozenset({"missing", "string_too_short"})


def _field_path(loc: tuple[int | str, ...]) -> str | None:
    if not loc:
        return None
    rendered = ""
    for part in loc:
        if isinstance(part, int):
            rendered += f"[{part}]"
        elif rendered:
            rendered += f".{part}"
        else:
            rendered = str(part)
    return rendered or None


def unit_error_from_validation(exc: ValidationError) -> UnitError:
    """The first validation failure, as this vendor's error."""
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
    """Validate ``body`` against ``model``, raising the vendor's error instead."""
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise unit_error_from_validation(exc) from exc
