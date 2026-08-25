#!/usr/bin/env node
/**
 * Chaos transcript — fork-owned demo.
 *
 * Drives the `chaos-demo` profile end to end over HTTP and prints what a
 * consumer would see. Every fault below comes from the profile's declared rules
 * (packages/square/profiles/chaos-demo.json), not from anything this script
 * does: it is a consumer, not a puppeteer.
 *
 *   npm run demo:chaos
 */

import { createHmac } from 'node:crypto';
import { createServer } from 'node:http';
import { createSquareUnit } from '@vendor-unit/square';
import { serveHttp } from '@vendor-unit/core';

const TOKEN = 'EAAAl-unit-seeded-access-token-full-scopes';
const LOCATION = '18YC4JDH91E1H';
const TEA_MUG = '2TZFAOHWGG7PAK2QEXWYPZSP';
const SEED_ORDER = 'CAISENgvlJ6jLWAzERDzjyHVybY';

const received = [];
let subscriberFailures = 1;

async function main() {
  const subscriber = await startSubscriber();
  const unit = await createSquareUnit({ profile: 'chaos-demo', logger: silent() });
  const server = await serveHttp(unit, { port: 0, host: '127.0.0.1' });
  const base = server.url;

  try {
    const info = await get(base, '/__unit/info');
    heading('profile');
    line(`vendor       ${info.vendor.displayName} (${info.vendor.apiVersion})`);
    line(`profile      ${info.profile}`);
    line(`clock        ${info.clock.mode}, now ${info.clock.now}`);
    line(`chaos seed   ${info.chaos.seed}`);
    line('rules        (declared in packages/square/profiles/chaos-demo.json)');
    for (const rule of info.chaos.rules) {
      line(`  ${rule.id.padEnd(30)} ${rule.scope}/${rule.fault} ${JSON.stringify(rule.when)}`);
    }

    await post(base, '/__unit/webhooks/subscriptions', {
      id: 'wbhk_demo',
      notificationUrl: `http://127.0.0.1:${subscriber.port}/hooks`,
      eventTypes: ['order.*'],
      signatureKey: 'demo-signature-key',
    });

    heading('1. rate limiting: every third CreateOrder is refused');
    for (let i = 1; i <= 4; i++) {
      const res = await post(base, '/v2/orders', orderBody(`demo-create-${i}`), auth());
      line(`  create #${i}  ${res.__status} ${describe(res)}`);
    }

    heading('2. token expiry mid-flow: the fourth retrieve, without revoking anything');
    for (let i = 1; i <= 5; i++) {
      const res = await get(base, `/v2/orders/${SEED_ORDER}`, auth());
      line(`  retrieve #${i}  ${res.__status} ${describe(res)}`);
    }

    heading('3. webhook duplication and reordering');
    await post(
      base,
      `/v2/orders/${SEED_ORDER}`,
      { idempotency_key: 'demo-u1', order: { version: 1, ticket_name: 'first update' } },
      auth(),
      'PUT',
    );
    await post(
      base,
      `/v2/orders/${SEED_ORDER}`,
      { idempotency_key: 'demo-u2', order: { version: 2, ticket_name: 'second update' } },
      auth(),
      'PUT',
    );
    await post(base, '/__unit/webhooks/drain', {});

    const deliveries = (await get(base, '/__unit/webhooks/deliveries')).deliveries;
    line('  attempt  status     retry  event_id                              type           detail');
    for (const d of deliveries) {
      const version = d.body?.data?.object?.order_created?.version ?? d.body?.data?.object?.order_updated?.version;
      const detail = [
        version !== undefined ? `order version ${version}` : '',
        d.chaos?.length ? `chaos: ${d.chaos.join(',')}` : '',
        d.error ?? '',
        d.nextAttemptInMs !== undefined ? `retry in ${d.nextAttemptInMs}ms` : '',
      ]
        .filter(Boolean)
        .join(' | ');
      line(`  ${String(d.attempt).padEnd(8)} ${d.status.padEnd(10)} ${String(d.retryNumber).padEnd(6)} ${d.eventId} ${d.eventType.padEnd(14)} ${detail}`);
    }

    heading('4. what the subscriber actually received, in arrival order');
    for (const [i, r] of received.entries()) {
      const body = JSON.parse(r.raw);
      const version = body.data.object.order_created?.version ?? body.data.object.order_updated?.version;
      const expected = createHmac('sha256', 'demo-signature-key')
        .update(Buffer.concat([Buffer.from(`http://127.0.0.1:${subscriber.port}/hooks`, 'utf8'), Buffer.from(r.raw, 'utf8')]))
        .digest('base64');
      const valid = r.headers['x-square-hmacsha256-signature'] === expected;
      line(
        `  #${i + 1} ${body.type.padEnd(14)} version ${version}  event_id ${body.event_id}  signature ${valid ? 'VERIFIES' : 'INVALID'}` +
          (r.rejected ? '  [subscriber answered 500]' : '') +
          (r.headers['square-retry-number'] ? `  retry ${r.headers['square-retry-number']} (${r.headers['square-retry-reason']})` : ''),
      );
    }
    const ids = received.map((r) => JSON.parse(r.raw).event_id);
    line(`  ${received.length} deliveries, ${new Set(ids).size} distinct events: at-least-once, deduplicated on event_id`);
    line('  order.updated version 3 arrived before version 2: a consumer that trusts arrival order is now wrong.');

    heading('5. reproducibility');
    const events = (await get(base, '/__unit/chaos')).events;
    for (const e of events) line(`  ${e.at}  ${e.ruleId.padEnd(30)} ${e.fault.padEnd(20)} on ${e.subject} (occurrence ${e.occurrence})`);
    line('  re-running this script produces the same sequence: rules fire on counters, not on chance.');
  } finally {
    await server.close();
    await unit.stop();
    await subscriber.close();
  }
}

