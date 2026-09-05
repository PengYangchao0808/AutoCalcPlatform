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

[0.1.0]: https://github.com/PengYangchao0808/AutoCalcPlatform/releases/tag/v0.1.0
