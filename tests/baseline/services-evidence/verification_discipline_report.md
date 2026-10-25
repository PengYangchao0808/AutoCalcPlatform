# ACP Services — Per-slice Verification Discipline Report (B13-1 / todo 66)

**Owner:** todo 66 (B13-1) · **Wave:** W14 · **Commit:** `test(services): per-slice verification discipline (guards preserved)`
**Scope of this record:** the verification-discipline closeout for the Services plan (`.omo/plans/acp-parallel-plan-2-services.md`). Product code, guards, and the plan file are **not** modified by this todo. This file is tracked at `tests/baseline/services-evidence/verification_discipline_report.md`.

---

## 1. Guard-suite closeout (run at HEAD `e021ad3`, plan-2 execution complete except gated W12/W13)

All commands run with `python3.11` from the repo root. Exit codes are real (not inferred).

| # | Suite | Command | Exit | Result | Notes |
|---|-------|---------|------|--------|-------|
| 1 | Architecture invariants | `python3.11 -m pytest tests/test_architecture_invariants.py -q` | **0** | 20 passed (11.25s) | Four-layer arch, unique primitive, dependency direction |
| 2 | Scheduler markers audit | `python3.11 -m pytest tests/test_scheduler_markers_audit.py -q` | **0** | 3 passed (0.33s) | `_SCHEDULER_MARKERS` full-set snapshot pin |
| 3 | Job-edit coverage | `python3.11 -m pytest tests/test_acp_job_edit.py -q` | **0** | 82 passed (25.66s) | `EDIT_ACTIVE_WORKFLOWS` + audit |
| 4 | Frames / VIEW_REGISTRY | `python3.11 -m pytest tests/test_acp_frames.py -q` | **0** | 10 passed (0.18s) | Frame contract, no bypass |
| 5 | Services isolation | `python3.11 -m pytest tests/test_acp_services_isolation.py -q` | **0** | 23 passed (1.50s) | AST: `acp_services` ↛ `acp`; ledger 6-col |
| 6 | cccp isolation | `python3.11 -m pytest tests/test_cccp_isolation.py -q` | **0** | 8 passed (4.30s) | `cccp` ↛ `acp` |
| 7 | Result publication | `python3.11 -m pytest tests/test_result_publication.py -q` | **0** | 11 passed (0.46s) | `result_manifest` write path |
| 8 | Execution-integrity E2E | `python3.11 -m pytest tests/test_execution_integrity_e2e.py -q` | **0** | 7 passed (2.51s) | CAS / control-intent / terminal-ordering |
| 9 | Checkpoint resume semantics | `python3.11 -m pytest tests/test_checkpoint_resume_semantics.py -q` | **0** | 18 passed (2.99s) | Identity reuse, conservative recompute |
| 10 | Recovery fixtures smoke | `python3.11 -m pytest tests/test_recovery_fixtures_smoke.py -q` | **0** | 19 passed (0.44s) | Live cross-version fixtures |
| 11 | Recovery cross-version | `python3.11 -m pytest tests/test_recovery_cross_version.py -q` | **0** | 8 passed (0.34s) | v1 checkpoint conservatism |
| 12 | Grep-gates script (suite) | `python3.11 scripts/check_grep_gates.py --suite architecture-remediation` | **0** | gates run 10 · blocking 0 · stale allowlist 0 | Four pins green |
| 13 | Grep-gates script (tests) | `python3.11 -m pytest tests/test_grep_gates_script.py -q` | **0** | 160 passed (37.00s) | `unique_run_scan`/`unique_run_irc`/`wave2_shermo_external`/`final_shermo` |
| 14 | **F4 scope audit** | `python3.11 -m pytest tests/test_f4_scope_audit.py -q` | **1** | **28 passed, 1 FAILED** (116.17s) | `test_migration_scope_audit_reaches_migration_files` — **see Finding 1** |
| 15 | compileall src | `python3.11 -m compileall -q src` | **0** | clean | Whole `src/` byte-compiles |

**Totals:** 14 of 15 verification commands green. **1 real failure** (F4 scope audit) reported below, not fixed (guards are out of scope for this todo).

### Grep-pin status (four pins of `scripts/check_grep_gates.py`)

| Pin | Gate | Status |
|-----|------|--------|
| `unique_run_scan` | `^def run_scan\(` only in `cccp` task | **PASS** |
| `unique_run_irc` | `^def run_irc\(` only in `cccp` task | **PASS** |
| `wave2_shermo_external` | `run_shermo` in the two `external_backend.py` | **PASS** |
| `final_shermo` | `run_shermo` under `src/cccp/` only | **PASS** |

