# Task 8 — B07 prepare the real science environment and close P0 (H2)

Date: 2026-10-22
Candidate label: `bb45a46`
H2 server revision: `58d81ec5ec528577692e6c1aa300c46fbf3b43c6`
H2 tree: `eb54b1a28a9922ffd2917758cc35530cc346af90` (equals `f67f90f` tree; `git status --porcelain` empty)
Commit message: `chore(acceptance): close P0 environment and fixture registry`

## H2 test candidate

- Built in `/ACPServer/acp2-development/B` from the committed `f67f90f` tree (B00–B06), **not** from the dirty post-freeze working tree.
- `git -C /ACPServer/acp2-development/B rev-parse HEAD` = `58d81ec5ec528577692e6c1aa300c46fbf3b43c6`.
- `git -C B rev-parse HEAD^{tree}` == `git -C /ACPServer/AutoCalcPlatform rev-parse f67f90f^{tree}` = `eb54b1a28a9922ffd2917758cc35530cc346af90`.
- H2 record: `reports/acp2_implementation/bb45a46/B/environment/h2-candidate.json`.
  - `server_revision` = H2 SHA (40-hex)
  - `client_revision` = `ef86091ef338551ebb42c07ebd14fbc81c90901a` (C HEAD, clean)
  - `harness_revision` = `41f4e45d757142fe93dbf1af2eb95cf33db7144e942b5605983bcd3cf281a7ff` (H2 candidate `scripts/acceptance/acp2_runtime.py`)
  - `harness_revision_b07_run` = `37af66de5058608395052f8902330650e5329eb6f0965306a205b6741e00fed9` (B07 body added)
  - `contract_digest` = `67073115cb0856db22d84e22215d54e4dbe2437aad964efc15d3b706641e11a4` (from `/api/health`)
  - `env_digest` = `2e6daea64a7999925e1dde9b5396a8249a837fb7c2b0fe24ffc0af7d69dcaebc` (sha256 of `acceptance-env.sh`)
- `docs/reference/next-change/acp2-contracts/runtime/baseline-lock.json` updated only for `revisions.server_revision`, `revisions.harness_revision`, `contract_digest`, `env_digest` (and their direct aliases/status text). `client_revision` remains C-owned/not delivered.

## Local scientific environment (ENV-06)

Record: `reports/acp2_implementation/bb45a46/B/environment/b07-environment.json` (sibling of B02 `b-runtime.json`).

- Real ORCA: `/home/xieningke/orca611/orca`, ELF x86-64, sha256 `d65fe8fe3136e0008de9e5784ab8d853b0ef51a02635d7d6fb0f6a1c95d648d1`, ORCA `6.1.1`.
- Minimal probe (tiny H2/STO-3G SP, never a large job):
  - input `reports/.../B/B07/environment/orca-h2-probe/h2.inp`
  - stdout `.../h2.out`, stderr `.../h2.err`
  - `****ORCA TERMINATED NORMALLY****`, `FINAL SINGLE POINT ENERGY -1.116759307204`, total run time `0.302 s`, wall clock `0.32 s`, Max RSS `113920 KB`, exit `0`.
- License: `/home/xieningke/orca611/Third_Party_Licenses_Academic_6.1.pdf` (sha256 `16f68a456fe4ef576ed037c2abc1d471336da9a02fedab9216f2f0c50cfea974`); probe ran without a license server/token checkout.
- `/usr/bin/orca` is the GNOME screen reader (rpm `orca-40.3-1.el9.noarch`, Python script) and is explicitly excluded from QC use.
- Present at configured absolute paths (not on PATH): xTB `6.7.1` (`/home/xieningke/xtb-dist/bin/xtb`), CREST `3.0.2` (`/home/xieningke/crest/crest`), CENSO `3.0.8` (`/home/xieningke/anaconda3/bin/censo`), Shermo `2.6.1` (`/home/xieningke/Shermo_2.6.1/Shermo`), Isostat/Molclus. The earlier “absent” note was PATH-only; absolute pins exist and execute.
- Host: Rocky Linux 9.8, glibc 2.34, 40 cores, 62.2 GiB RAM, Xeon E5-2673 v4. Run root `/ACPServer/acp2-development/data/b/run-root` is `xfs` with 47.56 GiB free. Production `8765` / `/var/lib/acp/runs` untouched.

## `acp doctor` (ENV-06)

Command (from the controller config cwd, H2 code):
`cd /ACPServer/acp2-development/data/b/controller-config && /ACPServer/acp2-development/venvs/b/bin/python -m acp doctor --log-level INFO`
Raw log: `reports/.../B/B07/doctor/acp-doctor.log` — exit `0`, `10 PASS / 0 FAIL / 0 NOT_VERIFIED`.

Gaps named in the record:
1. `run_root`/`store` validate `/home/xieningke/.local/share/acp/runs` (platform default), not the acceptance run root. H2 `resolve_run_root` ignores `deployment.params.run_root`; the Controller is started with explicit `--run-root`.
2. No remote node (`cluster.nodes=[]`), so doctor does not check LSF/SSH/queue/remote software.
3. Doctor is binary sanity only; ORCA execution evidence comes from the H2/STO-3G probe.
4. Doctor does not check Playwright/Chromium.

## LSF / SSH (ENV-06)

Raw log: `reports/.../B/B07/lsf/lsf-probe.log`.

