#!/usr/bin/env node
/**
 * Fork purity check — template-owned.
 *
 * The template boundary is only real if something enforces it. A fork that
 * "just tweaks one thing" in the core cannot take a core upgrade afterwards
 * without a merge conflict it will resolve badly at 5pm on a Friday. This tool
 * makes that divergence visible the moment it happens, in one command, with no
 * network and no git history required.
 *
 * Three ownership classes, declared in template.manifest.json:
 *
 *   template  Shipped by the template, never edited in a fork. Checksummed;
 *             a modification, deletion or unlisted addition is an ERROR.
 *   seeded    The template provides the first version, the fork owns it after
 *             that (the root package.json, the CI workflow it will customise).
 *             Divergence is reported, not punished.
 *   fork      Purely the fork's (the vendor surface, its fixtures, its docs).
 *             Not checked at all.
 *
 * Usage:
 *   node tools/template-check.mjs            verify
 *   node tools/template-check.mjs --update   re-record checksums after a
 *                                            deliberate template upgrade
 */

import { createHash } from 'node:crypto';
import { readFile, readdir, stat, writeFile } from 'node:fs/promises';
import { join, relative, resolve, dirname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const MANIFEST_PATH = join(REPO_ROOT, 'template.manifest.json');
const IGNORED_DIRS = new Set(['node_modules', 'dist', '.git', '.venv', '__pycache__', '.pytest_cache', '.cache']);

const args = new Set(process.argv.slice(2));
const UPDATE = args.has('--update');

main().catch((err) => {
  process.stderr.write(`${err instanceof Error ? (err.stack ?? err.message) : String(err)}\n`);
  process.exit(2);
});

async function main() {
  const manifest = JSON.parse(await readFile(MANIFEST_PATH, 'utf8'));
  const ownership = manifest.ownership ?? {};
  const files = await walk(REPO_ROOT);

  const classify = (path) => {
    if (matchesAny(ownership.template ?? [], path)) return 'template';
    if (matchesAny(ownership.seeded ?? [], path)) return 'seeded';
    return 'fork';
  };

  const current = { template: new Map(), seeded: new Map(), fork: [] };
  for (const path of files) {
    const bucket = classify(path);
    if (bucket === 'fork') {
      current.fork.push(path);
      continue;
    }
    current[bucket].set(path, await digest(join(REPO_ROOT, path)));
  }

  if (UPDATE) {
    manifest.templateVersion = manifest.templateVersion ?? '0.1.0';
    manifest.recordedAt = new Date().toISOString();
    manifest.files = Object.fromEntries([...current.template.entries()].sort(([a], [b]) => a.localeCompare(b)));
    manifest.seeds = Object.fromEntries([...current.seeded.entries()].sort(([a], [b]) => a.localeCompare(b)));
    await writeFile(MANIFEST_PATH, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
    process.stdout.write(
      `recorded ${Object.keys(manifest.files).length} template-owned and ${Object.keys(manifest.seeds).length} seeded files in template.manifest.json\n`,
    );
    return;
  }

  const recorded = manifest.files ?? {};
  const seeds = manifest.seeds ?? {};
  const problems = [];
  const notes = [];

  for (const [path, expected] of Object.entries(recorded)) {
    const actual = current.template.get(path);
    if (actual === undefined) {
      problems.push({ kind: 'missing', path, detail: 'template-owned file was deleted from this fork' });
    } else if (actual !== expected) {
      problems.push({ kind: 'modified', path, detail: `template-owned file was edited (${expected.slice(0, 12)} -> ${actual.slice(0, 12)})` });
    }
  }
  for (const path of current.template.keys()) {
    if (!(path in recorded)) {
      problems.push({ kind: 'untracked', path, detail: 'new file inside a template-owned path; it will be clobbered by the next template merge' });
    }
  }
  for (const [path, actual] of current.seeded.entries()) {
    const expected = seeds[path];
    if (expected === undefined) notes.push({ path, detail: 'seeded path with no recorded seed' });
    else if (expected !== actual) notes.push({ path, detail: 'diverged from the template seed (expected for a fork)' });
  }

  const lines = [];
  lines.push(`template check: ${manifest.template}@${manifest.templateVersion} (core ${manifest.core})`);
  lines.push(`  recorded        ${manifest.recordedAt ?? '(never)'}`);
  lines.push(`  template-owned  ${current.template.size} files, ${problems.length} problem(s)`);
  lines.push(`  seeded          ${current.seeded.size} files, ${notes.length} diverged`);
  lines.push(`  fork-owned      ${current.fork.length} files (not checked)`);
  lines.push('');
  for (const p of problems) lines.push(`  [${p.kind.toUpperCase()}] ${p.path}\n          ${p.detail}`);
  for (const n of notes) lines.push(`  [NOTE] ${n.path}\n          ${n.detail}`);
  if (problems.length === 0) {
    lines.push('');
    lines.push('  this fork has not modified any template code; a core upgrade is a version bump.');
  } else {
    lines.push('');
    lines.push('  Move the change into the fork (packages/<vendor>/) or upstream it into the');
    lines.push('  template, then re-run. `--update` only after a deliberate template upgrade.');
  }
  process.stdout.write(`${lines.join('\n')}\n`);
  process.exit(problems.length > 0 ? 1 : 0);
}

async function walk(root) {
  const out = [];
  const visit = async (dir) => {
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      if (entry.name.startsWith('.') && entry.name !== '.github' && entry.name !== '.dockerignore' && entry.name !== '.gitignore') continue;
      if (IGNORED_DIRS.has(entry.name)) continue;
      // Build artifact, gitignored: its bytes differ per machine, so checksumming
      // it would fail every fresh clone's first `template:check`.
      if (entry.name.endsWith('.tsbuildinfo')) continue;
      const full = join(dir, entry.name);
      if (entry.isDirectory()) await visit(full);
      else if (entry.isFile()) out.push(relative(root, full).split(sep).join('/'));
    }
  };
  await visit(root);
  return out.sort();
}

async function digest(path) {
  const info = await stat(path);
  if (!info.isFile()) return null;
  return createHash('sha256').update(await readFile(path)).digest('hex');
}

/** Minimal glob: `**` spans directories, `*` stays inside one segment. */
function matchesAny(patterns, path) {
  return patterns.some((pattern) => globToRegExp(pattern).test(path));
}

function globToRegExp(pattern) {
  let out = '';
  for (let i = 0; i < pattern.length; i++) {
    const char = pattern[i];
    if (char === '*') {
      if (pattern[i + 1] === '*') {
        out += '.*';
        i++;
        if (pattern[i + 1] === '/') i++;
      } else {
        out += '[^/]*';
      }
    } else if ('.+?^${}()|[]\\'.includes(char)) {
      out += `\\${char}`;
    } else {
      out += char;
    }
  }
  return new RegExp(`^${out}$`);
}
