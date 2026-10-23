# B26a — External reproduction from a clean shell (2026-10-23)

**Schema:** `acp2.b26a.reproduction-matrix.v1` · **Overall:** `FAIL` (two real FAIL rows; four NOT_VERIFIED; no row inflated)
**Release under test:** `0.3.0`, built from clean source `a84c4266bfc4738c8efc2988b7c5c37079a8a324` (`release/release-manifest.json`) · **Repo HEAD at reproduction:** `f60fda89b700bf1f828bf651183f88cf5e1913da` · **Controller env label in B15/B17 receipts:** `f60fda8…` (not a code claim)
**Independence:** `independent_operator: false (same agent/session)` — this is a **self-reproduction**, not an independent operator run.

## 1. Clean shell proof

- Clean root: `/tmp/opencode/b26a-clean` (outside the repository).
- Working directory: `/tmp/opencode/b26a-clean/work` (`pwd` recorded in `clean-shell.txt`).
- Launcher: `env -i HOME=$HOME USER=b26a-operator LOGNAME=b26a-operator PATH=/usr/bin:/bin SHELL=/bin/sh LANG=C.UTF-8 LC_ALL=C.UTF-8 TMPDIR=$TMPDIR /bin/bash --noprofile --norc -c "<command>"` (the exact 8-variable whitelist is in `clean-shell.txt`).
- No `PYTHONPATH`, no `PYTHONHOME`, no `SSH_AUTH_SOCK`, no author shell history or credentials.
- Release artifacts, docs, plugin bundle, legacy fixtures and the acceptance harness were copied into the clean root before the run; installed modules resolved under the clean venv `site-packages` (`install-import-proof.txt`).

## 2. Install / import proof

```text
venv:   /tmp/opencode/b26a-clean/venvs/web  (python3.11)
install: pip install --find-links /tmp/opencode/b26a-clean/release \
         /tmp/opencode/b26a-clean/release/acp_web-0.3.0-py3-none-any.whl
exit:    0
imports: acp / cccp / acp_services / acp_web_assets all under
         <clean-root>/venvs/web/lib64/python3.11/site-packages
repo path on sys.path: False
version: acp 0.3.0 / cccp 0.3.0
```

The full four-role clean-install scenario is `package-install/` (`summary.json`, receipt, logs, probes, assets audit). It exits 0 and records PASS for four fresh venvs, installed paths, the CCCP controlled sample without a Controller, Lab health, stdio MCP 8 tools, and assets/notices/licenses. `C33`/`C34` and the C static-asset mount remain `NOT_VERIFIED` at their registered paths.

## 3. Reproduction matrix (summary)

| Row | Result | Reason / note |
|---|---|---|
| Clean install | PASS | package-install scenario exit 0 |
| Third-party plugin install | NOT_VERIFIED(undocumented_step) | Raw run passed (discover/install/enable/freeze v1→v2, old task frozen at v1, no entrypoint execution), but no published operator procedure exists |
| Novice manual entry (standalone recipe) | PASS | `acp init` plan applied (doctor PASS 7/FAIL 0/NOT_VERIFIED 5); xTB closure `-5.070543980555 Hartree`; Lab `/` 200, `/api/health` ok, 15 workflows |
| Agent research entry | NOT_VERIFIED(c_driver_absent) | stdio MCP PASS (`2025-06-18`, eight tools, `acp_capabilities` result); official DSH/browser absent (C-owned) |
| Historical read-only | PASS | compat readers load S2/S3/S4/batch/refinement/candidate; historical v2 manifest loads with 1 product |
| Local execution | PASS | installed `acp run` xTB and ORCA singlepoint both exit 0; CCCP controlled sample exit 0 |
| LSF normal + control | NOT_VERIFIED(lsf_prerequisite_missing) | no `bsub`/`bjobs`/`bstop`/`bresume`/`LSF_ENVDIR`; no fake LSF invoked |
| Authorization (B15) | **FAIL** | 10 authz assertions PASS; key/account revocation SSE did not emit `permission_changed` (read-after-revoke is 401) |
| Budget (B17) | PASS | at-cap 201, over-cap 403, race one 201/one 403, restart preserves budget, settlement idempotent |
| Disconnect recovery | NOT_VERIFIED(installed_workbench_submission_failed) | job row and event cursor survived a real Controller restart, but the task itself failed before restart, so running-task recovery is not proven |
| Installed Lab/Workbench submission (extra defect row) | **FAIL** | 201 then `Submission error: no execution-tree files found under /tmp/opencode/b26a-clean/venvs/web/lib/python3.11 (expected src/acp, src/acp_services, src/cccp)` |

