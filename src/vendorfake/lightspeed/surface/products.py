"""The Products tag: the version-cursor list, one product, and the three writes.

DOCUMENTED, five of the tag's eight operations:

============================================  ====================  =================
``GET /products``                             ``ListProducts``      ``products:read``
``GET /products/{product_id}``                ``GetProductByID``    ``products:read``
``POST /products``                            ``CreateProduct``     ``products:write``
``PUT /products/{product_id}``                ``UpdateProduct``     ``products:write``
``DELETE /products/{product_id}``             ``DeleteProduct``     ``products:write``
============================================  ====================  =================

The three left out are ``UploadImage`` (multipart, and images are excluded by
issue #94), ``GetPriceBooksForProduct`` (the Price Books tag is excluded) and
``DeleteProductFamily`` -- see ``capabilities.py`` for each.

**Every write fires ``product.update``**, which is the journal's doing rather
than this module's: the mapper keys on the ``products`` collection
(``events.py``). A delete fires it too, because a delete here is a soft delete
and the entity is still there to carry.

THE LIST'S RESOURCE FILTERS OVERRIDE EVERYTHING, and that is documented in the
parameters' own descriptions: ``sku`` is "Allows loading a product by one of
its SKUs. Note that all other query params are ignored if this is provided",
and ``name``/``family_name`` say the same. So this handler branches: with
``sku`` or ``name`` present it does not read ``after``/``before``/
``page_size``/``deleted``/``include_images`` at all -- not even to validate
them, because "ignored" means ignored. Without them it is an ordinary
version-cursor page.

``name`` SELECTS A FAMILY, not a product: "This query typically retrieves all
products from the product family with the provided name." So a name match on
any product returns every product sharing that product's ``family_id``, which
is how a caller finds a parent and its variants in one request.
``family_name`` is documented as an alias for it and is treated as one.

VARIANTS ARE INLINE, and this is why ``POST /products`` answers an ARRAY of
ids. ``ProductCreateBody.variants`` is a list of ``ProductAddVariantPayload``,
and the create's own response schema describes ``data`` as "An array containing
the ID or IDs of the new products" -- plural, because a body with three variants
creates a parent and three children. The Variant Attributes TAG (five
operations that manage the attribute vocabulary) is deliberately not
implemented; see ``capabilities.py``.

THE TWO GENERATED MEMBERS, both JUDGMENT. ``Product`` requires ``handle`` and
``sku`` and ``ProductCreateBody`` requires neither, so:

* ``handle`` defaults to a slug of the name (``"Ridgeline Tee"`` ->
  ``"ridgeline-tee"``), which is what the vendor's own example shows for
  ``"Bravo"`` -> ``"bravo"``;
* ``sku`` defaults to the handle. Nothing in the specification says a SKU is
  unique -- the ``sku`` filter is documented as loading "a product by one of
  its SKUs" -- so no uniqueness is enforced and no 409 is invented for it.

THE UPDATE'S RESPONSE IS JUDGMENT. ``PUT /products/{product_id}`` declares its
200 as a bare ``{"type": "object"}`` with no properties. This unit answers
``{"data": {...the product...}}``, the single-record envelope every other
``Get``/``Update`` on this API uses, because a caller who has just written
wants to read back what was written.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import (
    HandlerArgs,
    ReplyInit,
    Route,
    UnitContext,
    UnitError,
    UnitErrorKind,
)
from vendorfake.lightspeed.config import SCOPE_PRODUCTS_READ, SCOPE_PRODUCTS_WRITE
from vendorfake.lightspeed.entities import COL, InventoryEntity, ProductEntity
from vendorfake.lightspeed.model.common import validate_body
from vendorfake.lightspeed.model.product import (
    PRODUCT_CODE_TYPES,
    ProductCodeIn,
    ProductCreateBody,
    ProductInventoryIn,
    ProductSupplierIn,
    ProductUpdateBody,
    ProductVariantIn,
    derive_prices,
    product_document,
    project_product,
    refuse_both_prices,
)
from vendorfake.lightspeed.model.scalars import decimal_text
from vendorfake.lightspeed.paths import (
    CREATE_PRODUCT,
    DELETE_PRODUCT,
    GET_PRODUCT_BY_ID,
    LIST_PRODUCTS,
    UPDATE_PRODUCT,
)
from vendorfake.lightspeed.surface.common import BEARER_AUTH, LightspeedDeps, stamp_version, wire_time
from vendorfake.lightspeed.surface.outlets import VERSION_CURSOR_PAGINATION
from vendorfake.lightspeed.versioning import envelope, read_list_query, select, single, version_of

__all__ = [
    "CAPABILITY",
    "FAMILY_NAME_PARAM",
    "INCLUDE_IMAGES_PARAM",
    "NAME_PARAM",
    "SKU_PARAM",
    "LightspeedProductsSurface",
    "product_routes",
    "slugify",
]

CAPABILITY = "products"

SKU_PARAM = "sku"
NAME_PARAM = "name"
FAMILY_NAME_PARAM = "family_name"
INCLUDE_IMAGES_PARAM = "include_images"
"""The four documented ``GET /products`` parameters beyond the version four.
``includes[]`` is the fifth and is accepted and unmodelled -- its one supported
value is ``composite_products`` and this unit has no composites."""

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """``"Ridgeline Tee"`` -> ``"ridgeline-tee"``. The default ``handle``."""
    return _SLUG_STRIP.sub("-", name.strip().lower()).strip("-")


class LightspeedProductsSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: LightspeedDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        # No `example_body`: the vendor's published example lives on
        # `POST /sales`, which is the repeatable, create-shaped,
        # event-producing route conformance C18 needs. `POST /products` would
        # serve as well, and publishing a second one would only make which of
        # the two C18 drives depend on route order. See surface/sales.py.
        return (
            Route(
                method="GET",
                path=LIST_PRODUCTS,
                capability=CAPABILITY,
                handler=self.list_products,
                auth=BEARER_AUTH,
                scopes=(SCOPE_PRODUCTS_READ,),
                pagination=VERSION_CURSOR_PAGINATION,
                operation_id="ListProducts",
                summary="Products, ascending by version; after/before/page_size/deleted, or sku/name to override.",
            ),
            Route(
                method="GET",
                path=GET_PRODUCT_BY_ID,
                capability=CAPABILITY,
                handler=self.get_product,
                auth=BEARER_AUTH,
                scopes=(SCOPE_PRODUCTS_READ,),
                operation_id="GetProductByID",
                summary="One product by id.",
            ),
            Route(
                method="POST",
                path=CREATE_PRODUCT,
                capability=CAPABILITY,
                handler=self.create_product,
                auth=BEARER_AUTH,
                scopes=(SCOPE_PRODUCTS_WRITE,),
                operation_id="CreateProduct",
                summary='Create a product and any inline variants; answers {"data": [id, ...]}.',
            ),
            Route(
                method="PUT",
                path=UPDATE_PRODUCT,
                capability=CAPABILITY,
                handler=self.update_product,
                auth=BEARER_AUTH,
                scopes=(SCOPE_PRODUCTS_WRITE,),
                operation_id="UpdateProduct",
                summary="Update a product from the two-block ProductUpdate21Request body; 404 or 422 otherwise.",
            ),
            Route(
                method="DELETE",
                path=DELETE_PRODUCT,
                capability=CAPABILITY,
                handler=self.delete_product,
                auth=BEARER_AUTH,
                scopes=(SCOPE_PRODUCTS_WRITE,),
                operation_id="DeleteProduct",
                summary="Soft-delete a product: deleted_at is set and it leaves the list unless deleted=true.",
            ),
        )

    # -- reads --------------------------------------------------------------

    def list_products(self, args: HandlerArgs) -> ReplyInit:
        rows = args.ctx.store.collection(COL.products).all()
        sku = args.query(SKU_PARAM)
        family_name = args.query(NAME_PARAM) or args.query(FAMILY_NAME_PARAM)
        if sku is not None:
            chosen = _ascending(row for row in rows if row.get("sku") == sku and row.get("deleted_at") is None)
            return json_(envelope([project_product(row) for row in chosen]))
        if family_name is not None:
            families = {
                str(row.get("family_id"))
                for row in rows
                if row.get("name") == family_name and row.get("deleted_at") is None
            }
            chosen = _ascending(
                row for row in rows if str(row.get("family_id")) in families and row.get("deleted_at") is None
            )
            return json_(envelope([project_product(row) for row in chosen]))
        include_images = _bool_query(args, INCLUDE_IMAGES_PARAM, default=True)
        page = select(rows, read_list_query(args))
        return json_(envelope([project_product(row, include_images=include_images) for row in page]))

    def get_product(self, args: HandlerArgs) -> ReplyInit:
        include_images = _bool_query(args, INCLUDE_IMAGES_PARAM, default=True)
        return json_(single(project_product(self._require(args), include_images=include_images)))

    # -- writes -------------------------------------------------------------

    def create_product(self, args: HandlerArgs) -> ReplyInit:
        body = validate_body(ProductCreateBody, args.body())
        refuse_both_prices(body.price_excluding_tax, body.price_including_tax)
        for index, variant in enumerate(body.variants):
            refuse_both_prices(variant.price_excluding_tax, variant.price_including_tax, where=f"variants[{index}].")
        self._check_outlets(args.ctx, body)

        family_id = self._deps.ids.product_family()
        parent_id = self._deps.ids.product()
        created: list[str] = [parent_id]
        products = args.ctx.store.collection(COL.products)

        handle = body.handle or slugify(body.name)
        sku = body.sku or handle
        net, gross = derive_prices(
            body.price_excluding_tax,
            body.price_including_tax,
            tax_rate=self._deps.config.product_tax_rate,
        )
        parent = ProductEntity(
            id=parent_id,
            name=body.name,
            handle=handle,
            sku=sku,
            family_id=family_id,
            price_excluding_tax=net,
            price_including_tax=gross,
            supply_price=_optional_decimal(body.supply_price, "supply_price"),
            # A parent that has variants does not itself hold stock: the
            # variants do. JUDGMENT -- the schema carries `has_inventory` on
            # every product and never says which one of a family owns it.
            has_inventory=not body.variants,
            has_variants=bool(body.variants),
            variant_name=body.name,
            variant_count=len(body.variants) or None,
            document=product_document(
                active=body.is_active,
                description=body.description,
                brand_id=body.brand_id,
                supplier_id=body.supplier_id,
                supplier_code=body.supplier_code,
                product_type_id=body.product_type_id,
                product_category_id=body.product_category_id,
                source=body.source,
                source_id=body.source_id,
                source_variant_id=body.source_variant_id,
                loyalty_amount=_optional_decimal(body.loyalty_amount, "loyalty_amount", allow_none=True),
                account_code_sale=body.account_code_sale,
                account_code_purchase=body.account_code_purchase,
                weight=_optional_decimal(body.weight, "weight", allow_none=True),
                weight_unit=body.weight_unit,
                height=_optional_decimal(body.height, "height", allow_none=True),
                width=_optional_decimal(body.width, "width", allow_none=True),
                length=_optional_decimal(body.length, "length", allow_none=True),
                dimensions_unit=body.dimensions_unit,
                tag_ids=list(body.tag_ids),
                attributes=[{"name": row.wire_name, "value": row.value} for row in body.attribute_rows],
                product_codes=self._product_codes(body.product_codes, "product_codes"),
                product_suppliers=self._product_suppliers(body.product_suppliers, parent_id, "product_suppliers"),
                outlet_taxes=[dict(row) for row in body.outlet_taxes],
            ),
            object_version=self._deps.versions.bump(),
        )
        products.insert(parent.to_entity(), {"operation_id": "CreateProduct"})
        self._insert_inventory(args.ctx, parent_id, body.inventory, "inventory")

        for index, variant in enumerate(body.variants):
            child_id = self._create_variant(args.ctx, parent=parent, family_id=family_id, variant=variant, index=index)
            created.append(child_id)
        # DOCUMENTED: "An array containing the ID or IDs of the new products",
        # and a documented 200 rather than a 201 -- CreateProduct declares only
        # `"200": {"description": "OK"}`, unlike CreateCustomer's 201.
        return json_({"data": created})

    def update_product(self, args: HandlerArgs) -> ReplyInit:
        # The body first, then the 404: a malformed body is malformed whichever
        # product it named. See `surface/registers.py::open_register`.
        body = validate_body(ProductUpdateBody, args.body())
        refuse_both_prices(body.details.price_excluding_tax, body.details.price_including_tax, where="details.")
        stored = self._require(args)
        product = ProductEntity.from_entity(stored)
        deps = self._deps
        common = body.common
        details = body.details
        prices: tuple[str, str] | None = None
        if details.price_excluding_tax is not None or details.price_including_tax is not None:
            prices = derive_prices(
                details.price_excluding_tax,
                details.price_including_tax,
                tax_rate=deps.config.product_tax_rate,
            )
        supply_price = (
            None if details.supply_price is None else decimal_text(details.supply_price, field="supply_price")
        )
        suppliers = common.product_suppliers if common.product_suppliers is not None else details.product_suppliers
        supplier_rows = (
            None if suppliers is None else self._product_suppliers(suppliers, product.id, "product_suppliers")
        )
        code_rows = (
            None if details.product_codes is None else self._product_codes(details.product_codes, "product_codes")
        )

        def mutate(draft: dict[str, Any]) -> None:
            document = dict(draft.get("document") or {})
            if common.name is not None:
                draft["name"] = common.name
            if common.description is not None:
                document["description"] = common.description
            if common.brand_id is not None:
                document["brand_id"] = common.brand_id
            if common.product_category_id is not None:
                document["product_category_id"] = common.product_category_id
            if common.account_code_sale is not None:
                document["account_code_sale"] = common.account_code_sale
            if common.account_code_purchase is not None:
                document["account_code_purchase"] = common.account_code_purchase
            if common.tag_ids is not None:
                document["tag_ids"] = list(common.tag_ids)
            if common.track_inventory is not None:
                # `common.track_inventory` is the update body's name for the
                # response schema's `has_inventory`; nothing else in the
                # document connects the two, so the mapping is JUDGMENT.
                draft["has_inventory"] = common.track_inventory
            if details.is_active is not None:
                document["active"] = details.is_active
            if prices is not None:
                draft["price_excluding_tax"], draft["price_including_tax"] = prices
            if supply_price is not None:
                draft["supply_price"] = supply_price
            if supplier_rows is not None:
                document["product_suppliers"] = supplier_rows
            if code_rows is not None:
                document["product_codes"] = code_rows
            if details.outlet_taxes is not None:
                document["outlet_taxes"] = [dict(row) for row in details.outlet_taxes]
            for key, raw in (
                ("loyalty_amount", details.loyalty_amount),
                ("weight", details.weight),
                ("height", details.height),
                ("width", details.width),
                ("length", details.length),
            ):
                if raw is not None:
                    document[key] = decimal_text(raw, field=key)
            for key, text in (
                ("weight_unit", details.weight_unit),
                ("dimensions_unit", details.dimensions_unit),
            ):
                if text is not None:
                    document[key] = text
            draft["document"] = document
            stamp_version(draft, deps)

        updated = args.ctx.store.collection(COL.products).update(
            product.id, mutate, meta={"operation_id": "UpdateProduct"}
        )
        return json_(single(project_product(updated)))

    def delete_product(self, args: HandlerArgs) -> ReplyInit:
        stored = self._require(args)
        product = ProductEntity.from_entity(stored)
        deleted_at = wire_time(args.ctx.clock)
        deps = self._deps

        def mutate(draft: dict[str, Any]) -> None:
            draft["deleted_at"] = deleted_at
            stamp_version(draft, deps)

        args.ctx.store.collection(COL.products).update(product.id, mutate, meta={"operation_id": "DeleteProduct"})
        # A SOFT delete: the row keeps its id and its version, gains a
        # `deleted_at`, and leaves every list that does not ask for
        # `deleted=true`. That is what the `deleted` list parameter --
        # "Indicates whether deleted items should be included in the
        # response" -- means: a hard delete would leave nothing for it to
        # include. It is also what lets `product.update` carry the tombstone.
        #
        # 200 with no body: DeleteProduct declares `"200": {"description":
        # "OK"}` and no content, exactly like DeleteWebhook.
        return ReplyInit(status=200, text="")

    # -- helpers ------------------------------------------------------------

    def _create_variant(
        self,
        ctx: UnitContext,
        *,
        parent: ProductEntity,
        family_id: str,
        variant: ProductVariantIn,
        index: int,
    ) -> str:
        """One child product, inserted, with its opening stock."""
        where = f"variants[{index}]."
        child_id = self._deps.ids.product()
        options = [
            {"name": str(row.get("attribute_id", "")), "value": str(row.get("value", ""))}
            for row in variant.variant_definitions
        ]
        suffix = " / ".join(row["value"] for row in options if row["value"])
        net, gross = derive_prices(
            variant.price_excluding_tax,
            variant.price_including_tax,
            tax_rate=self._deps.config.product_tax_rate,
            where=where,
        )
        if variant.price_excluding_tax is None and variant.price_including_tax is None:
            # A variant that names no price inherits the family's, which is
            # what a size variant of one shirt costs. JUDGMENT.
            net, gross = parent.price_excluding_tax, parent.price_including_tax
        parent_document = dict(parent.document)
        child = ProductEntity(
            id=child_id,
            name=parent.name,
            handle=parent.handle,
            sku=variant.sku or f"{parent.sku}-{index + 1}",
            family_id=family_id,
            price_excluding_tax=net,
            price_including_tax=gross,
            supply_price=_optional_decimal(variant.supply_price, f"{where}supply_price"),
            has_inventory=True,
            has_variants=False,
            variant_parent_id=parent.id,
            variant_name=f"{parent.name} / {suffix}" if suffix else parent.name,
            variant_options=options,
            document=product_document(
                active=bool(parent_document.get("active", True)),
                description=parent_document.get("description"),
                brand_id=parent_document.get("brand_id"),
                supplier_id=parent_document.get("supplier_id"),
                supplier_code=variant.supplier_code or parent_document.get("supplier_code"),
                product_type_id=parent_document.get("product_type_id"),
                product_category_id=parent_document.get("product_category_id"),
                source=parent_document.get("source"),
                tag_ids=list(parent_document.get("tag_ids", [])),
                attributes=list(parent_document.get("attributes", [])),
                product_codes=self._product_codes(variant.product_codes, f"{where}product_codes"),
                product_suppliers=self._product_suppliers(
                    variant.product_suppliers, child_id, f"{where}product_suppliers"
                ),
                outlet_taxes=[dict(row) for row in variant.outlet_taxes],
                weight=_optional_decimal(variant.weight, f"{where}weight", allow_none=True),
                weight_unit=variant.weight_unit,
                height=_optional_decimal(variant.height, f"{where}height", allow_none=True),
                width=_optional_decimal(variant.width, f"{where}width", allow_none=True),
                length=_optional_decimal(variant.length, f"{where}length", allow_none=True),
                dimensions_unit=variant.dimensions_unit,
            ),
            object_version=self._deps.versions.bump(),
        )
        ctx.store.collection(COL.products).insert(child.to_entity(), {"operation_id": "CreateProduct"})
        self._insert_inventory(ctx, child_id, variant.inventory, f"{where}inventory")
        return child_id

    def _insert_inventory(
        self,
        ctx: UnitContext,
        product_id: str,
        rows: Sequence[ProductInventoryIn],
        field: str,
    ) -> None:
        """The opening stock a create declared, one ``Inventory`` row per outlet.

        Each insert fires ``inventory.update``: the level went from "no record"
        to a number, which is a level change.
        """
        inventory = ctx.store.collection(COL.inventory)
        for index, row in enumerate(rows):
            entity = InventoryEntity(
                id=self._deps.ids.inventory(),
                product_id=product_id,
                outlet_id=row.outlet_id,
                current_inventory_level=decimal_text(
                    row.current_amount, field=f"{field}[{index}].current_amount", allow_negative=True
                ),
                reorder_amount=_optional_decimal(
                    row.reorder_amount, f"{field}[{index}].reorder_amount", allow_none=True
                ),
                reorder_point=_optional_decimal(row.reorder_point, f"{field}[{index}].reorder_point", allow_none=True),
                # DOCUMENTED enum, and a reorder rule the caller has only half
                # stated: a `reorder_point` with no method is FIXED, which is
                # the method whose two members are exactly the two this payload
                # carries. JUDGMENT.
                reorder_method=None if row.reorder_point is None else "FIXED",
                object_version=self._deps.versions.bump(),
            )
            inventory.insert(entity.to_entity(), {"operation_id": "CreateProduct"})

    def _check_outlets(self, ctx: UnitContext, body: ProductCreateBody) -> None:
        """Every ``outlet_id`` an opening-stock payload names must exist.

        A 422 rather than a silently orphaned inventory row: the row would be
        invisible to every read, since both inventory reads are scoped by
        outlet or by product and neither would ever return it.
        """
        outlets = {str(row["id"]) for row in ctx.store.collection(COL.outlets).all()}
        groups: list[tuple[str, Sequence[ProductInventoryIn]]] = [("inventory", body.inventory)]
        groups.extend(
            (f"variants[{index}].inventory", variant.inventory) for index, variant in enumerate(body.variants)
        )
        for field, rows in groups:
            for index, row in enumerate(rows):
                if row.outlet_id not in outlets:
                    raise UnitError(
                        UnitErrorKind.INVALID_VALUE,
                        detail=f"{field}[{index}].outlet_id {row.outlet_id!r} is not an outlet of this retailer.",
                        field=f"{field}[{index}].outlet_id",
                    )

    def _product_codes(self, rows: Sequence[ProductCodeIn], field: str) -> list[dict[str, Any]]:
        """``ProductCode`` rows with the ``id`` the schema requires and the
        caller never sends, and the documented ``type`` enum checked."""
        out: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            if row.type is not None and row.type not in PRODUCT_CODE_TYPES:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"{field}[{index}].type must be one of: {', '.join(PRODUCT_CODE_TYPES)}.",
                    field=f"{field}[{index}].type",
                    info={"supplied": row.type},
                )
            entry: dict[str, Any] = {"id": self._deps.ids.product_code(), "code": row.code}
            if row.type is not None:
                entry["type"] = row.type
            out.append(entry)
        return out

    def _product_suppliers(
        self, rows: Sequence[ProductSupplierIn], product_id: str, field: str
    ) -> list[dict[str, Any]]:
        """``ProductSupplier`` rows, with ``id`` minted and ``product_id``
        filled in: both are required on the response schema and neither is
        something a caller can know at the moment it writes the body."""
        out: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            out.append(
                {
                    "id": self._deps.ids.product_supplier(),
                    "product_id": product_id,
                    "supplier_id": row.supplier_id,
                    "supplier_name": row.supplier_name,
                    "code": row.code,
                    "price": None
                    if row.price is None
                    else float(Decimal(decimal_text(row.price, field=f"{field}[{index}].price"))),
                }
            )
        return out

    def _require(self, args: HandlerArgs) -> dict[str, Any]:
        product_id = args.params["product_id"]
        stored = args.ctx.store.collection(COL.products).get(product_id)
        if stored is None:
            raise UnitError(UnitErrorKind.NOT_FOUND, detail=f"Product {product_id} was not found.", field="product_id")
        return stored


def _ascending(rows: Any) -> list[dict[str, Any]]:
    """The rows a resource filter chose, in the one order this API has."""
    return sorted((dict(row) for row in rows), key=version_of)


def _bool_query(args: HandlerArgs, name: str, *, default: bool) -> bool:
    raw = args.query(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes"}


def _optional_decimal(value: object, field: str, *, allow_none: bool = False) -> Any:
    """``decimal_text`` for a member that may be absent. ``allow_none`` says
    whether absence stays absent (a nullable document member) or becomes
    ``"0"`` (``supply_price``, which the examples print as ``0``)."""
    if value is None:
        return None if allow_none else "0"
    return decimal_text(value, field=field)


def product_routes(deps: LightspeedDeps) -> tuple[Route, ...]:
    return LightspeedProductsSurface(deps).routes()
