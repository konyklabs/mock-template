import { SUBSCRIPTION_COLLECTION, UnitError, type SubscriptionEntity, type UnitContext } from '@vendor-unit/core';
import {
  COL,
  type CatalogObjectEntity,
  type LocationEntity,
  type MerchantEntity,
  type Money,
  type OrderEntity,
  type OrderLineItemEntity,
  type TokenEntity,
} from './entities.js';

/**
 * Seed scenario loader.
 *
 * The scenario is a JSON document, not code: a fork author (or a consumer
 * building a profile) describes a merchant, its locations, its catalog, the
 * orders that already exist and the tokens already issued, and gets a unit that
 * starts in that world. Seeded mutations are journalled with `seed: true` so the
 * dispatcher does not push an `order.created` for an order that has existed
 * since before the process started.
 *
 * Every seeded entity carries an explicit id, which is what makes two units
 * seeded from the same document hash identically (a conformance check).
 */
export interface SeedDocument {
  merchant: {
    id: string;
    businessName: string;
    country?: string;
    languageCode?: string;
    currency?: string;
  };
  locations: Array<{
    id: string;
    name: string;
    address?: Record<string, string>;
    timezone?: string;
    capabilities?: string[];
    status?: 'ACTIVE' | 'INACTIVE';
    currency?: string;
    country?: string;
    languageCode?: string;
    phoneNumber?: string;
    type?: 'PHYSICAL' | 'MOBILE';
    createdAt?: string;
  }>;
  catalog?: {
    items: Array<{
      id: string;
      name: string;
      description?: string;
      updatedAt?: string;
      catalogVersion?: number;
      variations: Array<{ id: string; name: string; priceMoney: Money; pricingType?: 'FIXED_PRICING' | 'VARIABLE_PRICING' }>;
    }>;
  };
  orders?: Array<{
    id: string;
    locationId: string;
    state?: string;
    referenceId?: string;
    customerId?: string;
    ticketName?: string;
    sourceName?: string;
    createdAt?: string;
    updatedAt?: string;
    version?: number;
    lineItems?: Array<{
      uid: string;
      name?: string;
      quantity: string;
      note?: string;
      catalogObjectId?: string;
      variationName?: string;
      basePriceMoney?: Money;
    }>;
  }>;
  tokens?: Array<{
    id?: string;
    accessToken: string;
    refreshToken: string;
    clientId?: string;
    scopes: string[];
    expiresInMs?: number;
    shortLived?: boolean;
    flow?: 'code' | 'pkce';
  }>;
  webhookSubscriptions?: Array<{
    id: string;
    name?: string;
    notificationUrl: string;
    eventTypes: string[];
    signatureKey: string;
    enabled?: boolean;
  }>;
}

const SEED_META = { seed: true, operationId: 'SeedScenario' };

