/**
 * DSH index-HTML injection rows for the ACP panels.
 *
 * The DSH index-inject table has an EXHAUSTIVE row-kind union; this module only
 * emits kinds that exist:
 *   global | script | script-src | script-preload | style | html
 * There is no `style-src` kind, so the stylesheet is an `html` row.
 *
 * Everything that reaches the browser is JSON-serialisable and contains no
 * secret: the API key stays on the host and is only ever written into an
 * outgoing proxy header.
 */

/**
 * Ordered client script stack, all relative to `uiBase`. The order is a hard
 * contract: adapters install the transport seam before the bundle boots, the
 * bundle defines the shell, and `mount.js` performs the actual mount.
 */
export const CLIENT_SCRIPTS = Object.freeze([
  'runtime-scope.js',
  'core.bundle.js',
  'mount.js',
]);

export const VENDOR_SCRIPT = 'vendor/3Dmol-2.5.5.min.js';

/** Escape for an HTML attribute value (double- or single-quoted). */
export function escapeAttr(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function assertNormalisedMountPath(label, value) {
  if (typeof value !== 'string' || !value.startsWith('/') || value === '/' || value.endsWith('/')) {
    throw new Error(`buildInjectionRows: ${label} must be a normalised mount path (got ${String(value)})`);
  }
  if (value.includes('..') || value.includes('//')) {
    throw new Error(`buildInjectionRows: ${label} is not normalised (got ${String(value)})`);
  }
}

/**
 * Build the ordered injection row list for one normalised config.
 * @param {{uiBase:string, apiPrefix:string, readOnly:boolean}} config
 * @returns {Array<object>} rows, in emit order
 */
export function buildInjectionRows(config) {
  assertNormalisedMountPath('uiBase', config.uiBase);
  assertNormalisedMountPath('apiPrefix', config.apiPrefix);

  const { uiBase, apiPrefix, readOnly } = config;

  const rows = [
    {
      kind: 'global',
      name: '__ACP_PANELS_CONFIG__',
      value: {
        uiBase,
        apiPrefix,
        readOnly: readOnly === true,
        controllerAvailable: true,
        focusEndpoint: `${apiPrefix}/ui/focus`,
        healthEndpoint: `${apiPrefix}/health`,
        assetBase: uiBase,
      },
    },
    {
      /* No `style-src` kind exists in DSH: a stylesheet link is raw head markup. */
      kind: 'html',
      placement: 'head',
      html: `<link rel="stylesheet" href="${escapeAttr(`${uiBase}/panels.css`)}" data-acp-dsh-css>`,
    },
    {
      /* Plain src: DSH applies no async/defer semantics here, and the loaders
       * that consume this row tolerate the library arriving late. */
      kind: 'script-src',
      placement: 'body',
      src: `${uiBase}/${VENDOR_SCRIPT}`,
    },
  ];

  for (const relative of CLIENT_SCRIPTS) {
    rows.push({ kind: 'script-src', placement: 'body', src: `${uiBase}/${relative}` });
  }

  return rows;
}

/**
 * Defence in depth: refuse to emit rows that would leak the resolved API key
 * into the served HTML.
 * @param {Array<object>} rows
 * @param {string|undefined} secret resolved key (never logged)
 * @throws {Error} when any row's serialised form contains the secret
 */
export function assertNoSecret(rows, secret) {
  if (typeof secret !== 'string' || secret.length === 0) return;
  for (const row of rows) {
    if (JSON.stringify(row).includes(secret)) {
      throw new Error('buildInjectionRows: refusing to inject a row that contains the resolved API key');
    }
  }
}
