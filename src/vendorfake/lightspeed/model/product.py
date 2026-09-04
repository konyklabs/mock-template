"""The product wire shape, and the two documented bodies that write it.

DOCUMENTED SCHEMAS (``api-2026-07``): ``Product`` (57 members, 21 of them
required), ``ProductCollection`` (``data`` + ``version``), ``ProductResponse``
(``data``), ``ProductCreateBody`` (``POST /products``) and
``ProductUpdate21Request`` (``PUT /products/{product_id}``).

THE CREATE AND THE UPDATE ARE NOT THE SAME SHAPE, which is the first surprise
this surface holds for a consumer. ``ProductCreateBody`` is a flat object with
``name`` as its only required member. ``ProductUpdate21Request`` is
``{"common": {...}, "details": {...}}`` -- a two-block document with a
different member list and no required member at all. Both are reproduced as
declared; neither is normalised into the other.

**Both prices may not be sent together.** The create operation's own
``requestBody.description`` says so verbatim: "**Note**: You cannot include
both ``price_including_tax`` and ``price_excluding_tax``." That is the one
documented 422 on this surface, and :func:`refuse_both_prices` is where it is
raised. The other price is derived from the one supplied -- see
:data:`PRICE_DERIVATION_NOTE`.

WHAT THIS PACKAGE DOES NOT RESOLVE, and why the wire shows an empty object for
it. ``Product`` embeds a ``BrandSample``, a ``SupplierSample`` and a
``ProductTypeSample``, each ``{id, name, version}``. The Brands, Suppliers and
Product Types tags are outside issue #94's scoped surface, so this unit has no
brand, supplier or product-type entity to resolve an id against. The three
sample objects are therefore always ``{}`` -- which is exactly what the
vendor's own ``GET /products`` example prints for a product with none
(``"brand": {}``, ``"supplier": {}``, ``"type": {}``) -- while ``brand_id``,
``supplier_id`` and ``product_type_id`` carry through whatever the caller set.
Recorded in ``capabilities.py`` under ``product-reference-tags``.

THE ATTRIBUTE INCONSISTENCY is the vendor's, not this package's.
``Product.attributes`` is ``array[ProductAttribute]`` where a
``ProductAttribute`` is ``{name, value}``; ``ProductCreateBody.attributes``
``$ref``s ``Attribute``, which is a SINGLE object of ``{key, value}`` -- a
different shape AND a different member name for the same thing, in the same
document. This module accepts either shape on the way in and answers the
documented ``{name, value}`` array on the way out, and says so here rather than
silently.

IMAGES. ``POST /products/{id}/actions/image_upload`` and the Product Images tag
are excluded by issue #94, so ``images`` and ``skuImages`` are always empty and
``image_url``/``image_thumbnail_url`` are stand-in placeholders on a reserved
example host -- never the vendor's own CDN URLs, which a fake has no business
pointing a consumer at. ``include_images=false`` drops all four, which is what
the documented parameter is for.

KEY ORDER. The projection emits members in alphabetical order, because that is
the order every response example in the specification prints them in.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.core.util.json import compact
from vendorfake.lightspeed.entities import ProductEntity
from vendorfake.lightspeed.model.scalars import decimal_text, wire_instant, wire_number

__all__ = [
    "DIMENSIONS_UNITS",
    "IMAGE_PLACEHOLDER_THUMB",
    "IMAGE_PLACEHOLDER_URL",
    "PRICE_DERIVATION_NOTE",
    "PRODUCT_CODE_TYPES",
    "WEIGHT_UNITS",
    "ProductAttributeIn",
    "ProductCodeIn",
    "ProductCreateBody",
    "ProductInventoryIn",
    "ProductSupplierIn",
    "ProductUpdateBody",
    "ProductVariantIn",
    "derive_prices",
    "product_document",
    "project_product",
    "refuse_both_prices",
]

IMAGE_PLACEHOLDER_URL = "https://images.example/product/no-image-standard.png"
IMAGE_PLACEHOLDER_THUMB = "https://images.example/product/no-image-thumb.png"
"""Stand-ins for ``image_url``/``image_thumbnail_url``. The vendor's example
prints its own CDN's placeholder; a fake that shipped that value would point a
consumer's client at a real host it has no business reaching. ``images.example``
is reserved by RFC 2606 and resolves nowhere. JUDGMENT."""

PRICE_DERIVATION_NOTE = (
    "The create body may carry price_including_tax or price_excluding_tax and not both (the operation's own "
    "requestBody description). The other is derived with the unit's product_tax_rate, because the Taxes tag "
    "is outside issue #94's scoped surface and there is no tax entity to read a real rate from. JUDGMENT."
)

PRODUCT_CODE_TYPES: tuple[str, ...] = ("CUSTOM", "EAN", "ISBN", "ITF", "JAN", "UPC")
"""``ProductCode.type``'s documented enum."""

