# Round-7 evidence — batch A+C (executor)

Scope: #1 singlepoint stale release recovery; #3 ORCA route basis invariant;
#7a xTB constraint validation; #7c ORCA executable probe; #7d DLPNO energy labels.

## #1 stale release
- `attempt_freeze.py`: `StaleReleaseRefreshError` (rule=stale_release, both digests,
  admin command), `refresh_local_release()` (wraps `build_local_release`, never
  skips the digest check), `current_release_identity()`.
- `scheduler/runner._freeze_default_attempt`: discard the cached `_default_release`,
  rebuild ONCE, retry freeze ONCE; injected freeze pins are not swapped;
  BatchOptimize does not use this path.
- `scheduler/remote/runner`: rebuild + retry once, still requires the rebuilt
  release id == verified remote `code_release`.
- Tests: `tests/test_acp_round7_release_recovery.py` — 10 passed (byte-identical old
  tree, tampered release refused, unversioned dev denied, injected pin intact,
  second failure typed with admin command, BatchOptimize untouched).

## #3 route basis invariant
- `cccp/qc/method_meta.py` classifiers (`classify_basis_token`,
  `orbital_basis_tokens`, `aux_basis_tokens`, `is_orbital_basis`, `is_aux_basis`).
- `cccp/qc/interfaces/route_render.py`: `resolve_route_orbital_basis` (typed
  `RouteBasisConflictError`, rule=basis_conflict), `_assert_single_orbital_basis`
  in `render_route_line`, `AuxiliaryBasisError`, and the pure preflight helper
  `detect_basis_conflicts`.
- `orca._build_input_blocks` integrates dedupe/refusal; composite routes unchanged
  (goldens pass).
- Tests: `tests/test_cccp_route_basis_invariant.py` — 21 passed.

## #7a xTB constraints
- `xtb_scan.py`: `validate_xcontrol_text` / `parse_xcontrol_constraints`
  (`XtbConstraintSyntaxError`, rule=constraint_syntax_invalid) and
  `detect_constraint_rejection` / `parse_constraint_evidence`
  (rule=constraints_not_honored).
- `xtb.py`: validates the composed xcontrol text before spawning;
  `xtb_constrained_opt` fails (INVALID_INPUT + metadata.constraint_evidence)
  when xTB dropped a constraint instead of reporting COMPLETED.
- Tests: `tests/test_cccp_xtb_constraint_failure.py` — 21 passed.

## #7c ORCA executable
- `cccp/software.py` probes `orca --version` at the resolution boundary; the GNOME
  screen reader is skipped, `resolve_executable` returns None and
  `require_executable` raises `SoftwareUnavailableError`
  (rule=backend_unavailable, field=executables.orca.path).
- Tests: `tests/test_cccp_orca_executable_probe.py` — 14 passed.

## #7d DLPNO energy
- `parse_energy_report` / `energy_report_for`: result = last
  `FINAL SINGLE POINT ENERGY`; `E(0)` labelled SCF reference, `E(TOT)`/`E(T)`
  metadata; E(0)==final falls back to E(TOT)+E(T) or refuses.
- Tests: `tests/test_cccp_dlpno_energy_labels.py` — 9 passed.

## NOT_VERIFIED
- Remote freeze retry path has no dedicated test.
- The exact `ma-def2-SVP` injection point needs the external agent's
  job.json/.inp (invariant + refusal implemented; origin not proven).
- Raw xTB `path_inp_text` origin unknown; the guard is covered separately
  (Lead follow-up + `tests/test_cccp_xtb_path_constraint_guard.py`).