`--suite architecture-remediation` reports **0 blocking findings, 0 stale allowlist entries** (`ARCHITECTURE_ALLOWLIST` is empty since todo 29 — machinery retained as a guard, not an escape hatch). The `tests/test_f4_scope_audit.py:1902-1909` revision-registration discipline (QC-interface changes must register a sanctioned amendment) is intact: the audit runs and the only failure is the *migration-station* sub-check, not an amendment-skip.

---

## 2. Finding 1 — F4 scope audit fails the migration-station cleanliness gate (REPORT, not fixed)

- **Failing test:** `tests/test_f4_scope_audit.py::test_migration_scope_audit_reaches_migration_files` (assert at `:2728`).
- **Rule:** every `.py` file changed since baseline `f410ac9` must either live under a registered `MIGRATION_STATION_PREFIXES` or be registered as a migration station; files changed outside the stations are flagged `"changed outside registered migration stations (unaudited)"`.
- **Observed:** 22 unaudited files. They are the **Services wave's own work** plus **lane-C science relocations**, e.g.:
  - `src/acp/init_wizard/{__init__,answers,capabilities,flows,gating,lab_doctor,options,persist,plan}.py` (B07 init unification, todos 36–41/74)
  - `src/acp/services_{bootstrap,data_bootstrap,events_bootstrap,publication_bootstrap}.py` (transitional assembly, todo 72)
  - `src/acp/confsearch/{sampling,sampling_models,selection}.py`, `src/acp/confsearch/shared/{boltzmann,deduplication}.py` (lane-C relocate, e.g. `457f28e`, `6b5ebec`)
  - `src/acp/chem/embedding.py`, `src/acp/intake/models.py` (lane-C `91b63a6`)
  - `src/acp/results/{frame_candidate_store,frames_adapter}.py`
- **Root cause (confirmed):** `test_f4_scope_audit.py` was **not amended after baseline** to register these new stations. Last change to the file was the pre-plan-2 redirect commit `50648b8`; no plan-2 commit touched it. The gate therefore reports the services/lane-C changes as unaudited.
- **Classification:** a **discipline-registration gap**, not a functional regression and not a relaxed guard. Per `MUST NOT`, guards were **not** deleted, weakened, or golden-updated to force a pass. The four grep pins and the architecture/markers/edit/frames/isolation/recovery guards are all independently green (rows 1–13).
- **Resolution owner:** this must be closed by registering the new migration stations (or an equivalent sanctioned amendment) in `tests/test_f4_scope_audit.py` by the owner of the F4 gate / lane-A-or-C amendment process — **not** by todo 66. Tracked here as an open item for the layered-closure pass (todos 69–71) and the F2/F4 final audits.

---

## 3. Plan/dependency static consistency check (new artifact)

**New tracked checker:** `tests/test_services_plan_consistency.py` (6 tests, all **PASS**). It parses `.omo/plans/acp-parallel-plan-2-services.md` and enforces the P1-5 static invariants:

| Test | Invariant | Result |
|------|-----------|--------|
| `test_plan_parses_without_problems` | plan parses; 83 todos (78 numeric + 5 `{t}` placeholders resolved) | PASS |
| `test_todo_numbering_unique_and_gap_free` | numeric ids unique + gap-free; F1–F5 contiguous | PASS |
| `test_blocked_by_references_resolve_to_existing_todos` | every `Blocked by` numeric / `F` ref → existing todo | PASS |
| `test_dependency_graph_is_acyclic` | DFS cycle detection over the dep graph (negative-verified on a synthetic 3-cycle) | PASS |
| `test_artifact_before_consumer_named_pairs` | contracts(3)→ports(6,11,12,30,31); worker(31)→chain-switch(32); release-prep(71)→release(76); release gated on F1–F4 | PASS |
| `test_final_wave_tasks_excluded_from_pre_release_dependencies` | only todo 76 may depend on F-tasks, and only on F1–F4 (never F5) | PASS |

**Dependency-graph verdict: PASS** — numbering unique, references valid, no cycles, artifact-before-consumer holds. Final-wave scoping (F1–F4 parallel after pre-release todos; 76 excluded by design; F5 post-release) matches the plan's own note.

**Gitignore graceful-skip:** `.omo/` is gitignored, so the plan file is absent in a clean checkout. The checker locates the plan **relative to the repo root** and calls `pytest.skip(..., allow_module_level=True)` with a clear message when absent — the repo test suite stays green in clean checkouts. Verified: module skips cleanly when `PLAN_PATH` is missing.

---

## 4. Per-slice closeout shape (discipline template)

