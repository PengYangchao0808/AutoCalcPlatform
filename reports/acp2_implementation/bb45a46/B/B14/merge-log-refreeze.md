# B14a re-freeze — P1 integration candidate merge log

Candidate: `77ac1de1af6a5db6b57f4f904a859305ff73042c`
Tree: `9809bea68e7132ae9ab5c0285b2c41373966f443`
Branch: `acp2/integration-p1` (worktree `/ACPServer/acp2-development/integration`)
Created: 2026-10-23 (Asia/Shanghai)

This re-freeze supersedes the `fbac5ab` candidate after the B-owned base-suite fixes
(B-fix-34/35/36) and the advanced A/C heads. The previous merge procedure and
ownership map were reused; no merge was rebuilt from scratch.

## 1. Base and changesets

| Element | SHA | Note |
|---|---|---|
| Previous candidate | `fbac5abb50ba7f86603b786fc98d857df52f5f2a` | 9 recorded failures (6 B-owned, now fixed) |
| B changeset (basis) | `12bf105f66bea49ea6e9bf5601b5c757131674a5` | B00-B13 + B-fix-34/35/36; `feat/frontend-plan1-closure` |
| A changeset | `1cfb1975d29a34fd76a986a6f293813579acd2aa` | `acp2/a-science` (committed head only) |
| C changeset | `052962eac7782dc2e00253dd5df120badc3bff52` | `acp2/c-experience` (clean worktree) |
| Historical delivery / debug base | `096cdffe9a3817d8ee443d824830d87ec6e07bc2` | common merge base of A/B/C |
| Final debug candidate (source overlay) | `sha256:bb45a46c93d241d03ffae2952a78a2736a637a3f2c8d69b72d2bd0c4fafc3e12` | B00 `final-debug-patch/manifest.json` |

Merge bases actually used by git:

| Merge | Merge base |
|---|---|
| integration HEAD + B `12bf105` | `2a57d6b6d91427093c6a10e710e5a52446ed801e` |
| integration HEAD + A `1cfb197` | `243d2b7c1f2d115b1fa67d8af35e4020fe7be2d8` |
| integration HEAD + C `052962e` | `e03a9dd9f6897ca0a8d43e69a10b22a20097f05d` |

A's worktree was dirty during the re-freeze (6 tracked modifications + 3 untracked
paths). Only the committed A head was merged; the dirty state is recorded as
`a_uncommitted_worktree_not_included` and is not part of the candidate.

## 2. Commit sequence (all in the integration worktree)

1. `5b365e14d5f8fea52083446c2abe8ee6e591bdac` — **merge B `12bf105` by domain ownership**
   (parents `fbac5ab` + `12bf105`; 68 staged paths).
   * Applies B-fix-34 (`_CompositePrepareExecutor.prefetch` forwarding), B-fix-35
     (`.omo` untracking, `services_mcp_bootstrap` station registration, bounded
     SQLite busy timeout) and B-fix-36's `paths.py` gate correction.
   * Refreshes the contract manifest and adds C's `scientific-evidence` request to
     `runtime/cross-track-requests.v1.json`.
   * Removes `reports/acp2_implementation/bb45a46/integration/candidate.json` from
     the candidate tree: the authoritative candidate.json is written after the
     freeze in the main checkout, so a tracked file cannot describe its own final
     SHA, and B's copy named the superseded `fbac5ab`.
   * `git rm -r --cached .omo` removes all 27 previously tracked `.omo` paths from
     the index (files remain on disk; `.omo/` is gitignored), matching B-fix-36.
2. `bd94a7b54c278040af284ee76d7657fcaedb71fe` — **merge A `1cfb197` by domain ownership**
   (parents `5b365e1` + `1cfb197`; 52 paths).
   * All 52 are A-owned: `src/cccp/plugin/**`, science handoff H4/H5 and ADR-007,
     `scripts/acceptance/acp2_science.py`, A plugin tests/fixtures and
     `tests/test_cccp_plugin_pilot.py`.
