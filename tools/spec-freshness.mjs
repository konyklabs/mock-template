#!/usr/bin/env node
/**
 * Freshness job — template-owned, vendor-agnostic.
 *
 * A vendor mock rots silently: the vendor ships a change, nobody notices, and
 * the mock keeps confidently returning last year's behaviour. This job is the
 * tripwire. It is deliberately NOT a code generator — the architecture does not
 * depend on the vendor's spec (see README, "Not spec-generation-reliant"); the
 * spec is used only as a signal that something we implement moved.
 *
 * What it checks, driven entirely by `<fork>/freshness.json`:
 *   1. Every operation THIS UNIT implements still exists in the vendor's
 *      published spec, at the same path and method.
 *   2. The request/response schema fingerprint of those operations is unchanged.
 *   3. The vendor's latest published API version is not ahead of the version
 *      the unit claims to implement.
 *
 * Severity is the point. A byte change anywhere in a 3 MB spec is noise and is
 * reported as information; a change to an operation we implement is an error.
 * A job that cries wolf gets muted, and a muted freshness job is worse than
 * none, because it looks like coverage.
 *
 * Usage:
 *   node tools/spec-freshness.mjs [--update] [--offline] [--strict] [--json]
 */

import { createHash } from 'node:crypto';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const CACHE_DIR = join(REPO_ROOT, 'tools', '.cache');

const args = new Set(process.argv.slice(2));
const UPDATE = args.has('--update');
const OFFLINE = args.has('--offline');
const STRICT = args.has('--strict');
const AS_JSON = args.has('--json');

main().catch((err) => {
  process.stderr.write(`${err instanceof Error ? (err.stack ?? err.message) : String(err)}\n`);
  process.exit(2);
});