WEIGHT_UNITS: tuple[str, ...] = ("CT", "G", "OZ", "LB", "KG")
DIMENSIONS_UNITS: tuple[str, ...] = ("IN", "CM", "MM", "YD")
"""``Product.weight_unit`` and ``Product.dimensions_unit``, as documented."""

_REQUEST = ConfigDict(extra="ignore", frozen=True)


class ProductAttributeIn(BaseModel):
    """One attribute on the way in, in EITHER documented spelling.

    ``ProductCreateBody`` says ``{key, value}`` and ``Product`` says
    ``{name, value}``; both are accepted and the response uses the latter. See
    the module docstring.
    """

    model_config = _REQUEST

    key: str | None = None
    name: str | None = None
    value: str = ""

    @property
    def wire_name(self) -> str:
        return self.name or self.key or ""


class ProductCodeIn(BaseModel):
    """``ProductCode`` minus its ``id``, which the caller never supplies."""

    model_config = _REQUEST

    code: str = Field(min_length=1)
    type: str | None = None


class ProductSupplierIn(BaseModel):
    """``ProductSupplier`` as a caller sends it. ``id`` and ``product_id`` are
    required on the response schema and are minted/filled by the surface."""

    model_config = _REQUEST

    supplier_id: str | None = None
    supplier_name: str | None = None
    code: str | None = None
    price: float | int | str | None = None


class ProductInventoryIn(BaseModel):
    """``ProductAddInventoryPayload``: the opening stock for one outlet."""

    model_config = _REQUEST

    outlet_id: str = Field(min_length=1)
    current_amount: float | int | str
    reorder_amount: float | int | str | None = None
    reorder_point: float | int | str | None = None


class ProductVariantIn(BaseModel):
    """``ProductAddVariantPayload``: one child of the product being created.

    ``variant_definitions`` is ``array[VariantAttribute]``, which is
    ``{attribute_id, value}``. The Variant Attributes tag -- the only place an
    ``attribute_id`` could be resolved to a display name -- is deferred, so the
    ``variant_options`` this becomes carries the ``attribute_id`` verbatim as
    its ``name``. Recorded in ``capabilities.py`` under ``variant-attributes``.
    """

    model_config = _REQUEST

    sku: str | None = None
    price_excluding_tax: float | int | str | None = None
    price_including_tax: float | int | str | None = None
    supply_price: float | int | str | None = None
    supplier_code: str | None = None
    variant_definitions: list[dict[str, Any]] = Field(default_factory=list)
    product_codes: list[ProductCodeIn] = Field(default_factory=list)
    product_suppliers: list[ProductSupplierIn] = Field(default_factory=list)
    inventory: list[ProductInventoryIn] = Field(default_factory=list)
    outlet_taxes: list[dict[str, Any]] = Field(default_factory=list)
    weight: float | int | str | None = None
    weight_unit: str | None = None
    height: float | int | str | None = None
    width: float | int | str | None = None
    length: float | int | str | None = None
    dimensions_unit: str | None = None


class ProductCreateBody(BaseModel):
    """``ProductCreateBody``. ``name`` is its one required member."""

    model_config = _REQUEST

    name: str = Field(min_length=1)
    handle: str | None = None
    sku: str | None = None
    description: str | None = None
    is_active: bool = True
    brand_id: str | None = None
    supplier_id: str | None = None
    supplier_code: str | None = None
    product_type_id: str | None = None
    product_category_id: str | None = None
    source: str | None = None
    source_id: str | None = None
    source_variant_id: str | None = None
    price_excluding_tax: float | int | str | None = None
    price_including_tax: float | int | str | None = None
    supply_price: float | int | str | None = None
    loyalty_amount: float | int | str | None = None
    account_code_sale: str | None = None
    account_code_purchase: str | None = None
    weight: float | int | str | None = None
    weight_unit: str | None = None
    height: float | int | str | None = None
    width: float | int | str | None = None
    length: float | int | str | None = None
    dimensions_unit: str | None = None
    tag_ids: list[str] = Field(default_factory=list)
    attributes: list[ProductAttributeIn] | ProductAttributeIn | None = None
    product_codes: list[ProductCodeIn] = Field(default_factory=list)
    product_suppliers: list[ProductSupplierIn] = Field(default_factory=list)
    outlet_taxes: list[dict[str, Any]] = Field(default_factory=list)
    inventory: list[ProductInventoryIn] = Field(default_factory=list)
    variants: list[ProductVariantIn] = Field(default_factory=list)

    @property
    def attribute_rows(self) -> list[ProductAttributeIn]:
        """``attributes`` as a list whichever documented shape arrived."""
        if self.attributes is None:
            return []
        if isinstance(self.attributes, ProductAttributeIn):
            return [self.attributes]
        return list(self.attributes)


