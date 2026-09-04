"""Turning a Pydantic validation failure into the error this vendor publishes,
so "clientId is required" is a ``missing_field`` naming ``clientId`` rather
than a Pydantic report.

INVARIANT: an absent field and an empty field are the same failure -- every
required string is spelled ``min_length=1``, and this module maps Pydantic's
``missing``/``string_too_short`` onto ``missing_field``; everything else is
``invalid_value`` (konyklabs/roadmap#35 tracks its cross-vendor extraction).
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = ["MISSING_ERROR_TYPES", "unit_error_from_validation", "validate_body", "validate_items"]

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


def validate_items(model: type[_M], body: Any, *, what: str) -> list[_M]:
    """Validate a non-empty JSON array of ``model`` (several Toast routes take
    a bare array); a failure names ``[i].field``, so a consumer sees which
    element was refused."""
    if not isinstance(body, list) or not body:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"The request body must be a non-empty array of {what}.",
            field="body",
        )
    out: list[_M] = []
    for index, row in enumerate(body):
        try:
            out.append(model.model_validate(row))
        except ValidationError as exc:
            err = unit_error_from_validation(exc)
            field = f"[{index}].{err.field}" if err.field else f"[{index}]"
            raise UnitError(err.kind, detail=f"{field}: {err.detail}", field=field) from exc
    return out