async function main() {
  const { config, configPath, packageDir } = await findConfig();
  const pinPath = join(packageDir, config.pinFile ?? 'spec-pin.json');
  const pin = await readJsonIfExists(pinPath);

  const routes = await implementedRoutes(config);
  const spec = await fetchJson(config.spec.url, 'api.json');
  const changelogHtml = config.changelog ? await fetchText(config.changelog.url, 'changelog.html') : null;

  const specDigest = sha256(spec.raw);
  const latestApiVersion = changelogHtml ? extractLatestVersion(changelogHtml, config.changelog.versionPattern) : null;

  const findings = [];
  const operations = {};
  const notInSpec = config.notInSpec ?? {};

  for (const route of routes) {
    const key = `${route.method} ${route.path}`;
    const specPath = route.path.replace(/:([A-Za-z0-9_]+)/g, '{$1}');
    const item = spec.json.paths?.[specPath];
    const operation = item?.[route.method.toLowerCase()];

    if (!operation) {
      if (key in notInSpec) {
        operations[key] = { operationId: route.operationId ?? null, presentInSpec: false, documentedAbsence: notInSpec[key] };
        continue;
      }
      findings.push({
        severity: 'error',
        key,
        message: `implemented operation is absent from the vendor spec at ${specPath}`,
        hint: 'The vendor removed or moved it, or this unit invented it. Either way a consumer is being lied to.',
      });
      operations[key] = { operationId: route.operationId ?? null, presentInSpec: false };
      continue;
    }

    const fingerprint = fingerprintOperation(operation);
    operations[key] = { operationId: operation.operationId ?? null, presentInSpec: true, fingerprint };

    const previous = pin?.operations?.[key];
    if (previous?.presentInSpec === false && !previous.documentedAbsence) {
      findings.push({ severity: 'info', key, message: 'operation appeared in the vendor spec since the last pin' });
    } else if (previous?.fingerprint && previous.fingerprint !== fingerprint) {
      findings.push({
        severity: 'error',
        key,
        message: `request/response shape changed in the vendor spec (${previous.fingerprint} -> ${fingerprint})`,
        hint: 'Re-read the operation reference and update the surface, then re-pin.',
      });
    }
    if (previous && previous.operationId && operation.operationId && previous.operationId !== operation.operationId) {
      findings.push({ severity: 'error', key, message: `operationId changed ${previous.operationId} -> ${operation.operationId}` });
    }
  }

  const pinnedKeys = Object.keys(pin?.operations ?? {});
  for (const key of pinnedKeys) {
    if (!(key in operations)) {
      findings.push({ severity: 'info', key, message: 'operation is pinned but no longer implemented by this unit' });
    }
  }

  if (pin && pin.specDigest !== specDigest) {
    const touchedOurSurface = findings.some((f) => f.severity === 'error');
    findings.push({
      severity: 'info',
      key: config.spec.url,
      message: touchedOurSurface
        ? `the vendor spec changed (${pin.specDigest.slice(0, 12)} -> ${specDigest.slice(0, 12)}); see the errors above for what it did to this unit's surface`
        : `the vendor spec changed (${pin.specDigest.slice(0, 12)} -> ${specDigest.slice(0, 12)}) but no operation this unit implements moved`,
    });
  }

  if (latestApiVersion && config.declaredApiVersion && latestApiVersion > config.declaredApiVersion) {
    findings.push({
      severity: 'warn',
      key: 'api-version',
      message: `the vendor's latest published version is ${latestApiVersion}; this unit declares ${config.declaredApiVersion}`,
      hint: `Read ${config.changelog.url} for what changed, then bump apiVersion in the fork's profiles.`,
    });
  }

  const report = {
    vendor: config.vendor,
    checkedAt: new Date().toISOString(),
    offline: OFFLINE,
    spec: { url: config.spec.url, digest: specDigest, bytes: spec.raw.length },
    declaredApiVersion: config.declaredApiVersion ?? null,
    latestApiVersion,
    operationsChecked: Object.keys(operations).length,
    findings,
  };

  if (UPDATE) {
    const next = {
      _comment: 'Generated by tools/spec-freshness.mjs --update. Fork-owned: it records what the vendor spec said when this unit was last reconciled with it.',
      vendor: config.vendor,
      pinnedAt: report.checkedAt,
      specUrl: config.spec.url,
      specDigest,
      specBytes: spec.raw.length,
      latestApiVersion,
      declaredApiVersion: config.declaredApiVersion ?? null,
      operations,
    };
    await writeFile(pinPath, `${JSON.stringify(next, null, 2)}\n`, 'utf8');
    process.stdout.write(`pinned ${Object.keys(operations).length} operations to ${relative(pinPath)}\n`);
  }

  if (AS_JSON) {
    process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  } else {
    printReport(report, configPath, pinPath, pin);
  }

  const errors = findings.filter((f) => f.severity === 'error').length;
  const warnings = findings.filter((f) => f.severity === 'warn').length;
  if (UPDATE) process.exit(0);
  process.exit(errors > 0 || (STRICT && warnings > 0) ? 1 : 0);
}

function printReport(report, configPath, pinPath, pin) {
  const lines = [];
  lines.push(`freshness: ${report.vendor}`);
  lines.push(`  config          ${relative(configPath)}`);
  lines.push(`  pin             ${relative(pinPath)}${pin ? ` (pinned ${pin.pinnedAt})` : ' (absent — run with --update to create it)'}`);
  lines.push(`  spec            ${report.spec.url}`);
  lines.push(`  spec digest     sha256:${report.spec.digest.slice(0, 32)}… (${report.spec.bytes} bytes)`);
  lines.push(`  api version     unit declares ${report.declaredApiVersion ?? '(none)'}, vendor latest ${report.latestApiVersion ?? '(unknown)'}`);
  lines.push(`  operations      ${report.operationsChecked} implemented operations reconciled against the spec`);
  lines.push('');
  if (report.findings.length === 0) {
    lines.push('  no drift.');
  } else {
    for (const f of report.findings) {
      lines.push(`  [${f.severity.toUpperCase()}] ${f.key}`);
      lines.push(`          ${f.message}`);
      if (f.hint) lines.push(`          ${f.hint}`);
    }
  }
  lines.push('');
  const errors = report.findings.filter((f) => f.severity === 'error').length;
  const warns = report.findings.filter((f) => f.severity === 'warn').length;
  const infos = report.findings.filter((f) => f.severity === 'info').length;
  lines.push(`  ${errors} error(s), ${warns} warning(s), ${infos} note(s)`);
  process.stdout.write(`${lines.join('\n')}\n`);
}

