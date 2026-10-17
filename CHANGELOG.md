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

## [0.2.0] - 2026-10-13

Release: NMR expansion, end-to-end memory budget chain, internal-coordinate
scans, and structure-viewer v2 — 600 commits since 0.1.3.

### Added — NMR

- DP4/DP5 pipeline expansion across assignment, scaling, spectra, and
  probability.
- FCHL atom representations (NumPy opt-in) with availability, neighbor, and
  domain limits.
- protocol-transfer A/B experiment harness and benchmark corpus.
- QC smoke gates for CENSO with an explicit NOT_VERIFIED tri-state (never a
  pass).

### Added — memory/resource chain

- job resources resolved once at the scheduler boundary (unitless GB →
  explicit MB), threaded through task cores, backends, LSF scripts, and
  provenance.
- ORCA per-process maxcore budgeting from the total memory budget
  (contradictory pins rejected); CENSO child budgeting; LSF per-process
  RLIMIT_AS.
- SCF convergence floor: registry-ordered tokens promote SCF below the method
  constraint (DLPNO); SCF directives in free-form `route_extras` are not
  duplicated.

### Added — internal-coordinate scans

- distance / angle / dihedral scan coordinates across the CLI, scheduler
  validation, catalog schema, and the workbench wizard.

### Added — structure viewer v2

- single-column result-first workspace, collapsible vibration dock, IRC frame
  projection and playback, overlay/RMSD, edited-structure assets, and frame
  candidates.
- ensemble thermodynamics in the viewer payload (conformers stripped) and
  remote catalog fetch with backend prefetch on terminal transitions.
- vendored 3Dmol (no CDN dependency) with revalidating static assets.

### Added — remote execution

- scheduler markers uploaded before `bsub` (flat v2 layout), attempt-fenced
  result cache, capability-filtered node selection, bounded task views.
- PES remote read root and review write-back; structure sources from terminal
  jobs; task custom names.

### Added — method validation & keywords

- unified QC keyword registry as the single source of truth and
  method-family-aware field gating (backend + wizard).
- case-insensitive enumerated keywords folded at exactly three choke points;
  end-to-end method-validation warnings.
- new workflows: XtbPathSearch and OrcaGradient.
- CLI: `acp init` interactive setup wizard (local/remote sniffing, TOFU
  connect test, merge-safe config persistence).

### Added — job editing

- edit-parameter projection: edited level fields reach every execution alias
  before validation/diff/fingerprint/submission; dedicated-contract workflows
  (tsmode / XtbPathSearch / OrcaGradient) surface targeted migration hints.

### Fixed

- remote execution integrity: pause/cancel intent never overwritten by stale
  polls; CANCELLING requires a confirmed remote stop; terminal side effects are
  idempotent and ordered after the terminal persistence.
- MPI ABI compatibility: ORCA-bundled OpenMPI is preferred over conda
  Hydra/MPICH; incompatible launchers are rejected with actionable errors.
- structure viewer: canvas sizing, annotation collisions, stale-bundle caching
  (frontend assets now revalidate), SSE and catalog prefetch on terminal
  transitions.
- scheduler: tasks projection bounded and self-healing on rerun; scan
  submission validated before queueing (422).

### Docs & audits

- API/web response audit retest issue (2026-10-12) with backend/browser repro
  harness.
- CCCP code audit (2026-10-11) with AST inventory and evidence.
- functional test manual for real computation task submission.
- AGENTS anti-patterns extended (remote scheduler markers, keyword folding,
  pending-fetch prefetch, read-root, API timeouts, edit effectiveness).

## [0.1.3] - 2026-09-07

Release: energy & trajectory viewer, CASSCF/electronic-state support, and
optimization trajectory repair.

### Added — Energy & Trajectory viewer

- unified trajectory-frame visualization for PES scans, geometry optimizations,
  conformer search, and standalone scans.
- generic frame inspector with lock / export / save-as-candidate operations.
- optimization convergence-criteria panel (RMS/MAX gradient & displacement vs
  threshold).
- conformer sampling history (MD energy trajectory, sampling-space map,
  saturation coverage) for xtb-md protocols.

### Added — CASSCF / electronic-state

- CASSCF/NEVPT2 calculation support with spin diagnostics parsing.
- structured %scf rendering for output analysis.
- electronic-state contracts and product kinds.
- batch item×state expansion for multi-state workflows.
- casscf CLI / workflow entry point.

### Fixed — optimization trajectory repair

- dangling geometry_ref recovery for robust trajectory capture.
- recorder hardening against edge-case state transitions.
- batch FIFO dispatch fix for correct task ordering.

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

[0.1.3]: https://github.com/PengYangchao0808/AutoCalcPlatform/releases/tag/v0.1.3
[0.1.2]: https://github.com/PengYangchao0808/AutoCalcPlatform/releases/tag/v0.1.2
[0.1.1]: https://github.com/PengYangchao0808/AutoCalcPlatform/releases/tag/v0.1.1
[0.1.0]: https://github.com/PengYangchao0808/AutoCalcPlatform/releases/tag/v0.1.0
