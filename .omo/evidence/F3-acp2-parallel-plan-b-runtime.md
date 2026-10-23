# F3 — ACP 2.0 Plan B runtime, independent QA re-review (B-fix-5)

**Reviewer:** independent F3 re-review (B runtime release candidate)
**Date:** 2026-10-24 (UTC+8 execution host)
**Plan:** `.omo/plans/acp2-parallel-plan-b-runtime.md` Final Verification Wave F3
**Candidate:** `reports/acp2_implementation/bb45a46/B/B25/release-fix5`
**Candidate source:** `6ec01ec96fa81b89bec8ff321c38e3db2dc154d2` (tree `e2b23771a47e7553738ef902cc4c1b62528fe508`), version `0.3.0`
**QA root:** `/tmp/opencode/f3-rerun` (fresh venvs, isolated run roots and HOME, no `PYTHONPATH`)
**Raw logs:** `/tmp/opencode/f3-rerun/logs/`; selected artifacts in `/tmp/opencode/f3-rerun/evidence/`
**Product code modified:** no. Plan file modified: no. Production `acp.service`/`8765`/`/var/lib/acp/runs`: not touched.

## 0. Prior review (context only; not reused as PASS)

The previous F3 review (2026-10-24, candidate `release-fix4`, source `abec2bc4f1d302827248d36171973df5bc1bc4dd`) returned **VERDICT: REJECT**. Its blocking findings were:

1. installed-wheel LSF normal completion failed before `bsub` because `acp.scheduler.remote.sync._project_root()` resolved to the venv interpreter directory (`sync_files=0`, empty manifest);
2. the bare root wheel could not run `acp run singlepoint` without `[remote]` (`ModuleNotFoundError: paramiko`);
3. the package-install receipt reported `candidate_revision=096cdffe9a3817d8ee443d824830d87ec6e07bc2` instead of the candidate;
4. HTTP/CLI/MCP did not project `release_id`/`release_digest`/`plan_digest`/`submission_id`.

DSH real-model was `NOT_VERIFIED` (no credentials; DSH headless boot error), and C33/C34/static-assets rows were already `NOT_VERIFIED`.

This document records a **new execution against `release-fix5`**; it does not reuse the prior PASS/FAIL receipts.

## 1. Verdict

**VERDICT: APPROVE**

All four prior blocking FAIL rows were re-executed against `release-fix5` and **PASS** with new TaskRefs/receipts:

| Prior FAIL | Re-run result | New evidence |
|---|---|---|
| Installed-wheel LSF normal | **PASS** — real `bsub` job `1766`, terminal `completed`, `release_id=1c79785a657c6614` | §4.1, §5 |
| Bare root wheel `acp run singlepoint` | **PASS** — exit `0`, ORCA 6.1.1 `-1.116773191779 Ha` | §4.2 |
| Package-install receipt candidate identity | **PASS** — `candidate_revision=6ec01ec96fa81b89bec8ff321c38e3db2dc154d2` | §4.3 |
| Cross-entry release projection | **PASS** — HTTP/CLI/MCP equal `release_id`, `release_digest`, `plan_digest`, `submission_id` | §4.4 |

Remaining `NOT_VERIFIED` rows are external prerequisites/out-of-window items, not candidate defects: DSH real-model routing (no model credentials; DSH headless boot defect), C33/C34/C static assets, and P2 budget/approval. They are listed explicitly in §3 and do not hide a FAIL.

## 2. Candidate and environment (T0)

### 2.1 Artifact identity

All eight release artifacts match `release-manifest.json` and `source.json` byte-for-byte (`logs/artifact-sha256.txt`):

| Artifact | SHA-256 | Match |
|---|---|---|
| `cccp-0.3.0-py3-none-any.whl` | `b505413147e3c3a07c9b405631e3ee244d0dc9f7a8f699a45ec112ec692c559c` | PASS |
| `cccp-0.3.0.tar.gz` | `31631e2f5c131ccba6e08ffac23035feb7670d226b0349cb6816c23959aea425` | PASS |
| `acp_worker-0.3.0-py3-none-any.whl` | `edd849a75b28f58bba54d8613ec14e1ecae46a325d51d3874967fc683c6ec949` | PASS |
| `acp-worker-0.3.0.tar.gz` | `223fd6aec2760c35d5346f56edf5b17a3dc0376481e5ea4b48348d36493358b5` | PASS |
| `acp_web-0.3.0-py3-none-any.whl` | `d055e869de602cf1a257d4542243849806b3e3ebe805b78c70a412e8e5a2b210` | PASS |
| `acp-web-0.3.0.tar.gz` | `3186b347377599c1e71514bdccc52d1ebf2811eb73b6d802d18b2141a8c927a7` | PASS |
| `root-build/acp-0.3.0-py3-none-any.whl` | `64548f8f25e6251532b8b551067f6a0c432a2668bfc20edd310ed2fcb7e4c56a` | PASS |
| `root-build/acp-0.3.0.tar.gz` | `f449f77d4c89476847a610ef3577a13ce6c1efb30dc77cdc8a5a522411b28987` | PASS |

`release-manifest.json.source.revision` = `6ec01ec96fa81b89bec8ff321c38e3db2dc154d2`, tree = `e2b23771a47e7553738ef902cc4c1b62528fe508`, version = `0.3.0`.

### 2.2 Host / dependency facts

| Fact | Observed | Status |
|---|---|---|
| Python in fresh venvs | `3.11.13` | PASS |
| Installed `acp`, `acp_services`, `cccp` | all under fresh venv `site-packages`, `0.3.0`, no repository `src` on `sys.path` | PASS |
| `acp doctor` (root role venv) | exit `0`, `Doctor finished: all checks passed`; node-157 Python `3.13.9`, ORCA/CREST/xTB/CENSO/Isostat/Molclus/OpenMPI resolved | PASS |
| ORCA | `/home/xieningke/orca611/orca`, `Program Version 6.1.1 - RELEASE` | PASS |
| xTB | `/home/xieningke/xtb-dist/bin/xtb`, `xtb version 6.7.1 (edcfbbe)` | PASS |
| OpenMPI | `/home/xieningke/openmpi418/bin/orterun`, `OpenRTE 4.1.8` | PASS |
| LSF | `/opt/openlava-2.0/bin`; `LSF_ENVDIR=/opt/openlava-2.0/etc`; `HOST_NAME workstation ok`; queue `normal Open:Active` | PASS |
| `node-157` | SSH password auth OK; remote Python `/home/xieningke/anaconda3/bin/python` `3.13.9`; ORCA 6.1.1 | PASS |
| DSH | source `/ACPServer/acp2-tools/deepseek-harness` @ `687f282e8625c6b8d11ff58211a90524be2e4317`, `0.2.1-rc.2`; Node `v22.19.0` | PASS |
| Real model routing | `DSH_HOME=/ACPServer/acp2-development/data/dsh-home`; `.credentials.yaml` contains only `client-connection/browser-session`; no model provider key/model selection in environment or config | **NOT_VERIFIED** (`real_model_credentials_absent`) |
| Isolated operator config | `cluster.pre_cmds` exporting ORCA/OpenMPI on node-157; `sha256(~/.cccp.yaml)` = `c3392e7e4b714d9c7bdcca41a45981c907d8674983456d4d2e4e51ab573ab9d9` | PASS |
| Production 8765 | `status=ok`, `read_only=true`, `candidate_revision=ab492cc8be9e46125fa2d2fae69a3ecaac64a548`; unchanged | PASS |

