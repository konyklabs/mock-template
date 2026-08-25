import { Rng } from '@vendor-unit/core';

/**
 * Square-shaped identifiers.
 *
 * Consumers pattern-match on id shape more often than they admit (log scrapers,
 * fixture assertions, column widths), so the mock mints ids that look like the
 * ones in Square's own documentation examples:
 *   order         CAISENgvlJ6jLWAzERDzjyHVybY   (27 chars, "CAIS" prefix)
 *   location      18YC4JDH91E1H                 (13 chars, upper alnum)
 *   merchant      MLQW2MYBY81PZ                 (13 chars, upper alnum)
 *   catalog       W62UWFY35CWMYGVWK6TWJDNI      (24 chars, upper alnum)
 *   auth code     sq0cgb-xJPZ8rwCk7KfapZz815Grw
 *   access token  EAAAl3ikZIe18J-2-cHlV2bL4-...
 *   subscription  wbhk_b35f6b3145074cf9ad513610786c19d5
 * Sources: the response examples on developer.squareup.com/reference/square/*.
 *
 * The generator is seeded, so the same scenario produces the same ids on every
 * run and a webhook transcript can be diffed between runs.
 */
const UPPER_ALNUM = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
const MIXED = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
const TOKEN_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
const HEX = '0123456789abcdef';

export class SquareIds {
  private readonly rng: Rng;

  constructor(seed: number) {
    // Salted so id generation never consumes the chaos engine's RNG stream.
    this.rng = new Rng((seed ^ 0x5175_4152) >>> 0);
  }

  private pick(alphabet: string, length: number): string {
    let out = '';
    for (let i = 0; i < length; i++) out += alphabet[this.rng.int(alphabet.length)];
    return out;
  }

  order(): string {
    return `CAIS${this.pick(MIXED, 23)}`;
  }

  lineItemUid(): string {
    return this.pick(MIXED, 22);
  }

  location(): string {
    return this.pick(UPPER_ALNUM, 13);
  }

  merchant(): string {
    return this.pick(UPPER_ALNUM, 13);
  }

  catalogObject(): string {
    return this.pick(UPPER_ALNUM, 24);
  }

  authorizationCode(): string {
    return `sq0cgb-${this.pick(TOKEN_CHARS, 22)}`;
  }

  accessToken(): string {
    return `EAAA${this.pick(TOKEN_CHARS, 60)}`;
  }

  refreshToken(): string {
    return `EQAA${this.pick(TOKEN_CHARS, 60)}`;
  }

  subscription(): string {
    return `wbhk_${this.pick(HEX, 32)}`;
  }

  signatureKey(): string {
    return this.pick(TOKEN_CHARS, 22);
  }

  tender(): string {
    return this.pick(MIXED, 27);
  }

  internal(prefix: string): string {
    return `${prefix}_${this.pick(HEX, 12)}`;
  }
}
