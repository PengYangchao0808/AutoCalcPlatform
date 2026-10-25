# B-fix-5 — F2/F3 blocking remediation

Candidate: `0.3.0`, source revision `5e2898acc386335ee1cb11ac9170650bb23d501d`
(tree `e2b23771a47e7553738ef902cc4c1b62528fe508`). Release artifacts are in
`../B25/release-fix5/` (digests: `../B25/release-fix5/source.json`,
`release-digests.json` here). Historical `B25/release*/` artifacts were not
overwritten.

## Results

| Row | Result | Evidence |
|---|---|---|
| F2 clean-checkout guards | PASS — 100 passed | `clean-guards-fix5.txt` |
| Installed wheel → non-empty release manifest | PASS — 510 files, `release_id=1c79785a657c6614`, local identity matches | `installed-manifest.json` |
| LSF normal singlepoint (real `bsub`) | PASS — job `1765`, terminal `completed`, ORCA 6.1.1 `-1.116759307204 Ha` | `lsf/job-terminal.json`, `lsf/remote/facts.json`, `lsf/local-artifacts/execution_receipt.json` |
| Bare root wheel `acp run singlepoint` (no extras) | PASS — exit 0, `-1.116773191779 Ha` | `bare-root/singlepoint.log` |
| Package-install receipt candidate identity | PASS — `candidate_revision=6ec01ec…` from `release_manifest.source.revision`; Lab health matches | `package-install/summary.json` |
| Cross-entry release projection | PASS — HTTP/CLI/MCP equal release/plan/submission fields | `three-entry-compare.json` |
| Targeted tests | PASS — 263 passed | `pytest-fix5.txt` |
| ruff check/format | PASS | `ruff-check-fix5.txt`, `ruff-format-fix5.txt` |
| `validate_acp2_contracts.py` | PASS — VALID | `validator-fix5.txt` |

## LSF environment prerequisite

The real LSF run used an isolated `HOME` whose `cluster.pre_cmds` exported the
remote ORCA/OpenMPI runtime:

```
export LD_LIBRARY_PATH=/home/redacted-user/openmpi418/lib:/home/redacted-user/orca611:$LD_LIBRARY_PATH
export CONFSEARCH_ORCA_PATH=/home/redacted-user/orca611/orca
export CONFSEARCH_ORCA_LD_LIBRARY_PATH=/home/redacted-user/openmpi418/lib:/home/redacted-user/orca611
export CONFSEARCH_ORCA_MPI_PATH=/home/redacted-user/openmpi418/bin/orterun
```

That is operator environment configuration; no scheduler or receipt result was
faked. `bsub` executed, the remote worker ran ORCA, the terminal receipt was
pulled, and `sp.out` contains `Program Version 6.1.1` plus the final energy.

## Defects closed

- `sync._project_root()` resolves installed package roots (not
  `Path(__file__).parents[4]`); installed sync keys are
  `acp/...`/`acp_services/...`/`cccp/...`, identical to
  `local_release_identity`, so the freeze identity matches the remote manifest.
- Installed releases add the release root to `PYTHONPATH`; source releases keep
  exactly the historical `src` line.
- `acp.services_data_bootstrap` imports `acp.scheduler.remote.paths` lazily, so a
  bare root wheel runs a local singlepoint without `paramiko`.
- `acp2_package_install` derives `candidate_revision` from the release manifest
  (or `source.json`) and pins installed Lab health to it.
- HTTP `V1JobRecordModel`, `project_task_status` and controller clients now
  expose `release_id`/`release_digest`/`plan_digest`/`submission_id`; the facts
  come from the frozen envelope/receipt when a legacy row lacks them.

## Cleanup

Test Lab/Controllers were stopped; ports `53201`/`53202`/`53203` are free; no
`worker_bootstrap`/ORCA process remains. Production `acp.service` on `8765` and
`/var/lib/acp/runs` were not touched.
