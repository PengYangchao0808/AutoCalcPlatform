# ACP2 Plan A — Deliverable Path Map and Ownership Boundary

Baseline: delivery revision `3bf5127365e63012eb0a9915467883433e7ad0cd` on `acp2/a-science`
(see `baseline.md`). All paths below are relative to the A worktree
`/opt/acp2-dev/A` unless prefixed with `$ACP2_EVIDENCE`.

Rule from the plan: every path marked **new** was searched against the frozen baseline first. If an
equivalent module already exists, the existing implementation is extended and this map records the
final landing point — no second implementation is created by copying a new name.

## 1. Path families owned by A

| Plan family | Concrete paths | Tasks | Status in baseline |
|---|---|---|---|
| `src/cccp/**` — capabilities | `src/cccp/calculation/capabilities/__init__.py` (exports only), `contracts.py`, `registry.py`, `builtin.py`, `task_binding.py` | A-04, A-05 | **new directory**; must adapt to existing `calculation/{requests,context,results,contracts}.py` and `tasks/` |
| `src/cccp/**` — recipes | `src/cccp/calculation/recipes/__init__.py` (exports only), `contracts.py`, `validation.py`, `compile.py` | A-06 | **new directory**; compiles to existing `TaskRequest` DAG, never a second executor |
| `src/cccp/**` — research science types | `src/cccp/analysis/research/__init__.py` (exports only), `reaction.py`, `question.py`, `evidence.py`, `provenance.py`, `validators.py` | A-02, A-03, A-11, A-12 | **new directory**; reuse `StructureArtifact`, existing electronic-state/method definitions, `analysis/` products |
| `src/cccp/**` — plugin extension | `src/cccp/plugin/metadata.py`, `discovery.py`, `host.py`, `sdk.py` (existing files) | A-23 | **existing**; extend metadata/schema only — discovery stays metadata-only, `cccp.calculation` stays the single execution station |
| `src/cccp/**` — bounded science products | `src/cccp/analysis/bounded/{identity,manifest,multiresolution}.py` (existing) | A-20/A-26 consumers | **existing** (present in B00 candidate); read/extend, do not duplicate |
| `src/cccp/**` — shared contracts | `src/cccp/calculation/{requests,context,results,contracts}.py`, `calculation/tasks/` (existing) | all | **existing**; A adds versioned compatible fields only; `TaskRequest/TaskContext/TaskResult` and the 15 task cores remain authoritative |
| `tests/test_acp2_science_*.py` | `tests/test_acp2_science_{schema,evidence_contract,reaction_contract,registry,task_binding,recipe,fixtures,artifact_validation,stationary_point,da_validators,da_recipe,energy_comparability,cross_domain,selectivity,plugin_registry,installed_package}.py` (only those actually delivered by their task) | A-01, A-02, A-04, A-05, A-06, A-07, A-11…A-28 | **new**; prefix is reserved to A. Old test files are **not** A-owned — patches go to B |
| `tests/fixtures/acp2/science/**` | `singlepoint/`, `optfreq/`, `diels_alder/v1/` (incl. `fixture-manifest.json`), `selectivity/v1/`, `contracts/{capability_*,evidence/**,reaction/}`, `external_plugin/` | A-01…A-28 | **new**; real native outputs and constructed negatives are labelled separately; no invented real-run data |
| `scripts/acceptance/acp2_science.py` | single file (not production CLI; `--help` / `--dry-run` first, B01 `acp2.acceptance.v1` receipt format) | A-00 harness prerequisite, A-08… | **new**; only A writes scientific assertions/thresholds |
| `docs/reference/next-change/acp2-contracts/science/**` | `baseline.md`, `path-map.md`, `handoff-H1.json`…`handoff-H7.json`, `requests/*.md` / `*.patch`, `examples/**`, `*.schema.json` delivered by A | A-00…A-29 | **new root** (this directory). Every `science/*` path in the plan is relative to here |
| Evidence (outside worktree) | `$ACP2_EVIDENCE/A/A-NN/<run-id>/{result.json,argv.json,stdout.log,stderr.log,…}` | all | run evidence only; never written inside the worktree |

## 2. Existing modules A must extend, not clone

| Existing baseline path | Why it is the landing point |
|---|---|
| `src/cccp/calculation/requests.py`, `context.py`, `results.py`, `contracts.py` | `TaskRequest / TaskContext / TaskResult` already defined — capability/recipe adapt to them |
| `src/cccp/calculation/tasks/` (15 task cores) | the single execution station; A-05 may only call `run_singlepoint / run_optimize / run_frequency` |
| `src/cccp/plugin/{metadata,discovery,host,sdk}.py` | plugin discovery/enable/invoke boundary already exists |
| `src/cccp/calculation/selection.py`, `cccp.software.resolve_executable` | the only software/method resolution paths; no second resolver |
| `src/cccp/analysis/{structure,pes,frequency_products,nmr,bounded}` | scientific analysis products consumed by validators (no re-implementation) |
| `src/cccp/analysis/identity.py` | identity primitives to be reused by recipe scientific identity; keep `plan_identity/step_identity` as the recovery truth |

## 3. Ownership boundary (hard)

A **may write** only:

- `src/cccp/**` — except version constants and release/shared configuration.
- `tests/test_acp2_science_*.py` (new A-prefixed files only).
- `tests/fixtures/acp2/science/**`.
- `scripts/acceptance/acp2_science.py`.
- `docs/reference/next-change/acp2-contracts/science/**` and this plan's own science notes.
- `$ACP2_EVIDENCE/A/**`.

A **must not edit** (hand off to the owning track):

| Forbidden path / asset | Owner |
|---|---|
| `src/acp/**` | B |
| `src/acp_services/**` | B |
| `frontend/**` | C |
| `rph/**` (planned C package) | C |
| root and nested `AGENTS.md` (root, `src/acp/`, `src/acp_services/`, `src/cccp/`, `tests/`, …) | B |
| `pyproject.toml` | B |
| `requirements*.txt` (`requirements.txt`, `requirements-node.txt`, …) | B |
| CI configuration (`.github/workflows/**` and any lint/format gates) | B |
| the four version constants (`src/cccp/__init__.py`, `src/cccp/version.py`, `src/acp/__init__.py`, `pyproject.toml`) | B |
| default YAML (`config/defaults.yaml`) and Python built-in defaults owned by B | B |
| `tests/baseline/**`, old recovery fixtures, model assets / notices | B (preserve; report gaps) |
| production `acp.service`, its DB/run root, global Node/Python/SSH config | B |

Cross-track needs are delivered as `science/requests/*.md` or `science/requests/*.patch` (or the
matching `shared-change-request`), never by editing the file directly. QC-interface changes would
require an F4 amendment request to B first; existing greps/guards must not be relaxed.

## 4. Verification binding

- Snapshot identity: 1906 files, SHA-256 per `candidate-snapshot.json`, drift 0 (see `baseline.md` §3).
- Import resolution: `cccp`, `acp`, `acp_services` all under
  `/opt/acp2-dev/A/src/` with `$ACP2_A_PYTHON` (see `baseline.md` §4).
- Baseline guards (A-00): `tests/test_architecture_invariants.py`, `tests/test_cccp_isolation.py`,
  `tests/test_recovery_fixtures_smoke.py` — raw output in `$ACP2_EVIDENCE/A/A-00/`.
