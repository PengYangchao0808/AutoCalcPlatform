# Task 11 (B10) — Real Local singlepoint baseline (B10a) and three-entry references (B10b)

Date: 2026-10-23
Candidate label: `bb45a46`
Branch: `feat/frontend-plan1-closure`
Repo HEAD at execution start: `964618321a63694112be25c297b927bfc776e3d0`

## 1. P1 test candidate

`<acp2-dev>/B` is a clean commit with an empty `git status --porcelain`.

| Fact | Value |
|---|---|
| P1 server revision | `10eb71e4bc1811054c9d19ae4fd71d974e027f8a` |
| P1 server tree | `316687f434b0a584614becdd7ef3f1032c5b1899` |
| Main HEAD base | `964618321a63694112be25c297b927bfc776e3d0` |
| Main HEAD base tree | `0982ff03355a5a82b6624116a75f38a82d4dcb85` |
| Base tree equals main HEAD 9646183 tree | `true` |
| P1 tree equals main HEAD 9646183 tree | `false` (see below) |
| P1 commit message | `chore(acceptance): freeze the P1 test candidate from B00-B09 plus B-fixes` |
| B-fix overlay | frozen final-debug candidate `sha256:bb45a46…c3e12`, applied from `a0185b1` onto `096cdff` |
| B-fix patch | `B10/p1/b-fix-overlay.patch`, sha256 `01b5bc74058fdc9165492fabcc534f67eafd87542634429651482bfd517e1baf` |
| Contract | version `1.1.0`, digest `67073115cb0856db22d84e22215d54e4dbe2437aad964efc15d3b706641e11a4` |
| Client revision | `NOT_VERIFIED` (C P1 Workbench/DSH not delivered; C HEAD `aecaea61…` recorded for identity only) |
| Harness revision | `ddf1e40ee8381adc4ff1f97c811df2dacb11bdfa834a204762c5948a9e6c5d36` (B10a receipt recorded `3812182b…`; the only later change corrects the gated CLI argument order) |
| `real_environment.py` revision | `21d3d86c520702b037726362879116a69107377d61e130f4a2969bb67e5e07b9` |
| Env digest | `bb1471716b8f05166b61bd331b328d9d293c9ae9b7e81b7238a79c44b01a3aec` (sha256 of `acceptance-env.sh`) |

### Why P1 is not tree-equal to `9646183`

`9646183` contains B00-B09 but lacks the frozen final-debug overlay. Two independent
pre-P1 runs prove the gap:

1. `B10/m1-baseline-pre-p1-112f93d/` — m1 baseline on the `9646183` tree: exit 0, but the
   `RESULT/task_result.json` document carried only the qualification, no `result` body and
   no energy product. There is no scientific body to re-read after restart.
2. `B10/local-singlepoint-aborted-harness-bug/` — the first B10a submission on the same
   tree: all execution facts passed, but `native_artifact_digests_agree` exposed a harness
   prefix-comparison bug; more importantly, the same tree has no R01 body publication.

Review Q04 explicitly says a SHA that lacks the final uncommitted fixes must not be treated
as the candidate; the actual candidate and its patch must be recorded. P1 therefore applies
the frozen overlay to `9646183` and records both the base and the patch digest. The overlay
is the only delta between the two trees; it also restores R01/R02/R05 (scientific body,
frozen environment snapshot, execution-right publication seam).

## 2. Independent m1 baseline (different run from the shared task)

Exact argv (run from `<acp2-dev>/B`, with the fixed
`real_environment.py` temporarily copied into the P1 tree so its Controller cwd is P1):

```
<acp2-dev>/venvs/b/bin/python scripts/acceptance/real_environment.py \
  --scenario m1-singlepoint \
  --out <repo>/reports/acp2_implementation/bb45a46/B/B10/m1-baseline \
  --timeout 300 --keep-temp
```

- `--help` exit: 0.
- Scenario exit: 0 (`PASS`).
- TaskRef: `20261023_031220_001_singlepoint`, attempt 1, release `98e0695545d668e6`.
- Energy: `-155.001263624935` hartree (CCO / r2SCAN-3c).
- The harness starts its own temporary Controller/DB under `/tmp/acp-m1-hl_kb0ye`; it is a
  **different run** from the shared B10 task on `127.0.0.1:18768` / `run-root-b10`.
- Pinned ORCA `/home/<user>/orca611/orca` (ELF); `/usr/bin/orca` was never used. The
  harness now maps `ACP_M1_ORCA` to `CONFSEARCH_ORCA_PATH` (previously the pin was inert).
