# ACP2 Plan A — Science Baseline Record (A-00 / B00)

Recorded: 2026-10-22 (UTC run `a00-20261022T111022Z`)
Owner: A (science track). This file is the single baseline record consumed by tasks A-01…A-29.

## 1. Revisions

| Field | Value |
|---|---|
| Actual worktree HEAD at A-00 start | `096cdffe9a3817d8ee443d824830d87ec6e07bc2` (bare base, no business fixes) |
| Branch | `acp2/a-science` (worktree `<acp2-dev>/A`) |
| Frozen base revision (`ACP2_BASE_REV`) | `096cdffe9a3817d8ee443d824830d87ec6e07bc2` — overlay restore/apply base only; **not** the code candidate |
| B00 candidate id (`candidate-snapshot.json::candidate_id`) | `sha256:bb45a46c93d241d03ffae2952a78a2736a637a3f2c8d69b72d2bd0c4fafc3e12` |
| Code-candidate revision (declared in repair report, historical reference) | `ab492cc8be9e46125fa2d2fae69a3ecaac64a548` |
| **Delivery revision** (baseline commit created by A-00) | **`2e0ce9c202f5a65180aa97ca2787f82dc0054e6b`** |
| Commit subject | `chore(cccp): lock ACP2 science baseline and ownership boundary` |

`096cdff` is the Git starting point only. The executed code is the overlay-restored candidate; `2e0ce9c` is the commit that materializes it on `acp2/a-science`.

## 2. B00 overlay (authoritative source of truth)

| Field | Value |
|---|---|
| Overlay archive (concrete path) | `<repo>/reports/architecture_repair_20261022/candidate-overlay.tar.gz` |
| Overlay archive SHA-256 | `3fb835b5069f96642bf6bb889a804532bbb70b69cf796d21208a6e3364117734` (matches `SHA256SUMS`) |
| `tracked-changes.patch` SHA-256 | `4578405dc1b99d9a884c651a8ea064a6eed311128b9564966200b33e95aa9242` |
| Snapshot-declared `tracked_patch_sha256` | `4578405dc1b99d9a884c651a8ea064a6eed311128b9564966200b33e95aa9242` (**match**) |
| `candidate-snapshot.json` SHA-256 | `33861de7d5578a8cfa5b48bcde27e7bd2b25ac3427c4dacb47c4eca921ae872c` |
| Patch content | 28 tracked files (`git apply` to `096cdff`) |
| New files | 144 files (`new-files/` copied over the worktree) |
| Excluded path | `tests/baseline/refactor-evidence/e2e-task-root.txt` — pre-existing local test-run marker; **not** committed, **not** modified |

Materialization commands (executed in `<acp2-dev>/A`):

```bash
tar -xzf <repo>/reports/architecture_repair_20261022/candidate-overlay.tar.gz -C /tmp/opencode/a00-overlay-a00-20261022T111022Z
git apply /tmp/opencode/a00-overlay-a00-20261022T111022Z/tracked-changes.patch
cp -a /tmp/opencode/a00-overlay-a00-20261022T111022Z/new-files/. .
```

## 3. Snapshot verification

Every path in `candidate-snapshot.json::files` was re-hashed from the materialized worktree:

| Metric | Result |
|---|---|
| Files verified | **1906 / 1906** |
| Drift | **0** |
| Missing | 0 |
| Result | **PASS** |

Raw machine record: `$ACP2_EVIDENCE/A/A-00/a00-20261022T111022Z/snapshot-verify.json`.

## 4. Interpreter and import resolution

| Field | Value |
|---|---|
| Role interpreter (`$ACP2_A_PYTHON`) | `<acp2-dev>/envs/a-python/bin/python` (CPython 3.11.13) |
| Editable install | `"$ACP2_A_PYTHON" -m pip install -e "$ACP2_A_SOURCE" --no-deps --no-build-isolation` (exit 0) |
| `cccp` import | `<acp2-dev>/A/src/cccp/__init__.py` |
| `acp` import | `<acp2-dev>/A/src/acp/__init__.py` |
| `acp_services` import | `<acp2-dev>/A/src/acp_services/__init__.py` |
| All imports in source tree | **YES** (all three resolve under `<acp2-dev>/A/src/`) |

Build note: the venv has no `wheel` distribution, so the first editable build failed at
`error: invalid command 'bdist_wheel'`. The build was re-run with the already-present local
build environment on `PYTHONPATH`
(`/tmp/acp-repair-build-env/lib/python3.11/site-packages`, wheel 0.48.0); no network install and
no new runtime package was added.

## 5. Evidence root

`$ACP2_EVIDENCE` = `<server-root>/acp2-evidence/a-science-20261022`

A-00 evidence: `<server-root>/acp2-evidence/a-science-20261022/A/A-00/a00-20261022T111022Z/`
(`result.json`, `argv.json`, `stdout.log`, `stderr.log`, `pre-state.txt`,
`snapshot-verify.json`, `overlay-digests.txt`, `failure-qa.txt`).

## 6. Dirty-path inventory before materialization

Command: `cd "$ACP2_A_SOURCE" && git status --short && git diff --stat`
(at HEAD `096cdffe9a3817d8ee443d824830d87ec6e07bc2`, branch `acp2/a-science`):

```text
<no output>   # git status --short returned 0 lines
<no output>   # git diff --stat returned no changes
```

Inventory: **0 dirty paths** (clean worktree, no staged/unstaged/untracked entries). The A worktree
was created fresh from the bare base; no other agent had written to it.

## 7. Cross-references

- Plan: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md`
- Index: `docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md`
- Repair report: `<repo>/reports/architecture_repair_20261022/REPORT.md`
- Deliverable path map / ownership boundary: `path-map.md` (same directory)
