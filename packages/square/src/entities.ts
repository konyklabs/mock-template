import type { Entity } from '@vendor-unit/core';

/**
 * Stored shapes. These are the unit's own model, in camelCase; the wire
 * projections in `model/` translate to Square's snake_case JSON. Keeping the
 * two apart means a field Square renames is a one-line change in a projector
 * rather than a rename across the state engine.
 */

export interface Money {
  amount: number;
  currency: string;
}

export interface MerchantEntity extends Entity {
  businessName: string;
  country: string;
  languageCode: string;
  currency: string;
  status: string;
}

export interface LocationEntity extends Entity {
  merchantId: string;
  name: string;
  address?: Record<string, string>;
  timezone: string;
  capabilities: string[];
  status: 'ACTIVE' | 'INACTIVE';
  country: string;
  languageCode: string;
  currency: string;
  phoneNumber?: string;
  businessName: string;
  type: 'PHYSICAL' | 'MOBILE';
}

export interface CatalogObjectEntity extends Entity {
  /** ITEM or ITEM_VARIATION. */
  objectType: 'ITEM' | 'ITEM_VARIATION';
  /** Square's catalog `version` is a millisecond-epoch-shaped int64. */
  catalogVersion: number;
  isDeleted: boolean;
  presentAtAllLocations: boolean;
  itemName?: string;
  itemDescription?: string;
  /** ITEM_VARIATION only. */
  itemId?: string;
  variationName?: string;
  pricingType?: 'FIXED_PRICING' | 'VARIABLE_PRICING';
  priceMoney?: Money;
}

export interface OrderLineItemEntity {
  uid: string;
  name?: string;
  quantity: string;
  note?: string;
  catalogObjectId?: string;
  variationName?: string;
  basePriceMoney: Money;
}

export interface TenderEntity {
  id: string;
  locationId: string;
  transactionId: string;
  createdAt: string;
  amountMoney: Money;
  type: string;
  paymentId: string;
}

export interface OrderEntity extends Entity {
  locationId: string;
  merchantId: string;
  referenceId?: string;
  customerId?: string;
  sourceName?: string;
  ticketName?: string;
  state: string;
  closedAt?: string;
  currency: string;
  lineItems: OrderLineItemEntity[];
  tenders: TenderEntity[];
  metadata?: Record<string, string>;
}

export interface AuthorizationCodeEntity extends Entity {
  /** `id` is the opaque code value itself. */
  clientId: string;
  merchantId: string;
  scopes: string[];
  redirectUri?: string;
  codeChallenge?: string;
  expiresAt: string;
  usedAt?: string;
}

export interface TokenEntity extends Entity {
  accessToken: string;
  refreshToken: string;
  clientId: string;
  merchantId: string;
  scopes: string[];
  /** RFC 3339, seconds precision — matches Square's `expires_at`. */
  expiresAt: string;
  refreshTokenExpiresAt?: string;
  shortLived: boolean;
  revokedAt?: string;
  flow: 'code' | 'pkce';
}

export const COL = {
  merchants: 'merchants',
  locations: 'locations',
  catalog: 'catalogObjects',
  orders: 'orders',
  codes: 'authorizationCodes',
  tokens: 'tokens',
} as const;
