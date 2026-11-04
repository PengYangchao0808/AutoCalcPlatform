import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

import { REQUIRED_SHELL_IDS } from './shell-ids.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN_ROOT = path.resolve(HERE, '..');
const FRONTEND_ROOT = path.resolve(PLUGIN_ROOT, '..');
const BUILD_SCRIPT = path.join(PLUGIN_ROOT, 'scripts', 'build.mjs');
const SOURCE_HTML = path.join(FRONTEND_ROOT, 'next', 'index.html');

const tempDirs = [];
after(() => {
  for (const dir of tempDirs) fs.rmSync(dir, { recursive: true, force: true });
});

function makeTempDir(prefix) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  tempDirs.push(dir);
  return dir;
}

function runBuild(args) {
  const result = spawnSync(process.execPath, [BUILD_SCRIPT, ...args], { encoding: 'utf8' });
  return result;
}

function buildInto(outDir) {
  const result = runBuild(['--out', outDir]);
  assert.equal(result.status, 0, `build failed:\n${result.stderr}${result.stdout}`);
  return result;
}

function readTree(dir) {
  const files = new Map();
  const walk = (current) => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const absolute = path.join(current, entry.name);
      if (entry.isDirectory()) {
        walk(absolute);
        continue;
      }
      const rel = path.relative(dir, absolute).split(path.sep).join('/');
      files.set(rel, fs.readFileSync(absolute));
    }
  };
  walk(dir);
  return files;
}

/** Build once into two independent temp dirs and reuse the result. */
let built = null;
function ensureBuilt() {
  if (built) return built;
  const first = makeTempDir('acp-dsh-build-a-');
  const second = makeTempDir('acp-dsh-build-b-');
  buildInto(first);
  buildInto(second);
  built = {
    first,
    second,
    tree: readTree(first),
    manifest: JSON.parse(readTree(first).get('manifest.json').toString('utf8')),
  };
  return built;
}

test('two independent builds are byte-identical', () => {
  const { first, second } = ensureBuilt();
  const a = readTree(first);
  const b = readTree(second);
  assert.deepEqual([...a.keys()].sort(), [...b.keys()].sort());
  for (const [rel, bytes] of a) {
    assert.ok(bytes.equals(b.get(rel)), `output differs between runs: ${rel}`);
  }
});

test('the manifest digest is recomputed from the sorted entry list', () => {
  const { manifest, tree } = ensureBuilt();
  assert.equal(manifest.schema, 'acp2.dsh.assets.v1');
  assert.equal(manifest.generated_by, 'frontend/dsh-plugin/scripts/build.mjs');
  assert.equal('timestamp' in manifest, false);
  assert.equal('generated_at' in manifest, false);

  const paths = manifest.entries.map((entry) => entry.path);
  assert.deepEqual(paths, [...paths].sort(), 'entries must be sorted by path');

  const lines = manifest.entries.map((entry) => `${entry.path} ${entry.sha256}\n`).join('');
  assert.equal(manifest.digest, crypto.createHash('sha256').update(lines, 'utf8').digest('hex'));

  /* No absolute paths anywhere in the manifest. */
  assert.equal(JSON.stringify(manifest).includes(FRONTEND_ROOT.replace(/\\/g, '\\\\')), false);
  assert.equal(JSON.stringify(manifest).includes('/home/'), false);
  assert.ok(tree.has('manifest.json'));
});

test('every manifest entry matches the bytes on disk', () => {
  const { manifest, tree } = ensureBuilt();
  for (const entry of manifest.entries) {
    const bytes = tree.get(entry.path);
    assert.ok(bytes, `manifest lists a missing file: ${entry.path}`);
    assert.equal(bytes.length, entry.size_bytes, `size mismatch for ${entry.path}`);
    assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), entry.sha256, `sha for ${entry.path}`);
    assert.equal(typeof entry.media_type, 'string');
  }
  /* Everything in the tree except the manifest itself is listed. */
  const listed = new Set(manifest.entries.map((entry) => entry.path));
  for (const rel of tree.keys()) {
    if (rel === 'manifest.json') continue;
    assert.ok(listed.has(rel), `file not in the manifest: ${rel}`);
  }
});

test('core.bundle.js carries a banner per core source and no demo mocks', () => {
  const { manifest, tree } = ensureBuilt();
  const bundle = tree.get('core.bundle.js').toString('utf8');

  assert.ok(manifest.core.length > 20, 'expected the classic script stack in the core bundle');
  for (const rel of manifest.core) {
    assert.ok(bundle.includes(`/* file: ${rel} */`), `missing banner for ${rel}`);
  }
  /* The demo mock FILES are excluded. `js/shell/recipes.js` legitimately still
   * names the mock module ids in its demo recipe, so assert on the file banners
   * and on the skip list rather than on the bare ids. */
  assert.equal(bundle.includes('/* file: js/shell/mock/'), false);
  assert.equal(manifest.core.some((rel) => rel.startsWith('js/shell/mock/')), false);
  assert.ok(manifest.skipped.some((entry) => entry.src === '/js/shell/mock/mock-alpha.js'));
  assert.ok(manifest.skipped.some((entry) => entry.src === '/js/shell/mock/mock-beta.js'));
  /* Native runtime must register real modules before boot; split artifacts remain inspectable. */
  for (const rels of Object.values(manifest.modules)) {
    for (const rel of rels) assert.ok(bundle.includes(`/* file: ${rel} */`));
  }
  assert.ok(bundle.includes("window.ACP_DSH_CREATE_RUNTIME = function"));
  /* The vendor library keeps its own injection row and is not inlined; core
   * sources may of course still mention $3Dmol. */
  assert.equal(bundle.includes('/* file: js/vendor/'), false);
  assert.equal(manifest.core.some((rel) => rel.startsWith('js/vendor/')), false);
  assert.ok(manifest.skipped.some((entry) => entry.src.includes('3Dmol')));
  assert.ok(tree.has('vendor/3Dmol-2.5.5.min.js'));
  const vendorSource = fs.readFileSync(path.join(FRONTEND_ROOT, 'js', 'vendor', '3Dmol-2.5.5.min.js'));
  assert.ok(tree.get('vendor/3Dmol-2.5.5.min.js').equals(vendorSource), 'vendor must be a verbatim copy');
});