### 2.3 Installed-wheel release manifest (the prior root cause)

From the installed root venv, using the candidate's own sync/release code:

```bash
cd /tmp/opencode/f3-rerun
env -u PYTHONPATH -u PYTHONHOME venvs/root/bin/python - <<'PY'
from acp.scheduler.remote.sync import _project_root, build_sync_file_list
from acp.scheduler.remote.release import build_release_manifest
from acp.calculations.attempt_freeze import local_release_identity
root = _project_root()
files = build_sync_file_list(root)
man = build_release_manifest(root)
ident = local_release_identity(root)
print("project_root", root)
print("sync_files", len(files))
print("manifest_files", len(man.files))
print("release_id", man.release_id)
print("local_identity_release_id", ident.release_id)
print("release_ids_match", man.release_id == ident.release_id)
PY
```

Observed (`logs/t0-installed-manifest.log`):

```text
project_root /tmp/opencode/f3-rerun/venvs/root/lib/python3.11/site-packages
sync_files 510
manifest_files 510
release_id 1c79785a657c6614
local_identity_release_id 1c79785a657c6614
release_ids_match True
```

The remote published release `.complete` on node-157 reports `release_id=1c79785a657c6614` and `files=510` (verified live, §2.4). This closes the `sync_files=0` / empty-manifest root cause.

### 2.4 Remote release verification on node-157

```bash
SSHPASS="$ACP_REMOTE_PASSWORD_NODE_157" sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 xieningke@10.16.5.157 \
  'python3 -c "import json,glob; p=glob.glob(\"/home/xieningke/acp_code/releases/1c79785a657c6614/.complete\")[0]; d=json.load(open(p)); print(d.get(\"release_id\")); print(len(d.get(\"files\",{})))"'
```

Observed: `1c79785a657c6614`, `510`.

## 3. Row table

Legend: `R` = re-executed by this F3 run; `U` = reused immutable evidence and not counted as an independent rerun.

| ID | Scenario | Result | R/U | Key evidence |
|---|---|---|---|---|
| T0.1 | Candidate/artifact digest verification | PASS | R | `logs/artifact-sha256.txt`; manifest `source.revision=6ec01ec96fa81b89bec8ff321c38e3db2dc154d2` |
| T0.2 | Fresh-venv import/version/path isolation | PASS | R | `acp/cccp=0.3.0`; `repo_src=False`; site-packages paths |
| T0.3 | `acp doctor` against configured node | PASS | R | `logs/t0-doctor-root.log` |
| T0.4 | LSF client/queue prerequisite | PASS | R | `bhosts workstation ok`, `normal Open:Active` |
| T0.5 | DSH/Node harness version | PASS | R | DSH `0.2.1-rc.2`; Node `v22.19.0` |
| T0.6 | Real model routing | **NOT_VERIFIED** `real_model_credentials_absent` | R | only `client-connection/browser-session` in `DSH_HOME/.credentials.yaml` |
| T1.1 | MCP stdio initialize `2025-06-18` + 8 tools + stdout/stderr split | PASS | R | `logs/mcp-inventory.log`; `serverInfo.name=acp-services-mcp` |
| T2.1 | MCP preflight zero side effect | PASS | R | `logs/mcp-preflight.log`: `would_create_task=false`, `would_reserve=false`, `executed=false` |
| T2.2 | MCP submit returns TaskRef | PASS | R | `20261024_025645_001_singlepoint` / attempt `1` |
| T2.3 | Duplicate submit idempotency | PASS | R | `logs/mcp-submit-2.log`: same TaskRef, `idempotent_replay=true` |
| T2.4 | HTTP GET same TaskRef | PASS | R | `completed`, attempt `1`, revision `6` |
| T2.5 | CLI query same TaskRef (read-only) | PASS | R | `logs/cli-query-local.json` |
| T2.6 | Fresh MCP process status same TaskRef | PASS | R | `logs/mcp-status-local.json` |
| T2.7 | Three-entry task/attempt/state/revision consistency | PASS | R | `evidence/three-entry-local.json` |
| T2.8 | Artifact prepare then identity read; digest equals disk | PASS | R | `op_248ce0ad4da9434fa27a396514b652ee`; 725 bytes; `sha256:f22e9464b53b15b2a0503d51dcc3ec7c245b85772aff6010acac40eb5dfff7a5` |
| T2.9 | Error semantics (`explicit`, unknown reference) | PASS | R | `input_invalid` exit `2`; `resource_not_ready` exit `3`, retryable |
| T2.10 | Release id/digest/plan/submission projected by all three entries | PASS | R | `evidence/three-entry-local.json`, `evidence/three-entry-lsf.json` |
| T3.1 | DSH MCP plugin config loads | **U** (prior immutable config; not re-executed) | U | `/tmp/opencode/f3-qa/logs/dsh-effective-config.yml`; not used for verdict |
| T3.2 | DSH headless real model tool call | **NOT_VERIFIED** | R | no model credentials; headless boot fails at `profile-boot.ts:16` (`FiberState` export) |
| T3.3 | Browser/Workbench real page + 15 workflows | PASS | R | `logs/browser-probe-web.log`; `evidence/workbench-browser-web.png` |
| T4.1 | Local real singlepoint (ORCA 6.1.1) | PASS | R | `logs/local-singlepoint.log`; `-1.116773191779 Ha` |
| T4.2 | Local real optimize (ORCA 6.1.1) | PASS | R | `logs/local-optimize.log`; converged; `-1.11751709882 Ha` |
| T4.3 | Local real frequency (ORCA 6.1.1) | PASS | R | `logs/local-frequency.log`; real mode `5038.6 cm-1` |
| T4.4 | Local real xTB optimize (xTB 6.7.1) | PASS | R | `logs/local-xtb.log`; converged; `-0.982686139534 Ha` |
| T4.5 | **LSF normal completion/read (real `bsub`)** | **PASS** | R | job `1766`; `release_id=1c79785a657c6614`; terminal receipt; energy `-1.116759307204 Ha` |
| T4.6 | Installed-wheel auto-sync root cause | PASS | R | 510 files; release id match (§2.3) |
| T4.7 | Real LSF control (`bstop`/`bresume`/cancel) | **NOT_VERIFIED** (B13-owned control fixture; not re-run in this bounded window) | — | no `bstop` goal in this F3 normal-completion window |
| T5.1 | Independent MCP process restart + same TaskRef | PASS | R | fresh `acp mcp serve` process; same state/attempt/revision |
| T5.2 | Duplicate submission computes once | PASS | R | same idempotency key → same TaskRef, `idempotent_replay=true` |
| T5.3 | Cancel race | PASS | R | `cancelling` → `cancelled`, `exit_code=130`, recorded PID gone |
| T5.4 | Publication failure + publication-only retry | PASS | R | one `process.starting`; manifest published after disarm; `written_at` unchanged |
| T5.5 | Budget/authorization negative P2 | **NOT_VERIFIED** (P2 fixture not in this bounded window) | — | no P2 fixture |
| P.1 | Clean-environment package install (4 role wheels) | PASS | R | `package-install/summary.json`, 19 assertions |
| P.2 | CCCP sample without Controller/ACP import | PASS | R | `package-install/cccp-sample.json`: `complete=true`, `energy=-1.0`, no `acp` import |
| P.3 | Installed Lab scheduler task (local H2/STO-3G) | PASS | R | completed; `-1.116759307204 Ha`; receipt `release_id=7698b06589bb30c2` |
| P.4 | Standalone CLI with prescribed extras | PASS | R | root venv `[remote,api]` local singlepoint/optimize/frequency/xtb_optimize |
| P.5 | **Bare root wheel `acp run singlepoint` (no extras)** | **PASS** | R | exit `0`; `-1.116773191779 Ha`; no `paramiko` error |
| P.6 | **Package-install receipt candidate identity** | **PASS** | R | `candidate_revision=6ec01ec96fa81b89bec8ff321c38e3db2dc154d2` |
| P.7 | A-27b/A-28 raw evidence aggregate | U (presence only, owner A) | U | `package-install` assertion; not F3-validated science |
| P.8 | C33/C34 raw evidence | **NOT_VERIFIED** (paths absent) | U | harness assertion `NOT_VERIFIED` |
| P.9 | C static-assets mount | **NOT_VERIFIED** `c_static_assets_absent` | U | harness assertion `NOT_VERIFIED` |
| H.1 | Historical read-only compatibility | PASS | R | 9 legacy readers; historical v1 batch manifest; pre-fix5 v2 task manifest; fixture hashes unchanged |

