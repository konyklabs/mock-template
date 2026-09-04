"""Merchant reference data on the wire: the merchant, locations and catalog objects.

INVARIANT: an absent optional emits no key, everywhere in this package, via
the core's ``compact()``. These projections take the raw entity mapping
rather than the typed view because store timestamps (``created_at``,
``updated_at``) belong to the row, not to the vendor model. A catalog ITEM
nests its variations; ``version`` is Square's catalog version, deliberately
not the store's entity version.

DOCUMENTED: shapes from
https://developer.squareup.com/reference/square/objects/Location and
https://developer.squareup.com/reference/square/catalog-api/retrieve-catalog-object.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.util.json import compact
from vendorfake.square.entities import CatalogObjectEntity, LocationEntity, MerchantEntity
from vendorfake.square.model.order import MoneyRequest

__all__ = [
    "ITEM",
    "ITEM_VARIATION",
    "CatalogExactQueryRequest",
    "CatalogItemDataRequest",
    "CatalogItemVariationDataRequest",
    "CatalogObjectRequest",
    "CatalogPrefixQueryRequest",
    "CatalogQueryRequest",
    "SearchCatalogObjectsRequest",
    "UpsertCatalogObjectRequest",
    "catalog_name_of",
    "project_catalog_object",
    "project_location",
    "project_merchant",
]

ITEM = "ITEM"
ITEM_VARIATION = "ITEM_VARIATION"
"""The two ``CatalogObjectType`` values this unit models. Square publishes
more; see the SHRINK in :mod:`vendorfake.square.surface.directory`."""


def project_merchant(entity: Mapping[str, Any], main_location_id: str | None) -> dict[str, Any]:
    """One stored merchant as Square's ``Merchant`` JSON. https://developer.squareup.com/reference/square/objects/Merchant
    JUDGMENT: ``main_location_id`` is resolved by the caller as the first seeded location, recorded on
    :func:`~vendorfake.square.surface.directory.main_location_of`."""
    merchant = MerchantEntity.from_entity(entity)
    created_at = entity.get("created_at")
    return compact(
        {
            "id": merchant.id,
            "business_name": merchant.business_name,
            "country": merchant.country,
            "language_code": merchant.language_code,
            "currency": merchant.currency,
            "status": merchant.status,
            "main_location_id": main_location_id,
            "created_at": None if created_at is None else str(created_at),
        }
    )


def project_location(entity: Mapping[str, Any]) -> dict[str, Any]:
    """One stored location as Square's ``Location`` JSON."""
    location = LocationEntity.from_entity(entity)
    created_at = entity.get("created_at")
    return compact(
        {
            "id": location.id,
            "name": location.name,
            "address": None if location.address is None else dict(location.address),
            "timezone": location.timezone,
            "capabilities": list(location.capabilities),
            "status": location.status,
            "created_at": None if created_at is None else str(created_at),
            "merchant_id": location.merchant_id,
            "country": location.country,
            "language_code": location.language_code,
            "currency": location.currency,
            "phone_number": location.phone_number,
            "business_name": location.business_name,
            "type": location.type,
        }
    )


