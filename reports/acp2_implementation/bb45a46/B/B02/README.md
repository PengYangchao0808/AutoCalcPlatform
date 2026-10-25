# B02 — Isolated Python, data root and ACP test configuration

**Status: PASS (with named gaps).** B02a environment handoff and B02b minimal package smoke are complete.

## Materialized candidate

| Field | Value |
|---|---|
| candidate id | `sha256:bb45a46c93d241d03ffae2952a78a2736a637a3f2c8d69b72d2bd0c4fafc3e12` |
| base commit | `3670790363c7a1959ad1dbe4b4297adec518952a` |
| worktree | `/opt/acp2-dev/B` |
| applied | `tracked-changes.patch` then `new-files/` per `apply-order.md` |
| per-file verification | **1906/1906 PASS**, 0 mismatches, 0 missing |
| materialized commit | `a0185b15c546fbd07919326d581fab13e05000fa` |
| `git status --porcelain` | empty |

`runtime/baseline-lock.json` now records the materialized commit, timestamp, bundle digest, per-file verification and base commit.

## Interpreters

| Role | Interpreter | Source assertion |
|---|---|---|
| A | `/opt/acp2-dev/envs/a-python/bin/python` | PASS |
| B | `/opt/acp2-dev/venvs/b/bin/python` | PASS |
| C | `/opt/acp2-dev/venvs/c/bin/python` | PASS |
| integration | `/opt/acp2-dev/venvs/integration/bin/python` | PASS |
| browser | `/opt/acp2-dev/venvs/browser/bin/python` | playwright 1.63.0 importable; Chromium not launched |

Full interpreter/cwd/`__file__`/candidate/dependency digest matrix: `role-interpreters.json`.

## Controller

- `$ACP2_DATA_ROOT/controller-config/cccp.yaml` (mode 0600): `cluster.execution_mode: local`, `cluster.nodes: []`, isolated run root, explicit local executable pins.
- `acp doctor`: 10 PASS / 0 FAIL / 0 NOT_VERIFIED.
- `acp run serve --help`: exit 0.
- Production service on 8765 was never touched (HTTP 200 before and after).

## B02b package smoke (P0 minimal, full B25 → P5)

| Row | Result |
|---|---|
| CCCP wheel + sdist built | PASS |
| Worker-role install + frozen worker probe | PASS |
| Web-role install + installed HTML/JS served with warm ETag 304 | PASS |
| File ownership / no cross-role duplicate files | PASS |
| Inter-package constraints (`cccp` env lacks acp/acp_services/fastapi; worker env lacks acp/fastapi) | PASS |
| Install 0.2.0 → upgrade 0.2.1 → uninstall removes module | PASS |
| Assets/notice/static locations | PASS |
| Import without external QC binaries (empty PATH) | PASS |
| Full B25 release delivery | NOT_VERIFIED (P5) |

## Named gaps

- LSF: `NOT_VERIFIED` (no `bsub`/`bjobs`; local-only by design).
- Chromium launch: `NOT_VERIFIED` (C T-08 H2).
- Functional scientific execution: `NOT_VERIFIED` (doctor is binary sanity only).
- Full B25 packaging: `NOT_VERIFIED` (P5).

## Files

- `env-probe.log`, `apply-verify.log`, `venv-install.log`, `import-paths.log`, `init-config.log`, `config-doctor.log`
- `pip-freeze.txt`, `role-interpreters.json`, `browser-env.json`, `b-runtime.json`, `summary.json`
- `package-build.log`, `package-role.log`, `package-lifecycle.log`
- `acceptance-env-check.log`, `cleanup-receipt.log`