### 3.1 Per-row execution facts (cwd / exit / TaskRef / release / digest)

`cwd=/tmp/opencode/f3-rerun` for all rows unless shown. `attempt` is `1` unless shown.

| ID | cwd | Exit | TaskRef / operation | Release / digest |
|---|---|---|---|---|
| T0.1 | repo | 0 | — | all 8 artifact SHA-256 match manifest + source |
| T0.2 | QA | 0 | — | installed modules under venv `site-packages`; `repo_src=False` |
| T0.3 | QA | 0 | — | `Doctor finished: all checks passed` |
| T0.4 | QA | 0 | — | LSF `workstation ok`, `normal Open:Active` |
| T0.5 | DSH source | 0 | — | DSH `0.2.1-rc.2`, Node `v22.19.0` |
| T0.6 | DSH home | — | — | credentials only browser-session; not executed |
| T1.1 | QA | 0 | — | protocol `2025-06-18`; 8 tools |
| T2.1 | QA | 0 | — (preflight) | `would_create_task=false` |
| T2.2 | QA | 0 | `20261024_025645_001_singlepoint` / 1 | receipt `release_id=7698b06589bb30c2` |
| T2.3 | QA | 0 | same TaskRef / 1 | `idempotent_replay=true` |
| T2.4 | QA | 0 | same TaskRef / 1 | `completed`, revision 6 |
| T2.5 | QA | 0 | same TaskRef / 1 | `completed`, revision 6 |
| T2.6 | QA | 0 | same TaskRef / 1 | fresh process, `completed`, revision 6 |
| T2.7 | QA | 0 | same TaskRef / 1 | task/attempt/state/revision equal |
| T2.8 | QA | 0 | `op_248ce0ad4da9434fa27a396514b652ee` / 1 | `ready`; bytes 725; `sha256:f22e9464b53b15b2a0503d51dcc3ec7c245b85772aff6010acac40eb5dfff7a5` |
| T2.9 | QA | 0 (process) | — | `input_invalid` exit 2; `resource_not_ready` exit 3 |
| T2.10 | QA | 0 | same TaskRef / 1 | `7698b06589bb30c2` / `c4b54feb6a0b25ccbf0163b852ec66e0a1f003e778081d079030844871e90cc0` / `v2:3825a8cc8e1a1b83f54e0661109f8c20` / `sub_55553640305f912f` |
| T3.3 | QA | 0 | — | title `ACP 分子计算工作台`, 15 workflows, zero console errors |
| T4.1 | QA | 0 | standalone CLI `work/local-sp` | ORCA 6.1.1; `-1.116773191779 Ha` |
| T4.2 | QA | 0 | standalone CLI `work/local-opt` | converged; `-1.11751709882 Ha` |
| T4.3 | QA | 0 | standalone CLI `work/local-freq` | `5038.6 cm-1` |
| T4.4 | QA | 0 | standalone CLI `work/local-xtb` | xTB 6.7.1; converged; `-0.982686139534 Ha` |
| T4.5 | QA | 0 | `20261024_031251_001_f3r-lsf-sp3` / 1 | `1c79785a657c6614` / `c5ccfcd11b9eca683c002149221085d1afe1cc6ba8cd75922d2cc92cec8635c1` / `v2:3825a8cc8e1a1b83f54e0661109f8c20` / `sub_a65baa96e5b08c47`; LSF `1766`; exit 0 |
| T4.7 | — | not executed | — | B13-owned; not in this window |
| T5.1 | QA | 0 | `20261024_031251_001_f3r-lsf-sp3` / 1 | same revision 11 after process restart |
| T5.2 | QA | 0 | same TaskRef / 1 | one completed task; replay true |
| T5.3 | QA | 0 | `20261024_032531_002_optimize` / 1 | `cancelled`, exit 130, pid gone |
| T5.4 | QA | 0 | `20261024_032808_001_f3r-publication-fault` / 1 | `publication_pending` → `publication_completed`; one `process.starting` |
| P.1 | QA | 0 | — | `PASS`; `candidate_revision=6ec01ec96fa81b89bec8ff321c38e3db2dc154d2`; 16 PASS + 3 NOT_VERIFIED assertions |
| P.2 | QA | 0 | — | `complete=true`, `energy=-1.0` |
| P.3 | QA | 0 | `20261024_032212_001_b25-scheduler-smoke` / 1 | `7698b06589bb30c2` / `c4b54feb6a0b25ccbf0163b852ec66e0a1f003e778081d079030844871e90cc0` / `sub_978b6738fc0096d8`; energy `-1.116759307204 Ha` |
| P.5 | QA | 0 | standalone CLI `work/local-sp-bare` | `-1.116773191779 Ha` |
| P.6 | QA | 0 | — | `candidate_revision=6ec01ec96fa81b89bec8ff321c38e3db2dc154d2`; installed Lab health same |
| H.1 | QA | 0 | — | fixture hashes unchanged; old fix5 task manifest read |

## 4. Four prior FAIL rows — re-executed detail

### 4.1 Prior FAIL 1 — installed-wheel LSF normal completion

Commands (exact):

