# Round-7 evidence — batch B+D (executor)

Scope: #2 per-role batch_roles SP; #4 thermochemistry readable via MCP;
#6 BatchOptimize/singlepoint preflight analyzers.

## #2 per-role single point
- `batch/options.py`: per-role SP fields (`minimum_sp_*` / `transition_state_sp_*`),
  `sp_for_role` resolution (role SP > legacy common SP > role optimization level),
  cache key includes per-role SP; removed the always-None fallback.
- `batch/effective_config.py`: per-role effective `single_point_*` + `source` +
  `single_point_scope`.
- `submission_contract.py`: `rule=missing_role_mirror` when a top-level SP mirror
  has no matching int role/field; `sp_method/sp_basis` accepted only as
  batch_roles mirrors.
- Tests: `tests/test_acp_round7_batch_roles_sp.py` — 12 passed;
  `tests/test_batch_optimize.py` — 86 passed (per-role assertion updated).

## #4 thermochemistry
- `results/thermochemistry.py`: ORCA THERMOCHEMISTRY block parser (ZPE/H/G/S,
  temperature/pressure, phase correction, provenance; no fabricated values),
  Shermo projection, `publish_thermochemistry` via the single manifest writer.
- `workflows/simple.py` (frequency) and `batch/engine.py` (per item) publish
  `RESULT/thermochemistry.json` / `RESULT/thermochemistry/<item>.json` as
  `ProductKind.THERMO_REPORT`.
- `artifact_descriptors.py`: `thermo_report → kind "thermochemistry"` alias
  (manifest enum unchanged).
- Tests: `tests/test_acp_round7_thermochemistry.py` — 11 passed (including the
  Lead follow-up: no absolute host path in the payload).

## #6 preflight analyzers
- `submission_validation.py`: `BatchOptimize` and `singlepoint` analyzers
  (per-role effective, per-item role projection, route/basis conflicts via the
  pure `detect_basis_conflicts` helper), read-only.
- `v1_routes._preflight_projection` merges `unverified_checks`; submit runs the
  same analyzer (Lead follow-up) so preflight/submit agree on `basis_conflict`.
- Tests: `tests/test_acp_round7_preflight_batch.py` — 11 passed (incl. the Lead
  parity test); `tests/test_acp_round5_preflight_parity.py` — 9 passed.

## NOT_VERIFIED
- Real frequency run / remote artifact fetch for thermochemistry.
- ORCA thermochemistry block format validated against one ORCA-6 output only.
- ma-def2-SVP injection point (needs the external agent's payload).
