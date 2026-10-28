# B-fix-4 (todo 37) — installed wheels execute scheduler tasks; installed-wheel revocation reset

Candidate: `0.3.0` (pre-release; no public publication). Machine-readable summary:
`summary.json`; release provenance: `../B25/release-fix4/source.json` and
`../B25/release-fix4/release-manifest.json`.

## Source and artifacts

- Build source revision `abec2bc4f1d302827248d36171973df5bc1bc4dd`, tree
  `c9844959c4c39814234a0a46c71d6f70a42456a4`.
- Base candidate: B25b `a84c4266bfc4738c8efc2988b7c5c37079a8a324`; the fix4
  build adds only the `attempt_freeze` installed-package fallback and its test.
  The B-fix-4 branch commit carries the same change on the current branch tip;
  the branch tips differ in unrelated files, so the wheels are the B25b-derived
  candidate that B26b consumes (not a build of the current branch tip).
- New artifacts in `../B25/release-fix4/` (historical `B25/release/`,
  `B25/root-build/`, `release-b25a-original/` untouched). All artifact digests
  are in `../B25/release-fix4/source.json` and the release manifest.

## Results (no inflation)

| Item | Result | Evidence |
|---|---|---|
| B26A-D1 installed release identity | PASS | `b26a-rerun/installed-identity-proof.txt`, `b26a-rerun/installed-identity.json` |
| Package-install scheduler assertion | PASS | `package-install/scheduler-task/scheduler-task.json` |
| B26a authorization revocation row | PASS | `b15/multitenant-multitenant_authz-20261023T144412Z-1699050-11be1713.json` |
| B26a installed Lab/Workbench submission | PASS | `b26a-rerun/http-row.json`, `b26a-rerun/cli-row.json` |
| Tests / ruff / validator | PASS | `pytest-fix4.txt`, `ruff-check-fix4.txt`, `ruff-format-fix4.txt`, `validator-fix4.txt` |

### D1 — installed release identity

`local_release_identity` still hashes `<root>/src/{acp,acp_services,cccp}` for
a source checkout. When `<root>/src` is absent it resolves the installed
package roots with `importlib.util.find_spec` and hashes them under stable
keys `acp/...`, `acp_services/...`, `cccp/...`; no absolute path enters the
digest. A missing package is a named `ReleaseIdentityError`.

Installed proof (`b26a-rerun/installed-identity-proof.txt`):

```
acp.__file__         = /tmp/opencode/b26a-fix4/venvs/lab/lib64/python3.11/site-packages/acp/__init__.py
acp_services.__file__= /tmp/opencode/b26a-fix4/venvs/lab/lib64/python3.11/site-packages/acp_services/__init__.py
cccp.__file__        = /tmp/opencode/b26a-fix4/venvs/lab/lib64/python3.11/site-packages/cccp/__init__.py
release_id           = 2c7ffd56045b5bd5
release_digest       = b59ec6b6b502ed70104baacb17b41873769813def34224840017792e438900b3
prefixes             = ['acp', 'acp_services', 'cccp']
keys_absolute        = False
contains_repo_path   = False
```

### Package-install scheduler assertion

`scripts/acceptance/acp2_package_install.py` now starts the installed Lab with
an isolated run root and submits one real H2/STO-3G singlepoint through
`POST /api/v1/jobs`. It waits for a terminal state and verifies the real
`RESULT/task_result.json` (`status=completed`, `complete=true`),
`RESULT/result_manifest.json` (`status=completed`, one product),
`execution_receipt.json` (`state=completed`), the frozen envelope release id,
and that the receipt release id equals the installed-package identity.
Observed energy: `-1.116759307204` Hartree; job status `completed`; release id
`2c7ffd56045b5bd5` matched. Exit `0`.

### D2 — installed-wheel revocation reset

Classification: **harness fixture-state defect, not an installed-code delta**.
`_ReauthorizingStreamAuthorizer` is byte-identical to the candidate. Reused
B15 fixture jobs were `PAUSED`; a Controller restart freezes them to `FAILED`
(`[RESTART_FAILED]`), so the open stream legitimately emitted `event: done`
and closed before revocation. `_b15_ensure_fixture_jobs` now restores reused
fixture jobs to their declared `PAUSED` state via `JobStore.transition` CAS
before the probe. The revocation assertion was not weakened: it still requires
an event before revocation, `permission_changed` after revocation, the stream
thread to stop, and `401` on the next read. Result: `permission_changed` was
observed for both the key and the account revocation, `line_count=12`.

## Cleanup

Test Controllers/Lab processes stopped; ports `18787/18788/18789` free; no
worker or `acp run` process left. Production `8765` stayed healthy and was not
touched.