```bash
cd /tmp/opencode/f3-rerun
HOME=/tmp/opencode/f3-rerun/home \
ACP_RUN_ROOT=/tmp/opencode/f3-rerun/lab-run-root-lsf \
ACP_CANDIDATE_REVISION=6ec01ec96fa81b89bec8ff321c38e3db2dc154d2 \
CONFSEARCH_ORCA_PATH=/home/xieningke/orca611/orca \
CONFSEARCH_ORCA_LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 \
CONFSEARCH_XTB_PATH=/home/xieningke/xtb-dist/bin/xtb \
LSF_ENVDIR=/opt/openlava-2.0/etc \
LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 \
PATH=/opt/openlava-2.0/bin:/home/xieningke/openmpi418/bin:/home/xieningke/xtb-dist/bin:/usr/local/bin:/usr/bin:/bin \
setsid env -u PYTHONPATH -u PYTHONHOME /tmp/opencode/f3-rerun/venvs/root/bin/acp run serve --host 127.0.0.1 --port 51872 --run-root /tmp/opencode/f3-rerun/lab-run-root-lsf --no-browser
```

```bash
cd /tmp/opencode/f3-rerun
curl -sS -m 60 -X POST http://127.0.0.1:51872/api/v1/jobs -H 'Content-Type: application/json' --data-binary @/tmp/opencode/f3-rerun/lsf-submit3.json
```

The isolated HOME contains the operator `cluster.pre_cmds` (ORCA/OpenMPI exports) and the B-fix-5 operator paths (`remote_code_dir=/home/xieningke/acp_code`, `remote_work_dir=/home/xieningke/acp_jobs`). No environment variable or code path was faked.

Observed:

| Fact | Value |
|---|---|
| POST | `201`, `job_id=20261024_031251_001_f3r-lsf-sp3`, `status=queued` |
| Real `bsub` | `remote.submitted_persisted`, `lsf_job_id=1766` |
| `bjobs` | `1766 DONE normal workstation workstation *6e5b08c47` |
| Terminal job | `completed`, attempt `1`, revision `11`, `exit_code=0`, `node_id=node-157` |
| `release_id` | `1c79785a657c6614` |
| `release_digest` | `c5ccfcd11b9eca683c002149221085d1afe1cc6ba8cd75922d2cc92cec8635c1` |
| `plan_digest` | `v2:3825a8cc8e1a1b83f54e0661109f8c20` |
| `submission_id` | `sub_a65baa96e5b08c47` |
| Remote manifest | `release_id=1c79785a657c6614`, `files=510`, `.complete` present |
| ORCA | `Program Version 6.1.1 - RELEASE`; `FINAL SINGLE POINT ENERGY -1.116759307204` |
| Artifact pull | events `remote.worker_artifacts_fetched`: `execution_receipt.json`, `WORK/00_RUNTIME/task_result.json` |
| Local/remote receipt digest | `sha256:d9299711d8961e9ae836e75ed33b2ff63a00b87a49e16f8447046f66a072c3b2` (identical on local and remote) |
| `task_result.result_digest` | `sha256:f21f8ac6fef56f14c3dd59d9b4bfc541864c01e7cf0248b71514a4a2f83b1270` (equals receipt `result_digest`) |

Events show `remote.code_release_ready` with `release_id=1c79785a657c6614`, `source=remote` (the installed manifest matched the immutable, content-addressed release already published on node-157), then `remote.attempt_frozen`, `remote.submitted`, `remote.lsf_status running`, `job.completed`, `job.publication_completed`.

**PASS.** The prior failure was an empty installed manifest (`sync_files=0`); this run proves the installed wheel produces a 510-file manifest with the same release id as the remote release and completes a real LSF job.

### 4.2 Prior FAIL 2 — bare root wheel `acp run singlepoint`

```bash
cd /tmp/opencode/f3-rerun
ACP_RUN_ROOT=/tmp/opencode/f3-rerun/run-root \
CONFSEARCH_ORCA_PATH=/home/xieningke/orca611/orca \
CONFSEARCH_ORCA_LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 \
LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 \
PATH=/home/xieningke/openmpi418/bin:/home/xieningke/xtb-dist/bin:/usr/local/bin:/usr/bin:/bin \
env -u PYTHONPATH -u PYTHONHOME /tmp/opencode/f3-rerun/venvs/bare/bin/acp run singlepoint \
  --input /tmp/opencode/f3-rerun/work/h2.xyz --method HF --basis STO-3G \
  --charge 0 --multiplicity 1 --nproc 1 --output /tmp/opencode/f3-rerun/work/local-sp-bare
```

Observed: exit `0`; `Single-point calculation completed`; `Energy: -1.116773191779 Hartree`; `sp.out` contains ORCA `Program Version 6.1.1 - RELEASE`. No `paramiko` import error. **PASS.**

### 4.3 Prior FAIL 3 — package-install receipt candidate identity

```bash
cd /tmp/opencode/f3-rerun
env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3.11 \
  /ACPServer/AutoCalcPlatform/scripts/acceptance/acp2_runtime.py \
  --scenario package-install \
  --fixture /tmp/opencode/f3-rerun/package-install-fixture.json \
  --out /tmp/opencode/f3-rerun/package-install \
  --timeout 1800
```

Observed (`package-install/summary.json` and the run receipt):

```text
result=PASS
candidate_revision=6ec01ec96fa81b89bec8ff321c38e3db2dc154d2
contract_version=1.3.0
run=package-install-main-20261023T192033Z-2041897-8e0c4e28.json
```

The receipt's `candidate_revision` is exactly `release-manifest.json.source.revision` (`6ec01ec96fa81b89bec8ff321c38e3db2dc154d2`), and installed Lab health reports the same value. Prior FAIL `096cdffe9a3817d8ee443d824830d87ec6e07bc2` is gone. **PASS.**

### 4.4 Prior FAIL 4 — cross-entry release projection

Same LSF TaskRef `20261024_031251_001_f3r-lsf-sp3` read by three entries (`evidence/three-entry-lsf.json`):

| Field | HTTP | CLI | MCP | Equal |
|---|---|---|---|---|
| `release_id` | `1c79785a657c6614` | `1c79785a657c6614` | `1c79785a657c6614` | yes |
| `release_digest` | `c5ccfcd11b9eca683c002149221085d1afe1cc6ba8cd75922d2cc92cec8635c1` | same | same | yes |
| `plan_digest` | `v2:3825a8cc8e1a1b83f54e0661109f8c20` | same | same | yes |
| `submission_id` | `sub_a65baa96e5b08c47` | same | same | yes |
| `attempt` | `1` | `1` | `1` | yes |
| `state` | `completed` | `completed` | `completed` | yes |
| `revision` | `11` | `11` | `11` | yes |

MCP machine output redacts task identifiers but not the release/plan/submission facts. The same equality holds for the local task (`evidence/three-entry-local.json`). **PASS.**

## 5. Other re-executed rows

### 5.1 Local real QC (installed root venv)

| Workflow | Result | Scientific fact |
|---|---|---|
| `singlepoint` HF/STO-3G H2 | completed | `-1.116773191779 Ha`; ORCA 6.1.1 |
| `optimize` HF/STO-3G H2 | completed | converged; `-1.11751709882 Ha`; H–H `0.712274 Å` |
| `frequency` HF/STO-3G H2 | completed | 6 modes; one real mode `5038.6 cm-1`, five zero modes |
| `xtb_optimize` GFN-xTB H2 | completed | xTB 6.7.1; converged; `-0.982686139534 Ha` |

### 5.2 MCP process (T1/T2)