- Evidence: `B10/m1-baseline/` (`taskref.json`, `m1-singlepoint.json`, `sp.inp`, `sp.out`,
  `task_result.json`, `result_manifest.json`, `execution_receipt.json`, stdout/stderr/exit).

## 3. Shared B10a run (Controller + real MCP, submitted once)

Controller (started from P1, explicit `--run-root`; never started by an adapter):

```
cd <acp2-dev>/data/b/controller-config
<acp2-dev>/venvs/b/bin/python -m acp run serve \
  --host 127.0.0.1 --port 18768 \
  --run-root <acp2-dev>/data/b/run-root-b10
```

MCP driver (real stdio adapter, B06/B05-shaped launch JSON pointed at the P1 source):

```
<acp2-dev>/venvs/b/bin/python scripts/acceptance/acp2_runtime.py \
  --scenario local-singlepoint --phase main \
  --controller http://127.0.0.1:18768 \
  --launch reports/acp2_implementation/bb45a46/B/B10/mcp-launch-b10.json \
  --fixture <acp2-dev>/data/b/fixtures/singlepoint.xyz \
  --out reports/acp2_implementation/bb45a46/B/B10/local-singlepoint \
  --timeout 300
```

Exit: **0 (PASS)**. Launch JSON: `B10/mcp-launch-b10.json` (sha256
`5697e73eb54b4c21f71dee785f9a14a8c6c20d39712341ef5263ba9e22642f43`), command
`<acp2-dev>/venvs/b/bin/python -m acp mcp serve --transport stdio --controller http://127.0.0.1:18768`,
cwd `<acp2-dev>/B/src`.

Submitted exactly once through `acp_submit` with method level `HF` / basis `STO-3G`,
charge 0, multiplicity 1, `nproc=1`, `mem=1024MB`, plan digest `sha256:000…0` (the
authorization value), idempotency key `b10-…`.

### B10a facts

| Fact | Value |
|---|---|
| TaskRef | `20261023_031453_001_singlepoint` |
| Attempt | 1 |
| Release | `98e0695545d668e6` |
| Submission | `sub_3006306693beb030` |
| Plan identity | `v2:0cbae6ed2d2bd8daf42b262881b5fe04` |
| Result digest | `sha256:cc9bf55634f73ce5e0355c64c1587f189979de46eb62e213cfb40965f7e07f4f` |
| TaskResult content digest | `sha256:60d22eff690b080ea38ea3462a615543601ad1c469db699516af5c83e7f376b4` |
| Manifest task_result digest | `sha256:60d22eff690b080ea38ea3462a615543601ad1c469db699516af5c83e7f376b4` |
| Energy | `-1.116759307204` hartree |
| Energy (native ORCA) | `-1.116759307204` hartree (`FINAL SINGLE POINT ENERGY`) |
| Geometry | `[H 0,0,0; H 0,0,0.74]` Å |
| Frequencies | `[]` (not present for a singlepoint; `b10.frequencies_present=false`) |
| ORCA termination | `****ORCA TERMINATED NORMALLY****` |
| ORCA processes | 1 unique PID (`596123`), max concurrent 1, 855 samples |
| `job.started` events | 1 |
| Task count | 0 → 1 |
| Native `sp.inp` | `30c08be9ec755050384418d62ffd67cc576c4335cf7a967b23e508e6f1d2f2f6` |
| Native `sp.out` | `b083545e1254c6ef5f2a9b91b0b515d77965ca56dce795bffb14435f7a6c43df` |
| Result body artifacts | `sp.inp` + `sp.out` checksums match TaskResult |

Assertions in the receipt: `one_mcp_submission_one_task`, `job_completed`,
`real_orca_process_once`, `orca_terminated_normally`, `orca_energy_matches_task_result`,
`task_result_complete`, `attempt_and_release_agree`, `manifest_agrees_with_task_result`,
`native_artifact_digests_agree`, `scientific_body_readable`, `exactly_one_dispatch` — all
PASS.

Receipt: `B10/local-singlepoint/local-singlepoint-main-20261022T191452Z-595994-7d3fd0b6.json`.
Native copies: `B10/local-singlepoint/native/`.

### Restart re-read

The Controller was stopped and restarted with the same `run-root-b10` DB and code.

- `B10/read-before-restart.json` and `B10/read-after-restart.json` are identical in the
  scientific body: same energy, symbols, coordinates, frequencies, manifest SHA-256,
  task-result SHA-256, release, plan identity and attempt.
- `B10/restart-reread-comparison.json`: every comparison `true`.
- No new task row (still exactly one), no new `job.started`, and event/manifest/task-result/
  `sp.out` digests unchanged.
- Health after restart: candidate `10eb71e4…`, contract digest `67073115…`.

## 4. B10b — three-entry matrix

