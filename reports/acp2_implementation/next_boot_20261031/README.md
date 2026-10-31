# /next/ boot + tool-drawer evidence (2026-10-31)

Scope: a **real headless Edge** boot of the static `/next/` entry, a real click
on the V7 tool drawer, and a **real 390×844 mobile emulation** via CDP. This is
not the Playwright suite and not a backend/service acceptance run.

## Commands

```powershell
python -m http.server 8766 --bind 127.0.0.1 --directory frontend
& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
  --headless=new --no-sandbox --disable-gpu --disable-crash-reporter `
  --no-first-run --no-default-browser-check `
  --user-data-dir=<temp profile> --window-size=1280,900 `
  --virtual-time-budget=10000 --screenshot=desktop-1280.png `
  http://127.0.0.1:8766/next/
```

The drawer screenshot uses a same-origin iframe probe that waits for `/next/`
to load and clicks `#acp-tools-btn`.

Mobile evidence uses Edge CDP (`--remote-debugging-port=9222`) with
`Emulation.setDeviceMetricsOverride {width:390,height:844,deviceScaleFactor:2,mobile:true}`,
`Page.navigate`, `Runtime.evaluate`, and `Page.captureScreenshot`.
Measured: `innerWidth=390`, `scrollWidth=390`, `data-acp-ready=true`,
`#acp-tools-btn` present; after click `#acp-tools-drawer.hidden=false`,
`aria-expanded=true`, 12 tabs, mounted module `task-wizard`, `scrollWidth=390`.

## What the screenshots show

- `desktop-1280.png`: shell topbar, three workspace tabs, agent/viewer/task
  panels, A-63 skip-link target and the new “工具” button.
- `tool-drawer-1280.png`: drawer opens on the right, lists 12 optional modules,
  mounts `task-wizard`. The 404 banner is expected: no API backend.
- `mobile-390.png`: single-column shell at 390 px, no horizontal overflow
  (`scrollWidth=390`), stacked panels, wrapped task toolbar.
- `mobile-390-tools.png`: full-width drawer at 390 px, 12 tabs, task-wizard
  mounted, no horizontal overflow.

## CDP interaction acceptance (1280×900, clean localStorage)

`cdp-acceptance-20261031.json` + `desktop-1280-interactions.png` record a CDP
run with `Runtime`/`Log`/`Page` enabled:

- `data-acp-ready="true"`; skip-link `href="#acp-ws-research"`, activation
  focuses `#acp-ws-research`; live region text `研究`.
- Shell tabs: research selected initially; `ArrowRight` on research moves focus
  and selection to molecular and switches workspace.
- Layout dialog: `role=dialog`, `aria-modal=true`, open, focus inside; Escape
  closes it (`open=false`, `display=none`). Focus returns to the active tab
  because the probe opened the dialog with a programmatic click (no button
  focus); a real pointer click would restore the opener button.
- Tool drawer: opens, 12 tabs, mounts `task-wizard`; clicking `job-edit`
  mounts `job-edit`.
- `uncaught=0`. Console errors are only the expected 404s from the no-backend
  static server.
- `drawer-module-sweep-20261031.json`: a real-browser sweep clicked all 12
  drawer tabs; each mounted its module wrapper (`task-wizard`, `job-edit`,
  `nmr-report`, `tsmode-editor`, `s2-scan`, `node-status`, `approval-card`,
  `capability-form`, `path-view`, `pes-handoff`, `mechanism-plan`,
  `research-record`) with **0 uncaught exceptions**. The module bodies show
  their own four-state error/empty content because the static server has no API.

## Limits

- API calls return 404; catalog/job data are intentionally absent.
- The Playwright suite (`acp2_next_entry.browser.js`) is not run: Playwright is
  not installed on this host. The CDP probe covers boot, 390 px layout and the
  drawer only.
- No real PES/Batch/QC, asset save/delete, authz, or console-error scan beyond
  the captured page state.