3. `77ac1de1af6a5db6b57f4f904a859305ff73042c` — **merge C `052962e` by domain ownership**
   (parents `bd94a7b` + `052962e`; 21 paths).
   * All 21 are C-owned: `frontend/**` (including `scientific-evidence.js`),
     experience contracts, C acceptance probes and
     `tests/test_acp2_experience_harness.py`.

Each merge was clean: the integration branch matched each track's merge base on
that track's owned paths, so no file needed an unreviewed shared-file edit.

Candidate delta over the B base `2a57d6b` (all three merges): 565 files,
+158934/-1108.

## 3. Overlay preservation check at the candidate

B00 overlay manifest total: 1906 files.

* 1866 files match the overlay byte-for-byte.
* 0 files are missing.
* 40 files deliberately differ because later reviewed changesets evolved them:
  * B00-B13 / B-fix runtime and tests (17 files, including
    `src/acp/services_data_bootstrap.py`, `src/acp_services/runtime/admission.py`,
    `tests/test_f4_scope_audit.py`),
  * C experience fixes (12 files under `frontend/**`),
  * A science plugin changeset (7 files under `src/cccp/plugin/**`,
    `src/cccp/calculation/__init__.py`, `tests/test_cccp_plugin_pilot.py`),
  * shared API/CLI/manager/contract files evolved by B00-B13 and C (4 files:
    `scripts/acceptance/real_environment.py`, `src/acp/api/routes.py`,
    `src/acp/api/v1_schemas.py`, `tests/test_services_runtime_usecases.py`).
* No file from the B00 manifest was lost.

`.omo/**` in the final candidate: `git ls-files -- .omo/` returns 0 paths.

## 4. Contract validity and digests

`/ACPServer/acp2-development/venvs/b/bin/python scripts/acceptance/validate_acp2_contracts.py`
from the candidate: **VALID**.

| Digest | Value |
|---|---|
| `contract_manifest_digest` | `sha256:cf9de34b84e5452ebd40a5ecd943a13eca29f0f5b0730caae849cccf80d40eb6` |
| `contract_digest` | `5e712587ee6d4a8d3c13800632605817ffc23359a6ac1f3245b7c45c50a81f49` |
| `contract_version` | `1.1.0` |
| `env_digest` | `sha256:67ab9c869e95c3c2a6180e96e7acefbaff9882a835bfb7b4691dfcf14d8e5553` |

The contract manifest changed because B-fix refreshed it; the new digest is
consistent with the delivered contract set and passes the validator.

## 5. Domain ownership decisions

Full record: `ownership-decisions-refreeze.json`.

* B: accepted `12bf105` (runtime fixes, contract refresh, Bfix3 evidence).
* A: accepted `1cfb197` (52 A-owned paths); A's dirty worktree excluded.
* C: accepted `052962e` (21 C-owned paths); no B edit to `frontend/**`.
* `.omo/**`: untracked in the candidate (B-fix-36 carry-over).
* Stale B-track `integration/candidate.json`: removed from the candidate tree.
* Sharedfile requests: no new four-version bump (B25a owns it), no config-default
  change, no shared dependency change, no new services-ledger row. Identity
  forwarders remain identity-equal to their `acp_services` implementations.

## 6. Source heads after the freeze

A advanced to `f981273fff99e8d198e157d02042a2688345e00c` and C advanced to
`39d4aeb20451c984e20d27e12270a15070adce12` while the re-freeze ran (the A session
was still working). The candidate intentionally keeps the recorded changesets
`1cfb197` and `052962e`; neither later head was merged and no evidence was assembled
from them.

## 7. Cleanliness

`git status --porcelain` is empty in the integration worktree at the candidate.
No code was edited after the candidate commit while guards and the base suite ran.
The base suite writes its temp root into the tracked audit trace
`tests/baseline/refactor-evidence/e2e-task-root.txt`; that file was restored to
`HEAD` after the suite (not committed), so the candidate tree and working tree stay
identical to `77ac1de`.
