"""The seed document, as a model: what a profile's ``seed`` file may say.

FOR: refusing a hand-edited scenario that is wrong *by name* at startup,
rather than loading half of it. ``extra="forbid"`` throughout, unlike the
request models: a seed is this project's own document, so an unknown key is
a typo and not a documented field this build happens not to model.

The only section at this commit is ``merchant`` -- the identity the authorize
redirect carries (``merchant_id``) and the record ``GET /v3/merchants/{mId}``
answers. PR E adds the rest.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = ["SeedAddress", "SeedDocument", "SeedMerchant", "SeedOwner", "parse_seed_document"]

_SEED = ConfigDict(extra="forbid", frozen=True)


class SeedOwner(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    name: str | None = None


class SeedAddress(BaseModel):
    model_config = _SEED

    address1: str | None = None
    address2: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    country: str | None = None


class SeedMerchant(BaseModel):
    """One merchant. ``currency`` is what an order created without one is
    denominated in (JUDGMENT on the orders surface)."""

    model_config = _SEED

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    currency: str = "USD"
    owner: SeedOwner | None = None
    address: SeedAddress | None = None


class SeedDocument(BaseModel):
    model_config = _SEED

    merchant: SeedMerchant


def parse_seed_document(raw: object) -> SeedDocument:
    """Validate a seed document, raising the vendor's ``invalid_value`` on
    the ``seed`` field with Pydantic's own path in the detail."""
    try:
        return SeedDocument.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        path = ".".join(str(part) for part in first.get("loc", ())) or "seed"
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"The seed document is not valid at {path}: {first.get('msg', 'invalid')}.",
            field="seed",
            info={"path": path},
        ) from exc