class ProductUpdateCommon(BaseModel):
    """``ProductUpdate21Request.common`` -- the members shared by every product
    in a family."""

    model_config = _REQUEST

    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    brand_id: str | None = None
    product_category_id: str | None = None
    account_code_sale: str | None = None
    account_code_purchase: str | None = None
    tag_ids: list[str] | None = None
    track_inventory: bool | None = None
    product_suppliers: list[ProductSupplierIn] | None = None


class ProductUpdateDetails(BaseModel):
    """``ProductUpdate21Request.details`` -- the members that belong to this one
    product (or variant)."""

    model_config = _REQUEST

    is_active: bool | None = None
    price_excluding_tax: float | int | str | None = None
    price_including_tax: float | int | str | None = None
    supply_price: float | int | str | None = None
    loyalty_amount: float | int | str | None = None
    product_codes: list[ProductCodeIn] | None = None
    product_suppliers: list[ProductSupplierIn] | None = None
    outlet_taxes: list[dict[str, Any]] | None = None
    weight: float | int | str | None = None
    weight_unit: str | None = None
    height: float | int | str | None = None
    width: float | int | str | None = None
    length: float | int | str | None = None
    dimensions_unit: str | None = None


class ProductUpdateBody(BaseModel):
    """``ProductUpdate21Request``: two blocks, neither of them required.

    An entirely empty body is legal -- the schema declares no ``required``
    list -- and updates nothing but the version, which is what a caller asking
    for no change has asked for.
    """

    model_config = _REQUEST

    common: ProductUpdateCommon = Field(default_factory=ProductUpdateCommon)
    details: ProductUpdateDetails = Field(default_factory=ProductUpdateDetails)


def refuse_both_prices(excluding: object, including: object, *, where: str = "") -> None:
    """The documented 422: the two price members are mutually exclusive.

    ``where`` prefixes the field name for a variant payload, so a caller is
    told *which* element of ``variants`` was refused.
    """
    if excluding is None or including is None:
        return
    field = f"{where}price_including_tax" if where else "price_including_tax"
    raise UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=(
            "You cannot include both price_including_tax and price_excluding_tax. "
            "(POST /products, requestBody description.)"
        ),
        field=field,
    )


def derive_prices(
    excluding: object,
    including: object,
    *,
    tax_rate: str,
    where: str = "",
) -> tuple[str, str]:
    """The pair ``(price_excluding_tax, price_including_tax)`` as decimal text.

    Exactly one of the two may be supplied (:func:`refuse_both_prices` has
    already run); the other is derived with ``tax_rate``. Supplying neither is
    a free product, which the schema permits -- ``ProductCreateBody`` requires
    only ``name``.
    """
    rate = Decimal(tax_rate)
    prefix = where
    if excluding is not None:
        net = Decimal(decimal_text(excluding, field=f"{prefix}price_excluding_tax"))
        gross = net * (1 + rate)
    elif including is not None:
        gross = Decimal(decimal_text(including, field=f"{prefix}price_including_tax"))
        net = gross / (1 + rate)
    else:
        net = gross = Decimal(0)
    return (
        decimal_text(net, field=f"{prefix}price_excluding_tax"),
        decimal_text(gross, field=f"{prefix}price_including_tax"),
    )


