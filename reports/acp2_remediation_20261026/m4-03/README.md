# M4-03 — real LSF submission, execution limits, control and recovery

Candidate: branch `acp2/integration-remediation` at `36b505a` (the M4-03 work is
uncommitted on top of it; the working tree also carries the parallel M4-04 DA
lane, untouched by this task).

Command (the authoritative run):

```bash
set -a; . /ACPServer/acp2-runs/remediation/env.sh; set +a
PYTHONPATH=src python3.11 scripts/acceptance/acp2_m403.py \
    --out reports/acp2_remediation_20261026/m4-03/
```

Result: **15/15 rows PASS, `cleanup_ok=true`** (`summary.json`).

## Matrix (each row is real, not a fixture)

| row | what ran | status | key native evidence |
|---|---|---|---|
| `environment` | Openlava client, host `workstation` ok, queue `normal` open, no unfinished job before the run | PASS | `native/bhosts.txt`, `native/bqueues.txt` |
| `code_release` | content-hash release `2d1ab37a0f988933` published + verified on the node (606 files; one batched `sha256sum`/SFTP session per chunk) | PASS | `native/code_release.json` |
| `submission` | HTTP submit with `execution_mode=remote`, `target_node=lsf-local`, `node_tags=["xtb"]` → real `bsub` job `1`; frozen JobSpec vs actual LSF target (queue, exec host, `submit.lsf`, `-J acp_<submission_id>`, release-bound `PYTHONPATH`, `cd <remote_dir>`) all agree; no `-W` for an unbounded approval | PASS | `native/submit-1.lsf`, `native/bjobs-1.txt`, `native/submission-job.json`, `native/submission-terminal.json` |
| `illegal_target` | unknown `target_node` and unsatisfied `node_tags` → 422 `input_invalid` (`unknown_target_node` / `target_node_incapable`) with **no** new LSF job | PASS | `native/illegal-target.json` |
| `lifecycle` | same TaskRef: `availability`, `energy-graph`, `prepare(artifact=result_manifest)` → cache-only `GET /prepared/manifest`, `prepare(structure-input)` → `GET /prepared/geometry`; the local task dir has **no** `RESULT/result_manifest.json`, so the read root is the remote cache; served manifest/geometry content matches the node's bytes; release identity published | PASS | `native/lifecycle.json` |
| `race` | real `bstop`/`bresume` (row `paused`↔`running`, LSF `USUSP`↔`RUN`) then concurrent `cancel`+`pause` on a second real job: exactly one winner (`cancelled`), LSF job gone | PASS | `native/race.json` |
| `wall_limit` | approval budget `wall_seconds=60` (real approval bound to the frozen plan) → `#BSUB -W 00:01` → native LSF `RUNLIMIT`/`Exited with exit code 140` + platform receipt (`verified=true`, `deadline_at = remote started_at + 60 s`) | PASS | `native/wall-limit.json`, `native/submit-104.lsf`, `native/bjobs-104.txt` |
| `queue_deadline` | `badmin hclose workstation`, approval `queue_seconds=40`, job really `PEND` (native PENDING REASONS captured) → expiry → terminal `failed` + `bkill` + receipt (`verified=true`) | PASS | `native/queue-deadline.json`, `native/bjobs-pending-105.txt` |
| `paused_timing` | 90 s wall, real `bstop` for 25 s, `bresume`; the absolute deadline stayed `started_at + 90 s` (pause did not extend it) and terminated the attempt | PASS | `native/paused-timing.json` |
| `local_wall` | same approval wall (10 s) on a real **local** job: process group (worker + ORCA children) killed via SIGCONT→SIGTERM→SIGKILL, PIDs gone, receipt `verified=true` | PASS | `native/local-wall.json` |
| `restart` | Controller restarted mid-run: re-adopted LSF `107`, **no second `bsub`**, same absolute deadline, remaining time restored | PASS | `native/restart.json` |
| `publication` | lost published manifest + `publication_pending` after a restart → publication retried from the existing receipt (same attempt, no recompute); a lost **execution receipt** → typed integrity refusal (`job.publication_pending`), never a fabricated result | PASS | `native/publication.json` |
| `late_done` | a late terminal observation appended after the terminal CAS does not change status/attempt (CAS + terminal scan set) | PASS | `native/late-done.json` |
| `budget_concurrency` | one approval with `task_ref=1`, two concurrent remote submits → `201` + `403 budget_exhausted`; exactly one LSF job | PASS | `native/budget-concurrency.json` |
| `multiuser` | API-key identities: alice (project A) submits a real remote job; bob (project B) ⇒ `403`, bob read of alice's job ⇒ `404`, read-only key submit ⇒ `403`, alice key revoked ⇒ `401`; no usage-write surface exists for a member | PASS | `native/multiuser.json` |

Cleanup: 9 tracked LSF job ids, all `DONE`/`EXIT`, `leftovers=[]`, `errors=[]`;
after the run `bjobs` reports `No unfinished job found` (`cleanup.json`).
No Controller process is left running. The run root, artifact store, release
cache, identity DB and remote job/code trees are isolated under
`/ACPServer/acp2-runs/remediation/m4-03/`; the production run root was not
touched.

## execution_limits judgement (evidence vs shipped claim)

`summary.json#execution_limits` is machine-readable; this is the reasoning.