`initialize` negotiates `2025-06-18`; `serverInfo.name=acp-services-mcp`; exactly eight tools: `acp_analyze`, `acp_cancel`, `acp_capabilities`, `acp_preflight`, `acp_prepare_artifact`, `acp_read_artifact`, `acp_status`, `acp_submit`. stdout carries only newline-delimited JSON-RPC frames; diagnostics go to stderr (empty on the inventory run). Each MCP adapter is a separate process and exits cleanly on stdin EOF.

Preflight returns `status=ok`, `dry_run=true`, `executed=false`, `would_create_task=false`, `would_reserve=false`. Submit returns `20261024_025645_001_singlepoint` / attempt 1. Duplicate submit returns the same TaskRef with `idempotent_replay=true`. HTTP, CLI, and a fresh MCP process all read `completed` / attempt 1 / revision 6. Artifact prepare reaches `ready` with 725 bytes and `sha256:f22e9464b53b15b2a0503d51dcc3ec7c245b85772aff6010acac40eb5dfff7a5`, identical to `sha256sum RESULT/task_result.json`. `acp_analyze` without `explicit=true` returns `input_invalid` / exit 2; an unknown reference returns `resource_not_ready` / exit 3 / retryable.

### 5.3 Package install, CCCP, installed Lab

The official `package-install` scenario ran against `release-fix5` and returned `PASS` with 19 assertions (16 PASS, 3 `NOT_VERIFIED` C rows):

- four fresh role venvs (`cccp`, `worker`, `web`, `root`) installed at `0.3.0`, modules resolve under `site-packages`, forbidden imports absent;
- CCCP controlled singlepoint ran without importing `acp`/`acp_services`: `complete=true`, `energy=-1.0`;
- installed Lab `/api/health` `ok`, `candidate_revision=6ec01ec96fa81b89bec8ff321c38e3db2dc154d2`;
- stdio MCP: `2025-06-18`, eight tools;
- installed Lab executed one real H2/STO-3G singlepoint: `completed`, `-1.116759307204 Ha`, receipt `release_id=7698b06589bb30c2`, `release_digest=c4b54feb6a0b25ccbf0163b852ec66e0a1f003e778081d079030844871e90cc0`, `submission_id=sub_978b6738fc0096d8`, envelope matches;
- assets/notices/licenses/model assets/`py.typed`/sample config/schema/SDK/docs present.

C33/C34 and the C static-assets manifest are absent → `NOT_VERIFIED` (same as prior review).

### 5.4 Cancel race (T5.3)

Submitted a 17-atom butanol `optimize` via MCP and cancelled from a second MCP process immediately. Status transitioned `cancelling` → `cancelled`; job `exit_code=130`; recorded PID `2043618` disappeared from `/proc`; no ORCA/worker process remained. **PASS.**

### 5.5 Independent MCP restart / disconnect (T5.1/T5.2)

A fresh `acp mcp serve` process queried the same LSF TaskRef after the submitting process had exited: `completed`, attempt `1`, revision `11`, same release/plan/submission fields. A duplicate LSF submit with the same idempotency key returned the same TaskRef with `idempotent_replay=true`. After stopping and restarting the Controller on the same run root, HTTP and CLI still returned the same task at revision `11` with the same release/plan/submission identity. **PASS.**

### 5.6 Publication failure + publication-only retry (T5.4)

Started the acceptance-only fault Controller on port `51873` with `{"armed": true, "fault": "publication_failure", "seam": "register_result_manifest"}`. Submitted `20261024_032808_001_f3r-publication-fault`.

Observed while armed:

```text
job.completion_deferred  code=publication_failed
job.completed            exit_code=0
job.publication_pending  code=publication_failed
RESULT/result_manifest.json absent
```

After rewriting the fault descriptor to `armed:false`, the Controller published on its next pass (`job.publication_completed`), `RESULT/result_manifest.json` appeared, `task_result.json` `written_at` remained `2026-10-23T19:28:12.367608+00:00`, and `process.starting` occurred exactly once. This is a publication-only retry, not recomputation. **PASS.**

### 5.7 Historical read-only (H.1)

Using the installed root venv, read all nine `acp.compat.legacy` manifests from `tests/fixtures/legacy_*.json`, the historical v1 batch manifest, and a pre-fix5 v2 task manifest (`/tmp/opencode/fix5-qa/lab-run-root3/uncategorized/2_H2_H_0_0_0_H_0_0_0.74_singlepoint_fix5c`). Hash snapshots of the fixture tree, the historical fixture tree, and the old task tree were identical before and after (`fixtures_unchanged`, `historical_unchanged`, `old_task_unchanged` all `true`). **PASS.**

## 6. DSH limitation (exact gap)

- `DSH_HOME=/ACPServer/acp2-development/data/dsh-home`; `.credentials.yaml` contains only a `client-connection/browser-session` record. There is no model provider credential, provider selection, or model id in the environment or profile.
- The locked DSH source (`/ACPServer/acp2-tools/deepseek-harness` @ `687f282e8625c6b8d11ff58211a90524be2e4317`, `0.2.1-rc.2`, Node `v22.19.0`) still fails headless profile boot before any model call:

```text
/ACPServer/acp2-tools/deepseek-harness/apps/cli/src/profile-boot.ts:16
import { FiberState, type Context } from '@deepseek-ai/cordis'
SyntaxError: The requested module '@deepseek-ai/cordis' does not provide an export named 'FiberState'
```

Therefore **T3.2 (DSH headless real model tool call) is `NOT_VERIFIED`**, not PASS. No natural-language model answer is substituted for a tool-call receipt. T3.3 browser/Workbench **is PASS** independently (web-role Lab on `127.0.0.1:51874`, title `ACP 分子计算工作台`, 15 workflows, zero console errors, screenshot in `evidence/workbench-browser-web.png`).

## 7. Caveats (explicit, not hidden)

- **LSF release source:** the LSF run verified the installed wheel's 510-file manifest against the immutable remote release `1c79785a657c6614` already published by B-fix-5 on node-157 (`source=remote`). The run used a **new** submission (`sub_a65baa96e5b08c47`, real `bsub` `1766`) and a **new** receipt; it did not reuse a job/receipt. A cold upload to a brand-new `remote_code_dir` was attempted with `remote_code_dir=/home/xieningke/acp_code_f3r`; the 73.4 MB sync was incomplete in the bounded window and the partial staging directory was removed. Cold-upload transfer speed is therefore **not** claimed as PASS. The prior root cause (empty installed manifest) is closed by §2.3 and §4.1.
- **T4.7 LSF control** (`bstop`/`bresume`/cancel) belongs to B13's bounded control fixture; it was not re-run here and is `NOT_VERIFIED`.
- **T5.5 P2 budget/approval** was not exercised; no P2 fixture was used. `NOT_VERIFIED`.
- **C33/C34/C static assets** remain absent; `NOT_VERIFIED`, unchanged from the prior review.
- T3.1 DSH MCP plugin config is an immutable prior config artifact (`U`), not an independent rerun; it is not used to support the verdict.

## 8. Cleanup and production isolation

