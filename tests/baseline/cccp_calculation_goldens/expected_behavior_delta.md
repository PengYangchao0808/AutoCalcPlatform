# Expected behavior delta — P0 fixes (approved deviations from pre-migration goldens)

**Scope:** Wave 0 / todo 2 companion to `tests/baseline/cccp_calculation_goldens/`.
**Policy (plan §Verification / A7):** P0 fixes that introduce *pre-launch rejection*
or change *error classification* are **approved deltas** — post-migration behavior
does not have to match the old error text verbatim. Every intentional delta MUST
be listed here before it lands; anything not listed is a regression.

**Baseline:** goldens generated from `manifest.json:source_commit` by
`generate_goldens.py` (pre-migration integration path, ACP present).

**Explicitly NOT a golden:** the "silent degradation when ACP is missing" bug
(DLPNO `auxJ/auxC` dropped and opt input generation failing under import
isolation). Isolation success is the *target* change (todos 3/10), not a
regression to preserve. Goldens record only the integrated-path behavior.

## Approved deltas

| ID | Area | Pre-migration behavior (goldens / pins) | Post-P0 behavior | Kind | Owner todo |
|---|---|---|---|---|---|
| D1 | Capability selection (`require_backend`) | Structural `issubclass(Protocol)` match in registry-sorted order; `optimization`/`single_point`/`frequency` select `CrestBackend`, whose capability methods are `NotImplementedError` stubs raised at **call** time | `BackendRegistry.require` filters by declared status == `AVAILABLE`; stubs can never be selected; unsupported capability rejected **before** backend construction with `UnsupportedCapabilityError` | error-classification + pre-launch rejection | 8 |
| D2 | `CAPABILITY_MATRIX` CREST `geometry_optimization` / `single_point` | `AVAILABLE` (declared) though the methods are stubs | `STUBBED` (declaration = implemented) | declaration semantics | 8 |
| D3 | `CAPABILITY_MATRIX` `external` `clustering`/`thermochemistry` | `MISSING_BINARY` (binary probing folded into the matrix) | `AVAILABLE`; binary absence judged at runtime by `is_isostat_available`/`is_shermo_available` → `BackendUnavailableError` | declaration semantics + error classification | 8 |
| D4 | `xtb` frequency capability | `require("frequency")` never reaches `xtb`; if selected via `supports()==False` path an `AttributeError` can escape from the missing method | Pre-launch structured rejection (`UnsupportedCapabilityError`) — no `AttributeError` escape | pre-launch rejection | 8 |
| D5 | Reverse-import-dependent defaults (`cccp/qc/interfaces/orca.py::_resolve_method_meta`, `cccp/core/protocols.py`) | Method-meta defaults resolved via lazy `acp` import; under isolation the import fails or silently degrades (DLPNO aux blocks dropped) | Pure cccp translation with identical effective defaults in isolation and integration (goldens' route lines remain the reference) | bug fix (not a delta to the goldens themselves) | 7, 10 |
| D6 | Error text/class for backend-call failures | Free-text `NotImplementedError`/`RuntimeError`/`OSError` bubbling with ad-hoc strings | Neutral typed errors (`TaskInputError` / `UnsupportedCapabilityError` / `BackendUnavailableError`, `cccp.calculation.errors`) thrown P0-onward; message text may differ, failure **classification tokens** in `error_tokens.json` (e.g. `scf_failure`, rescue strategies) stay stable | error classification | 9, 11 |
| D7 | CASSCF/NEVPT2 active-space rejection | `validate_casscf_spec` errors returned as failed `CalculationResult` strings; `ORCAInterface.casscf` returns `QCResult(success=False)` for bad active space | Same rejection semantics, surfaced as pre-launch `TaskInputError` where the request layer can reject earlier (messages may change) | pre-launch rejection | 17–22 (per-task) |
| D8 | XtbPathSearch / OrcaGradient typed error codes | `XTB_PATH_E_*` / `ORCA_GRADIENT_E_*` strings embedded in exception messages | Codes preserved; wrapper exception type may become the neutral errors where the task layer wraps them | error classification | 28 |

## Non-deltas (must stay identical)

* ORCA route lines and parsed method/basis/dispersion/auxJ/auxC/solvent/grid/SCF
  fields (`orca_routes.json`) — translation semantics, incl. builtin-dispersion
  token suppression, composite RI stripping, GFN ALPB token, NumFreq promotion.
* Hessian interval resolution + `%geom Recalc_Hess` emission
  (`hessian_resolution.json`, `test_p0_characterization.py::TestHessianOrcaEmission`).
* Unit strings (`unit_strings.json`).
* Rescue matrix actions/terminal flags (`optimize_rescue.json`).
* Scan multi-coordinate plan compilation and partial-failure counts (`scan.json`).
* IRC direction resolution and one-way completion semantics (`irc.json`).
* CASSCF/NEVPT2 input text and parsed energies/natural occupations
  (`casscf_nevpt2.json`).
* Shermo standard-state correction values and Gibbs selection (`shermo_standard_state.json`).
* CENSO record identity (conf_id → frame_index) and free-energy fields
  (`censo_records.json`).
* NMR atom index base and shielding values (`nmr_shielding.json`).
* Converted request fields and artifact digests for XtbPathSearch/OrcaGradient
  (`workflow_requests.json`).

## Rules

1. A behavior change not listed above requires an amendment row **in the same
   commit** as the implementation change, plus an update of the affected
   golden/pin in that commit.
2. Error **classification tokens** are contract; error **message text** is not.
3. When todo 8 lands, `tests/test_p0_characterization.py::TestCapabilitySelectionStatusQuo`
   must be updated in the same commit (the pins intentionally lock the current
   status quo; the delta table is the authorization to change them).