The discipline recorded here is the per-slice command sequence every Services slice must follow. Each completed slice's evidence file (`​.omo/evidence/acp-parallel-plan-2-services/task-<N>-*.md`, gitignored local artifact) carries its own commands + exit codes; this tracked report is the aggregate gate record. Template:

```
# 1. targeted contract/behavior/recovery test for the slice
python3.11 -m pytest tests/test_services_<slice>.py -q                     # → exit 0
# 2. in-slice guard re-run (isolation + any guard the slice touches)
python3.11 -m pytest tests/test_acp_services_isolation.py -q               # → exit 0
# 3. non-slow scoped closeout (slice's dependency neighborhood)
python3.11 -m pytest -m "not slow" -q --<slice-scope>                       # → exit 0
# 4. compile check (when the slice adds/changes src)
python3.11 -m compileall -q src                                            # → exit 0
```

Slice → targeted-test → non-slow scope → compile → result is captured per task in the evidence tree. **All 15 verification commands in §1 carry their real exit codes** (acceptance: "每切片证据含命令+退出码" satisfied for the wave-level closeout this todo owns).

---

## 5. Shared-gate / A-C evidence acceptance

- **No shared gate was changed during plan-2 execution.** Verified across all 136 commits `f410ac9..HEAD`: `git log` on the guard set (`scripts/check_grep_gates.py`, `tests/test_f4_scope_audit.py`, `tests/test_architecture_invariants.py`, `tests/test_scheduler_markers_audit.py`, `tests/test_acp_job_edit.py`, `tests/test_acp_frames.py`, `tests/test_cccp_isolation.py`, `tests/test_result_publication.py`, `tests/test_execution_integrity_e2e.py`, `tests/test_checkpoint_resume_semantics.py`, `tests/test_recovery_fixtures_smoke.py`, `tests/test_recovery_cross_version.py`, `tests/baseline/recovery_fixtures/`) shows **zero guard commits** in the plan-2 range. The only guard-touching commits are the pre-plan-2 remediation (`66222f7`, `231f563`) and unrelated pre-existing ones.
- **Therefore no A/C evidence merge was required for shared gates.** Where a shared gate *would* change, the discipline is: receive lane A/C's tracked evidence (`.omo/evidence/acp-parallel-plan-1-frontend/`, `.omo/evidence/acp-parallel-plan-3-cccp/`, `tests/baseline/cccp-evidence/`) and fold it into the ledger — **never relax a gate locally**. No such change occurred in this wave; nothing to accept, nothing relaxed.
- The one F4 failure (Finding 1) is a **missing registration**, not a gate change — resolving it requires *adding* stations (a strengthening), which is explicitly out of scope for todo 66 and routed to the F4-gate owner.

---

## 6. Ledger / registry status refresh

Checked the two Services ledgers:

- `tests/baseline/services-evidence/injection_registry.md` — rows reference migration tasks (todos 6–56, 72–78) and removal task 77; statuses are consistent with the current execution state (W14 done except gated W12/W13). **No status changed by todo 66** (this todo adds no product code and retires no injection).
- `tests/baseline/services-evidence/migration_ledger.md` — 6-column per-symbol ledger; unchanged by this todo.

**Status: no change needed** — todo 66 is a verification/discipline pass, not an implementation slice, so no ledger row's (implementation-residency / compat-entry / producer / exit-condition / acceptance-test) state moved. The ledgers remain accurate as-is; they will be updated by the implementation todos they track (and closed at todo 70).

---

## 7. Acceptance checklist (todo 66)

| Acceptance item | Status |
|-----------------|--------|
| 每切片证据含命令+退出码 | **PASS** — §1 table carries real exit codes for all 15 wave-closeout commands; per-slice evidence in the gitignored evidence tree |
| 护栏套件（架构/标记/编辑覆盖/frames/隔离）收口全绿 | **PASS (14/15)** — architecture/markers/edit-coverage/frames/isolation all green; **1 exception**: F4 scope-audit migration-station gate (Finding 1), reported not fixed |
| 台账逐项状态更新 | **PASS** — no status changes needed (§6); stated explicitly |
| 依赖图静态检查 PASS（编号唯一、引用有效、无环） | **PASS** — new `tests/test_services_plan_consistency.py`, 6/6 green |
| 不删护栏换通过 / 不放宽 grep / 不更新黄金值 | **PASS** — no guard touched; F4 failure surfaced honestly |

**Net:** the verification discipline is landed and green except one F4 migration-station registration gap that is a real finding, owned by the F4-gate process, and deliberately left unfixed by this todo.