- Stopped all test Controllers/Labs: `51872`, `51873`, `51874` all report `free`; no `f3-rerun` `run serve`/fault-controller process remains.
- No `acp_services.worker_bootstrap` or ORCA process remains.
- `bjobs -a`: only the completed test job `1766` (`DONE`); no unfinished job.
- MCP adapters were single-shot and exited on stdin EOF.
- Production `acp.service` remained `active`; `http://127.0.0.1:8765/api/health` still returns `status=ok`, `read_only=true`, `candidate_revision=ab492cc8be9e46125fa2d2fae69a3ecaac64a548`. `/var/lib/acp/runs` (`/home/xieningke/ACP_runs`) was never used; all test run roots live under `/tmp/opencode/f3-rerun`.
- Remote test cleanup: removed `/home/xieningke/acp_code_f3r`, `/home/xieningke/acp_jobs_f3r`, and the aborted partial staging `7698b06589bb30c2.7167f157be10` plus its ref. The completed test job's remote directory `/home/xieningke/acp_jobs/uncategorized/2_H2_H_0_0_0_H_0_0_0.74_singlepoint_f3r3` was left for audit; it is not referenced by any production task.
- No product code, plan file, systemd unit, or production data was modified. Only `/tmp/opencode/f3-rerun/` and this evidence file were written.

## 9. Exact command appendix

All commands ran with `env -u PYTHONPATH -u PYTHONHOME` (shown inline where relevant); all paths are absolute.

### 9.1 Fresh installs

```bash
cd /tmp/opencode/f3-rerun
/usr/bin/python3.11 -m venv venvs/root
/usr/bin/python3.11 -m venv venvs/web
/usr/bin/python3.11 -m venv venvs/bare
env -u PYTHONPATH -u PYTHONHOME venvs/root/bin/python -m pip install --disable-pip-version-check --no-input --find-links /ACPServer/AutoCalcPlatform/reports/acp2_implementation/bb45a46/B/B25/release-fix5 --find-links /ACPServer/AutoCalcPlatform/reports/acp2_implementation/bb45a46/B/B25/release-fix5/root-build 'acp[remote,api]==0.3.0'
env -u PYTHONPATH -u PYTHONHOME venvs/web/bin/python -m pip install --disable-pip-version-check --no-input --find-links /ACPServer/AutoCalcPlatform/reports/acp2_implementation/bb45a46/B/B25/release-fix5 /ACPServer/AutoCalcPlatform/reports/acp2_implementation/bb45a46/B/B25/release-fix5/acp_web-0.3.0-py3-none-any.whl
env -u PYTHONPATH -u PYTHONHOME venvs/bare/bin/python -m pip install --disable-pip-version-check --no-input --find-links /ACPServer/AutoCalcPlatform/reports/acp2_implementation/bb45a46/B/B25/release-fix5/root-build /ACPServer/AutoCalcPlatform/reports/acp2_implementation/bb45a46/B/B25/release-fix5/root-build/acp-0.3.0-py3-none-any.whl
```

### 9.2 Local real QC (root venv)

```bash
cd /tmp/opencode/f3-rerun
ACP_RUN_ROOT=/tmp/opencode/f3-rerun/run-root CONFSEARCH_ORCA_PATH=/home/xieningke/orca611/orca CONFSEARCH_ORCA_LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 PATH=/home/xieningke/openmpi418/bin:/home/xieningke/xtb-dist/bin:/usr/local/bin:/usr/bin:/bin venvs/root/bin/acp doctor
ACP_RUN_ROOT=/tmp/opencode/f3-rerun/run-root CONFSEARCH_ORCA_PATH=/home/xieningke/orca611/orca CONFSEARCH_ORCA_LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 PATH=/home/xieningke/openmpi418/bin:/home/xieningke/xtb-dist/bin:/usr/local/bin:/usr/bin:/bin venvs/root/bin/acp run singlepoint --input /tmp/opencode/f3-rerun/work/h2.xyz --method HF --basis STO-3G --charge 0 --multiplicity 1 --nproc 1 --output /tmp/opencode/f3-rerun/work/local-sp
ACP_RUN_ROOT=/tmp/opencode/f3-rerun/run-root CONFSEARCH_ORCA_PATH=/home/xieningke/orca611/orca CONFSEARCH_ORCA_LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 PATH=/home/xieningke/openmpi418/bin:/home/xieningke/xtb-dist/bin:/usr/local/bin:/usr/bin:/bin venvs/root/bin/acp run optimize --input /tmp/opencode/f3-rerun/work/h2.xyz --method HF --basis STO-3G --charge 0 --multiplicity 1 --nproc 1 --geom-maxiter 20 --output /tmp/opencode/f3-rerun/work/local-opt
ACP_RUN_ROOT=/tmp/opencode/f3-rerun/run-root CONFSEARCH_ORCA_PATH=/home/xieningke/orca611/orca CONFSEARCH_ORCA_LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 PATH=/home/xieningke/openmpi418/bin:/home/xieningke/xtb-dist/bin:/usr/local/bin:/usr/bin:/bin venvs/root/bin/acp run frequency --input /tmp/opencode/f3-rerun/work/h2.xyz --method HF --basis STO-3G --charge 0 --multiplicity 1 --nproc 1 --output /tmp/opencode/f3-rerun/work/local-freq
ACP_RUN_ROOT=/tmp/opencode/f3-rerun/run-root CONFSEARCH_XTB_PATH=/home/xieningke/xtb-dist/bin/xtb PATH=/home/xieningke/xtb-dist/bin:/usr/local/bin:/usr/bin:/bin venvs/root/bin/acp run xtb_optimize --input /tmp/opencode/f3-rerun/work/h2.xyz --charge 0 --multiplicity 1 --output /tmp/opencode/f3-rerun/work/local-xtb
```

### 9.3 Bare root wheel (no extras)

```bash
cd /tmp/opencode/f3-rerun
ACP_RUN_ROOT=/tmp/opencode/f3-rerun/run-root CONFSEARCH_ORCA_PATH=/home/xieningke/orca611/orca CONFSEARCH_ORCA_LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 PATH=/home/xieningke/openmpi418/bin:/home/xieningke/xtb-dist/bin:/usr/local/bin:/usr/bin:/bin env -u PYTHONPATH -u PYTHONHOME venvs/bare/bin/acp run singlepoint --input /tmp/opencode/f3-rerun/work/h2.xyz --method HF --basis STO-3G --charge 0 --multiplicity 1 --nproc 1 --output /tmp/opencode/f3-rerun/work/local-sp-bare
```

### 9.4 MCP stdio (root venv)