def product_document(
    *,
    active: bool,
    description: str | None = None,
    brand_id: str | None = None,
    supplier_id: str | None = None,
    supplier_code: str | None = None,
    product_type_id: str | None = None,
    product_category_id: str | None = None,
    source: str | None = None,
    source_id: str | None = None,
    source_variant_id: str | None = None,
    button_order: int = 0,
    loyalty_amount: str | None = None,
    account_code_sale: str | None = None,
    account_code_purchase: str | None = None,
    weight: str | None = None,
    weight_unit: str | None = None,
    height: str | None = None,
    width: str | None = None,
    length: str | None = None,
    dimensions_unit: str | None = None,
    tag_ids: list[str] | None = None,
    attributes: list[dict[str, Any]] | None = None,
    product_codes: list[dict[str, Any]] | None = None,
    product_suppliers: list[dict[str, Any]] | None = None,
    outlet_taxes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The pass-through half of a stored product, in one fixed key order.

    ``ProductEntity`` types the members this package reads and carries the rest
    here; see the note at the head of ``entities.py``. Written as a keyword
    function rather than a dict literal at each call site so the create path,
    the variant path and the seed loader cannot disagree about a key's name.
    """
    return compact(
        {
            "active": active,
            "description": description,
            "brand_id": brand_id,
            "supplier_id": supplier_id,
            "supplier_code": supplier_code,
            "product_type_id": product_type_id,
            "product_category_id": product_category_id,
            "source": source,
            "source_id": source_id,
            "source_variant_id": source_variant_id,
            "button_order": button_order,
            "loyalty_amount": loyalty_amount,
            "account_code_sale": account_code_sale,
            "account_code_purchase": account_code_purchase,
            "weight": weight,
            "weight_unit": weight_unit,
            "height": height,
            "width": width,
            "length": length,
            "dimensions_unit": dimensions_unit,
            "tag_ids": list(tag_ids or []),
            "attributes": [dict(row) for row in attributes or []],
            "product_codes": [dict(row) for row in product_codes or []],
            "product_suppliers": [dict(row) for row in product_suppliers or []],
            "outlet_taxes": [dict(row) for row in outlet_taxes or []],
        }
    )


def project_product(entity: Mapping[str, Any], *, include_images: bool = True) -> dict[str, Any]:
    """The documented ``Product`` document, members in alphabetical order.

    ``active`` and ``is_active`` are BOTH emitted: the schema declares
    ``active`` (required) and every response example prints both, so a consumer
    reading either finds it.
    """
    product = ProductEntity.from_entity(entity)
    document = dict(product.document)
    projected: dict[str, Any] = {
        "active": bool(document.get("active", True)),
        "attributes": [dict(row) for row in document.get("attributes", [])],
        "brand": {},
        "button_order": document.get("button_order", 0),
        "categories": [],
        "created_at": wire_instant(_opt_text(entity.get("created_at"))),
        "customizations": [],
        "family_id": product.family_id,
        "handle": product.handle,
        "has_inventory": product.has_inventory,
        "has_variants": product.has_variants,
        "id": product.id,
        "is_active": bool(document.get("active", True)),
        "is_composite": False,
        "name": product.name,
        "packaging": {"breaks_into": [], "made_from": []},
        "price_excluding_tax": wire_number(product.price_excluding_tax),
        "price_including_tax": wire_number(product.price_including_tax),
        "product_codes": [dict(row) for row in document.get("product_codes", [])],
        "product_suppliers": [dict(row) for row in document.get("product_suppliers", [])],
        "sku": product.sku,
        "supplier": {},
        "supply_price": wire_number(product.supply_price),
        "tag_ids": list(document.get("tag_ids", [])),
        "type": {},
        "updated_at": wire_instant(_opt_text(entity.get("updated_at"))),
        "variant_options": [dict(row) for row in product.variant_options],
        "version": product.object_version,
    }
    if include_images:
        projected["image_thumbnail_url"] = IMAGE_PLACEHOLDER_THUMB
        projected["image_url"] = IMAGE_PLACEHOLDER_URL
        projected["images"] = []
        projected["skuImages"] = []
    for key in (
        "account_code_purchase",
        "account_code_sale",
        "brand_id",
        "description",
        "dimensions_unit",
        "height",
        "length",
        "loyalty_amount",
        "outlet_taxes",
        "product_category_id",
        "product_type_id",
        "source",
        "source_id",
        "source_variant_id",
        "supplier_code",
        "supplier_id",
        "weight",
        "weight_unit",
        "width",
    ):
        value = document.get(key)
        if value is None or (isinstance(value, list) and not value):
            continue
        projected[key] = wire_number(value) if key in _NUMERIC_DOCUMENT_KEYS else value
    if product.deleted_at is not None:
        projected["deleted_at"] = product.deleted_at
    if product.variant_parent_id is not None:
        projected["variant_parent_id"] = product.variant_parent_id
    if product.variant_name is not None:
        projected["variant_name"] = product.variant_name
    if product.variant_count is not None:
        projected["variant_count"] = product.variant_count
    return dict(sorted(projected.items()))


_NUMERIC_DOCUMENT_KEYS = frozenset({"height", "length", "loyalty_amount", "weight", "width"})


def _opt_text(value: Any) -> str | None:
    return None if value is None else str(value)
