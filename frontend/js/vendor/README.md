3Dmol.js 2.5.5, vendored from https://registry.npmjs.org/3dmol/-/3dmol-2.5.5.tgz

Official source: https://github.com/3dmol/3Dmol.js
License: BSD-3-Clause (see 3Dmol-LICENSE).
Archive integrity: sha512-kqNHouGqq3YfW58174tdERvm0XYTmP0tavQKOqIw1ouc2OJ7epkXEFrtEkVXV0clBZT2Ze2xHRC/qxX0u0qCdw==

The workbench loads this pinned local build without a third-party network request.

## reaction-network graph dependencies

The reaction-network panel loads the following unmodified browser UMD assets
locally, in this order: `cytoscape` → `ELK` → `cytoscapeElk`. The final adapter
also auto-registers itself with Cytoscape; the module's guarded `use()` call is
idempotent for the module lifetime.

### Cytoscape.js 3.34.4

- Asset: `cytoscape-3.34.4.min.js` (from `dist/cytoscape.min.js`)
- Package: https://registry.npmjs.org/cytoscape/-/cytoscape-3.34.4.tgz
- Source: https://github.com/cytoscape/cytoscape.js/tree/e394aa858b0699a67be8f9ab426823ae1aae7813
- License: MIT (`cytoscape-LICENSE`)
- Archive integrity: `sha512-msY99tMY8iWTg+sIPGfSqDcPNOt4xcj+v/K/7qUFaCUxxlqnMkUYLBjRUzn9W5CU/R2hrmtJa5+UtovrEM2ctg==`

### elkjs 0.12.1

- Asset: `elk-0.12.1.bundled.js` (from `lib/elk.bundled.js`)
- Package: https://registry.npmjs.org/elkjs/-/elkjs-0.12.1.tgz
- Source: https://github.com/kieler/elkjs/tree/624ae90aa37110033a8af59c5ccbb292708b4317
- License expression: EPL-2.0 OR GPL-3.0-or-later; ACP uses EPL-2.0
- License/source notice: `elkjs-LICENSE`, `elkjs-NOTICE`
- Archive integrity: `sha512-IhMYKHE4S2vtx0QrhBP3lYOIKDeIiJXBrdq9MGYkuB8ZHqhrBnHNfSUndjXFewrX1CPK8chwq+2NA0K0hliYuw==`

### cytoscape-elk 2.3.0

- Asset: `cytoscape-elk-2.3.0.js` (from `dist/cytoscape-elk.js`)
- Package: https://registry.npmjs.org/cytoscape-elk/-/cytoscape-elk-2.3.0.tgz
- Source: https://github.com/cytoscape/cytoscape.js-elk/tree/30f85dd5a8b0c9682045c54d38c0b7b63cda0bef
- License: MIT (`cytoscape-elk-LICENSE`)
- Archive integrity: `sha512-1h2ZmPOy5HD2+mrfF3P2ICxfnDyPCWg/xLVs7fIjTOzdQu51ydrMtm6Sb7KnhFwLBzhGIVYI2Gbns0njggBarQ==`

No runtime CDN or frontend build step is used. The upstream packages do not
ship separate NOTICE/NOTICE.md files; `elkjs-NOTICE` is ACP's source-availability
notice for EPL-2.0 compliance.