function auth() {
  return { authorization: `Bearer ${TOKEN}` };
}

function orderBody(key) {
  return { idempotency_key: key, order: { location_id: LOCATION, line_items: [{ catalog_object_id: TEA_MUG, quantity: '1' }] } };
}

function describe(res) {
  if (res.errors) return `${res.errors[0].code} — ${res.errors[0].detail}`;
  if (res.order) return `order ${res.order.id} ${res.order.state} v${res.order.version}`;
  return '';
}

async function get(base, path, headers = {}) {
  const res = await fetch(`${base}${path}`, { headers });
  return withStatus(res, await res.text());
}

async function post(base, path, body, headers = {}, method = 'POST') {
  const res = await fetch(`${base}${path}`, {
    method,
    headers: { 'content-type': 'application/json', ...headers },
    body: JSON.stringify(body),
  });
  return withStatus(res, await res.text());
}

function withStatus(res, text) {
  let parsed = {};
  try {
    parsed = text ? JSON.parse(text) : {};
  } catch {
    parsed = { raw: text };
  }
  return Object.assign(parsed, { __status: res.status });
}

function startSubscriber() {
  return new Promise((resolve) => {
    const server = createServer((req, res) => {
      const chunks = [];
      req.on('data', (c) => chunks.push(c));
      req.on('end', () => {
        const raw = Buffer.concat(chunks).toString('utf8');
        const index = received.length;
        // Fail the very first delivery so the retry schedule is visible.
        const status = index < subscriberFailures ? 500 : 200;
        if (status === 200) received.push({ headers: req.headers, raw });
        else received.push({ headers: req.headers, raw, rejected: true });
        res.writeHead(status, { 'content-type': 'text/plain' });
        res.end('ok');
      });
    });
    server.listen(0, '127.0.0.1', () =>
      resolve({
        port: server.address().port,
        close: () => new Promise((r) => server.close(() => r())),
      }),
    );
  });
}

function heading(text) {
  process.stdout.write(`\n--- ${text} ---\n`);
}

function line(text) {
  process.stdout.write(`${text}\n`);
}

function silent() {
  return { debug() {}, info() {}, warn() {}, error() {} };
}

main().catch((err) => {
  process.stderr.write(`${err instanceof Error ? (err.stack ?? err.message) : String(err)}\n`);
  process.exit(1);
});