- `bsub`, `bjobs`, `bhosts`, `bqueues`, `lsid` = `ABSENT`; `LSF_ENVDIR`, `LSF_LIBDIR`, `ACP_LSF_NODE` unset.
- `ssh` = `/usr/bin/ssh` (OpenSSH 9.9p1).
- Acceptance cluster is `execution_mode=local`, `nodes=[]`.
- Verdict: **NOT_VERIFIED**; no remote job attempted.
- Prerequisite: install/initialize the LSF client (`LSF_ENVDIR`; `bsub/bjobs/bhosts/bqueues` on PATH) and configure a test node with SSH host identity/credential, queue, remote Python/software, release root and an independent remote work dir.

## `fixture-runs.json`

Path: `reports/acp2_implementation/bb45a46/B/fixture-runs.json` (schema `acp2.fixture-runs.v1`).
Validation raw log: `reports/.../B/B07/fixture-runs-validation.log` (exit 0).

Rows (9 unique):
| fixture_id | kind | backend | status | timeout |
|---|---|---|---|---|
| `fx-local-singlepoint-h2-sto3g-v1` | local-singlepoint | local | registered | 110 = 0+60+10+10+30 |
| `fx-lsf-singlepoint-v1` | lsf-singlepoint | lsf | NOT_VERIFIED | 2400 = 1800+300+120+60+120 |
| `fx-control-recovery-lsf-v1` | control-recovery | lsf | NOT_VERIFIED | 2400 = 1800+300+120+60+120 |
| `fx-artifact-local-v1` | artifact | local | planned | null |
| `fx-multi-singlepoint-v1` | multi | local | planned | null |
| `fx-da-bounded-dag-v1` | da | local | planned | null |
| `fx-plugin-lifecycle-v1` | plugin | local | planned | null |
| `fx-migration-v1` | migration | local | planned | null |
| `fx-package-install-v1` | package | local | planned | null |

`scenario_timeout_seconds` is arithmetic over the five maxima; no ×1.1 rule. `lsf_timeout_seconds=2400` is published for C. Local input: `/ACPServer/acp2-development/data/b/fixtures/singlepoint.xyz` sha256 `d117dc2f7956653e6ee7e4a543533ea98d50429acef5bee0d69e95faa31d34a0`. Control input: `control.json` sha256 `4b2061b1f1961718ed8ef89f6b6c64e642247ff3d3e85927c10baa1b2695a25a`. No duplicate expensive fixture; registration precedes submission.

## B04b two-adapter topology

Evidence: `reports/.../B/B07/topology/` (`topology-summary.json`, `controller.log`, `controller-health*.json`, `jobs-before/after.json`, `adapter-pids.txt`, launch manifest, four harness receipts).

- Controller: pid `365514`, H2 source `/ACPServer/acp2-development/B/src`, cwd `.../controller-config`, `--run-root .../run-root`, port 18765.
- Two real stdio adapters concurrently: pids `368344`, `368348`; both exited `0`.
- `entry-readonly` ×2 PASS; `mcp-stdio` ×2 PASS (eight-tool allowlist, capabilities, zero-side-effect preflight, not-prepared reads, registry-gated analyze).
- Task count before/after `2 → 2`; `jobs-before.json` and `jobs-after.json` byte-identical; no submitted job cancelled.
- Controller still serving after both adapters closed; then stopped with SIGTERM. Cleanup: `reports/.../B/B07/cleanup-receipt.json` (port 18765 free, production 8765 alive, no leftover adapter/Controller).

## P0 matrix (H2 stays OPEN)

Path: `reports/.../B/B07/p0-matrix.json` — `overall_status: OPEN`.

PASS (9): H2 candidate, Local ORCA binary+license, Local software inventory, `acp doctor`, run root, fixture registry, Controller health/contract, two-adapter topology, C ENV-03 node/DSH install (linked C-owned).

NOT_VERIFIED (6):
- `lsf_client_queue_ssh` — no LSF client/queue/node; exact prerequisite recorded.
- `c_dsh_real_handshake` — C T-11/T-12 not delivered; B does not sign.
- `c_chromium_launch` — C T-08 not signed (latest C run is NOT_VERIFIED).
- `c_route_json_diagnostic` — C diagnostic linked but non-authoritative/NOT_VERIFIED.
- `c_env_05_dsh_profile_model` — no C profile/overlay/model invocation receipt.
- `c_env_08_network` — no C per-target connection evidence.

Because required LSF/DSH/browser rows are missing, **P0/H2 is not closed**. H3 must use a P1 test candidate, not this H2 candidate.

## `env-runtime`

Command:
`python scripts/acceptance/acp2_runtime.py --scenario env-runtime --controller http://127.0.0.1:18765 --out "$ACP2_EVIDENCE/B/B07" --timeout 180`

Result: exit `0`, `PASS: env-runtime/main`.
Final receipt: `reports/.../B/B07/env-runtime-main-20261022T153700Z-399291-8bb987a9.json` (latest PASS receipt; an earlier `153311Z` NOT_VERIFIED run records the missing-record state before the environment record was placed at `B/environment/`), with assertions covering the real ORCA probe, doctor raw log/gaps, local run root, honest LSF NOT_VERIFIED gap, fixture arithmetic/unique IDs, H2 candidate identity/env digest, P0 matrix completeness, and Controller health/task-count binding.
