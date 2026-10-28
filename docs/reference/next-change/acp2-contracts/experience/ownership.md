# C Track Ownership — ACP 2.0 Experience / RPH / Workbench

Status: **frozen by T-01 / C00 on 2026-10-22; B00/B02a handoff adopted by T-04a
on 2026-10-22**. This file is the C track's single source of truth for the write
domain, the cross-track request paths and numbering, the interpreter roles, and
the evidence root. B owns `runtime/**`; C owns `experience/**` only.

## 1. C write domain (exclusive)

C may create or modify only the following paths inside `$ACP2_C_SOURCE`:

- `frontend/**`
- `rph/**` (new standalone pnpm workspace; no root `package.json`)
- `tests/test_acp2_experience_*.py`
- `scripts/acceptance/acp2_experience*.py` (and `scripts/acceptance/acp2_route_probe.sh`)
- `docs/reference/next-change/acp2-contracts/experience/**`
- `docs/reference/next-change/ACP_2.0_RPH_*.md`
- `docs/reference/next-change/ACP_2.0_Workbench_*.md`
- `$ACP2_EVIDENCE/C/**` (product acceptance evidence)

C must **not** write `src/**`, root `pyproject.toml`, `requirements-node.txt`, CI,
the four `__version__` locations, root/nested `AGENTS.md`,
`scripts/install_systemd.sh`, Services ledgers, or any B-owned
`docs/reference/next-change/acp2-contracts/runtime/**` file. Requests for those
go through the request paths below.

## 2. Cross-track request paths and numbering

Request files live under `docs/reference/next-change/acp2-contracts/experience/requests/`:

- A requests: `experience/requests/A-req-NN.md`
- B requests: `experience/requests/B-req-NN.md`

Allocated numbers (never reuse, never renumber):

- `B-req-01` … `B-req-17` are ALLOCATED. T-01 consumes `B-req-12` (B02a/B02b split)
  and `B-req-17` (B00 baseline-lock + manifest + absolute evidence root).
- `A-req-01` … `A-req-04` are ALLOCATED (all reserved by T-03 and downstream; T-01
  creates none).

Every request must contain a `## Failure output` section with at least one concrete
failure artifact, not prose.

## 3. Environment variables (absolute values)

| Variable | Absolute value | State |
| --- | --- | --- |
| `ACP2_CHECKOUT_ROOT` | `/ACPServer/acp2-development` | delivered — B00 manifest `ac43c16` records the C track materialization; the checkout root is unchanged |
| `ACP2_PROJECT_ROOT` | `/ACPServer/AutoCalcPlatform` | adopted 2026-10-22 — resolves B00 project-relative `evidence_root` values |
| `ACP2_C_SOURCE` | `/ACPServer/acp2-development/C` | delivered by C/T-01 (branch `acp2/c-experience`) |
| `ACP2_A_SOURCE` | `/ACPServer/acp2-development/A` | A-owned, read-only for C; **C never reads it**. The task brief expected it absent, but an A worktree (`acp2/a-science`, base `096cdffe…`) was observed present during execution. It is not a C dependency and was not consumed. |
| `ACP2_INTEGRATION_SOURCE` | `/ACPServer/acp2-development/integration` | delivered by B02a (`0dee16f`); detached checkout at the C baseline commit `75a8f08…`, tree-equal to B `a0185b15…` |
| `ACP2_SOURCE` | `/ACPServer/acp2-development/integration` | alias of `ACP2_INTEGRATION_SOURCE` (B/C single meaning; never A) |
| `ACP2_TOOLS` | `/ACPServer/acp2-tools` | delivered by C/T-04 — C's Node/pnpm/DSH/browser toolchain root. Intentionally **diverges** from B02a's `ACP2_TOOLS=/ACPServer/acp2-development/venvs`; see the adoption addendum |
| `ACP2_DATA_ROOT` | `/ACPServer/acp2-development/data` | C's shared acceptance-env root. B02a's own data root is `/ACPServer/acp2-development/data/b`; both are delivered and neither replaces the other |
| `ACP2_CONTRACTS` | `/ACPServer/acp2-development/C/docs/reference/next-change/acp2-contracts` | delivered by C/T-01 (C-owned `experience/**`; B keeps `runtime/**`) |
| `ACP2_BASELINE_LOCK` | `/ACPServer/AutoCalcPlatform/docs/reference/next-change/acp2-contracts/runtime/baseline-lock.json` | delivered by B00 (`ac43c16`), committed-blob sha256 `fe13e474b8bd0acec19c84a3569324036bd4514d0d67633947d72d3592a12dc5`; `readiness: verified` |
| `ACP2_EVIDENCE` | `/ACPServer/AutoCalcPlatform/reports/acp2_implementation/bb45a46` | adopted B00 absolute root (`ac43c16`); equals B02a's `ACP2_EVIDENCE` value; `readiness: verified` |
| `ACP2_C_PYTHON` | `/ACPServer/acp2-development/venvs/c/bin/python` | delivered by B02a (`0dee16f`), `readiness: integration_ready`; C created the venv during T-01 bootstrap and B02a verified its import prefix |
| `ACP2_INTEGRATION_PYTHON` | `/ACPServer/acp2-development/venvs/integration/bin/python` | delivered by B02a (`0dee16f`), `readiness: integration_ready`; imports `$ACP2_INTEGRATION_SOURCE` |
| `ACP2_BROWSER_PYTHON` | `/ACPServer/acp2-development/venvs/browser/bin/python` | delivered by B02a (`0dee16f`), `readiness: integration_ready`; `playwright==1.63.0` importable, Chromium launch is C/T-08 and not claimed |
| `ACP2_PYTHON` | `/ACPServer/acp2-development/venvs/c/bin/python` | alias of `$ACP2_C_PYTHON` (fixture/dry-run default) |

