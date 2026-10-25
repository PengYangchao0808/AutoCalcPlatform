# B14a re-freeze — P1 integration candidate merge log

Candidate: `77ac1de1af6a5db6b57f4f904a859305ff73042c`
Tree: `9809bea68e7132ae9ab5c0285b2c41373966f443`
Branch: `acp2/integration-p1` (worktree `/opt/acp2-dev/integration`)
Created: 2026-10-23 (Asia/Shanghai)

This re-freeze supersedes the `fbac5ab` candidate after the B-owned base-suite fixes
(B-fix-34/35/36) and the advanced A/C heads. The previous merge procedure and
ownership map were reused; no merge was rebuilt from scratch.

## 1. Base and changesets

| Element | SHA | Note |
|---|---|---|
| Previous candidate | `fbac5abb50ba7f86603b786fc98d857df52f5f2a` | 9 recorded failures (6 B-owned, now fixed) |
| B changeset (basis) | `308ef76e014e43a061d0cbffe219b2451de8feba` | B00-B13 + B-fix-34/35/36; `feat/frontend-plan1-closure` |
| A changeset | `aa5e19399fd44a7cec0ae4e7632d9a4683151815` | `acp2/a-science` (committed head only) |
| C changeset | `49ddfad860e3ba4bdd8a23e3b1d7e20919cf50bd` | `acp2/c-experience` (clean worktree) |
| Historical delivery / debug base | `3670790363c7a1959ad1dbe4b4297adec518952a` | common merge base of A/B/C |
| Final debug candidate (source overlay) | `sha256:bb45a46c93d241d03ffae2952a78a2736a637a3f2c8d69b72d2bd0c4fafc3e12` | B00 `final-debug-patch/manifest.json` |

Merge bases actually used by git:

| Merge | Merge base |
|---|---|
| integration HEAD + B `308ef76` | `a72e8db08416c882d52e1cfefbdf8b1f1b3d74d4` |
| integration HEAD + A `aa5e193` | `9977a106445177708e929557f9460e86914787ad` |
| integration HEAD + C `49ddfad` | `35614fd83f616005a0546af35631c477151e7dde` |

A's worktree was dirty during the re-freeze (6 tracked modifications + 3 untracked
paths). Only the committed A head was merged; the dirty state is recorded as
`a_uncommitted_worktree_not_included` and is not part of the candidate.

## 2. Commit sequence (all in the integration worktree)

1. `5b365e14d5f8fea52083446c2abe8ee6e591bdac` — **merge B `308ef76` by domain ownership**
   (parents `fbac5ab` + `308ef76`; 68 staged paths).
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
2. `bd94a7b54c278040af284ee76d7657fcaedb71fe` — **merge A `aa5e193` by domain ownership**
   (parents `5b365e1` + `aa5e193`; 52 paths).
   * All 52 are A-owned: `src/cccp/plugin/**`, science handoff H4/H5 and ADR-007,
     `scripts/acceptance/acp2_science.py`, A plugin tests/fixtures and
     `tests/test_cccp_plugin_pilot.py`.
3. `77ac1de1af6a5db6b57f4f904a859305ff73042c` — **merge C `49ddfad` by domain ownership**
   (parents `bd94a7b` + `49ddfad`; 21 paths).
   * All 21 are C-owned: `frontend/**` (including `scientific-evidence.js`),
     experience contracts, C acceptance probes and
     `tests/test_acp2_experience_harness.py`.

Each merge was clean: the integration branch matched each track's merge base on
that track's owned paths, so no file needed an unreviewed shared-file edit.

Candidate delta over the B base `a72e8db` (all three merges): 565 files,
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

`/opt/acp2-dev/venvs/b/bin/python scripts/acceptance/validate_acp2_contracts.py`
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

* B: accepted `308ef76` (runtime fixes, contract refresh, Bfix3 evidence).
* A: accepted `aa5e193` (52 A-owned paths); A's dirty worktree excluded.
* C: accepted `49ddfad` (21 C-owned paths); no B edit to `frontend/**`.
* `.omo/**`: untracked in the candidate (B-fix-36 carry-over).
* Stale B-track `integration/candidate.json`: removed from the candidate tree.
* Sharedfile requests: no new four-version bump (B25a owns it), no config-default
  change, no shared dependency change, no new services-ledger row. Identity
  forwarders remain identity-equal to their `acp_services` implementations.

## 6. Source heads after the freeze

A advanced to `8f4d9b98a584575cf74c0ff1ba7eb693468f288b` and C advanced to
`6905477c51c34fa1f8d53421553805ecbf88d897` while the re-freeze ran (the A session
was still working). The candidate intentionally keeps the recorded changesets
`aa5e193` and `49ddfad`; neither later head was merged and no evidence was assembled
from them.

## 7. Cleanliness

`git status --porcelain` is empty in the integration worktree at the candidate.
No code was edited after the candidate commit while guards and the base suite ran.
The base suite writes its temp root into the tracked audit trace
`tests/baseline/refactor-evidence/e2e-task-root.txt`; that file was restored to
`HEAD` after the suite (not committed), so the candidate tree and working tree stay
identical to `77ac1de`.