test('per-module bundles keep document order with model.js first', () => {
  const { manifest, tree } = ensureBuilt();
  const ids = Object.keys(manifest.modules);
  assert.ok(ids.includes('task-config'));
  assert.deepEqual(manifest.modules['task-config'], [
    'js/modules/task-config/model.js',
    'js/modules/task-config/module.js',
  ]);
  for (const [id, rels] of Object.entries(manifest.modules)) {
    const bundle = tree.get(`modules/${id}.js`).toString('utf8');
    let cursor = -1;
    for (const rel of rels) {
      const at = bundle.indexOf(`/* file: ${rel} */`);
      assert.ok(at > cursor, `${id}: banner order broken at ${rel}`);
      cursor = at;
    }
  }
});

test('skel.js carries the icon sprite and every required shell id', () => {
  const { tree } = ensureBuilt();
  const skel = tree.get('skel.js').toString('utf8');
  assert.ok(skel.startsWith('window.ACP_DSH_SKELETON = "'));

  /* REQUIRED_SHELL_IDS is parsed from client/mount.js, the runtime contract
   * that refuses to boot when an id is absent; the build assertion therefore
   * cannot drift from the shipped shell. */
  for (const id of REQUIRED_SHELL_IDS) {
    assert.ok(skel.includes(id), `skel.js is missing #${id}`);
  }
  /* Dynamic tab/slot ids are prefix-checked by the runtime, so only the prefix
   * can be asserted on the built skeleton. */
  assert.ok(skel.includes('acp-tab-'), 'skel.js has no acp-tab-* markup');
  assert.ok(skel.includes('acp-slot-'), 'skel.js has no acp-slot-* markup');
  assert.ok(skel.includes('acp-i-research'), 'skel.js is missing the icon sprite');

  /* The skeleton must be a single JSON string with a balanced div tree: decode
   * it and re-count, so a scanner bug cannot ship a truncated shell. */
  const payload = skel.slice('window.ACP_DSH_SKELETON = '.length, skel.lastIndexOf(';'));
  const html = JSON.parse(payload);
  const opens = (html.match(/<div\b/g) ?? []).length;
  const closes = (html.match(/<\/div>/g) ?? []).length;
  assert.equal(opens, closes, 'unbalanced <div> tree in the extracted skeleton');
  assert.ok(html.includes('<symbol id="acp-i-research"'));
});

test('panels.css concatenates the stylesheets in document order', () => {
  const { manifest, tree } = ensureBuilt();
  const css = tree.get('panels.css').toString('utf8');

  const html = fs.readFileSync(SOURCE_HTML, 'utf8').replace(/<!--[\s\S]*?-->/g, '');
  const expected = [];
  for (const tag of html.match(/<link\b[^>]*>/gi) ?? []) {
    if (!/\brel\s*=\s*["']stylesheet["']/i.test(tag)) continue;
    const href = /\bhref\s*=\s*["']([^"']+)["']/i.exec(tag);
    if (href) expected.push(href[1].replace(/^\/+/, ''));
  }
  assert.ok(expected.length > 20);
  assert.deepEqual(manifest.css, expected);

  let cursor = -1;
  for (const rel of expected) {
    const at = css.indexOf(`/* source: ${rel} */`);
    assert.ok(at > cursor, `panels.css order broke at ${rel}`);
    cursor = at;
  }
});

test('--check is clean against a fresh build and reports drift otherwise', () => {
  const { first } = ensureBuilt();

  const clean = runBuild(['--check', '--out', first]);
  assert.equal(clean.status, 0, clean.stderr);
  assert.match(clean.stdout, /up to date/);

  /* Introduce drift and prove the check refuses it. */
  const drifted = makeTempDir('acp-dsh-drift-');
  fs.cpSync(first, drifted, { recursive: true });
  fs.appendFileSync(path.join(drifted, 'core.bundle.js'), '\n/* drift */\n');
  const drift = runBuild(['--check', '--out', drifted]);
  assert.equal(drift.status, 1);
  assert.match(drift.stderr, /core\.bundle\.js/);
});

test('the mount state is recorded and mount.js always exists', () => {
  const { manifest, tree } = ensureBuilt();
  assert.ok(manifest.mount === 'present' || manifest.mount === 'absent');
  assert.equal(manifest.mount, fs.existsSync(path.join(PLUGIN_ROOT, 'client', 'mount.js')) ? 'present' : 'absent');
  assert.ok(tree.has('mount.js'), 'mount.js must always be emitted');
});

test('the build works from any working directory', () => {
  const out = makeTempDir('acp-dsh-cwd-');
  const result = spawnSync(process.execPath, [BUILD_SCRIPT, '--out', out], {
    cwd: os.tmpdir(),
    encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.stderr);
  assert.ok(fs.existsSync(path.join(out, 'manifest.json')));
});