Invariant: `ACP2_SOURCE` == `ACP2_INTEGRATION_SOURCE` == `/ACPServer/acp2-development/integration`.

## 4. Candidate binding

- `candidate_id`: `sha256:bb45a46c93d241d03ffae2952a78a2736a637a3f2c8d69b72d2bd0c4fafc3e12`
- `source_candidate_id`: `sha256:bb45a46c93d241d03ffae2952a78a2736a637a3f2c8d69b72d2bd0c4fafc3e12`
- `server_revision`: `75a8f08a06b5528b310b26bd309940e99d9cd9bd`
- `client_revision`: `75a8f08a06b5528b310b26bd309940e99d9cd9bd`
- `harness_revision`: `75a8f08a06b5528b310b26bd309940e99d9cd9bd`
- `baseline_source`: `reports/architecture_repair_20261022 (candidate-overlay.tar.gz + candidate-snapshot.json)`
- `historical_debug_revision`: `096cdffe9a3817d8ee443d824830d87ec6e07bc2`
- `materialized_commit`: `a0185b15c546fbd07919326d581fab13e05000fa` (B02, `/ACPServer/acp2-development/B`)
- `c_baseline_commit`: `75a8f08a06b5528b310b26bd309940e99d9cd9bd` (tree `1a1b4b23c554acaf2818db1dcee336b69494a887`)
- `tree_equality`: C `75a8f08^{tree}` == B `a0185b15^{tree}` == `1a1b4b23c554acaf2818db1dcee336b69494a887`
- `verification`: `1906/1906` per-file matches in B02's clean reconstruction (`materialization.verification`)
- `b00_commit`: `ac43c16` (`manifest.json`, `runtime/baseline-lock.json`, `runtime/dependencies.json`)
- `b00_committed_blob_sha256`: manifest `b83919a71f55c4131b4daa7e82b13369863aa495e951665178f9be63c1b78769`; baseline-lock `fe13e474b8bd0acec19c84a3569324036bd4514d0d67633947d72d3592a12dc5`; dependencies `96862edd6534614bfa64620f170161c7cbad6c2117d941dd215e49ab9131c4ac`
- `b02a_commit`: `0dee16f`; B02a env committed-blob/on-disk sha256 `d16b1d70f5e840637252a3b34c46057c3b4440aca7428e7b44052118c13ddbf7`
- `readiness`: B00 baseline-lock/manifest `verified`; B02a environment `integration_ready`

Frozen baseline construction (reproducible):

1. `git worktree add <path> -b acp2/c-experience 096cdffe9a3817d8ee443d824830d87ec6e07bc2`
2. extract `reports/architecture_repair_20261022/candidate-overlay.tar.gz` to a temp dir
3. `git apply tracked-changes.patch`
4. copy `new-files/**` **excluding `new-files/.omo/**`** (planning state must not overwrite current plans)
5. verify every `candidate-snapshot.json["files"]` path except `.omo/**` and the
   `excluded` map entry; expected zero drift
