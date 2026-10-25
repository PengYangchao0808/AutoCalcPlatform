# ACP 2.0 Plan B — H7 release candidate changelog

**Candidate:** `acp2.plan-b.h7.release-fix5` · version `0.3.0`
**Release source:** `5e2898acc386335ee1cb11ac9170650bb23d501d` (tree `e2b23771a47e7553738ef902cc4c1b62528fe508`)
**Landed code-fix HEAD at index time:** `c134109c696e9bcb941e176bf681d45a569370b8` (tree `f0f940498b56bdf9377e49efb1b9a29e0e3a3d4a`)
**Release directory:** `reports/acp2_implementation/bb45a46/B/B25/release-fix5/`
**Manifest:** `release-manifest.json` (sha256 `9abdf987749573429a6328af8b6243049e11955e426fbd61257860a4c47bb2ec`)

**5e2898a..c134109c696e9bcb941e176bf681d45a569370b8 -- src delta:** 10 files, +823/-161 (B-fix-4/B-fix-5 runtime/cgroup/attempt-recovery changes plus the B-fix-7 `batch/options.py` overlay); release-fix5 artifacts remain built from `5e2898a` with unchanged digests.

This is a reviewable release candidate, **not a production deployment**. `acp.service`
was not restarted and `/var/lib/acp/runs` was not touched.

## Artifacts and digests

| Artifact | SHA-256 |
|---|---|
| `cccp-0.3.0-py3-none-any.whl` | `b505413147e3c3a07c9b405631e3ee244d0dc9f7a8f699a45ec112ec692c559c` |
| `cccp-0.3.0.tar.gz` | `31631e2f5c131ccba6e08ffac23035feb7670d226b0349cb6816c23959aea425` |
| `acp_worker-0.3.0-py3-none-any.whl` | `edd849a75b28f58bba54d8613ec14e1ecae46a325d51d3874967fc683c6ec949` |
| `acp-worker-0.3.0.tar.gz` | `223fd6aec2760c35d5346f56edf5b17a3dc0376481e5ea4b48348d36493358b5` |
| `acp_web-0.3.0-py3-none-any.whl` | `d055e869de602cf1a257d4542243849806b3e3ebe805b78c70a412e8e5a2b210` |
| `acp-web-0.3.0.tar.gz` | `3186b347377599c1e71514bdccc52d1ebf2811eb73b6d802d18b2141a8c927a7` |
| `root-build/acp-0.3.0-py3-none-any.whl` | `64548f8f25e6251532b8b551067f6a0c432a2668bfc20edd310ed2fcb7e4c56a` |
| `root-build/acp-0.3.0.tar.gz` | `f449f77d4c89476847a610ef3577a13ce6c1efb30dc77cdc8a5a522411b28987` |

All eight digests were recomputed from disk by the H7 aggregation; see
`integration/release-candidate.json`. Historical `release/`, `root-build/`,
`release-b25a-original/`, `root-build-b25a-original/` and `release-fix4/` artifacts were
not overwritten.

## Changes since the frozen B25a/B25b candidate

### B25b — role package correction

- Rebuilt the 0.3.0 four-role release from clean source; original B25a artifacts are
  retained under `release-b25a-original/` and `root-build-b25a-original/`.
- Fixed the B25a `acp_web` startup defect: `create_app()` passed
  `OriginPolicy(public_origin=...)` before `boundary.py` accepted the field. The
  installed Lab now starts and serves `/api/health`.
- Added the `package-install` acceptance scenario and evidence-identity checks.

### B26a — external reproduction defects (both fixed in B-fix-4)

- **B26A-D1 (high, scheduler execution).** `local_release_identity()` required
  `<root>/src/{acp,acp_services,cccp}`. An installed `acp-web`/`acp-worker` wheel places
  packages under `site-packages`, so Lab/Workbench submissions and `acp task execute`
  failed before dispatch while direct `acp run` worked.
  **Fix:** `src/acp/calculations/attempt_freeze.py::_release_sources` now derives the
  execution tree from installed package roots (`importlib.util.find_spec`) with stable
  `acp/...`, `acp_services/...`, `cccp/...` keys; `build_local_release` copies from the
  resolved source map. `tests/test_acp_attempt_freeze.py` covers both layouts, and the
  package-install scenario now asserts one real scheduler task.
  **Verified:** installed Lab HTTP submit `201` → `completed`;
  `acp task execute --follow` exit 0 → `completed`; release identity equals installed
  identity.