```bash
cd /tmp/opencode/f3-rerun
HOME=/tmp/opencode/f3-rerun/home venvs/root/bin/python mcp_ops.py /tmp/opencode/f3-rerun/venvs/root/bin/acp http://127.0.0.1:51872 inventory
HOME=/tmp/opencode/f3-rerun/home venvs/root/bin/python mcp_ops.py /tmp/opencode/f3-rerun/venvs/root/bin/acp http://127.0.0.1:51872 preflight '{"workflow":"singlepoint","input":{"source_type":"xyz_text","source":"2\nH2\nH 0 0 0\nH 0 0 0.74\n","charge":0,"multiplicity":1},"method":{"levels":{"single_point":{"engine":"orca","functional":"HF","basis":"STO-3G"}}},"resources":{"nproc":1,"mem":"1024MB"},"idempotency_key":"f3r-preflight-1"}'
HOME=/tmp/opencode/f3-rerun/home venvs/root/bin/python mcp_ops.py /tmp/opencode/f3-rerun/venvs/root/bin/acp http://127.0.0.1:51872 submit '{"workflow":"singlepoint","input":{"source_type":"xyz_text","source":"2\nH2\nH 0 0 0\nH 0 0 0.74\n","charge":0,"multiplicity":1},"method":{"levels":{"single_point":{"engine":"orca","functional":"HF","basis":"STO-3G"}}},"resources":{"nproc":1,"mem":"1024MB"},"execution_mode":"local","idempotency_key":"f3r-idem-1"}'
HOME=/tmp/opencode/f3-rerun/home venvs/root/bin/python mcp_ops.py /tmp/opencode/f3-rerun/venvs/root/bin/acp http://127.0.0.1:51872 status '{"reference":"20261024_025645_001_singlepoint"}'
HOME=/tmp/opencode/f3-rerun/home venvs/root/bin/python mcp_ops.py /tmp/opencode/f3-rerun/venvs/root/bin/acp http://127.0.0.1:51872 prepare '{"purpose":"artifact","schema_version":1,"job_id":"20261024_025645_001_singlepoint","artifact_id":"task_result.json","attempt":1,"relative_path":"RESULT/task_result.json","idempotency_key":"f3r-prepare-1"}'
HOME=/tmp/opencode/f3-rerun/home venvs/root/bin/python mcp_ops.py /tmp/opencode/f3-rerun/venvs/root/bin/acp http://127.0.0.1:51872 read '{"artifact_ref":{"task_id":"20261024_025645_001_singlepoint","job_id":"20261024_025645_001_singlepoint","attempt":1,"artifact_id":"task_result.json","generation":1,"digest":"sha256:f22e9464b53b15b2a0503d51dcc3ec7c245b85772aff6010acac40eb5dfff7a5"},"max_bytes":4096,"encoding":"identity"}'
HOME=/tmp/opencode/f3-rerun/home venvs/root/bin/python mcp_ops.py /tmp/opencode/f3-rerun/venvs/root/bin/acp http://127.0.0.1:51872 analyze '{"workflow":"singlepoint","input":{"source_type":"xyz_text","source":"2\nH2\nH 0 0 0\nH 0 0 0.74\n"}}'
HOME=/tmp/opencode/f3-rerun/home venvs/root/bin/python mcp_ops.py /tmp/opencode/f3-rerun/venvs/root/bin/acp http://127.0.0.1:51872 status '{"reference":"does-not-exist-f3r"}'
```

### 9.5 LSF normal (real `bsub`)

```bash
cd /tmp/opencode/f3-rerun
HOME=/tmp/opencode/f3-rerun/home \
ACP_RUN_ROOT=/tmp/opencode/f3-rerun/lab-run-root-lsf \
ACP_CANDIDATE_REVISION=6ec01ec96fa81b89bec8ff321c38e3db2dc154d2 \
CONFSEARCH_ORCA_PATH=/home/xieningke/orca611/orca \
CONFSEARCH_ORCA_LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 \
CONFSEARCH_XTB_PATH=/home/xieningke/xtb-dist/bin/xtb \
LSF_ENVDIR=/opt/openlava-2.0/etc \
LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 \
PATH=/opt/openlava-2.0/bin:/home/xieningke/openmpi418/bin:/home/xieningke/xtb-dist/bin:/usr/local/bin:/usr/bin:/bin \
setsid env -u PYTHONPATH -u PYTHONHOME /tmp/opencode/f3-rerun/venvs/root/bin/acp run serve --host 127.0.0.1 --port 51872 --run-root /tmp/opencode/f3-rerun/lab-run-root-lsf --no-browser
curl -sS -m 60 -X POST http://127.0.0.1:51872/api/v1/jobs -H 'Content-Type: application/json' --data-binary @/tmp/opencode/f3-rerun/lsf-submit3.json
curl -sS -m 5 http://127.0.0.1:51872/api/v1/jobs/20261024_031251_001_f3r-lsf-sp3
```

`lsf-submit3.json` requests `workflow=singlepoint`, H2 `xyz_text`, HF/STO-3G, `nproc=1`, `mem=1024MB`, `execution_mode=remote`, `target_node=node-157`, `idempotency_key=f3r-lsf-3`, `remark=f3r3`.

### 9.6 Cross-entry

```bash
cd /tmp/opencode/f3-rerun
HOME=/tmp/opencode/f3-rerun/home venvs/root/bin/acp task query --controller http://127.0.0.1:51872 --job 20261024_031251_001_f3r-lsf-sp3
HOME=/tmp/opencode/f3-rerun/home venvs/root/bin/python mcp_ops.py /tmp/opencode/f3-rerun/venvs/root/bin/acp http://127.0.0.1:51872 status '{"reference":"20261024_031251_001_f3r-lsf-sp3"}'
curl -sS -m 5 http://127.0.0.1:51872/api/v1/jobs/20261024_031251_001_f3r-lsf-sp3
```

### 9.7 Package install

```bash
cd /tmp/opencode/f3-rerun
env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3.11 /ACPServer/AutoCalcPlatform/scripts/acceptance/acp2_runtime.py --scenario package-install --fixture /tmp/opencode/f3-rerun/package-install-fixture.json --out /tmp/opencode/f3-rerun/package-install --timeout 1800
```

### 9.8 Cancel race

```bash
cd /tmp/opencode/f3-rerun
HOME=/tmp/opencode/f3-rerun/home venvs/root/bin/python mcp_ops.py /tmp/opencode/f3-rerun/venvs/root/bin/acp http://127.0.0.1:51872 submit '{"workflow":"optimize","input":{"source_type":"xyz_text","source":"17\nbutanol\nC -1.509828 0.690635 0.378555\nC -0.633130 -0.507183 0.708426\nC 0.294997 -0.918358 -0.438209\nC 1.304953 0.143279 -0.861738\nO 2.166728 0.488145 0.212723\nH -2.083508 0.517692 -0.537412\nH -2.218606 0.872743 1.192804\nH -0.914593 1.599246 0.249025\nH -1.281952 -1.358270 0.948415\nH -0.051269 -0.296340 1.612653\nH 0.842786 -1.812343 -0.115114\nH -0.306854 -1.206294 -1.308199\nH 1.928688 -0.248996 -1.671364\nH 0.818305 1.050001 -1.233486\nH 1.643282 0.986044 0.862921\n","charge":0,"multiplicity":1},"method":{"levels":{"optimization":{"engine":"orca","functional":"HF","basis":"STO-3G"}}},"resources":{"nproc":1,"mem":"2048MB"},"execution_mode":"local","idempotency_key":"f3r-cancel-1"}'
HOME=/tmp/opencode/f3-rerun/home venvs/root/bin/python mcp_ops.py /tmp/opencode/f3-rerun/venvs/root/bin/acp http://127.0.0.1:51872 cancel '{"reference":"20261024_032531_002_optimize"}'
```

### 9.9 Publication retry