6. commit `chore(acp2-c): freeze C baseline from 2026-10-22 repair overlay`

## 5. External handoffs

- B00 (`baseline-lock` / `manifest` / `evidence_root`): **delivered** in commit
  `ac43c16`. Adopted values: `candidate_id`
  `sha256:bb45a46c93d241d03ffae2952a78a2736a637a3f2c8d69b72d2bd0c4fafc3e12`,
  project-relative `evidence_root=reports/acp2_implementation/bb45a46`,
  `materialization.materialized_commit=a0185b15c546fbd07919326d581fab13e05000fa`,
  `verification=1906/1906`. Readiness `verified`.
- B02a (tools root / data root / role interpreters / browser venv, **excluding**
  Chromium launch): **delivered** in commit `0dee16f` (`data/b/acceptance-env.sh`,
  sha256 `d16b1d70f5e840637252a3b34c46057c3b4440aca7428e7b44052118c13ddbf7`).
  The C/integration/browser role interpreters and their import prefixes are
  verified in `reports/acp2_implementation/bb45a46/B/B02/role-interpreters.json`.
  Readiness `integration_ready`. B02a is not accepted on Chromium launch;
  Chromium is C's T-08.
- B02b (package smoke) is not a T-04 precondition.
- The `ACP2_TOOLS` roots are intentionally different: B02a's env root is
  `/ACPServer/acp2-development/venvs` (Python venvs), while C keeps
  `/ACPServer/acp2-tools` (Node/pnpm/DSH/browser toolchain). Neither root is
  claimed to have replaced the other.

## 6. Evidence root

- Product evidence: `/ACPServer/AutoCalcPlatform/reports/acp2_implementation/bb45a46/C/<scenario>/<run_id>/`
  (the absolute B00 root adopted on 2026-10-22; shared with B's `reports/acp2_implementation/bb45a46` tree).
- Plan receipts: `.omo/evidence/acp2-plan-c-rph-workbench/task-<N>-acp2-plan-c-rph-workbench.md`
  in the **main** checkout (orchestration state; never a product evidence root).

## 7. Adoption addendum 2026-10-22

T-04a replaced the T-01/T-04 provisional environment with the already-committed
B00/B02a handoff. Only committed blobs were read; B did not need to re-deliver.

- **Tree equality (proven).** `git -C /ACPServer/acp2-development/C rev-parse
  75a8f08^{tree}` and `git -C /ACPServer/acp2-development/B rev-parse
  a0185b15^{tree}` both return `1a1b4b23c554acaf2818db1dcee336b69494a887`.
- **Real evidence root.** `ACP2_EVIDENCE=/ACPServer/AutoCalcPlatform/reports/acp2_implementation/bb45a46`,
  identical to B02a's value. The old `/ACPServer/acp2-development/evidence` root
  is retained only as a documented historical alias and is rejected by `c_accept`.
- **Resolution rule.** `c_accept` reads B00's `runtime/baseline-lock.json`
  (C's `$ACP2_CONTRACTS/runtime/…` when present, otherwise
  `$ACP2_BASELINE_LOCK`). An absolute `evidence_root` must equal
  `$ACP2_EVIDENCE`; a project-relative `evidence_root` is resolved as
  `$ACP2_PROJECT_ROOT/<value>` and must equal `$ACP2_EVIDENCE`. When
  `evidence_roots.C` is present it must equal the resolved root + `/C`. Every run
  exports `ACP2_EVIDENCE_BINDING=b00_project_root`; no `not_verified` stamp is
  written for the evidence root.
- **`evidence_roots.C`.** B00 registers
  `evidence_roots.C=reports/acp2_implementation/bb45a46/C`; resolving it against
  `ACP2_PROJECT_ROOT` yields
  `/ACPServer/AutoCalcPlatform/reports/acp2_implementation/bb45a46/C`, exactly
  the C product-evidence subtree.
- **`ACP2_TOOLS` divergence.** B02a's env sets
  `ACP2_TOOLS=/ACPServer/acp2-development/venvs`; C keeps
  `ACP2_TOOLS=/ACPServer/acp2-tools` because C's Node/pnpm/DSH/browser
  toolchain from T-04 lives there. The roots are documented as intentionally
  distinct; no unification is claimed.
- **B02a data root.** B02a's own `ACP2_DATA_ROOT` is
  `/ACPServer/acp2-development/data/b`; C's shared env file stays under
  `/ACPServer/acp2-development/data`.