| limit | real evidence gathered here | shipped claim | decision |
|---|---|---|---|
| `walltime_admission_bound` | unchanged (M1-04) | `enforced` | keep |
| `walltime_process_termination` | **all present**: (a) three-way LSF check — approval 60 s → `#BSUB -W 00:01` → native `RUNLIMIT`/exit 140, receipt `verified=true`; (b) local process-group kill with 7 real PIDs gone; (c) PAUSED time counted (absolute deadline unchanged); (d) restart restores the same absolute deadline | `NOT_VERIFIED` | **evidence complete → `upgrade-to-enforced` prepared, not flipped** |
| `queue_deadline` | real `PEND` job (`badmin hclose`) with a 40 s queue bound → expiry → terminal + `bkill` + receipt `verified=true` | `NOT_VERIFIED` | **evidence complete → `upgrade-to-enforced` prepared, not flipped** |

Why not flipped in this commit: the two rows are *also* pinned as
`NOT_VERIFIED` by the parallel Diels–Alder lane's acceptance
(`scripts/acceptance/acp2_m404_da.py:3142-3143`) and by
`docs/reference/next-change/adr/ADR-008-limited-diels-alder-criteria.md`, both
of which are explicitly outside this task's scope ("不碰 ccp/analysis/research 与
science_criteria / ADR-008 面"). Flipping them here would break a live
acceptance owned by the other lane. The exact flip (one atomic change, to be
done by the integration owner once both lanes agree) is:

1. `src/acp_services/runtime/execution_limits.py` — `EXECUTION_LIMITS`
   `walltime_process_termination` and `queue_deadline` →
   `BudgetCapability.ENFORCED`, with `evidence` pointing at
   `scripts/acceptance/acp2_m403.py` + `tests/test_acp2_m403_lsf.py` and the
   receipts under this directory; the same two rows in `CLOCK_CONTRACT`.
2. `src/acp_services/runtime/deadline_wiring.py` — `DEADLINE_SURFACES`
   `local_process_group`, `remote_lsf`, `queue_clock` → `enforced`; update the
   module docstring.
3. `src/acp_services/runtime/enforcement_projection.py` — the two
   `UPGRADE_GATES` rows → `enforced`; module docstring table.
4. `tests/test_acp2_execution_limits.py` and
   `tests/test_acp2_m203_deadline_projection.py` — expectation flips.
5. `scripts/acceptance/acp2_m404_da.py` + `tests/test_acp2_m404_da.py` +
   `docs/reference/next-change/adr/ADR-008-...md` — the DA lane's pins (owner
   approval required).
6. `PYTHONPATH=src python3.11 scripts/acceptance/check_execution_limits_consistency.py --check`
   (currently PASS: 0 findings, 6 units, 6 execution limits).

## Verification commands actually run

```bash
PYTHONPATH=src python3.11 scripts/acceptance/acp2_m403.py --out reports/acp2_remediation_20261026/m4-03/   # 15/15 PASS
PYTHONPATH=src python3.11 -m pytest tests/test_acp2_m403_lsf.py tests/test_remote_phase1.py \
    tests/test_remote_phase2.py tests/test_acp2_execution_limits.py -q --run-slow                             # 101 passed
PYTHONPATH=src python3.11 scripts/acceptance/check_execution_limits_consistency.py --check                   # PASS (0 findings)
python3.11 -m ruff check <touched files> && python3.11 -m ruff format --check <touched files>                 # clean
```

## Environment findings (recorded, not hidden)

* **`CONFSEARCH_ORCA_PATH=/usr/bin/orca` in the environment profile is the GNOME
  screen reader, not ORCA the QC engine.** An ORCA job started with it dies with
  `以下参数无效: sp.inp` / `未能启动屏幕阅读器`. The acceptance exports the real
  ORCA 6.1.1 (`/home/xieningke/orca611/orca`) to the Controller and to remote
  jobs (`pre_cmds`); this is the only deviation from the profile's executable
  paths. The profile should be corrected by the environment owner.
* **Openlava 2.0 `mbatchd` crashes under load** (`general protection fault`,
  dmesg) — see `environment-fault.json`. It happened during one matrix attempt,
  which the product survived as follows (hardening added by this task):
  the deadline sweep moved to its own thread (a hung cluster can no longer
  starve local deadlines), every remote termination call is bounded, the
  deadline is claimed atomically *before* the process is stopped, and an
  unconfirmed receipt is upgraded later instead of blocking the transition.
  Root restarts were not permitted for this user; the queue state was backed up
  (`/ACPServer/acp2-runs/remediation/m4-03/lsf-backup/`) and reset, after which
  `mbatchd` returned and the authoritative run completed cleanly.
* **Localhost SSH** is used as the "remote" node (key prepared under
  `m4-03/ssh/`, public key appended to `~/.ssh/authorized_keys`). The remote
  path is the real one (SSH/SFTP + code release + `bsub`/`bjobs`/`bkill`), not a
  local shortcut.

## Platform changes that made the matrix possible (see the task report)

* remote submission now renders the approval wall bound into `bsub -W`
  (`admission_walltime_minutes` from the durable admission projection);
* remote attempts start their wall clock at the first `RUN` observation
  (`result["remote"]["started_at"]`), never at dispatch — queue waiting cannot
  consume the approved budget;
* a deadline is enforced by the platform (CAS → terminate → receipt) for local
  process groups and LSF jobs; native `RUNLIMIT` deaths are recorded as
  receipts by the runner;
* batched release verification/upload (one `sha256sum`/SFTP session per chunk
  instead of per file) — a 600-file release no longer costs ~600 SSH round
  trips per submission;
* the remote execution release no longer requires `acp.scheduler` at import
  time (`services_data_bootstrap` imports the structure-source store lazily),
  so a real node can import the workflow;
* approval budget comparison treats an omitted client budget as "not
  restated" (the approved budget remains the only authority), which is what
  makes an approval-bound submit reachable through the v1 field policy.
