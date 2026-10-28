# ACP2 Plan A — F4 Amendment Check (A-08)

Status: **no F4 amendment requested.**

## Check performed

The F4 guard (`tests/test_f4_scope_audit.py`) protects the QC interface and
backend scope against undeclared implementation changes. A checked whether the
P0 science work touches that scope:

```bash
git diff --name-only 096cdffe9a3817d8ee443d824830d87ec6e07bc2..HEAD \
  -- src/cccp/qc src/cccp/backends src/cccp/calculation/tasks
```

Observed output: **empty** (no path under `src/cccp/qc`, `src/cccp/backends` or
`src/cccp/calculation/tasks` changed by the P0 science commits).

The P0 science changes are:

- new modules under `src/cccp/calculation/capabilities/` and
  `src/cccp/calculation/recipes/`;
- new research contract types under `src/cccp/analysis/research/`;
- three added lazy exports in `src/cccp/calculation/__init__.py` for
  `run_xtb_constrained_opt`, `XtbConstrainedOptOptions` and
  `XtbConstrainedOptPayload` (export-only; no task body, QC interface, backend
  or subprocess behavior changed);
- science docs and A-owned tests/fixtures.

None of these add, remove or alter a QC interface method, backend adapter,
subprocess launch site, ORCA/xTB/CENSO argument, or the single execution
station's task bodies. `tests/test_f4_scope_audit.py` is untouched and remains
green.

## Decision

No F4 amendment is needed for ACP2 P0. If a later task needs to change a QC
interface or a backend adapter, A must file an F4 amendment request here first
and must not relax the existing F4 greps or guards.

## Re-check command

Run the command above before any future handoff that adds files under the three
protected directories. A non-empty output requires an amendment entry naming the
files, the sanctioned scope and the guard teeth.
