# ACP2 Plan A — Capability, Version and Limitation Support Matrix (A-29)

Status: **release-scope support record, static/constructed only**. This matrix is
published with `handoff-H7.json`. It is not a product release and it does not
claim that any capability has been executed with a real QC binary.

Source of truth:

- built-in catalog: `src/cccp/calculation/capabilities/builtin.py` (A-05b, frozen
  at four capabilities);
- domain extension: `src/cccp/calculation/capabilities/domains.py` (A-24/A-24b,
  `DOMAIN_EXTENSION_VERSION = 1`);
- task inventory: `src/cccp/calculation/requests.py` (`TaskKind`, 15 members) and
  `docs/reference/next-change/acp2-contracts/science/task-compatibility-matrix.md`.

Contract set: `acp2.science`, version `1`, frozen. Capability descriptors use
`capability.schema.json` (`schema_version` 1). The detailed fifteen-task matrix
lives in `task-compatibility-matrix.md`; this file only states the release-facing
support position and deliberately does not duplicate that matrix.

## 1. Capabilities (exactly seven)

| # | Capability | Kind | Task kind / implementation | Schema version | Contract state | Real-run state | Limitations |
|---|---|---|---|---|---|---|---|
| 1 | `electronic.energy` | compute | `singlepoint` → `cccp.calculation.tasks.singlepoint` | capability.schema v1 | `verified-static` | `NOT_VERIFIED` | Evaluates one supplied geometry; no relaxation. No real ORCA single point was run. |
| 2 | `geometry.optimize` | compute | `optimize` → `cccp.calculation.tasks.optimize` | capability.schema v1 | `verified-static` | `NOT_VERIFIED` | A converged geometry is not a minimum; a separate `vibration.compute` result is required. No real optimizer run. |
| 3 | `vibration.compute` | compute | `frequency` → `cccp.calculation.tasks.frequency` | capability.schema v1 | `verified-static` | `NOT_VERIFIED` | Imaginary modes are curvature facts, not a reaction path. No real frequency run. |
| 4 | `vibration.analyze` | pure_analysis | `cccp.analysis.frequency_products.build_frequency_report` | capability.schema v1 | `verified-static` | `not_applicable` | Reads existing frequency/Hessian products only; never launches QC. |
| 5 | `nmr.shielding` | compute | `nmr_shielding` → `cccp.calculation.tasks.nmr_shielding` | capability.schema v1 + domain extension v1 | `constructed` | `NOT_VERIFIED` | Stops at the GIAO shielding table. No real shielding computation or spectrum exists. |
| 6 | `nmr.compare` | pure_analysis | `cccp.analysis.nmr.comparison` | capability.schema v1 + domain extension v1 | `constructed` | `NOT_VERIFIED` | Compares an existing table with a supplied peak list; no experimental quantitative claim. |
| 7 | `thermochemistry.ensemble` | pure_analysis | `cccp.analysis.ensemble.science` | capability.schema v1 + domain extension v1 | `constructed` | `NOT_VERIFIED` | Weights are caller-supplied and recorded, never re-derived; one declared temperature only. |

State semantics:

- `verified-static` — the descriptor, binding, preconditions and completion checks
  are backed by executed static tests; no real scientific execution is claimed.
- `constructed` — the contract behaviour is verified only through constructed
  fixtures; no real domain run exists.
- `NOT_VERIFIED` — real evidence is absent. A `NOT_VERIFIED` capability must never
  be rendered as verified.

All four built-in compute/analysis capabilities are `verified-static`. The three
domain capabilities are `constructed`: H6 verified their contracts with
constructed NMR/thermochemistry fixtures and `real_run=false`, while selectivity
and C T-40b remain `NOT_VERIFIED`.

## 2. Fifteen task kinds

`TaskKind` has exactly fifteen members, each with a `run_*` task core under
`src/cccp/calculation/tasks/`:

`singlepoint`, `optimize`, `frequency`, `scan`, `irc`, `casscf`, `thermochemistry`,
`conformer_search`, `md_sampling`, `clustering`, `censo_refine`, `nmr_shielding`,
`xtb_path_search`, `orca_gradient`, `xtb_constrained_opt`.

Every one is `implemented` with a `QC-subprocess` mechanism; none is
`pure-analysis` (analysis is a capability, not a task kind). No task kind was
launched for this matrix. See `task-compatibility-matrix.md` for the full
per-kind entry point, options/payload types and export status.

## 3. Unsupported capability families

The frozen built-in catalog declares ten families as unsupported because it is
pinned at four capabilities:

`casscf`, `clustering`, `censo_refine`, `conformer_search`,
`constrained_optimization`, `gradient`, `md_sampling`, `nmr`, `reaction_path`,
`thermochemistry`.

The A-24 domain extension supersedes the `nmr` and `thermochemistry` entries for
the three explicitly seeded domain capabilities above. Families that still have
**no capability descriptor** are therefore:

`casscf`, `clustering`, `censo_refine`, `conformer_search`,
`constrained_optimization`, `gradient`, `md_sampling`, `reaction_path`.

A direct `TaskKind` reference remains possible for those task kinds, but
`bind_capability` raises the typed `UnsupportedBindingError` instead of inventing
a capability route.

## 4. Non-claims

- No real QC run, scheduler job, LSF submission, platform task or production
  service is claimed.
- No B25a release-candidate wheel exists; the A-27b/A-28 probes use a
  reproducible **local candidate** wheel only.
- Real ORCA, real NMR/thermochemistry, DA TS/IRC/energy, selectivity and
  C T-40b signoff remain `NOT_VERIFIED`; H7 therefore reports
  `release_readiness.ready = false`.
