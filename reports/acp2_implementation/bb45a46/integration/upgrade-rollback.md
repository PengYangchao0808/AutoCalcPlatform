# ACP 2.0 Plan B — install, upgrade and rollback path

**Candidate:** `acp2.plan-b.h7.release-fix4` · version `0.3.0`
**Source:** `abec2bc4f1d302827248d36171973df5bc1bc4dd`
**Artifacts:** `reports/acp2_implementation/bb45a46/B/B25/release-fix4/`
**Scope:** reviewable release candidate only. No production deployment is authorized by
this document, and `acp.service` was not restarted.

## 1. Roles and layout

| Distribution | Contents | Entry point |
|---|---|---|
| `cccp` | `cccp/**`, science model assets, LICENSE/NOTICE, `py.typed` | Python API, no CLI |
| `acp-worker` | `acp_services/**` | `acp-worker` / `python -m acp_services.worker_bootstrap` |
| `acp-web` | `acp/**`, `acp_web_assets/**` | `acp` |
| root `acp` (alternate) | all three packages | `acp` |

Install either the three role packages **or** the root distribution in one environment.
Do not mix both releases in the same environment.

## 2. Fresh install (outside the repository)

```bash
python3.11 -m venv /opt/acp-0.3.0
/opt/acp-0.3.0/bin/python -m pip install -U pip setuptools wheel
/opt/acp-0.3.0/bin/python -m pip install \
  --find-links /ACPServer/AutoCalcPlatform/reports/acp2_implementation/bb45a46/B/B25/release-fix4 \
  'cccp==0.3.0' 'acp-worker==0.3.0' 'acp-web==0.3.0'
```

Verify the installed identity and that no source checkout is on `sys.path`:

```bash
env -u PYTHONPATH /opt/acp-0.3.0/bin/python -c \
  'import acp, cccp, acp_services; print(acp.__file__, cccp.__file__, acp_services.__file__)'
env -u PYTHONPATH /opt/acp-0.3.0/bin/acp --help
```

Expected: all three modules resolve under `/opt/acp-0.3.0/lib/.../site-packages`.
`acp --help` exits 0.

## 3. Lab and MCP smoke (isolated run root and loopback port)

```bash
export ACP_RUN_ROOT=/var/lib/acp-0.3.0-test/runs
/opt/acp-0.3.0/bin/acp run serve --host 127.0.0.1 --port 18786 --no-browser
curl -fsS http://127.0.0.1:18786/api/health
/opt/acp-0.3.0/bin/acp mcp serve --controller http://127.0.0.1:18786
```

Expected: `/api/health` returns `status=ok`; MCP `initialize` negotiates protocol
`2025-06-18` and `tools/list` returns the eight P1 tools. Do not point `ACP_RUN_ROOT`
at production `/var/lib/acp/runs`.

## 4. Upgrade from the previous 0.2.x candidate

1. Stop only the **test** Controller. Preserve the old run root and take a backup.
2. Install the new role set into a fresh venv (preferred) or upgrade in place:
   ```bash
   /opt/acp-0.2.x/bin/python -m pip install --upgrade \
     --find-links /ACPServer/AutoCalcPlatform/reports/acp2_implementation/bb45a46/B/B25/release-fix4 \
     'cccp==0.3.0' 'acp-worker==0.3.0' 'acp-web==0.3.0'
   ```
3. Run the schema/backup path before serving:
   ```bash
   /opt/acp-0.2.x/bin/acp task query --run-root "$ACP_RUN_ROOT" --job <known-job>
   ```
4. Start one test Controller, confirm `/api/health`, and submit one small local task
   through the real scheduler path (`acp task execute --follow`) as in the package-install
   evidence.
5. Verify `RESULT/result_manifest.json`, `RESULT/task_result.json` and
   `execution_receipt.json` for the new task, and read an old task read-only.

Rollback is triggered by any failed step, not by a timeout that leaves an unknown state.

## 5. Rollback

Rollback is code rollback only; do **not** delete the jobs DB or run root.

1. Stop the test Controller.
2. Reinstall the previous release into a fresh venv from its retained artifact directory
   (for the previous line, `B/B25/release/`; the original B25a artifacts are under
   `B/B25/release-b25a-original/`).
3. Restore the backed-up run root only if the upgrade wrote new schema and the previous
   version cannot read it. Prefer reading the backup in a separate path first.
4. Re-run the old version's `acp task query` against a copy and confirm every identity,
   approval, budget and idempotency row is present.
5. Keep the 0.3.0 evidence and artifacts for audit; do not overwrite old attempt receipts.

Irreversible steps: deleting a run root, deleting `acp_jobs.db`, or purging a production
data directory. None is part of this candidate's rollback.

## 6. Known gaps and gates

- H4/H6/H7 are OPEN; A-29 and C35 have not signed.
- C33/C34 install evidence, C static assets, LSF, DSH/browser, and running-task
  disconnect recovery are `NOT_VERIFIED`.
- The release candidate was not deployed to production. Production rollout requires
  prior explicit user authorization and the release gate; this plan never restarts
  `acp.service`.