```bash
cd /tmp/opencode/f3-rerun
printf '{"armed": true, "fault": "publication_failure", "seam": "register_result_manifest"}\n' > /tmp/opencode/f3-rerun/fault.json
HOME=/tmp/opencode/f3-rerun/home \
ACP_RUN_ROOT=/tmp/opencode/f3-rerun/fault-run-root \
ACP_CANDIDATE_REVISION=6ec01ec96fa81b89bec8ff321c38e3db2dc154d2 \
CONFSEARCH_ORCA_PATH=/home/xieningke/orca611/orca \
CONFSEARCH_ORCA_LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 \
LD_LIBRARY_PATH=/home/xieningke/openmpi418/lib:/home/xieningke/orca611 \
PATH=/home/xieningke/openmpi418/bin:/home/xieningke/xtb-dist/bin:/usr/local/bin:/usr/bin:/bin \
setsid env -u PYTHONPATH -u PYTHONHOME /tmp/opencode/f3-rerun/venvs/root/bin/python /ACPServer/AutoCalcPlatform/scripts/acceptance/acp2_fault_controller.py --source /tmp/opencode/f3-rerun/fault-source --fault-file /tmp/opencode/f3-rerun/fault.json --candidate-revision 6ec01ec96fa81b89bec8ff321c38e3db2dc154d2 --orca /home/xieningke/orca611/orca --orca-ld-library-path /home/xieningke/openmpi418/lib:/home/xieningke/orca611 -- run serve --host 127.0.0.1 --port 51873 --run-root /tmp/opencode/f3-rerun/fault-run-root --poll-interval 1 --no-browser
curl -sS -m 30 -X POST http://127.0.0.1:51873/api/v1/jobs -H 'Content-Type: application/json' --data-binary @/tmp/opencode/f3-rerun/fault-submit.json
printf '{"armed": false, "fault": "publication_failure", "seam": "register_result_manifest"}\n' > /tmp/opencode/f3-rerun/fault.json
```

### 9.10 Browser

```bash
cd /tmp/opencode/f3-rerun
PLAYWRIGHT_BROWSERS_PATH=/home/xieningke/.cache/ms-playwright /ACPServer/acp2-development/venvs/browser/bin/python /tmp/opencode/f3-rerun/browser_probe_web.py
```

`browser_probe_web.py` loads `http://127.0.0.1:51874/`, records title/h1, console errors, `/api/status`, `/api/workflows`, and writes `/tmp/opencode/f3-rerun/evidence/workbench-browser-web.png`.

### 9.11 Historical read-only

```bash
cd /tmp/opencode/f3-rerun
env -u PYTHONPATH -u PYTHONHOME venvs/root/bin/python /tmp/opencode/f3-rerun/historical_read.py
```

### 9.12 Cleanup

```bash
cd /tmp/opencode/f3-rerun
kill -TERM $(cat /tmp/opencode/f3-rerun/lab-lsf.pid) $(cat /tmp/opencode/f3-rerun/lab-web.pid) $(cat /tmp/opencode/f3-rerun/fault.pid)
SSHPASS="$ACP_REMOTE_PASSWORD_NODE_157" sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 xieningke@10.16.5.157 'rm -rf /home/xieningke/acp_code_f3r /home/xieningke/acp_jobs_f3r /home/xieningke/acp_code/releases/.staging/7698b06589bb30c2.7167f157be10; rm -f /home/xieningke/acp_code/releases/.refs/7698b06589bb30c2.5436479f6050.ref'
```

## 10. Evidence index

| Path | Contents |
|---|---|
| `/tmp/opencode/f3-rerun/logs/artifact-sha256.txt` | 8 artifact digests |
| `/tmp/opencode/f3-rerun/logs/t0-installed-manifest.log` | installed manifest 510 / release id |
| `/tmp/opencode/f3-rerun/logs/t0-doctor-root.log` | `acp doctor` pass |
| `/tmp/opencode/f3-rerun/logs/mcp-inventory.log` | protocol + 8 tools |
| `/tmp/opencode/f3-rerun/logs/mcp-preflight.log` | zero-side-effect preflight |
| `/tmp/opencode/f3-rerun/logs/mcp-submit-1.log` | local submit TaskRef |
| `/tmp/opencode/f3-rerun/logs/mcp-submit-2.log` | idempotent replay |
| `/tmp/opencode/f3-rerun/logs/mcp-prepare.log` | artifact prepare |
| `/tmp/opencode/f3-rerun/logs/mcp-read-artifact.log` | identity read |
| `/tmp/opencode/f3-rerun/logs/mcp-analyze-negative.log` | `input_invalid` |
| `/tmp/opencode/f3-rerun/logs/mcp-status-negative.log` | `resource_not_ready` |
| `/tmp/opencode/f3-rerun/evidence/three-entry-local.json` | local three-entry equality |
| `/tmp/opencode/f3-rerun/logs/lsf-create3.json` | accepted LSF job |
| `/tmp/opencode/f3-rerun/logs/lsf-job-terminal.json` | terminal LSF row |
| `/tmp/opencode/f3-rerun/logs/http-lsf-job.json` | HTTP LSF row |
| `/tmp/opencode/f3-rerun/logs/cli-query-lsf.json` | CLI LSF row |
| `/tmp/opencode/f3-rerun/logs/mcp-status-lsf.json` | MCP LSF row |
| `/tmp/opencode/f3-rerun/evidence/three-entry-lsf.json` | LSF three-entry equality |
| `/tmp/opencode/f3-rerun/evidence/lsf-sp.out` | remote ORCA output (6.1.1) |
| `/tmp/opencode/f3-rerun/logs/package-install.log` | package-install PASS |
| `/tmp/opencode/f3-rerun/package-install/summary.json` | `candidate_revision=6ec01ec96fa81b89bec8ff321c38e3db2dc154d2` |
| `/tmp/opencode/f3-rerun/package-install/package-install-main-20261023T192033Z-2041897-8e0c4e28.json` | 19-assertion receipt |
| `/tmp/opencode/f3-rerun/logs/mcp-submit-cancel.json` | cancel-race submit |
| `/tmp/opencode/f3-rerun/logs/mcp-cancel.json` | cancel-race control |
| `/tmp/opencode/f3-rerun/logs/http-cancel-job.json` | `cancelled`, exit 130 |
| `/tmp/opencode/f3-rerun/logs/fault-controller.log` | fault seam |
| `/tmp/opencode/f3-rerun/logs/fault-job.json` | publication pending |
| `/tmp/opencode/f3-rerun/logs/browser-probe-web.log` | Workbench 15 workflows |
| `/tmp/opencode/f3-rerun/evidence/workbench-browser-web.png` | Workbench screenshot |
| `/tmp/opencode/f3-rerun/logs/historical-read.json` | historical read-only |
| `/tmp/opencode/f3-rerun/logs/local-singlepoint.log` | Local singlepoint |
| `/tmp/opencode/f3-rerun/logs/local-optimize.log` | Local optimize |
| `/tmp/opencode/f3-rerun/logs/local-frequency.log` | Local frequency |
| `/tmp/opencode/f3-rerun/logs/local-xtb.log` | Local xTB optimize |
| `/tmp/opencode/f3-rerun/logs/local-singlepoint-bare.log` | bare root wheel |
| `/tmp/opencode/f3-rerun/logs/lab-server-lsf.log` | LSF Lab |
| `/tmp/opencode/f3-rerun/logs/lab-server-lsf-restart.log` | Controller restart |

**VERDICT: APPROVE**
