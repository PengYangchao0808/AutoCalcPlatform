#!/usr/bin/env node
/**
 * Deterministic asset build for @acp/dsh-panels — no bundler, no deps.
 *
 * Input : <repo>/frontend/next/index.html (the classic entry) + the files it
 *         references + <plugin>/client/ (mount.js, optional adapters/).
 * Output: <plugin>/assets/  (committed build product)
 *
 * Determinism rules: LF line endings everywhere, no timestamps, no absolute
 * paths, sorted manifest entries, two-space JSON, one trailing newline. Running
 * the build twice must produce byte-identical trees — `--check` proves it
 * against the committed tree.
 *
 * Usage:
 *   node scripts/build.mjs                       # write ./assets
 *   node scripts/build.mjs --out <dir>           # write elsewhere (tests)
 *   node scripts/build.mjs --html <path>         # alternate source entry
 *   node scripts/build.mjs --check               # rebuild + compare, non-zero on drift
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

import { MEDIA_TYPES } from '../src/static-assets.js';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN_ROOT = path.resolve(SCRIPT_DIR, '..');
/** <repo>/frontend — the classic asset root; index.html uses absolute URLs. */
const FRONTEND_ROOT = path.resolve(PLUGIN_ROOT, '..');
const DEFAULT_HTML = path.join(FRONTEND_ROOT, 'next', 'index.html');
const DEFAULT_OUT = path.join(PLUGIN_ROOT, 'assets');
const CLIENT_DIR = path.join(PLUGIN_ROOT, 'client');

const SCHEMA = 'acp2.dsh.assets.v1';
const GENERATED_BY = 'frontend/dsh-plugin/scripts/build.mjs';
const VENDOR_SOURCE = 'js/vendor/3Dmol-2.5.5.min.js';
const VENDOR_TARGET = 'vendor/3Dmol-2.5.5.min.js';

/**
 * `/js/vendor/**` is deliberately NOT concatenated into core.bundle.js.
 * The vendor library is loaded by its own injection row
 * (`<uiBase>/vendor/3Dmol-2.5.5.min.js`), so bundling it as well would execute
 * it twice and redefine window.$3Dmol under the shell. Flip this to false to
 * get the literal "everything under /js/" reading of the spec.
 */
const VENDOR_IN_CORE_BUNDLE = false;

// ── small helpers ──────────────────────────────────────────────────────────

const toLf = (text) => text.replace(/\r\n?/g, '\n');
const sha256 = (buffer) => crypto.createHash('sha256').update(buffer).digest('hex');

function readText(absPath) {
  return toLf(fs.readFileSync(absPath, 'utf8'));
}

function mediaTypeFor(relPath) {
  return MEDIA_TYPES[path.extname(relPath).toLowerCase()] ?? 'application/octet-stream';
}

/** Strip HTML comments so tag scanners never see markup quoted in prose. */
function stripComments(html) {
  return html.replace(/<!--[\s\S]*?-->/g, '');
}

/** Index of the '>' that closes the tag opened at `from`, honouring quotes. */
function findTagEnd(html, from) {
  let quote = null;
  for (let i = from; i < html.length; i += 1) {
    const ch = html[i];
    if (quote) {
      if (ch === quote) quote = null;
    } else if (ch === '"' || ch === "'") {
      quote = ch;
    } else if (ch === '>') {
      return i;
    }
  }
  return -1;
}

// ── parsing ────────────────────────────────────────────────────────────────