def project_catalog_object(
    entity: Mapping[str, Any],
    catalog: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """One stored catalog object, with an ITEM's variations nested inside it. ``catalog`` is every stored object,
    scanned linearly to find an ITEM's variations rather than through a maintained index."""
    obj = CatalogObjectEntity.from_entity(entity)
    updated_at = entity.get("updated_at")
    base: dict[str, Any] = {
        "type": obj.object_type,
        "id": obj.id,
        "updated_at": None if updated_at is None else str(updated_at),
        "version": obj.catalog_version,
        "is_deleted": obj.is_deleted,
        "present_at_all_locations": obj.present_at_all_locations,
    }
    if obj.object_type == ITEM:
        variations = [
            project_catalog_object(child, catalog)
            for child in catalog
            if child.get("object_type") == ITEM_VARIATION and child.get("item_id") == obj.id
        ]
        return compact(
            {
                **base,
                "item_data": compact(
                    {
                        "name": obj.item_name,
                        "description": obj.item_description,
                        "variations": variations,
                    }
                ),
            }
        )
    return compact(
        {
            **base,
            "item_variation_data": compact(
                {
                    "item_id": obj.item_id,
                    "name": obj.variation_name,
                    "pricing_type": obj.pricing_type,
                    "price_money": None if obj.price_money is None else obj.price_money.to_entity(),
                }
            ),
        }
    )


def catalog_name_of(entity: Mapping[str, Any]) -> str | None:
    """The ``name`` attribute a catalog query searches: ``item_data.name`` for an ITEM, ``item_variation_data.name`` for an ITEM_VARIATION."""
    obj = CatalogObjectEntity.from_entity(entity)
    return obj.variation_name if obj.is_variation else obj.item_name


# Requests. Strict, ``extra="ignore"``: an unrecognised value is refused naming the field; unmodeled fields don't fail.

_REQUEST = ConfigDict(extra="ignore", frozen=True, strict=True)


class CatalogPrefixQueryRequest(BaseModel):
    """``query.prefix_query``: attribute values prefixed by the given value.
    https://developer.squareup.com/reference/square/objects/CatalogQueryPrefix"""

    model_config = _REQUEST

    attribute_name: str = Field(min_length=1)
    attribute_prefix: str = Field(min_length=1)


class CatalogExactQueryRequest(BaseModel):
    """``query.exact_query``: the named attribute equals the value exactly.
    https://developer.squareup.com/reference/square/objects/CatalogQueryExact"""

    model_config = _REQUEST

    attribute_name: str = Field(min_length=1)
    attribute_value: str = Field(min_length=1)


class CatalogQueryRequest(BaseModel):
    """``query``. Square documents eleven query kinds; this unit answers two (find by name). See the SHRINK in
    :mod:`vendorfake.square.surface.catalog`. https://developer.squareup.com/reference/square/objects/CatalogQuery"""

    model_config = _REQUEST

    prefix_query: CatalogPrefixQueryRequest | None = None
    exact_query: CatalogExactQueryRequest | None = None


class SearchCatalogObjectsRequest(BaseModel):
    """``POST /v2/catalog/search``. https://developer.squareup.com/reference/square/catalog-api/search-catalog-objects"""

    model_config = _REQUEST

    cursor: str | None = None
    #: Unspecified returns objects of all top-level types at the API version used.
    object_types: list[str] | None = None
    #: Include deleted objects in the results.
    include_deleted_objects: bool = False
    #: Include objects related to the requested objects.
    include_related_objects: bool = False
    #: Only objects modified after this RFC 3339 timestamp.
    begin_time: str | None = None
    query: CatalogQueryRequest | None = None
    #: Advisory; ignored if negative, zero, or above the 1,000 max.
    limit: int | None = None


class CatalogItemVariationDataRequest(BaseModel):
    """``item_variation_data`` on an ITEM_VARIATION. https://developer.squareup.com/reference/square/objects/CatalogItemVariation
    ``pricing_type`` defaults to "FIXED_PRICING" when ``price_money`` is sent; ``item_id`` may name the enclosing
    item's temporary id, resolved by the surface."""

    model_config = _REQUEST

    item_id: str | None = None
    name: str | None = None
    pricing_type: str | None = None
    price_money: MoneyRequest | None = None


class CatalogItemDataRequest(BaseModel):
    """``item_data`` on an ITEM, with its nested variations. https://developer.squareup.com/reference/square/objects/CatalogItem"""

    model_config = _REQUEST

    name: str | None = None
    description: str | None = None
    variations: list[CatalogObjectRequest] | None = None


class CatalogObjectRequest(BaseModel):
    """``object`` on UpsertCatalogObject, and each entry of ``item_data.variations``.
    https://developer.squareup.com/reference/square/objects/CatalogObject
    ``version`` must match the stored version on update or the write is rejected as conflicting; optional here,
    required by the surface on update."""

    model_config = _REQUEST

    type: str = Field(min_length=1)
    id: str = Field(min_length=1)
    version: int | None = None
    present_at_all_locations: bool = True
    item_data: CatalogItemDataRequest | None = None
    item_variation_data: CatalogItemVariationDataRequest | None = None


class UpsertCatalogObjectRequest(BaseModel):
    """``POST /v2/catalog/object``; ``idempotency_key`` is required and read by the kernel.
    https://developer.squareup.com/reference/square/catalog-api/upsert-catalog-object"""

    model_config = _REQUEST

    idempotency_key: str | None = None
    object: CatalogObjectRequest
