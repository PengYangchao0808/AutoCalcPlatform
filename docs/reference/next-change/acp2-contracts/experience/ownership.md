# C Track Ownership — ACP 2.0 Experience / RPH / Workbench

Status: **frozen by T-01 / C00 on 2026-10-22**. This file is the C track's single
source of truth for the write domain, the cross-track request paths and numbering,
the interpreter roles, and the evidence root. B owns `runtime/**`; C owns
`experience/**` only.

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
| `ACP2_CHECKOUT_ROOT` | `<acp2-dev>` | provisional — B00 has not published a manifest; `NOT_VERIFIED(b00_handoff)` |
| `ACP2_C_SOURCE` | `<acp2-dev>/C` | delivered by C/T-01 (branch `acp2/c-experience`) |
| `ACP2_A_SOURCE` | `<acp2-dev>/A` | A-owned, read-only for C; **C never reads it**. The task brief expected it absent, but an A worktree (`acp2/a-science`, base `096cdffe…`) was observed present during execution. It is not a C dependency and was not consumed. |
| `ACP2_INTEGRATION_SOURCE` | `<acp2-dev>/integration` | provisional — C created a detached checkout at the C baseline commit because B has delivered no frozen integration candidate; `NOT_VERIFIED(b00_handoff)` |
| `ACP2_SOURCE` | `<acp2-dev>/integration` | alias of `ACP2_INTEGRATION_SOURCE` (B/C single meaning; never A) |
| `ACP2_TOOLS` | `<server-root>/acp2-tools` | provisional — empty, B02a has delivered nothing; `NOT_VERIFIED(b02a_handoff)` |
| `ACP2_DATA_ROOT` | `<acp2-dev>/data` | provisional — empty, B02a has delivered nothing; `NOT_VERIFIED(b02a_handoff)` |
| `ACP2_CONTRACTS` | `<acp2-dev>/C/docs/reference/next-change/acp2-contracts` | delivered by C/T-01 (C-owned `experience/**`; B keeps `runtime/**`) |
| `ACP2_EVIDENCE` | `<acp2-dev>/evidence` | **PROVISIONAL — B00 must confirm or reassign; `NOT_VERIFIED(b00_handoff)`** |
| `ACP2_C_PYTHON` | `<acp2-dev>/venvs/c/bin/python` | provisional — C-created because B02a role interpreters are absent; `NOT_VERIFIED(b02a_handoff)` |
| `ACP2_INTEGRATION_PYTHON` | `<acp2-dev>/venvs/integration/bin/python` | provisional — C-created; imports `$ACP2_INTEGRATION_SOURCE`; `NOT_VERIFIED(b02a_handoff)` |
| `ACP2_BROWSER_PYTHON` | `<acp2-dev>/venvs/browser/bin/python` | provisional — C-created with `playwright==1.63.0`; Chromium not installed (T-08); `NOT_VERIFIED(b02a_handoff)` |
| `ACP2_PYTHON` | `<acp2-dev>/venvs/c/bin/python` | alias of `$ACP2_C_PYTHON` (fixture/dry-run default) |

Invariant: `ACP2_SOURCE` == `ACP2_INTEGRATION_SOURCE` == `<acp2-dev>/integration`.

## 4. Candidate binding

- `candidate_id`: `sha256:bb45a46c93d241d03ffae2952a78a2736a637a3f2c8d69b72d2bd0c4fafc3e12`
- `source_candidate_id`: `sha256:bb45a46c93d241d03ffae2952a78a2736a637a3f2c8d69b72d2bd0c4fafc3e12`
- `server_revision`: `75a8f08a06b5528b310b26bd309940e99d9cd9bd`
- `client_revision`: `75a8f08a06b5528b310b26bd309940e99d9cd9bd`
- `harness_revision`: `75a8f08a06b5528b310b26bd309940e99d9cd9bd`
- `baseline_source`: `reports/architecture_repair_20261022 (candidate-overlay.tar.gz + candidate-snapshot.json)`
- `historical_debug_revision`: `096cdffe9a3817d8ee443d824830d87ec6e07bc2`
- `not_verified`: `NOT_VERIFIED(b00_handoff)` — B00 has not published
  `runtime/baseline-lock.json`, `runtime/manifest.json`, or an authoritative
  `evidence_root`. The candidate above comes from the 2026-10-22 repair report and
  is the best available provenance, not a B00-signed baseline.

Frozen baseline construction (reproducible):

1. `git worktree add <path> -b acp2/c-experience 096cdffe9a3817d8ee443d824830d87ec6e07bc2`
2. extract `reports/architecture_repair_20261022/candidate-overlay.tar.gz` to a temp dir
3. `git apply tracked-changes.patch`
4. copy `new-files/**` **excluding `new-files/.omo/**`** (planning state must not overwrite current plans)
5. verify every `candidate-snapshot.json["files"]` path except `.omo/**` and the
   `excluded` map entry; expected zero drift
6. commit `chore(acp2-c): freeze C baseline from 2026-10-22 repair overlay`

## 5. External handoffs

- B00 (`baseline-lock` / `manifest` / `evidence_root`): `NOT_VERIFIED(b00_handoff)`.
- B02a (tools root / data root / role interpreters / browser venv, **excluding**
  Chromium launch): `NOT_VERIFIED(b02a_handoff)`. B02a must not be accepted on
  Chromium launch; Chromium is C's T-08.
- B02b (package smoke) is not a T-04 precondition.
- C created only provisional local equivalents so the track can proceed. Creating
  them is **not** delivery of B02a, and no B handoff is marked delivered here.

## 6. Evidence root

- Product evidence: `<acp2-dev>/evidence/C/<scenario>/<run_id>/`
  (provisional absolute root above; `NOT_VERIFIED(b00_handoff)` until B00 confirms).
- Plan receipts: `.omo/evidence/acp2-plan-c-rph-workbench/task-<N>-acp2-plan-c-rph-workbench.md`
  in the **main** checkout (orchestration state; never a product evidence root).
