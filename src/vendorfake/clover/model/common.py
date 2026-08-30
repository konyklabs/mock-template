"""Turning a Pydantic validation failure into the error this vendor publishes.

FOR: letting a surface state its request shape as a model and still answer
with the vendor's own error vocabulary, so that "client_id is required" is a
``missing_field`` naming ``client_id`` -- carried in the ``message`` and the
``unit_error.field`` sidecar -- rather than a Pydantic report a consumer has
never seen.

INVARIANT: **an absent field and an empty field are the same failure.** A
form-encoded body parses ``client_id=`` to the empty string rather than to a
missing key, so every required string in a request model is spelled
``min_length=1`` and this module maps Pydantic's ``missing`` and
``string_too_short`` onto the one ``missing_field`` kind. Everything else is
``invalid_value``. Same rule, same reasoning, as the Square package's
``model/common.py`` -- the cross-vendor dedup of this module is filed
separately.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = ["MISSING_ERROR_TYPES", "unit_error_from_validation", "validate_body"]

_M = TypeVar("_M", bound=BaseModel)

MISSING_ERROR_TYPES = frozenset({"missing", "string_too_short"})
"""Pydantic error types that mean "you did not send this field"."""


def _field_path(loc: tuple[int | str, ...]) -> str | None:
    """Pydantic's location tuple as the path this vendor puts in ``field``.

    Indexes in brackets (``lineItems[0].price``), dots between names --
    matching the paths the surfaces build by hand, so one logical field never
    has two spellings. Clover documents no field-path notation at all, so the
    convention is this project's, shared with the Square package.
    """
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
    """The first validation failure, as this vendor's error.

    The first only: a consumer fixing one field at a time sees the same
    sequence either way, and the body stays identical whichever content type
    carried the request.
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

    The one call-site pattern for every request model in this package, so that
    no surface has to remember to catch :class:`ValidationError`.
    """
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise unit_error_from_validation(exc) from exc