/**
 * Fingerprint an operation by the SHAPE a consumer sees, not by its prose: the
 * schema references on the request and each response, plus the parameter names.
 * A documentation wording change must not wake anybody up at 3am.
 */
function fingerprintOperation(operation) {
  const parameters = (operation.parameters ?? []).map((p) => `${p.in}:${p.name}${p.required ? '!' : ''}`).sort();
  const requestSchema = schemaRef(operation.requestBody?.content?.['application/json']?.schema);
  const responses = Object.entries(operation.responses ?? {})
    .map(([status, response]) => `${status}=${schemaRef(response?.content?.['application/json']?.schema)}`)
    .sort();
  return sha256(JSON.stringify({ parameters, requestSchema, responses })).slice(0, 16);
}

function schemaRef(schema) {
  if (!schema) return null;
  if (schema.$ref) return schema.$ref;
  if (schema.type === 'array') return `array<${schemaRef(schema.items)}>`;
  return schema.type ?? 'inline';
}

function extractLatestVersion(html, pattern) {
  const regex = new RegExp(pattern ?? '(\\d{4}-\\d{2}-\\d{2})', 'g');
  const found = [...html.matchAll(regex)].map((m) => m[1]).filter(Boolean);
  return found.length > 0 ? found.sort().at(-1) : null;
}

/** Load the fork's own unit and ask it what it implements. */
async function implementedRoutes(config) {
  const module = await import(config.unitModule);
  const factory = module[config.unitFactory];
  if (typeof factory !== 'function') {
    throw new Error(`${config.unitModule} does not export ${config.unitFactory}()`);
  }
  const silent = { debug() {}, info() {}, warn() {}, error() {} };
  const unit = await factory({ profile: config.profile ?? 'full', logger: silent });
  try {
    return unit.routes
      .filter((r) => !r.internal)
      .map((r) => ({ method: r.method.toUpperCase(), path: r.path, operationId: r.operationId }))
      .sort((a, b) => `${a.path} ${a.method}`.localeCompare(`${b.path} ${b.method}`));
  } finally {
    await unit.stop();
  }
}

async function findConfig() {
  const explicit = process.argv.slice(2).find((a) => a.endsWith('freshness.json'));
  if (explicit) {
    const path = resolve(explicit);
    return { config: JSON.parse(await readFile(path, 'utf8')), configPath: path, packageDir: dirname(path) };
  }
  const packagesDir = join(REPO_ROOT, 'packages');
  for (const entry of await readdir(packagesDir)) {
    const path = join(packagesDir, entry, 'freshness.json');
    const parsed = await readJsonIfExists(path);
    if (parsed) return { config: parsed, configPath: path, packageDir: dirname(path) };
  }
  throw new Error('no packages/*/freshness.json found');
}

async function readJsonIfExists(path) {
  try {
    return JSON.parse(await readFile(path, 'utf8'));
  } catch (err) {
    if (err && typeof err === 'object' && err.code === 'ENOENT') return null;
    throw err;
  }
}

async function fetchText(url, cacheName) {
  await mkdir(CACHE_DIR, { recursive: true });
  const cachePath = join(CACHE_DIR, cacheName);
  if (OFFLINE) {
    const cached = await readFile(cachePath, 'utf8').catch(() => null);
    if (cached === null) throw new Error(`--offline but no cached copy at ${cachePath}`);
    return cached;
  }
  const res = await fetch(url, { headers: { 'user-agent': 'vendor-unit-freshness/0.1' } });
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  const text = await res.text();
  await writeFile(cachePath, text, 'utf8');
  return text;
}

async function fetchJson(url, cacheName) {
  const raw = await fetchText(url, cacheName);
  return { raw, json: JSON.parse(raw) };
}

function sha256(input) {
  return createHash('sha256').update(input).digest('hex');
}

function relative(path) {
  return path.startsWith(REPO_ROOT) ? path.slice(REPO_ROOT.length + 1) : path;
}
