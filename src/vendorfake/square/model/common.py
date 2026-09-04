"""Turning a Pydantic validation failure into the error this vendor publishes.

An absent field and an empty field are the same failure: every required
string uses ``min_length=1``, so ``missing`` and ``string_too_short`` map
onto one ``missing_field`` kind; everything else is ``invalid_value``.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = ["MISSING_ERROR_TYPES", "unit_error_from_validation", "validate_body"]

_M = TypeVar("_M", bound=BaseModel)

MISSING_ERROR_TYPES = frozenset({"missing", "string_too_short"})
"""Pydantic error types meaning "you did not send this field"."""


def _field_path(loc: tuple[int | str, ...]) -> str | None:
    """Pydantic's location tuple as ``field``, e.g. ``order.line_items[0].quantity``. JUDGMENT:
    Square documents no array notation for ``Error.field``
    (https://developer.squareup.com/reference/square/objects/Error)."""
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
    """The first validation failure only, so a consumer sees a stable sequence fixing one field at
    a time."""
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
    """Validate ``body`` against ``model``, raising the vendor's error
    instead of ``ValidationError``."""
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise unit_error_from_validation(exc) from exc