Each row in `reproduction-matrix.json` carries `command`, `cwd`, `exit_code`, `report_path`, `result`, `scope`, `prerequisites`, `documented_step`, `stale_pass_reused: false` and `owner`.

## 4. Defects found by the external reproduction

1. **B26A-D1 (high): installed wheels cannot execute scheduler tasks.** `local_release_identity()` expects `<root>/src/{acp,acp_services,cccp}`; the wheel installs packages directly under `site-packages`. Direct `acp run` bypasses the scheduler freeze and works, but Lab/Workbench and `acp task execute` fail before dispatch. This also blocks scheduler-driven local execution, running-task disconnect recovery, and B17 dispatched science (budget enforcement itself passes).
2. **B26A-D2 (medium): B15 revocation SSE reset not observed.** Read-after-revoke is 401, but the open `/api/events/stream` subscription did not emit `permission_changed` within the harness window.

Both are recorded in `reproduction-matrix.json` (`defects`) and `documentation-gaps.json`.

## 5. Independence limitation

No separate human or agent operator was available. All rows were executed by the same agent/session that authored the B evidence. `independent-operator.json` records the sanitized environment, the self-reproduction row list, and everything not claimed (independent operator, C DSH/browser, LSF, A/C sign-off, C35, production).

## 6. A/C raw facts

`a-c-refs.json` references A-27b and A-28 raw evidence at their registered paths without re-signing. C33/C34 are absent at their registered paths (`NOT_VERIFIED`); C35 is not a B26a prerequisite; the C static-asset mount is `NOT_VERIFIED(c_static_assets_absent)`.

## 7. Validation and cleanup

- `pytest`: 39 passed (`pytest.txt`, `pytest.exit`).
- `ruff check` / `ruff format --check`: clean on the touched acceptance/freeze files (`ruff-*.txt`).
- `validate_acp2_contracts.py`: `VALID` (`validator-contracts.txt`).
- `acp2_runtime.py --check-only` on the package-install summary and on this directory's `summary.json`: `VALID` (`validator-check-only.txt`, `check-only-package-install.txt`).
- All B26a Lab/Controller processes were terminated by PID; 18787/18788/18789 verified free; temp venvs/data roots removed after logs were copied; production `acp.service` on 8765 was only observed, never restarted and `/var/lib/acp/runs` was not touched (`cleanup.json`).

## 8. Rerun commands

```bash
# clean shell (new empty dir outside the repo; do not reuse the author's env)
env -i HOME=/tmp/b26a-home USER=operator PATH=/usr/bin:/bin SHELL=/bin/sh \
  LANG=C.UTF-8 TMPDIR=/tmp/b26a-tmp /bin/bash --noprofile --norc

# install (published packaging/README.md form)
python3.11 -m venv /tmp/b26a-venv
/tmp/b26a-venv/bin/python -m pip install --find-links <release-dir> <acp_web-wheel>
cd /tmp/b26a-work   # no cccp.yaml inherited
/tmp/b26a-venv/bin/python -c 'import acp, cccp, acp_services; print(acp.__file__)'

# acceptance scenarios (same harness copy used here)
python3.11 scripts/acceptance/acp2_runtime.py --scenario package-install \
  --fixture package-install.json --out <evidence> --timeout 1800
python3.11 scripts/acceptance/acp2_runtime.py --scenario multitenant \
  --phase multitenant:authz --controller http://127.0.0.1:18788 \
  --fixture multi.json --out <evidence> --timeout 600
python3.11 scripts/acceptance/acp2_runtime.py --scenario approval-budget \
  --phase budget --controller http://127.0.0.1:18789 \
  --fixture approval-b17.json --out <evidence> --timeout 600
python3.11 scripts/acceptance/acp2_runtime.py --check-only <evidence>/summary.json
```
