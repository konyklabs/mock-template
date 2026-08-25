import { compact, json, type Route } from '@vendor-unit/core';
import { COL, type CatalogObjectEntity, type LocationEntity } from '../entities.js';

/**
 * Merchant reference data: the entities an order refers to.
 *
 * ListLocations GET /v2/locations      https://developer.squareup.com/reference/square/locations-api/list-locations
 * ListCatalog   GET /v2/catalog/list   https://developer.squareup.com/reference/square/catalog-api/list-catalog
 *
 * A separate capability from `order-lifecycle` on purpose: a consumer that only
 * syncs the catalog enables `merchant-directory` alone, and a consumer that
 * already has location ids hard-coded in fixtures can turn it off. It is also
 * the demonstration that the capability mechanism is not limited to the four
 * capabilities the slice names.
 */
export function directoryRoutes(): Route[] {
  return [
    {
      method: 'GET',
      path: '/v2/locations',
      capability: 'merchant-directory',
      auth: 'bearer',
      scopes: ['MERCHANT_PROFILE_READ'],
      operationId: 'ListLocations',
      summary: 'Every location for the seeded merchant.',
      handler: ({ ctx }) => {
        const locations = ctx.store.collection<LocationEntity>(COL.locations).all();
        return json({ locations: locations.map(projectLocation) });
      },
    },
    {
      method: 'GET',
      path: '/v2/catalog/list',
      capability: 'merchant-directory',
      auth: 'bearer',
      scopes: ['ITEMS_READ'],
      operationId: 'ListCatalog',
      summary: 'Catalog objects, filtered by type and cursor-paginated.',
      handler: ({ ctx, query }) => {
        const collection = ctx.store.collection<CatalogObjectEntity>(COL.catalog);
        const types = (query('types') ?? 'ITEM')
          .split(',')
          .map((t) => t.trim().toUpperCase())
          .filter(Boolean);
        const all = collection
          .all()
          .filter((o) => types.includes(o.objectType) && !o.isDeleted)
          .sort((a, b) => a.id.localeCompare(b.id));
        const page = collection.paginate(all, {
          cursor: query('cursor'),
          limit: query('limit') ? Number(query('limit')) : undefined,
          fingerprint: { types },
          defaultLimit: 100,
        });
        return json({
          objects: page.items.map((o) => projectCatalogObject(o, collection.all())),
          ...(page.cursor ? { cursor: page.cursor } : {}),
        });
      },
    },
  ];
}

function projectLocation(l: LocationEntity): Record<string, unknown> {
  return compact({
    id: l.id,
    name: l.name,
    address: l.address,
    timezone: l.timezone,
    capabilities: l.capabilities,
    status: l.status,
    created_at: l.createdAt,
    merchant_id: l.merchantId,
    country: l.country,
    language_code: l.languageCode,
    currency: l.currency,
    phone_number: l.phoneNumber,
    business_name: l.businessName,
    type: l.type,
  });
}

/**
 * An ITEM nests its ITEM_VARIATION objects, matching the RetrieveCatalogObject
 * example at https://developer.squareup.com/reference/square/catalog-api/retrieve-catalog-object.
 * `version` is the Square catalog version (a millisecond-epoch-shaped int64),
 * not the unit's internal entity version.
 */
function projectCatalogObject(o: CatalogObjectEntity, all: CatalogObjectEntity[]): Record<string, unknown> {
  const base = {
    type: o.objectType,
    id: o.id,
    updated_at: o.updatedAt,
    version: o.catalogVersion,
    is_deleted: o.isDeleted,
    present_at_all_locations: o.presentAtAllLocations,
  };
  if (o.objectType === 'ITEM') {
    return compact({
      ...base,
      item_data: compact({
        name: o.itemName,
        description: o.itemDescription,
        variations: all.filter((v) => v.objectType === 'ITEM_VARIATION' && v.itemId === o.id).map((v) => projectCatalogObject(v, all)),
      }),
    });
  }
  return compact({
    ...base,
    item_variation_data: compact({
      item_id: o.itemId,
      name: o.variationName,
      pricing_type: o.pricingType,
      price_money: o.priceMoney,
    }),
  });
}
