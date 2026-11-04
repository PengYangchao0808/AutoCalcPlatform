# ACP 3.0 view adapter (`acp.view.v1`) — A-00 + A-32

- Contract: `frontend/js/visualization/view_model.js`
- Source adapter: `frontend/js/visualization/view_source.js`
- Fixtures: `frontend/js/visualization/fixtures/views/{structure,energy,catalog}.js`
- Tests: `frontend/js/module_tests/acp2_view_model.test.js` (27 cases)

## Envelope

Panels consume only this shape (frozen in the plan §3):

```js
{
  schema: "acp.view.v1",
  viewId, family, source: "live" | "fixture",
  availability: "ready" | "empty" | "denied" | "error",
  fixture, provenance, axes, series, frames, annotations, objects,
  groups, entries, overlay, modes, vibration, thermo, warnings
}
```

- `loading` is UI state, not a data availability. `denied` is rendered through
  the panel's error state.
- `objects` holds family payloads keyed by `geometryRef`; structure frames
  reference `objects[frame.geometryRef] = {format:"xyz", text}`.
- `catalog` carries `groups` + `entries`; entry geometry is referenced by
  `geometryRef`/`frameIndex` and loaded lazily (never embedded in the catalog).
- `overlay` carries the server mapping/RMSD; `ok:false` is a warning, not a
  hidden success — the viewer writes `data-acp-overlay-measurements="blocked"`.
- `modes`/`vibration` carry native mode indices and vectors; unavailable data
  is `empty` with a reason and no fabricated mode rows.
- `thermo` is the ensemble summary; missing values are omitted with an explicit
  “无热化学数据” state, never zero-filled.
- Missing data is declared in `warnings[]`; points keep `null` (never `0`),
  and `availability:"ready"` without data is downgraded to `empty`.
- `source:"fixture"` forces `fixture:true`; a raw field cannot hide it.

## Source adapter

```js
var adapter = ACPView.source.create({ mode, baseUrl, fetch, fixtures, location, storage });
adapter.mode;          // effective mode
adapter.health();      // { mode, ok, reason? }
adapter.load(viewId, { family, structureRef? , jobId? });
```

Mode selection: `?view=` → `localStorage["acp.view.source"]` → `mode` → `live`.
`?view=v3` auto-falls back to `live` with a console warning. An explicit
`create({mode:"v3"})` returns an adapter whose `load()` rejects with
`E_NOT_IMPLEMENTED` (reserved until BE-GAP-01).

Currently mapped live families:

| family | live endpoint | notes |
|---|---|---|
| `structure` | `GET /api/v1/jobs/{id}/files/{path}`; `…/remote-files/{path}/preview?mode=structure`; inline XYZ | exact legacy URLs, same response envelopes |
| `structure-entry` | `GET /api/v1/jobs/{id}/structure-viewer/entries/{entryId}/geometry` | catalog entry geometry; store identity is `job:entry:<id>` |
| `energy` | `GET /api/v1/jobs/{id}/energy-graph` | `nodes`/`series.values`/`x_values` → `frames`/`points`; nulls preserved; `viewType`/`xAxisMode`/`itemId` kept in provenance |

Panels: `molecule-viewer` consumes `structure` / `catalog`; `energy-profile`
consumes `energy` and renders it through the canonical `energy_chart_renderer.js`.
| `catalog` | `GET /api/v1/jobs/{id}/structure-viewer` | groups/entries; `ensemble_thermo` → `thermo`; `pending_fetch` → empty + warning, `unavailable` → error |
| `overlay` | `GET …/structure-viewer/overlay?entry_a=&entry_b=` + entry-B geometry | mapping/RMSD from the server only; `ok:false` is visible and blocks measurements |
| `vibration` | `GET …/structure-viewer/entries/{entryId}/vibrations` | modes keep native `mode_index`/vectors; `available:false` is an empty view with reason, never fake rows |

Other families return `E_FAMILY_NOT_IMPLEMENTED` rather than an empty success.

`?view=fixture` drives `molecule-viewer` without a selected task and renders the
visible fixture badge (`data-acp-fixture="true"`). With a bound task, the lazy
catalog toggle loads the fixture catalog; selecting an entry renders the fixture
structure through the same `structure-entry` path.
