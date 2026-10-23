# H7 receipt — ACP 2.0 Plan B final index (B26b)

**Status: OPEN.** H7 is not signed. The release candidate is frozen and reviewable, but
A-29 and C35 are absent, H4/H6 remain open, and the external reproduction is not
independent.

| Field | Value |
|---|---|
| Candidate label | `bb45a46` |
| Release source candidate | `abec2bc4f1d302827248d36171973df5bc1bc4dd` (tree `c9844959c4c39814234a0a46c71d6f70a42456a4`) |
| Landed HEAD | `3871ccbdc02c7dae69fb2103bc4921ae13ca58ee` (tree `3263a8557495d9be08bb4159552fe7fcd7f1ef7a`) |
| Client revision | `39d4aeb20451c984e20d27e12270a15070adce12` (C HEAD; C24a/C25/C35 absent) |
| A revision | `1b580a3dcda1351a0869353e6a505acde10dcfe0` (A HEAD; A-20/A-29 absent) |
| Harness revision | `4dce23bdfaf4bce044910cff290f7f9a61fb9071c0e860e7691ed3c258b4cdb5` |
| Evidence revision | `3c16877f422933b47243e255f8b3a45efea05bfe1dd176f5f643a6764a7b8aa3` |
| Contract | `1.3.0`; manifest `sha256:5ddf8c4a…`, acceptance schema `sha256:81f653be…` |
| Version | `0.3.0` in all four locations |
| Production deployed | **false** |
| `acp.service` restarted | **false** |

## H0–H7 rows

| Handoff | Result | Gate | Reason |
|---|---|---|---|
| H0 baseline/ownership/manifest | PASS | CLOSED | historical baseline and manifest validated |
| H1 minimal contracts | NOT_VERIFIED | OPEN | A science fixture and C-authored sample absent |
| H2 real MCP/DSH | NOT_VERIFIED | OPEN | C DSH handshake absent; LSF absent |
| H3 Local/LSF loop | NOT_VERIFIED | OPEN | LSF, C DSH/Workbench and A audit absent |
| H4 limited DA | NOT_VERIFIED | OPEN | B20a-exec/search, A-20 and C25 absent |
| H5 plugin extension | PASS | CLOSED | B21 install + B22 upgrade/rollback, reused PASS mapped |
| H6 reliability | NOT_VERIFIED | OPEN | LSF/DSH/A/C/selectivity/real-DA rows absent |
| H7 release/install | NOT_VERIFIED | OPEN | A-29/C35/independent operator absent |

No FAIL was rewritten as NOT_VERIFIED. B signed only B-owned rows.

## C35 disposition

**`c35_absent`.** The C plan (`docs/reference/next-change/ACP_2.0_Parallel_Plan_C_RPH_Workbench_20261020.md`)
lists C32–C34 as C35 prerequisites; it does not make C35 wait for B26. C35's evidence
root is absent on this host, so H7 stays open and B does not sign C experience scope.
`C35_INPUT_CYCLE` is **not** recorded.

## Release candidate

- Directory: `reports/acp2_implementation/bb45a46/B/B25/release-fix4/`
- Manifest: `release-manifest.json` (sha256 `1f47a5e0…`)
- All eight artifact digests recomputed and verified (`release-candidate.json`).
- Changelog records B26A-D1, B26A-D2 and the B-fix-4 fix; no defect is hidden.

## Validation and cleanup

- `cd /ACPServer/AutoCalcPlatform && /usr/bin/python3.11 scripts/acceptance/acp2_runtime.py --check-only reports/acp2_implementation/bb45a46/integration/summary.json`
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
- `.omo/evidence/task-33-acp2-parallel-plan-b-runtime.md`