export function hydrateSquare(ctx: UnitContext, seed: unknown, defaults: { clientId: string; apiVersion: string; accessTokenTtlMs: number }): void {
  if (!seed || typeof seed !== 'object') {
    throw new UnitError('internal', { detail: 'No seed scenario was supplied. Set `seed` in the profile or UNIT_SEED.' });
  }
  const doc = seed as SeedDocument;
  const currency = doc.merchant.currency ?? 'USD';

  ctx.store.collection<MerchantEntity>(COL.merchants).insert(
    {
      id: doc.merchant.id,
      businessName: doc.merchant.businessName,
      country: doc.merchant.country ?? 'US',
      languageCode: doc.merchant.languageCode ?? 'en-US',
      currency,
      status: 'ACTIVE',
    },
    SEED_META,
  );

  const locations = ctx.store.collection<LocationEntity>(COL.locations);
  for (const l of doc.locations) {
    locations.insert(
      {
        id: l.id,
        merchantId: doc.merchant.id,
        name: l.name,
        address: l.address,
        timezone: l.timezone ?? 'America/Los_Angeles',
        capabilities: l.capabilities ?? ['CREDIT_CARD_PROCESSING'],
        status: l.status ?? 'ACTIVE',
        country: l.country ?? doc.merchant.country ?? 'US',
        languageCode: l.languageCode ?? doc.merchant.languageCode ?? 'en-US',
        currency: l.currency ?? currency,
        phoneNumber: l.phoneNumber,
        businessName: doc.merchant.businessName,
        type: l.type ?? 'PHYSICAL',
        ...(l.createdAt ? { createdAt: l.createdAt } : {}),
      },
      SEED_META,
    );
  }

  const catalog = ctx.store.collection<CatalogObjectEntity>(COL.catalog);
  for (const item of doc.catalog?.items ?? []) {
    const version = item.catalogVersion ?? 1479335124878;
    catalog.insert(
      {
        id: item.id,
        objectType: 'ITEM',
        catalogVersion: version,
        isDeleted: false,
        presentAtAllLocations: true,
        itemName: item.name,
        itemDescription: item.description,
        ...(item.updatedAt ? { updatedAt: item.updatedAt } : {}),
      },
      SEED_META,
    );
    for (const v of item.variations) {
      catalog.insert(
        {
          id: v.id,
          objectType: 'ITEM_VARIATION',
          catalogVersion: version,
          isDeleted: false,
          presentAtAllLocations: true,
          itemId: item.id,
          variationName: v.name,
          pricingType: v.pricingType ?? 'FIXED_PRICING',
          priceMoney: v.priceMoney,
          ...(item.updatedAt ? { updatedAt: item.updatedAt } : {}),
        },
        SEED_META,
      );
    }
  }

  const orders = ctx.store.collection<OrderEntity>(COL.orders);
  for (const o of doc.orders ?? []) {
    const location = locations.get(o.locationId);
    if (!location) {
      throw new UnitError('internal', { detail: `Seed order ${o.id} references unknown location ${o.locationId}.` });
    }
    const lineItems: OrderLineItemEntity[] = (o.lineItems ?? []).map((li) => {
      let basePriceMoney = li.basePriceMoney;
      let variationName = li.variationName;
      let name = li.name;
      if (li.catalogObjectId) {
        const variation = catalog.get(li.catalogObjectId);
        if (!variation || variation.objectType !== 'ITEM_VARIATION') {
          throw new UnitError('internal', { detail: `Seed order ${o.id} references unknown catalog variation ${li.catalogObjectId}.` });
        }
        basePriceMoney = basePriceMoney ?? variation.priceMoney;
        variationName = variationName ?? variation.variationName;
        name = name ?? (variation.itemId ? catalog.get(variation.itemId)?.itemName : undefined);
      }
      if (!basePriceMoney) {
        throw new UnitError('internal', { detail: `Seed order ${o.id} line item ${li.uid} has no price.` });
      }
      return { uid: li.uid, name, quantity: li.quantity, note: li.note, catalogObjectId: li.catalogObjectId, variationName, basePriceMoney };
    });

    orders.insert(
      {
        id: o.id,
        locationId: o.locationId,
        merchantId: doc.merchant.id,
        referenceId: o.referenceId,
        customerId: o.customerId,
        ticketName: o.ticketName,
        sourceName: o.sourceName,
        state: o.state ?? 'OPEN',
        currency: location.currency,
        lineItems,
        tenders: [],
        version: o.version ?? 1,
        ...(o.createdAt ? { createdAt: o.createdAt } : {}),
        ...(o.updatedAt ? { updatedAt: o.updatedAt } : {}),
      },
      SEED_META,
    );
  }

  const tokens = ctx.store.collection<TokenEntity>(COL.tokens);
  for (const [i, t] of (doc.tokens ?? []).entries()) {
    tokens.insert(
      {
        id: t.id ?? `tok_seed_${String(i + 1).padStart(2, '0')}`,
        accessToken: t.accessToken,
        refreshToken: t.refreshToken,
        clientId: t.clientId ?? defaults.clientId,
        merchantId: doc.merchant.id,
        scopes: t.scopes,
        expiresAt: ctx.clock.isoSeconds(t.expiresInMs ?? defaults.accessTokenTtlMs),
        shortLived: t.shortLived ?? false,
        flow: t.flow ?? 'code',
      },
      SEED_META,
    );
  }

  const subscriptions = ctx.store.collection<SubscriptionEntity>(SUBSCRIPTION_COLLECTION);
  for (const s of doc.webhookSubscriptions ?? []) {
    subscriptions.insert(
      {
        id: s.id,
        name: s.name ?? 'Seeded subscription',
        notificationUrl: s.notificationUrl,
        eventTypes: s.eventTypes,
        signatureKey: s.signatureKey,
        enabled: s.enabled ?? true,
        apiVersion: defaults.apiVersion,
      } as SubscriptionEntity,
      SEED_META,
    );
  }
}
