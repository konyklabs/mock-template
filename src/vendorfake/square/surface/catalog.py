"""The Catalog surface beyond the listing: resolve one object, search by name or
by change time, and upsert.

Routes: ``GET /v2/catalog/object/{object_id}``, ``POST /v2/catalog/search``,
``POST /v2/catalog/object``
(https://developer.squareup.com/reference/square/catalog-api). All three share
the ``merchant-directory`` capability with ``ListCatalog`` in
:mod:`vendorfake.square.surface.directory`.

INVARIANT -- a rejected upsert changes nothing: every object in the request is
resolved and version-checked before the first write, and a rejected request
mints no temporary ids.

SHRINK (prototype) -- of Square's eleven ``CatalogQuery`` kinds only
``prefix_query`` and ``exact_query`` are answered, only on ``name``; the rest
are refused with ``invalid_value`` naming the key, rather than silently
ignored. ``include_related_objects`` answers a variation's parent ITEM only.
Upsert takes ``ITEM`` and ``ITEM_VARIATION``; batch upsert/delete are not
implemented.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import (
    HandlerArgs,
    IdempotencySpec,
    PaginationSpec,
    ReplyInit,
    Route,
    UnitError,
    UnitErrorKind,
)
from vendorfake.core.state.store import Collection, Entity
from vendorfake.core.util.json import compact
from vendorfake.square.entities import COL, CatalogObjectEntity, Money
from vendorfake.square.model.catalog import (
    ITEM,
    ITEM_VARIATION,
    CatalogObjectRequest,
    SearchCatalogObjectsRequest,
    UpsertCatalogObjectRequest,
    catalog_name_of,
    project_catalog_object,
)
from vendorfake.square.model.common import validate_body
from vendorfake.square.surface.common import SquareDeps, instant_ms
from vendorfake.square.surface.directory import CAPABILITY, CATALOG_DEFAULT_LIMIT, CATALOG_MAX_LIMIT

__all__ = [
    "CAPABILITY",
    "SEARCHABLE_ATTRIBUTE",
    "TEMPORARY_ID_PREFIX",
    "CatalogSurface",
    "catalog_routes",
]

SEARCHABLE_ATTRIBUTE = "name"
"""The one attribute a prefix or exact query may name here. See the SHRINK."""

TEMPORARY_ID_PREFIX = "#"
"""DOCUMENTED -- how a caller says "mint one for me" on upsert.
https://developer.squareup.com/reference/square/catalog-api/upsert-catalog-object
"""

_UNSUPPORTED_QUERIES: tuple[str, ...] = (
    "sorted_attribute_query",
    "text_query",
    "set_query",
    "range_query",
    "item_variations_for_item_option_values_query",
    "items_for_tax_query",
    "items_for_modifier_list_query",
    "items_for_item_options_query",
)
"""``CatalogQuery`` keys this unit refuses rather than ignores; see the SHRINK."""


@dataclass(frozen=True, slots=True)
class _Planned:
    """One object an upsert will write, fully resolved before any write happens."""

    entity: Entity
    #: Present when the caller sent a ``#temporary`` id, for ``id_mappings``.
    client_object_id: str | None
    #: ``None`` for an insert; the stored catalog version for an update.
    replaces_version: int | None


class CatalogSurface:
    """The three catalog routes beyond the listing, bound to one vendor."""

    __slots__ = ("_deps",)

    def __init__(self, deps: SquareDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="GET",
                path="/v2/catalog/object/{object_id}",
                capability=CAPABILITY,
                handler=self.retrieve_catalog_object,
                auth="bearer",
                scopes=("ITEMS_READ",),
                operation_id="RetrieveCatalogObject",
                summary="One catalog object; an ITEM carries its variations.",
            ),
            Route(
                method="POST",
                path="/v2/catalog/search",
                capability=CAPABILITY,
                handler=self.search_catalog_objects,
                auth="bearer",
                scopes=("ITEMS_READ",),
                operation_id="SearchCatalogObjects",
                summary="Catalog objects by type, by name prefix or exactly, or changed since a time.",
                # An empty query lists the whole catalog; no example body needed.
                pagination=PaginationSpec(style="cursor", where="body", items_path="objects"),
            ),
            Route(
                method="POST",
                path="/v2/catalog/object",
                capability=CAPABILITY,
                handler=self.upsert_catalog_object,
                auth="bearer",
                scopes=("ITEMS_WRITE",),
                idempotency=IdempotencySpec(key_path="idempotency_key", scope="catalog.upsert", required=True),
                # A CREATE, so it is repeatable: "#" marks a temporary id and a
                # fresh real one is minted per call.
                example_body={
                    "object": {"type": "ITEM", "id": "#conformance-item", "item_data": {"name": "Conformance Blend"}}
                },
                operation_id="UpsertCatalogObject",
                summary="Create or update one ITEM (with its variations) or one ITEM_VARIATION.",
            ),
        )

    # -- GET /v2/catalog/object/{object_id} ---------------------------------

    def retrieve_catalog_object(self, args: HandlerArgs) -> ReplyInit:
        """One object, with ``related_objects`` on request.
        https://developer.squareup.com/reference/square/catalog-api/retrieve-catalog-object

        DOCUMENTED -- ``include_related_objects`` adds a variation's parent ITEM only; an ITEM's
        own variations are nested inside it already.

        JUDGMENT / NOT VERIFIED -- a deleted object returns flagged ``is_deleted`` rather than 404.
        """
        collection = args.ctx.store.collection(COL.catalog)
        stored = collection.get(args.params["object_id"])
        if stored is None:
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=f"Catalog object {args.params['object_id']} was not found.",
                field="object_id",
            )
        catalog = collection.all()
        related: list[dict[str, Any]] | None = None
        if _flag(args.query("include_related_objects")):
            parent_id = stored.get("item_id")
            parent = None if parent_id is None else collection.get(str(parent_id))
            related = [] if parent is None else [project_catalog_object(parent, catalog)]
        return json_(
            compact(
                {
                    "object": project_catalog_object(stored, catalog),
                    "related_objects": related or None,
                }
            )
        )

    # -- POST /v2/catalog/search -------------------------------------------

    def search_catalog_objects(self, args: HandlerArgs) -> ReplyInit:
        """Search by type, name, or change time -- any combination.
        https://developer.squareup.com/reference/square/catalog-api/search-catalog-objects

        DOCUMENTED -- ``begin_time`` keeps objects strictly newer than ``updated_at``;
        ``latest_time`` is the newest ``updated_at`` across the catalog, not the page, so polling
        with it as the next ``begin_time`` sees each change exactly once. An out-of-range ``limit``
        is ignored, not refused, and pages at the default.

        JUDGMENT / NOT VERIFIED -- name matching is case-insensitive; Square says nothing about
        case for prefix/exact queries.
        """
        body = args.body()
        request = validate_body(SearchCatalogObjectsRequest, body)
        _refuse_unsupported_queries(body)
        types = frozenset(t.strip().upper() for t in request.object_types or [] if t.strip()) or frozenset({ITEM})
        collection = args.ctx.store.collection(COL.catalog)
        catalog = collection.all()
        after = instant_ms(request.begin_time)
        query = request.query

        def matches(entity: Mapping[str, Any]) -> bool:
            if str(entity.get("object_type", "")) not in types:
                return False
            if entity.get("is_deleted") is True and not request.include_deleted_objects:
                return False
            if after is not None:
                updated = instant_ms(_opt_str(entity.get("updated_at")))
                if updated is None or updated <= after:
                    return False
            if query is None:
                return True
            name = (catalog_name_of(entity) or "").casefold()
            if query.prefix_query is not None:
                _require_name_attribute(query.prefix_query.attribute_name, "query.prefix_query.attribute_name")
                if not name.startswith(query.prefix_query.attribute_prefix.casefold()):
                    return False
            if query.exact_query is not None:
                _require_name_attribute(query.exact_query.attribute_name, "query.exact_query.attribute_name")
                if name != query.exact_query.attribute_value.casefold():
                    return False
            return True

        # Code point, never locale collation -- the same ordering as ListCatalog.
        matching = sorted((entity for entity in catalog if matches(entity)), key=lambda entity: str(entity["id"]))
        limit = request.limit
        if limit is not None and not (1 <= limit <= CATALOG_MAX_LIMIT):
            limit = None
        fingerprint = {name: value for name, value in body.items() if name not in ("cursor", "limit")}
        page = collection.paginate(
            matching,
            limit=limit,
            cursor=request.cursor,
            fingerprint=fingerprint,
            default_limit=CATALOG_DEFAULT_LIMIT,
            max_limit=CATALOG_MAX_LIMIT,
        )
        related: list[dict[str, Any]] | None = None
        if request.include_related_objects:
            parents: dict[str, Mapping[str, Any]] = {}
            for entity in page.items:
                parent_id = entity.get("item_id")
                if parent_id is None or str(parent_id) in parents:
                    continue
                parent = collection.get(str(parent_id))
                if parent is not None:
                    parents[str(parent_id)] = parent
            related = [project_catalog_object(parents[key], catalog) for key in sorted(parents)]
        return json_(
            compact(
                {
                    # Present even when empty; see the empty-array rule in
                    # vendorfake.square.model.order.
                    "objects": [project_catalog_object(entity, catalog) for entity in page.items],
                    "related_objects": related or None,
                    "cursor": page.cursor,
                    "latest_time": _latest_time(catalog),
                }
            )
        )

    # -- POST /v2/catalog/object -------------------------------------------

    def upsert_catalog_object(self, args: HandlerArgs) -> ReplyInit:
        """Create or update one object and, for an ITEM, its variations.

        DOCUMENTED -- a temporary id (``#``-prefixed) creates; an existing id must supply a
        matching ``version`` or the write is rejected as conflicting
        (https://developer.squareup.com/reference/square/objects/CatalogObject). A non-prefixed id
        that doesn't exist is ``invalid_value``, since Square mints catalog ids.

        INVARIANT -- every object is resolved and version-checked before the first write, so a
        conflict on the third variation leaves the item and the first two untouched; each written
        object takes the same new, strictly-advancing version. A variation's
        ``item_variation_data.item_id`` may name the enclosing item's temporary id, creating both
        in one call.
        """
        request = validate_body(UpsertCatalogObjectRequest, args.body())
        collection = args.ctx.store.collection(COL.catalog)
        planned = self._plan(collection, request.object, int(args.ctx.clock.now()), set(), path="object")
        version = max(
            int(args.ctx.clock.now()),
            max((plan.replaces_version or 0) for plan in planned) + 1,
        )
        for plan in planned:
            plan.entity["catalog_version"] = version

        # Everything above could refuse; nothing below can. Mint now, in
        # request order, and resolve the temporary ids the plans refer to.
        mappings: dict[str, str] = {}
        for plan in planned:
            if plan.client_object_id is not None:
                mappings[plan.client_object_id] = self._deps.ids.catalog_object()
                plan.entity["id"] = mappings[plan.client_object_id]
        for plan in planned:
            parent = plan.entity.get("item_id")
            if isinstance(parent, str) and parent in mappings:
                plan.entity["item_id"] = mappings[parent]

        written: list[Entity] = []
        for plan in planned:
            if plan.replaces_version is None:
                written.append(collection.insert(plan.entity, {"operation_id": "UpsertCatalogObject"}))
                continue
            assigned = plan.entity

            def mutate(draft: Entity, assigned: Entity = assigned) -> None:
                draft.clear()
                draft.update(assigned)

            written.append(
                collection.update(str(plan.entity["id"]), mutate, meta={"operation_id": "UpsertCatalogObject"})
            )

        catalog = collection.all()
        return json_(
            compact(
                {
                    "catalog_object": project_catalog_object(written[0], catalog),
                    "id_mappings": [
                        {"client_object_id": client_id, "object_id": object_id}
                        for client_id, object_id in mappings.items()
                    ]
                    or None,
                }
            )
        )

    def _plan(
        self,
        collection: Collection,
        spec: CatalogObjectRequest,
        version: int,
        temporaries: set[str],
        *,
        path: str,
        parent_item_id: str | None = None,
    ) -> list[_Planned]:
        """Resolve one request object -- and an ITEM's nested variations -- into the entities to
        write, checking versions and leaving temporary ids for the caller to mint once everything
        has passed. ``parent_item_id`` binds a nested variation to its enclosing item regardless
        of any stated ``item_id``; a top-level variation must name one that resolves.
        """
        kind = spec.type.upper()
        if kind not in (ITEM, ITEM_VARIATION):
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"{path}.type must be ITEM or ITEM_VARIATION; this unit models no other catalog type.",
                field=f"{path}.type",
                info={"allowed": [ITEM, ITEM_VARIATION]},
            )
        if parent_item_id is not None and kind != ITEM_VARIATION:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"{path}.type must be ITEM_VARIATION inside item_data.variations.",
                field=f"{path}.type",
            )

        object_id, client_id = _classify_id(collection, spec.id, temporaries, path)
        current = None if client_id is not None else collection.get(object_id)
        if current is not None:
            stored_kind = str(current.get("object_type", ""))
            if stored_kind != kind:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"{path}.id names an existing {stored_kind}, not an {kind}.",
                    field=f"{path}.type",
                )
            if spec.version is None:
                raise UnitError(
                    UnitErrorKind.MISSING_FIELD,
                    detail=f"{path}.version is required when updating an existing object.",
                    field=f"{path}.version",
                )
            stored_version = CatalogObjectEntity.from_entity(current).catalog_version
            if spec.version != stored_version:
                raise UnitError(
                    UnitErrorKind.VERSION_CONFLICT,
                    detail=(
                        f"Supplied version {spec.version} does not match the current version "
                        f"{stored_version} of catalog object {object_id}."
                    ),
                    field=f"{path}.version",
                    info={"id": object_id, "supplied": spec.version, "current": stored_version},
                )
        replaces = None if current is None else CatalogObjectEntity.from_entity(current).catalog_version

        if kind == ITEM:
            data = spec.item_data
            if data is None or not data.name:
                raise UnitError(
                    UnitErrorKind.MISSING_FIELD,
                    detail=f"{path}.item_data.name is required.",
                    field=f"{path}.item_data.name",
                )
            entity = CatalogObjectEntity(
                id=object_id,
                object_type="ITEM",
                catalog_version=version,
                is_deleted=False,
                present_at_all_locations=spec.present_at_all_locations,
                item_name=data.name,
                item_description=data.description,
            ).to_entity()
            plans = [_Planned(entity=entity, client_object_id=client_id, replaces_version=replaces)]
            for index, child in enumerate(data.variations or []):
                plans.extend(
                    self._plan(
                        collection,
                        child,
                        version,
                        temporaries,
                        path=f"{path}.item_data.variations[{index}]",
                        parent_item_id=object_id,
                    )
                )
            return plans

        data_v = spec.item_variation_data
        if data_v is None or not data_v.name:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail=f"{path}.item_variation_data.name is required.",
                field=f"{path}.item_variation_data.name",
            )
        item_id = parent_item_id
        if item_id is None:
            stated = data_v.item_id
            if not stated:
                raise UnitError(
                    UnitErrorKind.MISSING_FIELD,
                    detail=f"{path}.item_variation_data.item_id is required on a top-level ITEM_VARIATION.",
                    field=f"{path}.item_variation_data.item_id",
                )
            item_id = stated
            # The one top-level object is this variation, so a temporary
            # item id can name nothing in this request.
            parent = None if stated.startswith(TEMPORARY_ID_PREFIX) else collection.get(item_id)
            if parent is None or parent.get("object_type") != ITEM:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"{path}.item_variation_data.item_id {stated} is not an ITEM in this catalog.",
                    field=f"{path}.item_variation_data.item_id",
                )
        price = data_v.price_money
        pricing = (data_v.pricing_type or ("FIXED_PRICING" if price is not None else "VARIABLE_PRICING")).upper()
        if pricing not in ("FIXED_PRICING", "VARIABLE_PRICING"):
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"{path}.item_variation_data.pricing_type must be FIXED_PRICING or VARIABLE_PRICING.",
                field=f"{path}.item_variation_data.pricing_type",
            )
        if pricing == "FIXED_PRICING" and price is None:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail=f"{path}.item_variation_data.price_money is required for FIXED_PRICING.",
                field=f"{path}.item_variation_data.price_money",
            )
        entity = CatalogObjectEntity(
            id=object_id,
            object_type="ITEM_VARIATION",
            catalog_version=version,
            is_deleted=False,
            present_at_all_locations=spec.present_at_all_locations,
            item_id=item_id,
            variation_name=data_v.name,
            pricing_type="FIXED_PRICING" if pricing == "FIXED_PRICING" else "VARIABLE_PRICING",
            price_money=None if price is None else Money(amount=price.amount, currency=price.currency or "USD"),
        ).to_entity()
        return [_Planned(entity=entity, client_object_id=client_id, replaces_version=replaces)]


def catalog_routes(deps: SquareDeps) -> tuple[Route, ...]:
    """The catalog routes beyond the listing, for one vendor."""
    return CatalogSurface(deps).routes()


def _classify_id(collection: Collection, raw: str, temporaries: set[str], path: str) -> tuple[str, str | None]:
    """``(id as sent, temporary id or None)`` for one request object.

    A temporary id may appear once per request, since ``id_mappings`` could
    carry only one answer for it. Nothing is minted here.
    """
    if raw.startswith(TEMPORARY_ID_PREFIX):
        if raw in temporaries:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"{path}.id {raw} is used twice in this request.",
                field=f"{path}.id",
            )
        temporaries.add(raw)
        return raw, raw
    if not collection.has(raw):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                f"{path}.id {raw} does not exist. To create a new object, use a temporary ID "
                f"prefixed with {TEMPORARY_ID_PREFIX!r}."
            ),
            field=f"{path}.id",
        )
    return raw, None


def _flag(raw: str | None) -> bool:
    """A boolean query parameter, ``true`` spelled as Square's examples do."""
    return raw is not None and raw.strip().lower() == "true"


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _refuse_unsupported_queries(body: Mapping[str, Any]) -> None:
    """A query kind this unit does not answer is refused, naming it."""
    query = body.get("query")
    if not isinstance(query, Mapping):
        return
    for key in _UNSUPPORTED_QUERIES:
        if key in query:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"query.{key} is not supported by this unit; use prefix_query or exact_query on name.",
                field=f"query.{key}",
                info={"supported": ["prefix_query", "exact_query"]},
            )


def _require_name_attribute(attribute: str, field: str) -> None:
    if attribute != SEARCHABLE_ATTRIBUTE:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} must be {SEARCHABLE_ATTRIBUTE!r}; no other attribute is searchable in this unit.",
            field=field,
            info={"supported": [SEARCHABLE_ATTRIBUTE]},
        )


def _latest_time(catalog: Sequence[Mapping[str, Any]]) -> str | None:
    """The newest ``updated_at`` across the catalog, or ``None`` when empty."""
    stamps = [str(entity["updated_at"]) for entity in catalog if entity.get("updated_at") is not None]
    if not stamps:
        return None
    return max(stamps, key=lambda stamp: instant_ms(stamp) or 0.0)
