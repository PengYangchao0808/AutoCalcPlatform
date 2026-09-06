# Changelog

All notable changes to the Auto-Calc Platform (ACP) are documented here.

## Versioning policy (0.N.X)

- **0** — major pinned at zero until the accompanying publication is released.
- **N** — minor; bumped per external sync milestone and kept in lockstep with
  the GitHub Release sequence (`v0.N.*`).
- **X** — patch; bumped for every small fix merged between milestones.

The git tag `v0.N.X` on `main` is the single source of truth; the four
in-repo version sites (`pyproject.toml`, `src/acp/__init__.py`,
`src/cccp/__init__.py`, `src/cccp/version.py`) must always equal the latest tag.

## [0.1.2] - 2026-09-06

Patch release: post-milestone frontend and API fixes.

### Fixes

- optimization: rebuild optimization energy viewer and trajectory capture.
- api: upload parses via detect_and_parse candidate-fallback chain.
- frontend: task-result scope, parse race and empty-state clarity in wizard.
- frontend: collision-aware annotation layout for energy chart.

## [0.1.1] - 2026-09-05

Patch release: CI enablement repairs and post-milestone fixes.

### Fixes

- ci: install `.[dev,api,remote]` extras + nmrglue — CI was all-red since
  enablement (fastapi/paramiko imported at test collection; the full `[nmr]`
  extra is not CI-installable).
- compat: py3.10 matrix leg — `datetime.UTC` → `timezone.utc` alias (2 sites),
  `typing.NotRequired` → `total=False` TypedDict subclass,
  `assert_never` via `typing_extensions` (matching the existing pattern).
- test: mock the centralized `resolve_executable` in Shermo path-limit tests
  (binary discovery is not what those tests pin).
- pes: stabilize energy-graph outputs; frontend energy chart is now fully
  responsive (min-width 0 + overflow hidden) with the regression guard
  updated to the new contract.
- scheduler: structure-source filter honours the PEB manual-review manifest
  format (`pes_candidate_*` ids, nested metadata, RESULT-relative paths).

### Governance

- `main` branch protection enabled: PR + all three CI matrix legs required,
  no force-push, no deletion.
- Legacy branches pruned; `legacy/pre-refactor` retained read-only (v0.0.0).

## [0.1.0] - 2026-09-05

First external sync milestone: post-refactor minimal architecture
(`refactor/calc-cleanup`, ~120 commits ahead of the previous `main`).

### Highlights

- Ten active workflows: Confsearch (4 protocols), PESsearch, BatchOptimize,
  irc, scan, nmr (DP4/DP5), and simple (singlepoint/optimize/frequency/xtb-optimize);
  14 legacy workflow entries retired to read-only catalog display.
- Calculation primitives consolidated under `acp/calculations/` (sp/opt/freq/
  scan/irc/thermochemistry + plan executor + checkpoint resume).
- Scheduler: `PAUSED` lifecycle (SIGSTOP/SIGCONT, LSF bstop/bresume),
  checkpoint continue / rerun / cascade purge, job-detail recovery matrix.
- Remote LSF execution: SSH pool, incremental code sync, bsub/bjobs,
  result fetch, retention cleanup.
- Two-layer directory contract: run_root resolution via
  `acp/core/paths.py::resolve_run_root` (native filesystem only).
- Unified v2 `result_manifest.json` read/write + read-only legacy compat layer.

### Fixes

- scheduler: single-instance run_root guard + peer-aware restart recovery
  (a second server booting against the same run_root no longer kills the
  owner's healthy RUNNING jobs).
- pes: split SP-stage nproc budget across batch workers (per-job cap 4 cores)
  to prevent MPI oversubscription gridlock.
- cli: preflight warning now states that a missing executable only affects
  engine configurations that actually call it.

[0.1.2]: https://github.com/PengYangchao0808/AutoCalcPlatform/releases/tag/v0.1.2
[0.1.1]: https://github.com/PengYangchao0808/AutoCalcPlatform/releases/tag/v0.1.1
[0.1.0]: https://github.com/PengYangchao0808/AutoCalcPlatform/releases/tag/v0.1.0
