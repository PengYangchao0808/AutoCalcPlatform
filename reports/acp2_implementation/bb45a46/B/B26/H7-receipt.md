# H7 receipt — ACP 2.0 Plan B final index (B-fix-5 refresh)

**Status: OPEN.** H7 is not signed. Release-fix5 is frozen and reviewable, with a real
LSF normal PASS (bsub job `1765`) and an installed-release sync PASS, but A-29 and C35
are absent, H4/H6 remain open, and the external reproduction is not independent.

| Field | Value |
|---|---|
| Candidate label | `bb45a46` |
| Release source candidate | `6ec01ec96fa81b89bec8ff321c38e3db2dc154d2` (tree `e2b23771a47e7553738ef902cc4c1b62528fe508`) |
| Release directory | `B/B25/release-fix5` (manifest sha256 `380b4915…`) |
| Landed code-fix HEAD | `493790709830eac4ce347b5fb81414050f7a1e21` (tree `f0f940498b56bdf9377e49efb1b9a29e0e3a3d4a`) |
| Landed HEAD delta | `6ec01ec..4937907 -- src`: 10 files, +823/-161 (B-fix-4/B-fix-5 runtime/cgroup/attempt recovery plus the B-fix-7 `batch/options.py` overlay). Release-fix5 remains built from `6ec01ec`; digests unchanged. |
| Client revision | `39d4aeb20451c984e20d27e12270a15070adce12` (C HEAD; C24a/C25/C35 absent) |
| A revision | `1b580a3dcda1351a0869353e6a505acde10dcfe0` (A HEAD; A-20/A-29 absent) |
| Harness revision | `dad07c7660ebda3d99c2d728f4da38b65d774e8729f9bdc66f5361c34f7e623b` |
| Evidence revision | `0790a6e717add4cf31ae9a997a03cbc87408b8772e72e24aaa78894b160b321d` |
| Installed release identity | `release_id=1c79785a657c6614`, 510 files, `sync_files == manifest_files` |
| LSF normal singlepoint | PASS — job `1765` terminal `completed`, `sub_0fea781820a81666`, −1.116759307204 Ha |
| Contract | `1.3.0`; manifest `sha256:5ddf8c4a…`, acceptance schema `sha256:81f653be…` |
| Version | `0.3.0` in all four locations |
| Production deployed | **false** |
| `acp.service` restarted | **false** |

## H0–H7 rows

| Handoff | Result | Gate | Reason |
|---|---|---|---|
| H0 baseline/ownership/manifest | PASS | CLOSED | historical baseline and manifest validated |
| H1 minimal contracts | NOT_VERIFIED | OPEN | A science fixture and C-authored sample absent |
| H2 real MCP/DSH | NOT_VERIFIED | OPEN | C DSH handshake absent |
| H3 Local/LSF loop | NOT_VERIFIED | OPEN | Local rows PASS and LSF normal PASS (job 1765); C DSH/Workbench and A audit absent |
| H4 limited DA | NOT_VERIFIED | OPEN | B20a-exec/search, A-20 and C25 absent |
| H5 plugin extension | PASS | CLOSED | B21 install + B22 upgrade/rollback, reused PASS mapped |
| H6 reliability | NOT_VERIFIED | OPEN | DSH/A/C/selectivity/real-DA rows absent |
| H7 release/install | NOT_VERIFIED | OPEN | A-29/C35/independent operator, C33/C34/DSH absent |

No FAIL was rewritten as NOT_VERIFIED. B signed only B-owned rows. The LSF gap previously
listed under H3/H6/H7 is closed by B-fix-5; the rows stay `NOT_VERIFIED` for their other
unverified dimensions.


## C35 disposition

**`c35_absent`.** The C plan (`docs/reference/next-change/ACP_2.0_Parallel_Plan_C_RPH_Workbench_20261020.md`)
lists C32–C34 as C35 prerequisites; it does not make C35 wait for B26. C35's evidence
root is absent on this host, so H7 stays open and B does not sign C experience scope.
`C35_INPUT_CYCLE` is **not** recorded.

## Release candidate

- Directory: `reports/acp2_implementation/bb45a46/B/B25/release-fix5/`
- Manifest: `release-manifest.json` (sha256 `380b4915…`); source `6ec01ec`, base `abec2bc`.
- All eight artifact digests recomputed and verified (`release-candidate.json`).
- Installed validation (`B/Bfix5/summary.json`): installed release manifest PASS (510 files,
  `release_id=1c79785a657c6614`), real LSF normal singlepoint PASS (bsub job `1765`,
  `submission_id=sub_0fea781820a81666`, −1.116759307204 Ha), bare-root `acp run singlepoint`
  PASS (exit 0, −1.116773191779 Ha), package-install candidate identity PASS
  (`candidate_revision=6ec01ec…`), cross-entry release projection PASS (HTTP/CLI/MCP equal).
- Changelog records B26A-D1, B26A-D2, the B-fix-4 release rebuild and the B-fix-5 F2/F3
  remediation; no defect is hidden.

## Validation and cleanup

- `cd repo-root && /usr/bin/python3.11 scripts/acceptance/acp2_runtime.py --check-only reports/acp2_implementation/bb45a46/integration/summary.json`
  → `VALID`, exit 0 (`B26/check-only.txt` plus `check-only.pwd.txt`, `check-only.command.txt`,
  `check-only.stdout.txt`, `check-only.stderr.txt`, `check-only.exit`).
- No active queued/running/cancelling/waiting_review test job remains in a live run root.
  One stale `RUNNING` row with a dead PID was reconciled to `CANCELLED` after confirming
  `/proc/<pid>` was absent; frozen B24 migration fixture DBs and B15 PAUSED fixture rows
  are retained and labelled, not counted as active compute.
- No test process and no listener on ports `1876x`/`1878x`. Production PID `336067`
  (`acp run serve --port 8765`) was observed and left untouched.

## Evidence

- `integration/summary.json`, `integration/receipts/h0.json` … `h7.json`
- `integration/h7-index.json`, `integration/release-candidate.json`,
  `integration/changelog.md`, `integration/upgrade-rollback.md`,
  `integration/cleanup.json`
- `B/B26/open-rows.json`, `B/B26/h7-receipt.json`
- `B/Bfix5/summary.json`, `B/Bfix5/installed-manifest.json`, `B/Bfix5/lsf/`,
  `B/Bfix5/bare-root/`, `B/Bfix5/package-install/`, `B/Bfix5/three-entry-compare.json`
- `.omo/evidence/task-33-acp2-parallel-plan-b-runtime.md`
