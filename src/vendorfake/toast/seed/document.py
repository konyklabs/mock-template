"""The seed document's schema, as a model rather than as a cast.

FOR: stating what a scenario file may contain, so that a typo in one is a
startup failure naming the field instead of a unit that starts with an empty
world and answers 404 to every read.

INVARIANT: **a scenario is validated before a single entity is inserted.**
Every model here sets ``extra="forbid"``; hydration parses the whole document
first.

Keys: the top level is snake_case like every JSON this project publishes; the
entity documents use Toast's own camelCase field names, so a documented
example pastes straight in.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = [
    "SeedDocument",
    "SeedRestaurant",
    "SeedRestaurantGeneral",
    "SeedToken",
    "parse_seed_document",
]

_SEED = ConfigDict(extra="forbid")


class SeedRestaurantGeneral(BaseModel):
    """The documented ``general`` block (toast-restaurants-api.yaml)."""

    model_config = _SEED

    name: str = Field(min_length=1)
    locationName: str | None = None
    #: "3 or 4 letter code".
    locationCode: str | None = Field(default=None, min_length=3, max_length=4)
    description: str | None = None
    #: IANA zone.
    timeZone: str = "UTC"
    #: 0-12, documented.
    closeoutHour: int = Field(default=0, ge=0, le=12)
    managementGroupGuid: str | None = None
    #: ISO-4217.
    currencyCode: str = "USD"


class SeedRestaurant(BaseModel):
    model_config = _SEED

    guid: str = Field(min_length=1)
    general: SeedRestaurantGeneral
    location: dict[str, Any] = Field(default_factory=dict)
    urls: dict[str, Any] = Field(default_factory=dict)
    schedules: dict[str, Any] = Field(default_factory=dict)
    delivery: dict[str, Any] = Field(default_factory=dict)
    onlineOrdering: dict[str, Any] = Field(default_factory=dict)
    prepTimes: dict[str, Any] = Field(default_factory=dict)


class SeedToken(BaseModel):
    """A pre-minted bearer. ``scopes`` defaults to the client's full set; the
    expiration is stamped at hydrate from the configured TTL."""

    model_config = _SEED

    id: str = Field(min_length=1)
    access_token: str = Field(min_length=1)
    scopes: list[str] | None = None
    client_id: str | None = None


class SeedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    comment: list[str] | None = Field(default=None, alias="_comment")
    restaurant: SeedRestaurant
    tokens: list[SeedToken] = Field(default_factory=list)


def _refuse(path: str, message: str) -> UnitError:
    return UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f"The seed document is not valid at {path}: {message}.",
        field="seed",
        info={"path": path},
    )


def parse_seed_document(raw: object) -> SeedDocument:
    """Validate a seed document, raising the vendor's ``invalid_value`` on the
    ``seed`` field with the offending path in the detail."""
    try:
        return SeedDocument.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        path = ".".join(str(part) for part in first.get("loc", ())) or "seed"
        raise _refuse(path, str(first.get("msg", "invalid"))) from exc