/** Stylesheet hrefs in document order. */
function extractStylesheets(html) {
  const hrefs = [];
  const tagRe = /<link\b[^>]*>/gi;
  let match;
  while ((match = tagRe.exec(html)) !== null) {
    const tag = match[0];
    if (!/\brel\s*=\s*["']stylesheet["']/i.test(tag)) continue;
    const href = /\bhref\s*=\s*["']([^"']+)["']/i.exec(tag);
    if (href) hrefs.push(href[1]);
  }
  return hrefs;
}

/** `<script src>` values in document order (inline scripts have no src). */
function extractScripts(html) {
  const srcs = [];
  const tagRe = /<script\b[^>]*>/gi;
  let match;
  while ((match = tagRe.exec(html)) !== null) {
    const src = /\bsrc\s*=\s*["']([^"']+)["']/i.exec(match[0]);
    if (src) srcs.push(src[1]);
  }
  return srcs;
}

const MODULE_RE = /^\/js\/modules\/([^/]+)\/([^/]+\.js)$/;

/**
 * Split the script list into the core bundle, per-module bundles and skips.
 * Module files keep document order except that `model.js` always precedes
 * `module.js` (a module's model must be defined before it registers).
 */
function classifyScripts(srcs) {
  const core = [];
  const modules = new Map();
  const skipped = [];

  for (const src of srcs) {
    if (/^\/js\/shell\/mock\//.test(src)) {
      skipped.push({ src, reason: 'demo mock, not shipped' });
      continue;
    }
    if (/^\/js\/vendor\//.test(src) && !VENDOR_IN_CORE_BUNDLE) {
      skipped.push({ src, reason: 'loaded by its own injection row (see manifest.vendor)' });
      continue;
    }
    const moduleMatch = MODULE_RE.exec(src);
    if (moduleMatch) {
      const [, id, file] = moduleMatch;
      if (!modules.has(id)) modules.set(id, []);
      modules.get(id).push(file);
      continue;
    }
    if (/^\/js\//.test(src)) {
      core.push(src);
      continue;
    }
    skipped.push({ src, reason: 'not under /js/' });
  }

  for (const [id, files] of modules) {
    const rank = (file) => (file === 'model.js' ? 0 : 1);
    modules.set(
      id,
      files
        .map((file, index) => ({ file, index }))
        .sort((a, b) => rank(a.file) - rank(b.file) || a.index - b.index)
        .map((entry) => entry.file),
    );
  }

  return { core, modules, skipped };
}

/**
 * The `<svg>` sprite that carries the `<symbol id="acp-i-…">` icon defs.
 * @returns {string|null}
 */
function extractIconSprite(html) {
  const tagRe = /<svg\b[^>]*>/gi;
  let match;
  while ((match = tagRe.exec(html)) !== null) {
    const start = match.index;
    const end = html.indexOf('</svg>', start);
    if (end === -1) break;
    const block = html.slice(start, end + '</svg>'.length);
    if (/<symbol\b[^>]*\bid\s*=\s*["']acp-i-/.test(block)) return block;
    tagRe.lastIndex = end + '</svg>'.length;
  }
  return null;
}

/**
 * The `<div class="acp-shell" id="acp-shell" …>` element, up to and including
 * its MATCHING `</div>`. Depth is counted on `div` tags only, with a quote-aware
 * tag scanner so attribute values cannot break the count.
 * @returns {string|null}
 */
function extractShellMarkup(html) {
  const anchor = /\bid\s*=\s*["']acp-shell["']/.exec(html);
  if (!anchor) return null;
  const start = html.lastIndexOf('<', anchor.index);
  if (start === -1) return null;

  let depth = 0;
  let i = start;
  while (i < html.length) {
    const lt = html.indexOf('<', i);
    if (lt === -1) return null;

    if (html.startsWith('<!--', lt)) {
      const end = html.indexOf('-->', lt);
      if (end === -1) return null;
      i = end + 3;
      continue;
    }

    const gt = findTagEnd(html, lt);
    if (gt === -1) return null;
    const tag = html.slice(lt, gt + 1);

    if (tag.startsWith('</')) {
      const closing = /^<\/\s*([A-Za-z][A-Za-z0-9-]*)/.exec(tag);
      if (closing && closing[1].toLowerCase() === 'div') {
        depth -= 1;
        if (depth === 0) return html.slice(start, gt + 1);
      }
      i = gt + 1;
      continue;
    }

    const opening = /^<\s*([A-Za-z][A-Za-z0-9-]*)/.exec(tag);
    if (opening && opening[1].toLowerCase() === 'div' && !/\/\s*>$/.test(tag)) {
      depth += 1;
    }
    i = gt + 1;
  }
  return null;
}

// ── build ──────────────────────────────────────────────────────────────────

const cssBanner = (rel) => `/* source: ${rel} */`;
const jsBanner = (rel) => `/* file: ${rel} */`;

function buildCss(stylesheetHrefs) {
  const parts = stylesheetHrefs.map((href) => {
    const rel = href.replace(/^\/+/, '');
    const absolute = path.join(FRONTEND_ROOT, rel);
    return `${cssBanner(rel)}\n${readText(absolute)}`;
  });
  /* CSS needs no separator between files; a newline keeps banners legible. */
  // Chromium's native CSS scope contains universal selectors and rules nested
  // in media queries as well. Page roots become the embedding container.
  const css = parts.join('\n').replace(/:root\b/g, ':scope')
    .replace(/(^|[{},\n])(\s*)(?:html|body)(?=[\s,{.:#>+~\[])/g, '$1$2:scope');
  return `@scope (.acp-dsh-host) {\n${css}\n}\n`
    + readText(path.join(CLIENT_DIR, 'host.css'));
}

function buildJsBundle(relPaths) {
  const parts = relPaths.map((rel) => `${jsBanner(rel)}\n${readText(path.join(FRONTEND_ROOT, rel))}`);
  return `${parts.join('\n;\n')}\n`;
}

function buildRuntimeBundle(relPaths) {
  const sources = relPaths.map((rel) => ({ rel, text: readText(path.join(FRONTEND_ROOT, rel)) }));
  const names = [...new Set(sources.flatMap(({ text }) =>
    [...text.matchAll(/window\.(ACP\w+)\s*=/g)].map((match) => match[1])))].sort();
  const aliases = names.map((name) => `var ${name} = window.${name} = {};`).join('\n');
  const sync = names.map((name) => `${name} = window.${name};`).join('\n');
  const body = sources.map(({ rel, text }) => `${jsBanner(rel)}\n(function () {\n${text}\n}).call(window);\n${sync}`).join('\n;\n');
  // Re-evaluate the same shipped code in a fresh mount scope, without eval,
  // duplicated DOM roots, or publishing ACP singletons on the DSH window.
  return `window.ACP_DSH_CREATE_RUNTIME = function (window) {\n`
    + `var document = window.document, globalThis = window, self = window;\n`
    + `var setTimeout = window.setTimeout, clearTimeout = window.clearTimeout;\n`
    + `var setInterval = window.setInterval, clearInterval = window.clearInterval;\n`
    + `var fetch = function () { return window.fetch.apply(window, arguments); };\n`
    + `${aliases}\n${body}\nreturn window;\n};\n`;
}

const MOUNT_PLACEHOLDER = `/* PLACEHOLDER — client/mount.js is absent in this checkout.
   The DSH client half (mount + adapters) is built by a separate workstream.
   assets/manifest.json records "mount": "absent" while this file is a stub. */
(function () {
  var message = '[acp-panels] client/mount.js is absent: the DSH client mount was not built. '
    + 'assets/manifest.json records mount:"absent". Build/install the client half and re-run '
    + 'node scripts/build.mjs.';
  if (typeof window !== 'undefined') {
    window.ACP_DSH_MOUNT = { ok: false, code: 'mount_absent', message: message };
  }
  throw new Error(message);
})();
`;

/**
 * @param {{htmlPath:string}} options
 * @returns {{files: Map<string, Buffer>, manifest: object}}
 */
function buildAssets(options) {
  const html = stripComments(readText(options.htmlPath));

  const stylesheetHrefs = extractStylesheets(html);
  const scriptSrcs = extractScripts(html);
  const { core, modules, skipped } = classifyScripts(scriptSrcs);

  const coreRels = core.map((src) => src.replace(/^\/+/, ''));
  const moduleRels = new Map(
    [...modules].map(([id, files]) => [
      id,
      files.map((file) => `js/modules/${id}/${file}`),
    ]),
  );
  const cssRels = stylesheetHrefs.map((href) => href.replace(/^\/+/, ''));

  /** @type {Map<string, Buffer>} */
  const files = new Map();
  const textFile = (rel, text) => files.set(rel, Buffer.from(text, 'utf8'));

  textFile('panels.css', buildCss(stylesheetHrefs));
  const adapterSourceRels = ['env', 'base-url', 'restricted-client', 'theme', 'focus-bridge']
    .map((name) => `js/adapters/dsh/${name}.js`);
  textFile('core.bundle.js', buildRuntimeBundle([
    ...adapterSourceRels, ...coreRels, ...[...moduleRels.values()].flat(),
  ]));
  for (const [id, rels] of moduleRels) {
    textFile(`modules/${id}.js`, buildJsBundle(rels));
  }

  const sprite = extractIconSprite(html);
  const shell = extractShellMarkup(html);
  if (sprite === null) throw new Error('build: could not find the acp-i-* svg sprite in the source HTML');
  if (shell === null) throw new Error('build: could not find <div id="acp-shell"> in the source HTML');
  const skeleton = `${sprite}${shell}`;
  textFile('skel.js', `window.ACP_DSH_SKELETON = ${JSON.stringify(skeleton)};\n`);

  /* Vendor library: copied verbatim (bytes untouched — it is a third-party artifact). */
  const vendorSource = path.join(FRONTEND_ROOT, VENDOR_SOURCE);
  if (!fs.existsSync(vendorSource)) throw new Error(`build: missing vendor source ${VENDOR_SOURCE}`);
  files.set(VENDOR_TARGET, fs.readFileSync(vendorSource));

  /* Client half: copied verbatim when present, stubbed when a sibling
   * workstream has not landed it yet — a missing mount must not fail the build,
   * but it must be visible in the manifest. */
  const mountSource = path.join(CLIENT_DIR, 'mount.js');
  const mountState = fs.existsSync(mountSource) ? 'present' : 'absent';
  files.set('mount.js', mountState === 'present' ? fs.readFileSync(mountSource) : Buffer.from(MOUNT_PLACEHOLDER, 'utf8'));

  const adapterRels = [];
  const adaptersSource = path.join(FRONTEND_ROOT, 'js', 'adapters', 'dsh');
  if (fs.existsSync(adaptersSource)) {
    for (const entry of fs.readdirSync(adaptersSource, { withFileTypes: true })) {
      if (!entry.isFile() || !entry.name.endsWith('.js')) continue;
      files.set(`adapters/${entry.name}`, fs.readFileSync(path.join(adaptersSource, entry.name)));
      adapterRels.push(`adapters/${entry.name}`);
    }
    adapterRels.sort();
  }

  files.set('runtime-scope.js', fs.readFileSync(path.join(CLIENT_DIR, 'runtime-scope.js')));

  const entries = [...files.keys()]
    .sort()
    .map((rel) => ({
      path: rel,
      sha256: sha256(files.get(rel)),
      size_bytes: files.get(rel).length,
      media_type: mediaTypeFor(rel),
    }));

  const digest = sha256(
    Buffer.from(entries.map((entry) => `${entry.path} ${entry.sha256}\n`).join(''), 'utf8'),
  );

  const manifest = {
    schema: SCHEMA,
    generated_by: GENERATED_BY,
    entries,
    digest,
    core: coreRels,
    modules: Object.fromEntries([...moduleRels].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))),
    css: cssRels,
    mount: mountState,
    vendor: [{ path: VENDOR_SOURCE, target: VENDOR_TARGET }],
    adapters: adapterRels,
    skipped,
  };

  files.set('manifest.json', Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`, 'utf8'));
  return { files, manifest };
}

// ── output / comparison ────────────────────────────────────────────────────

function writeTree(outDir, files) {
  fs.mkdirSync(outDir, { recursive: true });
  for (const [rel, buffer] of files) {
    const absolute = path.join(outDir, rel);
    fs.mkdirSync(path.dirname(absolute), { recursive: true });
    fs.writeFileSync(absolute, buffer);
  }
}

/** @returns {Map<string,Buffer>} every regular file under `dir`, dir-relative, sorted. */
function readTree(dir) {
  const found = new Map();
  if (!fs.existsSync(dir)) return found;
  const walk = (current) => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const absolute = path.join(current, entry.name);
      if (entry.isDirectory()) {
        walk(absolute);
        continue;
      }
      if (!entry.isFile()) continue;
      const rel = path.relative(dir, absolute).split(path.sep).join('/');
      found.set(rel, fs.readFileSync(absolute));
    }
  };
  walk(dir);
  return new Map([...found].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
}

function compareTrees(expected, actual) {
  const report = [];
  for (const [rel, buffer] of expected) {
    if (!actual.has(rel)) {
      report.push({ path: rel, kind: 'missing' });
      continue;
    }
    if (!actual.get(rel).equals(buffer)) report.push({ path: rel, kind: 'differs' });
  }
  for (const rel of actual.keys()) {
    if (!expected.has(rel)) report.push({ path: rel, kind: 'unexpected' });
  }
  return report.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
}

// ── CLI ────────────────────────────────────────────────────────────────────

function parseArgs(argv) {
  const options = { htmlPath: DEFAULT_HTML, out: DEFAULT_OUT, check: false, help: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--check') options.check = true;
    else if (arg === '--help' || arg === '-h') options.help = true;
    else if (arg === '--out') {
      const value = argv[i + 1];
      if (!value) throw new Error('--out requires a directory');
      options.out = path.resolve(process.cwd(), value);
      i += 1;
    } else if (arg === '--html') {
      const value = argv[i + 1];
      if (!value) throw new Error('--html requires a file path');
      options.htmlPath = path.resolve(process.cwd(), value);
      i += 1;
    } else {
      throw new Error(`unknown argument: ${arg}`);
    }
  }
  return options;
}

function main(argv) {
  let options;
  try {
    options = parseArgs(argv);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    return 2;
  }
  if (options.help) {
    process.stdout.write(
      'node scripts/build.mjs [--out <dir>] [--html <path>] [--check]\n',
    );
    return 0;
  }

  const { files, manifest } = buildAssets({ htmlPath: options.htmlPath });

  if (!options.check) {
    writeTree(options.out, files);
    process.stdout.write(
      `built ${files.size} files -> ${path.relative(FRONTEND_ROOT, options.out) || options.out} `
        + `(digest ${manifest.digest.slice(0, 16)}, mount: ${manifest.mount})\n`,
    );
    return 0;
  }

  /* --check: rebuild into a temp dir, then compare byte-for-byte with --out. */
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'acp-dsh-assets-'));
  try {
    writeTree(tempRoot, files);
    const drift = compareTrees(readTree(tempRoot), readTree(options.out));
    if (drift.length === 0) {
      process.stdout.write(`assets are up to date (${files.size} files, digest ${manifest.digest.slice(0, 16)})\n`);
      return 0;
    }
    process.stderr.write(`asset drift against ${options.out}:\n`);
    for (const entry of drift) process.stderr.write(`  ${entry.kind.padEnd(10)} ${entry.path}\n`);
    process.stderr.write('re-run: node scripts/build.mjs\n');
    return 1;
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
}

process.exitCode = main(process.argv.slice(2));
