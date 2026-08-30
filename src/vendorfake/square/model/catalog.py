"""Merchant reference data on the wire: the merchant, locations and catalog objects.

FOR: projecting the entity kinds an order points at into the documented Square
JSON, so that a consumer priming its own fixtures from ``GET /v2/merchants``,
``GET /v2/locations`` and ``GET /v2/catalog/list`` gets the shapes its SDK
deserialises.

INVARIANT: **an absent optional emits no key**, as everywhere else in this
package -- every projection here goes through the core's ``compact()``. A
location with no phone number omits ``phone_number``; it does not send
``null``. See :mod:`vendorfake.square.entities` for why that is a rule rather
than an accident of the language.

Shapes from
https://developer.squareup.com/reference/square/objects/Location and
https://developer.squareup.com/reference/square/catalog-api/retrieve-catalog-object
(both fetched 2026-08-25).

WHY THESE TAKE THE RAW ENTITY AND NOT THE TYPED VIEW
----------------------------------------------------
``created_at`` on a location and ``updated_at`` on a catalog object are the
*store's* timestamps, written by ``Collection.insert``/``update``, not fields
this vendor models -- which is right, because they mean "when this unit learned
about it" and a vendor that carried its own copy would have two answers. The
typed readers in :mod:`vendorfake.square.entities` therefore do not expose
them, and these functions take the entity mapping so that the one place that
needs both views has both. The typed view is derived here rather than by the
caller so that no surface has to remember to pass two representations of one
row.

A catalog ITEM nests its variations, matching Square's RetrieveCatalogObject
example. ``version`` is Square's catalog version -- a millisecond-epoch-shaped
int64 that a seller's catalog carries -- and deliberately *not* the store's
entity version, which counts mutations of this unit's copy.
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
    """One stored merchant as Square's ``Merchant`` JSON.

    Field set and order from
    https://developer.squareup.com/reference/square/objects/Merchant:
    ``id, business_name, country, language_code, currency, status,
    main_location_id, created_at``.

    ``main_location_id`` is resolved by the caller rather than stored, because
    Square defines it as "The ID of the main Location for this merchant" and
    the seed document has no such field -- the first seeded location is the
    main one. JUDGMENT, recorded on :func:`~vendorfake.square.surface.directory.main_location_of`.
    """
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
    """One stored catalog object, with an ITEM's variations nested inside it.

    ``catalog`` is every stored object, which is how an ITEM finds its
    variations. Passing the whole collection rather than a pre-built index is
    the reference's shape and is right at this size; an index would be a second
    thing to keep in step with the store for a saving no consumer can measure
    against a seeded scenario.
    """
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
    """The ``name`` attribute a catalog query searches: ``item_data.name`` for
    an ITEM, ``item_variation_data.name`` for an ITEM_VARIATION."""
    obj = CatalogObjectEntity.from_entity(entity)
    return obj.variation_name if obj.is_variation else obj.item_name


# ---------------------------------------------------------------------------
# Requests. Strict, ``extra="ignore"``, for the reasons
# :mod:`vendorfake.square.model.order` gives: a value the reference would have
# silently coerced is refused naming the field, and a field this unit does not
# model does not fail the request.
# ---------------------------------------------------------------------------

_REQUEST = ConfigDict(extra="ignore", frozen=True, strict=True)


class CatalogPrefixQueryRequest(BaseModel):
    """``query.prefix_query``: "The query filter to return the search result
    whose named attribute values are prefixed by the specified attribute value."
    https://developer.squareup.com/reference/square/objects/CatalogQueryPrefix
    """

    model_config = _REQUEST

    attribute_name: str = Field(min_length=1)
    attribute_prefix: str = Field(min_length=1)


class CatalogExactQueryRequest(BaseModel):
    """``query.exact_query``: the named attribute equals the value exactly.
    https://developer.squareup.com/reference/square/objects/CatalogQueryExact
    """

    model_config = _REQUEST

    attribute_name: str = Field(min_length=1)
    attribute_value: str = Field(min_length=1)


class CatalogQueryRequest(BaseModel):
    """``query``. Square documents eleven query kinds; this unit answers the
    two a consumer uses to find an item or a modifier by name. See the SHRINK
    in :mod:`vendorfake.square.surface.catalog`.
    https://developer.squareup.com/reference/square/objects/CatalogQuery
    """

    model_config = _REQUEST

    prefix_query: CatalogPrefixQueryRequest | None = None
    exact_query: CatalogExactQueryRequest | None = None


class SearchCatalogObjectsRequest(BaseModel):
    """``POST /v2/catalog/search``.
    https://developer.squareup.com/reference/square/catalog-api/search-catalog-objects
    """

    model_config = _REQUEST

    cursor: str | None = None
    #: "If this is unspecified, the operation returns objects of all the top
    #: level types at the version of the Square API used to make the request."
    object_types: list[str] | None = None
    #: "If `true`, deleted objects will be included in the results."
    include_deleted_objects: bool = False
    #: "If `true`, the response will include additional objects that are
    #: related to the requested objects."
    include_related_objects: bool = False
    #: "Return only objects modified after this timestamp, in RFC 3339 format."
    begin_time: str | None = None
    query: CatalogQueryRequest | None = None
    #: "The limit is advisory - the implementation may return more or fewer
    #: results. If the supplied limit is negative, zero, or is higher than the
    #: maximum limit of 1,000, it will be ignored."
    limit: int | None = None


class CatalogItemVariationDataRequest(BaseModel):
    """``item_variation_data`` on an ITEM_VARIATION.
    https://developer.squareup.com/reference/square/objects/CatalogItemVariation

    ``item_id`` may name the enclosing item's temporary id, as Square's own
    upsert example does; the surface resolves it. ``pricing_type`` defaults
    from the presence of ``price_money`` -- "FIXED_PRICING" when a price is
    sent -- which is the reading of "The item variation's price, if fixed
    pricing is used" that needs no extra rule.
    """

    model_config = _REQUEST

    item_id: str | None = None
    name: str | None = None
    pricing_type: str | None = None
    price_money: MoneyRequest | None = None


class CatalogItemDataRequest(BaseModel):
    """``item_data`` on an ITEM, with its nested variations.
    https://developer.squareup.com/reference/square/objects/CatalogItem
    """

    model_config = _REQUEST

    name: str | None = None
    description: str | None = None
    variations: list[CatalogObjectRequest] | None = None


class CatalogObjectRequest(BaseModel):
    """``object`` on UpsertCatalogObject, and each entry of
    ``item_data.variations``.
    https://developer.squareup.com/reference/square/objects/CatalogObject

    ``version`` is "The version of the object. When updating an object, the
    version supplied must match the version in the database, otherwise the
    write will be rejected as conflicting." Optional here, required by the
    surface on an update, so the sentence a caller reads names the field.
    """

    model_config = _REQUEST

    type: str = Field(min_length=1)
    id: str = Field(min_length=1)
    version: int | None = None
    present_at_all_locations: bool = True
    item_data: CatalogItemDataRequest | None = None
    item_variation_data: CatalogItemVariationDataRequest | None = None


class UpsertCatalogObjectRequest(BaseModel):
    """``POST /v2/catalog/object``. ``idempotency_key`` is required and read
    by the kernel; declared here so the body is documented in one place.
    https://developer.squareup.com/reference/square/catalog-api/upsert-catalog-object
    """

    model_config = _REQUEST

    idempotency_key: str | None = None
    object: CatalogObjectRequest