- **B26A-D2 (medium, revocation SSE).** After key/account revocation the next read was
  `401`, but an already-open `/api/events/stream` closed via `done` before emitting
  `permission_changed`. Root cause was fixture state, not installed code: reused PAUSED
  fixture jobs had been frozen to `FAILED` at Controller restart.
  **Fix:** the B15 harness resets reused fixture jobs to their declared `PAUSED` state
  through the `JobStore.transition` CAS. The twelve strict assertions were not weakened.
  **Verified:** both `revocation.key.read_and_stream` and
  `revocation.account.read_and_stream` emitted `permission_changed`; reads after revoke
  returned `401`.

### B-fix-4 — release rebuild

- Built from a detached clean commit `abec2bc` = B25b `a84c426` + only the D1 fix.
- Regenerated all role wheels/sdists and the root build; `release-fix4/` is retained as
  the previous frozen candidate.
- Re-ran `package-install` (PASS incl. scheduler task), B15 authorization (PASS) and the
  affected regression tests.

### B-fix-5 — F2/F3 blocking remediation (final candidate)

- Rebuilt every role artifact and the root build from a clean detached worktree at
  `5e2898a`; `release-fix5/` is the frozen candidate consumed by this index.
- **F2 — installed-wheel remote sync.** `sync._project_root()` now resolves installed
  package roots instead of `Path(__file__).parents[4]`, so the synced keys are
  `acp/...`/`acp_services/...`/`cccp/...` and the remote release manifest is non-empty.
  An installed release adds its own root to `PYTHONPATH`; a source release keeps exactly
  the historical `src` line. Result: 510 synced files, `manifest_files == sync_files == 510`,
  `release_id=1c79785a657c6614`, and `local_release_identity` matches the remote manifest.
- **F3 — bare-root run and projection.** `acp.services_data_bootstrap` imports
  `acp.scheduler.remote.paths` lazily, so a root wheel without the `remote` extra runs
  `acp run singlepoint` (exit 0, −1.116773191779 Ha) without `paramiko`.
  HTTP `V1JobRecordModel`, `project_task_status` and controller clients expose
  `release_id`/`release_digest`/`plan_digest`/`submission_id` from the frozen
  envelope/receipt; HTTP, CLI and MCP report equal values for job
  `20261024_015940_001_fix5-lsf-sp4`.
- **Candidate identity.** The package-install harness derives `candidate_revision` from
  `release_manifest.source.revision` (or `source.json`) and pins installed Lab health to
  it: `5e2898acc386335ee1cb11ac9170650bb23d501d`.
- **Real LSF normal PASS.** `bsub` job `1765` on `node-157` ran ORCA 6.1.1, terminal
  state `completed`, exit 0, −1.116759307204 Ha, `release_id=1c79785a657c6614`,
  `release_digest=c5ccfcd1…`, `submission_id=sub_0fea781820a81666`. The remote ORCA
  runtime was exported through `cluster.pre_cmds` in an isolated HOME — operator
  environment configuration, not a code fake.
- **Guards.** 100 clean-checkout guards passed at `5e2898a`; 263 targeted tests passed;
  ruff check/format and `validate_acp2_contracts.py` PASS.

## Known gaps (not closed by this changelog)

- H4 and H6 remain OPEN; A-29 and C35 signatures are absent, so H7 is OPEN. H3 also stays
  OPEN on C DSH/Workbench and the A scientific audit even though its LSF row now PASSes.
- C33/C34 install evidence, C DSH/model scope and C's static-asset manifest are absent.
- The B26a reproduction is same-agent self-reproduction, not an independent operator.
- C DSH/browser handshake and running-task disconnect recovery remain `NOT_VERIFIED`.
- ADR-005 licensing/open-source release remains a user decision; no license changed.