Descriptor: `B10/three-entry-request.json` (carries the B10a TaskRef; it never submits).

Command:

```
<acp2-dev>/venvs/b/bin/python scripts/acceptance/acp2_runtime.py \
  --scenario three-entry --phase main \
  --controller http://127.0.0.1:18768 \
  --fixture reports/acp2_implementation/bb45a46/B/B10/three-entry-request.json \
  --out reports/acp2_implementation/bb45a46/B/B10/three-entry --timeout 300
```

Exit: **2 (NOT_VERIFIED)** — required Workbench row absent; never a fake 0.

| Entry | Verdict | Identity used |
|---|---|---|
| CLI — `acp task query --controller http://127.0.0.1:18768 --job 20261023_031453_001_singlepoint` | PASS | job/attempt/release/plan identity/manifest digest/result digest |
| HTTP — `GET /api/v1/jobs/{id}` + `GET /api/v1/jobs/{id}/files/execution_receipt.json` + `…/files/RESULT/result_manifest.json` | PASS | same |
| C Workbench (RPH/DSH) | **NOT_VERIFIED** (`c_driver_absent`) | C P1 UI/DSH not delivered; no Chromium/DSH on this host |

Compared keys (all equal CLI vs HTTP): `job_id`, `attempt`, `release_id`, `plan_identity`,
`manifest_sha256`, `result_digest`, `method`, `input_hash`. `mismatched=[]`.
The three-entry invocation was read-only: task count `1 → 1`.

**CLI surface distinction recorded explicitly:** the equivalence gate is the gated
Controller query `acp task query --controller <url> --job <id>`. The legacy
`acp run singlepoint` was **never** executed, because it would create a second task
(“same-ID” cross-check is forbidden). `b10.legacy_acp_run_used=false`.

Matrix: `B10/three-entry-matrix.json`; receipt: `B10/three-entry/three-entry-main-20261022T191725Z-598020-1111d458.json`.

## 5. Fixture registry

`reports/acp2_implementation/bb45a46/B/fixture-runs.json` row
`fx-local-singlepoint-h2-sto3g-v1`:

- `submitted_once: true`
- `task: 20261023_031453_001_singlepoint`, `attempt: 1`
- `release: {release_id: 98e0695545d668e6, release_digest: c3efc22cb25f40101c97e9db203e4d02d2648edc6ab83bacbda8e657a4faf98a}`
- `status: completed`, `server_revision: 10eb71e4…`
- method: `HF` / `STO-3G`, charge 0, multiplicity 1
- exactly one row for the fixture id; no duplicate.

## 6. Harness scenario implementation

`scripts/acceptance/acp2_runtime.py`:

- `local-singlepoint`: requires `--fixture` and `--launch` (missing `--launch` → exit 3
  before any DB/process/job), submits once via the real MCP stdio `acp_submit`, polls with
  the registered bounded timeout, samples real ORCA processes, reads native output,
  TaskResult, receipt, attempt/release and manifest, and writes a schema-valid receipt with
  all B10a assertions.
- `three-entry`: three rows; reads the TaskRef from the fixture descriptor; CLI and HTTP
  rows compare identity/digests; an absent Workbench row returns 2 with
  `three_entry_workbench_not_verified:c_driver_absent` and the exact prerequisite.
- Registry, owners, phases, args and 0/1/2/3 exit semantics unchanged.
- `real_environment.py` was fixed to send a valid input/method payload and to map
  `ACP_M1_ORCA` to `CONFSEARCH_ORCA_PATH`; its scenario enum was not modified.

## 7. Verification

- `pytest tests/test_acp2_runtime_acceptance_harness.py tests/test_acp2_runtime_mcp_backends.py -q` → 27 passed, exit 0 (`B10/pytest.txt`).
- `ruff check` → all passed (`B10/ruff-check.txt`); `ruff format --check` → already formatted (`B10/ruff-format.txt`).
- `python scripts/acceptance/validate_acp2_contracts.py` → VALID, exit 0 (`B10/validator.txt`).
- `acp2_runtime.py --help` exit 0; `local-singlepoint` without `--launch` exit 3.

## 8. Cleanup

`B10/cleanup.json`: Controller PID stopped, port 18768 free, 0 ORCA processes, 0 MCP adapter
processes, run root preserved for evidence, 1 job row in the DB. Production `:8765` and
`/var/lib/acp/runs` were never touched.

## 9. H3

H3 remains **OPEN**: Local B10a facts and restart re-read pass; B10b CLI+HTTP pass but the
C Workbench row is `NOT_VERIFIED`; LSF remains `NOT_VERIFIED` (B11). B10 does not close H3.
