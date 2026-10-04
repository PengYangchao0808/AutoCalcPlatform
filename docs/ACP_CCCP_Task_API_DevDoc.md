# ACP / CCCP Task API — Current Specification (Draft v1)

**Status:** draft, authoritative as of todo 11 (contract split + request/result
envelope + in-memory adapters).  This file is the **single current spec** for
the `cccp.calculation` task API; todos 17–24 implement against it and todo 33
finalizes it.  The historical design drafts (Task API proposal v1–v3.2 in
`.omo/drafts/acp-cccp-architecture-remediation.md`) are **archive-only** —
implementation and tests reference only this document.

**Scope of this draft:** contract shapes and conversion rules only.  Task
*execution* (`run_*` behavior, backend selection, translation) is specified
here as signatures/rules but implemented by todos 12–23 (see §"Two tables").

---

## 1. Module map and purity

| Module | Contents | May import |
|---|---|---|
| `cccp.calculation.errors` | neutral error taxonomy | stdlib |
| `cccp.calculation.contracts` | scientific types (single structure / single state / single-run params / artifact refs) + scientific validation/serialization + strict envelope serializers | stdlib, `errors` |
| `cccp.calculation.requests` | `TaskKind`, `TaskRequest`, `MethodSpec`, `StructureInput`, `TaskResources`, typed options union, `validate_request` | stdlib, `errors`, `contracts` |
| `cccp.calculation.results` | `TaskResult`, `ErrorKind`, typed payload union, artifact ser/de | stdlib, `errors`, `contracts`, `requests` |
| `cccp.calculation.progress` | `ProgressEvent`, `TaskProgressSink` (scientific events) | stdlib |
| `cccp.calculation.context` | `TaskContext`, `resolve_context` | stdlib, `contracts`, `progress`, `requests` |
| `cccp.calculation.selection` | two-step backend selection: `CapabilityRequirement`, `BackendSelection`, `ProgramRequirement`, `select_semantic`, `precheck_runtime`, `select_backend` | stdlib, `errors`, `contracts`, `requests`, `context`, `cccp.backends.matrix`, `cccp.backends.registry`, `cccp.software` |
| `cccp.calculation.__init__` | PEP 562 lazy re-exports | lazy |

Purity rules (asserted by tests):

* `import cccp.calculation` does **not** load `cccp.calculation.tasks` or any
  `cccp.qc.*` module (package init is lazy).
* `import cccp.calculation.contracts` loads no task module and no
  `cccp.qc.interfaces` (pure-type isolation).
* Nothing in `cccp/**` imports `acp`.

ACP keeps the orchestration/compat contracts in `acp.calculations.contracts`
(`StepKind`, `CalculationStep`, `CalculationPlan`, `validate_plan`,
`TaskManifest`, `Checkpoint`, workflow/profile/candidate identity,
`ElectronicStateConfig` state-sweep orchestration, legacy
`CalculationRequest`/`CalculationResult`).  Relocated scientific types are
re-exported there with identity (`A is B`); semantically changed shapes
(`StructureArtifact`, `Provenance`) are distinct types converted explicitly by
`acp.calculations.legacy_adapters`.

## 2. Entry signatures

```python
def run_singlepoint(request: TaskRequest, *, context: TaskContext | None = None) -> TaskResult: ...
def run_optimize(request: TaskRequest, *, context: TaskContext | None = None) -> TaskResult: ...
def run_frequency(request: TaskRequest, *, context: TaskContext | None = None) -> TaskResult: ...
def run_scan(request: TaskRequest, *, context: TaskContext | None = None) -> TaskResult: ...
def run_irc(request: TaskRequest, *, context: TaskContext | None = None) -> TaskResult: ...
def run_casscf(request: TaskRequest, *, context: TaskContext | None = None) -> TaskResult: ...
def run_thermochemistry(request: TaskRequest, *, context: TaskContext | None = None) -> TaskResult: ...
```

`TaskRequest` is serializable intent; `TaskContext` is runtime state and is
never serialized.  Implementations live in `cccp.calculation.tasks` (todos
17–22): all seven core tasks are executable.

## 3. TaskRequest (serializable envelope)

| Field | Type | Default | Notes |
|---|---|---|---|
| `task` | `TaskKind` | — | `singlepoint`/`optimize`/`frequency`/`scan`/`irc`/`casscf`/`thermochemistry` (P2 extends) |
| `structure` | `StructureInput \| None` | `None` | single structure; **required** except thermochemistry |
| `charge` | `int` | `0` | execution default 0 |
| `multiplicity` | `int` | `1` | execution default 1 |
| `level` | `MethodSpec` | empty | backend-independent level of theory |
| `backend` | `str \| None` | `None` | `None` = deterministic selection; **`"auto"` is rejected** (use `None`) |
| `electronic_state` | `ElectronicStateSpec \| None` | `None` | exactly **one** target state (state sweeps are pre-expanded by ACP) |
| `options` | typed options \| `None` | `None` | closed union; `None` = typed defaults; class must match `task` |
| `resources` | `TaskResources` | empty | per-task quota |
| `output_dir` | `Path \| None` | `None` | task artifact root |
| `schema_version` | `int` | `1` | serialization rule S1 |

**Platform identity ban:** `workflow`, `profile`, `candidate_id`,
`trajectory_item_id`, and `state_sweep` MUST NOT appear anywhere in a request
(no field, no options key, no metadata).  They travel on the ACP side
(`LegacyBinding` / task events).

`MethodSpec`: `method, basis, dispersion, solvent, solvent_model,
integration_grid, scf, ri_approximation, auxiliary_basis_j,
auxiliary_basis_c`.  Empty values are filled by the **translation layer**
from cccp method metadata (single source of defaults) — never by callers or
adapters.

`StructureInput`: `path | None`, `coordinates | None` (Å), `symbols | None`,
`elements`, `role` (`minimum`/`transition_state`), `source`.  At least one of
`path` or (`coordinates` + `symbols`) is required for structure tasks; when
both are present the inline geometry overrides the file at execution (legacy
`load_inputs` precedence).

`TaskResources`:

| Field | Type | Semantics |
|---|---|---|
| `nproc` | `int \| None` | cores for this task (per-task quota) |
| `mem` | `str \| int \| None` | total task memory budget: int = MB, or `"32GB"`/`"32000MB"`/`"32g"` strings |
| `maxcore` | `int \| None` | MB **per core** (ORCA `%maxcore` semantics) |
| `timeout_s` | `float \| None` | whole-task wall budget (R7) |

**Resource semantics (`mem` vs `maxcore`):** `maxcore` is per-core memory,
`mem` is the total budget.  When `nproc`, `maxcore` and `mem` are all
resolvable, `nproc * maxcore <= mem_total_mb` must hold (else
`TaskInputError`).  All values must be positive/finite.  `TaskResources` is a
**per-task quota**; batch-level concurrency/budget is separate (R5).

## 4. Seven-core typed options (task → options table ①)

Closed discriminated union; the discriminator is `task` and the registry is
`cccp.calculation.requests.TASK_OPTIONS_TYPES`.  `validate_request` rejects a
mismatched class with `TaskInputError`.

| task | options type | fields |
|---|---|---|
| `singlepoint` | `SinglePointOptions` | `stability_check: bool \| None` |
| `optimize` | `OptimizeOptions` | `mode: OptimizationMode` (`unconstrained`/`transition_state`/`constrained`), `level: MethodSpec \| None` (single theory carrier — solvent/grid/SCF/basis/dispersion/RI/aux live ONLY here), `initial_hessian: str \| None` (`model`/`calculate`/`auto`/path), `recalc_hess: int \| None`, `trust_radius: float \| None`, `max_cycles: int \| None`, `constraints: ReactionCoordinatePlan \| None`, `ts: TsSpec \| None`, `rescue: RescueSpec`, `geom_maxiter: int \| None` |
| `frequency` | `FrequencyOptions` | *(empty in v1 — numerical differentiation is not promised)* |
| `scan` | `ScanOptions` | `coordinates: tuple[ScanCoordinateSpec, ...]`, `points: int \| None`, `values: tuple[float, ...]` (explicit grid), `mode: ScanMode` (only `relaxed`; `rigid` is reserved and rejected) |
| `irc` | `IrcOptions` | `directions: tuple[IrcDirection, ...]` (`forward`/`reverse`, unique, non-empty; default both), `maxpoints: int \| None`, `step: float \| None`, `initial_hessian: str \| None` (no `ts_mode` — v3.2 §6) |
| `casscf` | `CasscfOptions` | `spec: CASSCFSpec` (required; active space + NEVPT2 selection); read-only `orbital_selection` property = `spec.orbital_selection` (no duplicated storage — `CASSCFSpec` owns the field) |
| `thermochemistry` | `ThermochemistryOptions` | `freq_log_path: Path \| None` (**required** — this is the input shape), `sp_energy_hartree: float \| None`, `temperature_k: float \| None`, `pressure_atm: float \| None`, `standard_state: str \| None` (`"1atm"`/`"1M"`; `None` = contract default `"1atm"` at execution), `scl_zpe: float \| None`, `ilowfreq: int \| None`, `imagreal: int \| None`, `conc: float \| None` |

### 4.1 P2 typed options (todo 24; execution wires in 42/43)

| task | options type | fields |
|---|---|---|
| `conformer_search` | `ConformerSearchOptions` | `energy_window: float \| None`, `gfn_level: int \| None` |
| `md_sampling` | `MdSamplingOptions` | `md_method: str \| None`, `gfn_level: int \| None`, `temperature_k`, `time_ps`, `dump_fs`, `step_fs`, `hmass: float \| None`, `shake: bool \| None`, `nvt: bool \| None`, `seed: int \| None` |
| `clustering` | `ClusteringOptions` | `edis`, `gdis`, `temperature_k: float \| None`, `nout: int \| None` |
| `censo_refine` | `CensoRefineOptions` | `preset: str \| None`, `level_overrides: tuple[CensoLevelOverride, ...]` (`part`/`func`/`basis`/`threshold`), `temperature_k: float \| None`.  **No template/rcfile text field** — template text is a translation-layer product (workflows must never assemble it) |
| `nmr_shielding` | `NmrShieldingOptions` | `atom_indices: tuple[int, ...]`, `atom_index_base: 0\|1 = 0` (explicit base, record identity) |
| `xtb_path_search` | `XtbPathSearchOptions` | `end_structure: StructureInput \| None` (**required** — the request `structure` is the start of the pair), `gfn_level: int \| None`, `uhf: int \| None`, `seed: int \| None`, `backend_inputs: tuple[BackendInputFragment, ...]` (kinds `path_inp_text`/`extra_args` only) |
| `orca_gradient` | `OrcaGradientOptions` | `backend_inputs: tuple[BackendInputFragment, ...]` (kinds `route_extras`/`extra_blocks`/`output_name` only).  `scf_convergence` lives on `level.scf`; geometry on the request `structure` |

**Scoped backend-input fragments** (`BackendInputFragment`): the only channel
for raw backend knobs (`path_inp_text`, `extra_args`, `route_extras`,
`extra_blocks`, `output_name` — closed `BackendInputKind` vocabulary).  Each
fragment records `kind`, `source` (legacy field name), verbatim `content`,
`content_digest` (sha256 over canonical JSON of the content), and
`conflict_rule` from `FRAGMENT_CONFLICT_RULES` (fixed per kind: `path_inp_text`
/`extra_args`/`route_extras`/`output_name` = `structured_fields_win`,
`extra_blocks` = `reject_on_conflict`).  A fragment is **never** a renamed
unconstrained passthrough: unknown kinds are rejected, each task accepts only
its own fragment kinds, and the rule cannot be chosen by the caller.

**Deterministic conflict behavior** (`fragment_structured_conflicts` /
`resolve_fragment_conflicts`): a fragment token listed in
`FRAGMENT_KNOB_TOKENS` that assigns a value **disagreeing** with the
structured field is a conflict (matching values are consistent duplicates and
keep the effective input identical before/after conversion).  Under
`structured_fields_win` the structured value is authoritative and the
conflicting fragment directive is ignored downstream (the fragment stays
recorded verbatim); under `reject_on_conflict` `validate_request` raises
`TaskInputError`.  Bare flags without a value count as disagreement.

**Cache signature**: `XtbPathSearchOptions.cache_signature()` /
`OrcaGradientOptions.cache_signature()` include every fragment's
`{kind, content_digest}` — the raw fragments that actually affect the
computation are part of any cache identity derived from the serialized
request.

Supporting types:

* `TsSpec(enabled: bool = False, mode_index: int | None = None)` — replaces
  legacy `ts_mode: bool|int` (v3.1 §4).  `mode_index=None` follows the lowest
  imaginary mode; an explicit index is the mapped target; `mode_index` is only
  legal with `enabled=True` (`TaskInputError` otherwise).
* `RescueSpec(policy: str = "adaptive", max_rescue: int | None = None,
  failure_type: FailureType | None = None)` — `policy` values `"adaptive"`/`"off"`
  (other legacy strings preserved verbatim); `failure_type` is the
  **caller-supplied restore input** (`None` = derive from the task's error
  classification); derived diagnostics land in
  `OptimizePayload.rescue_failure_type`/`rescue_structure_kind` — input and
  output never share one writable field.  Rescue is internal task policy
  (never cross-task orchestration).
* `ScanCoordinateSpec(atoms: tuple[int, ...], start, end, kind="distance",
  atom_index_base: 0|1 = 1)` — atom indices carry an explicit base
  (record-identity rule).

**Optimize role consistency (todo 18):** the structure role derives from
`mode`/`ts` (TS-ness = `mode=transition_state` or `ts.enabled`) and the
caller-passed `StructureInput.role` must agree — a mismatch raises
`TaskInputError`; there is no second independently-settable `structure_kind`
field.  TS-ness dispatches `transition_state_opt`, otherwise `optimize`
(`constrained_optimization` → `constrained_optimize`).  Theory fields resolve
through the single `ResolvedCalculationSpec` priority
(explicit / options / config default / method default) — `OptimizeOptions`
never keeps a second solvent/grid/SCF copy.

## 5. TaskResult (typed result envelope)

| Field | Type | Notes |
|---|---|---|
| `task` | `TaskKind` | |
| `status` | `str` | `"completed"` / `"failed"` (legacy values pass verbatim through the adapter) |
| `complete` | `bool` | `False` = partial scientific output with valid sub-items (no global `PARTIAL` status) |
| `error_kind` | `ErrorKind \| None` | closed enum: `invalid_input`, `unsupported_capability`, `backend_unavailable`, `backend_failure`, `not_converged`, `timeout`, `parse_failure`, `cancelled` |
| `errors` | `tuple[str, ...]` | stable texts; classification tags like `[SCF_FAILURE]` preserved |
| `energy_hartree` | `float \| None` | Hartree |
| `coordinates` | `tuple[tuple[float,float,float], ...] \| None` | Å |
| `symbols` | `tuple[str, ...] \| None` | atom order = `coordinates` order |
| `frequencies` | `tuple[float, ...]` | cm⁻¹ |
| `converged` | `bool \| None` | |
| `artifacts` | `tuple[ArtifactRef, ...]` | `path/type/checksum/source`; task-layer paths are **artifact-root-relative** |
| `provenance` | `Provenance \| None` | `backend/method/version/input_signature` (no profile) |
| `payload` | typed payload \| `None` | closed union, class must match `task` |
| `metadata` | `JsonObject` | read-only extras, schema-versioned; not a typed dumping ground |
| `schema_version` | `int` | `1` |

### 5.1 Seven-core typed payloads (table ① continued)

| task | payload type | fields |
|---|---|---|
| `singlepoint` | `SinglePointPayload` | `electronic_state: JsonObject \| None` (state diagnostics summary) |
| `optimize` | `OptimizePayload` | `optimization_status`, derived diagnostics `rescue_failure_type`/`rescue_structure_kind`, `rescue_actions: tuple[str,...]`, `rescue_attempts: int \| None`, `rescue_terminal: bool \| None`, `tsmode_explicit_target: int \| None`, `tsmode_target_preserved: bool \| None`, `electronic_state`, `trajectory_ref: ArtifactRef \| None` |
| `frequency` | `FrequencyPayload` | `n_imaginary: int \| None`, `freq_log_ref`, `analysis: FrequencyAnalysis \| None`, `electronic_state` |
| `scan` | `ScanPayload` | `frames: tuple[ScanFrame, ...]`, `profile_ref: ArtifactRef \| None` |
| `irc` | `IrcPayload` | `directions: tuple[IrcDirectionResult, ...]` |
| `casscf` | `CasscfPayload` | `root_energies`, `natural_occupations`, `nevpt2_energies`, `active_space: str` (projection of legacy `metadata["multireference"]`) |
| `thermochemistry` | `ThermochemistryPayload` | `enthalpy_hartree`, `gibbs_hartree`, `entropy_au`, `gibbs_source`, `standard_state` |

`ScanFrame(index, values, energy_hartree, geometry_ref, converged, success)`:
`index` is the **original** frame index (failed frames keep it and are never
renumbered); `geometry_ref` is an `ArtifactRef` (type `frame_geometry`) to the
per-frame geometry, root-relative; `values` carries the id-keyed coordinate
targets of the frame in plan order.  `ScanPayload.profile_ref` (type
`scan_profile`) references the task-written `scan_profile.json` energy
profile (`scan_profile_v1`: per-frame `index`/`progress`/`values`/
`coordinate_values`/`energy_hartree`/`converged`/`success`).  The scan task
(landed todo 20) implements the relaxed mode only — `rigid` is rejected and
`optimizer_level`/`single_point_level` are NOT in the v1 options contract;
platform products (`RESULT/structures` frame copies, the
`scan_trajectory.json` view product and `result_manifest.json` registration)
are materialised by the ACP wrapper, never by the task core.

`IrcDirectionResult(direction, energy_hartree, coordinates, symbols, converged,
steps, success, trajectory_ref)`: one entry per requested direction; one-way
runs keep the valid direction as a sub-result.

`FrequencyAnalysis(frequencies, imaginary_frequencies, ir_intensities,
mode_frequencies, mode_vectors, mode_ir_intensities)`: the parsed vibration
science returned by the frequency task (plan todo 19) — the frequency list
(cm⁻¹) and IR intensities (km/mol, `ir_intensities` aligned with
`frequencies`) plus the indexed mode maps (ORCA native mode indices, zero
modes kept in the maps).  It is produced by the single scientific parse
(`cccp.calculation.frequency_parse`); the `normal_modes.json` product
format, geometry binding and manifest registration stay ACP-side and never
require re-parsing the QC output.

Payload fields use `None`/empty as "absent" — converters only write fields
that are set (never invent keys or defaults).

### 5.2 P2 typed payloads (todo 24)

| task | payload type | fields |
|---|---|---|
| `conformer_search` | `ConformerSearchPayload` | `ensemble_ref: ArtifactRef \| None`, `conformer_count: int`, `energy_table: tuple[ConformerEnergy, ...]` (`conf_id`, `frame_index`, `energy_hartree`) |
| `md_sampling` | `MdSamplingPayload` | `trajectory_ref: ArtifactRef \| None`, `n_frames: int` |
| `clustering` | `ClusteringPayload` | `assignments: tuple[ClusterAssignment, ...]` (`cluster_id`, `representative_index`, `member_indices`), `clustered_ref: ArtifactRef \| None` |
| `censo_refine` | `CensoRefinePayload` | `records: tuple[CensoRefineRecord, ...]` (`conf_id`, `frame_index`, `energy_hartree`, `free_energy_hartree`, `weight`), `refined_ensemble_ref: ArtifactRef \| None` |
| `nmr_shielding` | `NmrShieldingPayload` | `shieldings: dict[int, NmrShielding]` — atom → `{symbol, isotropic}` key shape kept verbatim; JSON string keys restored to integer atom indices on `from_dict` |
| `xtb_path_search` | `XtbPathSearchPayload` | `trajectory_ref: ArtifactRef \| None`, `frames: tuple[XtbPathFrame, ...]` (`index`, `energy_hartree`), `start_frame_index: int \| None`, `end_frame_index: int \| None` |
| `orca_gradient` | `OrcaGradientPayload` | `gradients: tuple[tuple[float, float, float], ...]`, `energy_hartree: float \| None`, `gradient_unit` (default `"Eh/bohr"`), `gradient_convention` (default `"energy_gradient_dE_dX"` — not the force) |

## 6. Serialization rules

Rules S1–S7 apply to `TaskRequest`/`TaskResult` `to_dict()`/`from_dict()`
(strict envelope serializers in `cccp.calculation.contracts`).  The older
per-type scientific parsers (`electronic_state_spec_from_dict` etc.) keep
their legacy coercion semantics unchanged.

* **S1 schema_version:** `to_dict` emits `schema_version: 1`.  `from_dict`
  accepts a missing key as 1; any other value → `TaskInputError`
  ("unsupported schema_version").
* **S2 unknown fields:** ignored on `from_dict` (forward compatibility);
  `to_dict` emits known fields only.  Round-trip therefore drops unknown
  input keys by design.
* **S3 invalid enums:** `TaskInputError` listing allowed values (strict).
* **S4 NaN/Inf:** rejected with `TaskInputError` on both directions (JSON has
  no NaN/Inf); applies recursively to numbers inside lists/mappings.
* **S5 paths:** serialized as strings; `from_dict` accepts `str`/`Path`.
* **S6 containers:** tuples serialize as JSON lists; `from_dict` returns
  tuples; wrong container/wrong scalar type → `TaskInputError`.
* **S7 discriminator:** the `task` field selects the options/payload class
  (`TASK_OPTIONS_TYPES` / `TASK_PAYLOAD_TYPES`); a mismatched class raises
  `TaskInputError` in `validate_request` / `TaskResult.__post_init__`.

## 7. Input shapes and field applicability

| task | input shape | required | not applicable |
|---|---|---|---|
| `singlepoint`, `optimize`, `frequency`, `casscf` | single structure | `structure` | `ThermochemistryOptions.freq_log_path` |
| `scan`, `irc` | single structure | `structure` (+ coordinate/direction options) | — |
| `thermochemistry` | frequency log | `options.freq_log_path` | `structure` (rejected), inline geometry |
| `conformer_search`, `md_sampling`, `nmr_shielding`, `orca_gradient` | single structure | `structure` | `options.end_structure` |
| `clustering`, `censo_refine` | ensemble (multi-frame structure input) | `structure` (path or inline multiframe geometry) | `options.end_structure` |
| `xtb_path_search` | structure pair | `structure` (start) **and** `options.end_structure` (end) | — |

Per-task input shape / backend capability / success-partial-empty semantics /
artifact-record identity live in the code mapping table
`cccp.calculation.requests.P2_TASK_CONTRACTS` (tested against
`tests/test_cccp_calculation_contracts.py`).

## 8. Path rules

* **Relative input basis (R2):** relative input paths (`structure.path`,
  `ThermochemistryOptions.freq_log_path`, `OptimizeOptions.initial_hessian`
  paths, `CASSCFSpec.orbital_source`, `GuessSpec.orbital_source`) resolve
  against `TaskContext.input_base` (default: process CWD at execution start).
* **Artifact root:** `TaskRequest.output_dir` when set, else
  `TaskContext.workdir`, else CWD.  Task-produced `ArtifactRef.path` values
  are **relative to the artifact root**.
* **Remote mapping:** ACP re-bases the artifact root when results are pulled
  from remote nodes (see `scheduler/remote`); CCCP never writes outside the
  artifact root.
* The legacy adapter maps absolute legacy artifact paths ↔ root-relative via
  `LegacyBinding.artifact_root` (paths outside the root pass through
  verbatim; an already-relative legacy path is treated as root-relative).

## 9. Identity quarantine — field-level mapping table

| field / identity | home | reason |
|---|---|---|
| single-structure geometry, `StructureRole`, elements | cccp `contracts` | scientific |
| `ElectronicStateSpec` + guess/spin/wavefunction/diagnostics/gate | cccp `contracts` | scientific (single state) |
| `OptimizationSpec`/`OptimizationMode`, `CASSCFSpec` | cccp `contracts` | scientific single-run params |
| `ArtifactRef`, task `Provenance` (no profile) | cccp `contracts` | scientific artifact refs |
| `workflow` | **ACP** (`LegacyBinding.workflow`, plans) | platform orchestration |
| `profile` | **ACP** (`LegacyBinding.profile`, legacy `Provenance.profile`) | platform |
| `candidate_id` | **ACP** (`LegacyBinding.candidate_id`, legacy `StructureArtifact`) | platform identity |
| `trajectory_item_id` | **ACP** (`LegacyBinding.trajectory_item_id`) | platform identity |
| `state_sweep` / state-set `ElectronicStateConfig` | **ACP** (batch pre-expansion) | platform orchestration |
| `StepKind`/`CalculationStep`/`CalculationPlan`/`validate_plan` | **ACP** | workflow planning |
| `TaskManifest`/`Checkpoint` | **ACP** | platform persistence |
| `reaction_id`/`plan_sha256`/`request_sha256`/`config_digest`/`adapter_version`/`schema_version` (pes2ts) | **ACP** (`LegacyBinding.platform_identity`) | platform identity — never a cccp request field |

CCCP may own **local scientific record numbers** (scan frame indices, IRC
direction entries, attempt counters); only ACP platform identity is
quarantined.

## 10. Unified failure / partial-result / record-identity contracts

1. **Pre-launch errors are raised**, never returned: `TaskInputError`
   (invalid request/options), `UnsupportedCapabilityError` (capability not
   declared/implemented), `BackendUnavailableError` (missing binary).
2. **Scientific/runtime failures return structured results:** QC
   non-convergence, timeout, parse failure → `status="failed"` with
   `error_kind` (`not_converged`/`timeout`/`parse_failure`/`backend_failure`)
   and **valid partial artifacts kept**.
3. **Partial results:** no global `PARTIAL` status.  Partial output is
   `complete=False` + valid sub-items (scan frames / IRC directions with
   original indices).  Overall status rule: `completed` iff **every** requested
   unit succeeded; otherwise `failed` + `complete=False` with the valid
   sub-results retained.  ACP maps this explicitly per workflow.
4. **Progress-callback failure ≠ scientific failure:** sink exceptions are
   isolated by `TaskContext.emit_progress` (recorded in
   `context.progress_errors()`), never change `status`/`error_kind`.
5. **Publication failure is an ACP concern:** a scientific success stays a
   scientific success even if ACP-side persistence fails (persistence layer
   handles retry; todo 16).
6. **Internal programming errors keep diagnosable exceptions:** they are
   never swallowed into failed results.
7. **Record identity:**
   * atom indices carry an explicit base (`GuessSpec.atom_index_base`,
     `ScanCoordinateSpec.atom_index_base`; `orca_flip_atoms()` converts to
     ORCA 0-based at render time);
   * scan/IRC failed frames keep their **original** index (never renumbered);
   * gradient/vibration data carries unit + shape + atom order
     (`FrequencyPayload.analysis` carries the vectors; `normal_modes`
     products built from it bind symbols/atom order);
   * NMR JSON integer-key restoration: `NmrShieldingPayload.shieldings` keeps
     the atom → `{symbol, isotropic}` key shape; JSON string keys restore to
     integer atom indices (`from_dict`), and `NmrShieldingOptions.atom_index_base`
     declares the index base (todo 24);
   * CENSO ordering/filtering ↔ original conformer mapping: every
     `CensoRefineRecord` carries `conf_id` + original `frame_index`; the
     `ConformerSearchPayload.energy_table` rows keep original conformer
     indices likewise (todo 24);
   * P2 trajectory/frame payloads (`MdSamplingPayload`, `XtbPathSearchPayload`)
     keep original frame indices (never renumbered);
   * `OrcaGradientPayload.gradients` carry unit + convention + input atom
     order (row *i* = input atom *i*, Eh/bohr, energy gradient dE/dX);
   * CCCP local scientific record numbers are legitimate; ACP platform
     identity is not.

## 11. TaskContext runtime rules

* **R1 `context=None` acquisition:** `resolve_context(request, None)` returns
  `TaskContext(workdir=request.output_dir or CWD, input_base=CWD,
  config=None)`; `config=None` means the execution layer loads config at call
  time.
* **R2 relative input basis:** see §8.
* **R3 artifact cleanup:** the task may clean only its own scratch files under
  the workdir; rescue/intermediate artifacts referenced by results are kept;
  CCCP never deletes caller data (platform retention is ACP's).
* **R4 no overwriting:** repeated calls, rescue attempts, and batch items must
  not overwrite pre-existing outputs — rescue attempts use fresh attempt
  directories; batch items get distinct output dirs (ACP item naming).
* **R5 per-task quota vs batch budget:** `TaskResources` is one task's quota;
  a batch executor must not reuse the same `nproc` as both concurrency and
  per-entry cores (total usage = concurrency × per-entry cores is verified by
  the batch layer, todo 12).
* **R6 progress thread safety:** sinks may be reached from multiple threads;
  `TaskContext.emit_progress` serialises sink calls under a lock and isolates
  sink exceptions (recorded, logged, never raised).
* **R7 timeout scope:** `resources.timeout_s` (or `context.timeout_s`
  override) bounds the **whole** task including all rescue attempts;
  per-attempt timeouts are not separately configurable in v1.
* **R8 cancellation:** `context.is_cancelled()` is polled before each pending
  unit (rescue attempt / scan point / IRC direction).  On cancellation no new
  unit starts, running child processes are terminated (process group), and the
  result is `status="failed"`, `error_kind="cancelled"` with valid partials.
* **R9 scientific events only:** progress carries scientific events; ACP
  converts them to LiveMetric display metrics.  UI fields (`label_key`,
  `priority`, display ordering) never enter CCCP.
* **R10 errors vs exceptions:** rule set §10.
* **R11 `context.backend` (runtime seam, todo 17):** an already-resolved
  backend *instance* supplied by a legacy caller (e.g.
  `run_prepared_frames(backend, …)`).  Selection still runs semantically;
  the runtime precheck is skipped (the caller vouches for the instance) and
  the instance is what executes.  `None` = registry acquisition after
  selection.
* **R12 `context.capability_extras` (runtime seam, todo 17):** verbatim
  legacy capability kwargs (`output_name`, `scf_maxiter`, `route_extras`, …)
  handed to the translation entry unchanged until the full translation-layer
  cleanup (todo 25).

Translation-layer minimal public entry (todo 17): `resolve_spec`
(`ResolvedCalculationSpec`, single resolution point) + `render_backend_input`
(explicit request values pass through **verbatim**; absent values are never
invented — method-inherent defaults are materialised by the backend input
renderer from the same cccp method metadata; the pre-migration goldens
freeze exactly these effective parameters).

## 12. Progress events

`ProgressEvent(kind, stage, metric, value, unit, message, index)` with
`kind ∈ {stage_started, stage_completed, stage_failed, metric, message}`.
`metric` uses stable scientific names (`energy_hartree`, `cycle`, `s2`,
`scan_point`, …) and `unit`; `index` carries the original scientific record
number when relevant (§10.7).  Sink protocol: `emit(event) -> None`.

## 13. Legacy adapter mapping tables

`acp.calculations.legacy_adapters` converts the ACP compatibility contracts ↔
task envelopes.  **Data transform only:** no file writes, no platform
publication, no default filling.  Platform identity + verbatim residue travel
on `LegacyBinding` (ACP-side; passed in by the caller and returned with the
result).

API: `to_task_request(request, task, *, directions=None) -> (TaskRequest,
LegacyBinding)`, `to_legacy_request(task_request, binding) -> CalculationRequest`,
`to_task_result(result, task, *, binding=None) -> (TaskResult, LegacyBinding)`,
`to_legacy_result(result, binding) -> CalculationResult`.

### 13.1 Request keys (legacy `resources` → typed homes)

| legacy key | typed home | notes |
|---|---|---|
| `backend` / `engine` | `TaskRequest.backend` | alias preserved (`resources_key_names`) |
| `basis` | `level.basis` | |
| `method` (top-level) | `level.method` | wins over `resources["method"]`; original kept in `binding.legacy_method` |
| `method` (resources) | `level.method` only when top-level empty | otherwise verbatim residue |
| `charge` / `multiplicity` | `charge` / `multiplicity` | presence-gated (absent keys never re-emitted) |
| `coordinates` (geometry matrix) | `structure.coordinates` | raw form preserved |
| `symbols` | `structure.symbols` | raw form preserved |
| `nproc` / `mem` / `maxcore` / `timeout_s` | `TaskResources.*` | `mem` keeps int/str form |
| `output_dir` | `TaskRequest.output_dir` | also `binding.artifact_root` |
| `result_dir` | `binding.artifact_root` fallback | raw-preserved |
| `config` | `binding.config` (context-side) | raw-preserved |
| `electronic_state` | `TaskRequest.electronic_state` (single state) | raw-preserved; `state_sweep` → `TaskInputError` (must be pre-expanded) |
| `structure_kind` | `OptimizeOptions.mode` derivation (`"ts"` → `transition_state`) | raw-preserved; role derives mode when absent; no independent `structure_kind` field remains |
| `ts_mode` | `OptimizeOptions.ts` (`TsSpec`) | raw-preserved |
| `opt_rescue_policy` / `opt_max_rescue` / `failure_type` | `OptimizeOptions.rescue` | |
| `initial_hessian`/`opt_initial_hessian`, `trust_radius`/`opt_trust_radius`, `recalc_hess`/`opt_recalc_hess`, `geom_maxiter`, `max_cycles` | `OptimizeOptions.*` | alias preserved |
| `stability_check` | `SinglePointOptions.stability_check` (task=singlepoint) | other tasks: verbatim residue |
| `scan_points` | `ScanOptions.points` | |
| `scan_coordinates` / `coordinate` / `coordinates` (non-geometry) | `ScanOptions.coordinates` | raw-preserved (legacy string form) |
| `scan_plan` | `ScanOptions.coordinates` + `points` | raw-preserved (coupled grids stay ACP/PES) |
| `freq_log_path`, `sp_energy_hartree`, `temperature`, `pressure`, `standard_state`, `scl_zpe`/`scale_factor`, `ilowfreq`, `imagreal`, `conc` | `ThermochemistryOptions.*` | `temperature`→`temperature_k`, `pressure`→`pressure_atm` |
| `casscf` | `CasscfOptions.spec` | raw-preserved |
| `directions`, `maxpoints`, `step` | `IrcOptions.*` | out-of-band directions passed via `directions=` |
| `trajectory_item_id` | `binding.trajectory_item_id` | platform identity (never a request field) |
| *(anything else)* | `binding.resources_extra` | verbatim residue (lossless) |

### 13.2 Result metadata (legacy `metadata` → typed payloads)

| legacy key | typed home | notes |
|---|---|---|
| `optimization_status` | `OptimizePayload.optimization_status` | |
| `rescue_failure_type` (alias `failure_type`) | `OptimizePayload.rescue_failure_type` | derived diagnostic |
| `rescue_structure_kind` / `rescue_actions` / `rescue_attempts` / `rescue_terminal` | `OptimizePayload.*` | derived diagnostics; emitted only when the rescue plan was built |
| `tsmode_explicit_target` / `tsmode_target_preserved` | `OptimizePayload.*` | explicit mapped TS target diagnostics |
| `electronic_state` | `*.electronic_state` (sp/opt/freq payloads) | verbatim JsonObject |
| `n_imaginary` | `FrequencyPayload.n_imaginary` | |
| `multireference` / `casscf` | `CasscfPayload.*` via `results.casscf_payload_from_multireference` (single mapping: rebuild shape wins when present; else production shape) | raw-preserved (raw wins on rebuild) |
| `enthalpy_hartree`, `gibbs_hartree`, `entropy_au`, `standard_state` | `ThermochemistryPayload.*` | |
| `selected_gibbs_source` | `ThermochemistryPayload.gibbs_source` | alias preserved |
| *(anything else)* | `binding.metadata_extra` | verbatim residue |

### 13.3 Envelope mapping and task-only fields

`energy↔energy_hartree`, `coords↔coordinates`, `frequencies↔frequencies`,
`artifacts↔artifacts` (root-relative mapping), `status↔status`,
`errors↔errors`, `provenance↔provenance` with `profile` ⇄ `binding.profile`.

**Task-only fields (not representable in legacy results; documented
exclusions from the round-trip):** `symbols`, `complete`, `converged`,
`error_kind`, and typed payload detail without legacy homes
(`ScanFrame` lists, `IrcDirectionResult`, `trajectory_ref`/`freq_log_ref`/
`FrequencyPayload.analysis`/`profile_ref`).

**Round-trip guarantee:** for committed legacy fields with canonical values,
`to_legacy_request(*to_task_request(req)) == req` and
`to_legacy_result(*to_task_result(res)) == res` exactly — absent keys stay
absent (no invented defaults), alias key names are restored, candidate/profile
come back through the binding.  Documented coercions: numeric strings →
numbers for typed scalar homes; `Path` normalisation for path homes.

### 13.4 pes2ts → CCCP field-level conversion (todo 24)

`acp.calculations.legacy_adapters.pes2ts_xtb_path_to_task_request` /
`pes2ts_orca_gradient_to_task_request` convert the frozen `pes2ts_*_v1`
requests (old CLI schema stays ACP-side; the cccp task schema never
references `pes2ts_*` names).  Field-level tables:
`PES2TS_XTB_PATH_CONVERSION` / `PES2TS_ORCA_GRADIENT_CONVERSION`
(`Pes2tsConversionRow(source, category, target, notes)`).

| category | fields (xtb path / orca gradient) | target |
|---|---|---|
| `platform_identity` | `schema_version`, `reaction_id`, `request_sha256`, `config_digest`, `adapter_version`, `plan_sha256` / `schema_version`, `request_sha256` | `LegacyBinding.platform_identity` (ACP only — never a cccp request field) |
| `scientific` | `start_xyz_text`, `end_xyz_text`, `charge`, `multiplicity`, `gfn_level`, `uhf`, `seed`, `threads`, `timeout_seconds` / `coordinates`, `symbols`, `charge`, `multiplicity`, `method`, `basis`, `scf_convergence`, `nproc`, `timeout_seconds` | `structure` / `options.end_structure`, `charge`, `multiplicity`, `options.*`, `level.*` (`method`/`basis`/`scf`), `resources.nproc`, `resources.timeout_s` |
| `raw_fragment` | `path_inp_text`, `extra_args` / `route_extras`, `extra_blocks`, `output_name` | scoped `BackendInputFragment` entries (verbatim content + `content_digest` + `source` + `conflict_rule`) |

**Equivalence guarantee:** the effective input is identical before and after
conversion — no defaults filled, no fragment rewritten, `threads`→`nproc` /
`timeout_seconds`→`timeout_s` renames are 1:1 value carries.  Conflicts
between fragments and structured fields follow the deterministic rules of
§4.1 (agreeing duplicates stay valid; disagreements resolve per the
fragment's fixed `conflict_rule`).  Cache signatures include fragment content
digests (§4.1).

## 14. Two tables (explicitly separated)

**Table ① — task → options/payload types (complete in todo 11; P2 rows in
todo 24, §4.1 + §5.2):** see §4 and §5.  Serialization and adapter
conversion are fully testable against this table.

**Table ② — task → execution function (NOT part of todo 11):**

| task | execution | todo |
|---|---|---|
| `singlepoint` | `run_singlepoint` | 17 |
| `optimize` | `run_optimize` | 18 |
| `frequency` | `run_frequency` | 19 |
| `scan` | `run_scan` | 20 |
| `irc` | `run_irc` | 21 |
| `thermochemistry` | `run_thermochemistry` | 22 |
| `casscf` | `run_casscf` | 22 |
| batch/cache | `cccp.calculation.batch` | 12 |
| P2 seven (contracts only in 24) | `run_*` | 42–43 |

This draft defines the contract shapes; it does **not** claim any task is
executable.

## 15. Two-step backend selection (todo 13)

Entry shapes (both covered by `tests/test_cccp_calculation_selection.py`):

```python
def select_semantic(request: TaskRequest) -> BackendSelection: ...
def precheck_runtime(selection: BackendSelection, context: TaskContext | None = None) -> BackendSelection: ...
def select_backend(request: TaskRequest, *, context: TaskContext | None = None) -> BackendSelection: ...
def select_capability(capability: str, *, backend: str | None = None,
                      also_required: Sequence[str] = (), task: TaskKind | None = None) -> BackendSelection: ...
```

`select_backend(request, *, context)` ≡ `precheck_runtime(select_semantic(request), context)`.

**Step ① — semantic selection.** Capability determination input = task kind
+ scientific options + method/electronic-state requirements → required
capability + constraints → implementing backends → required software.  The
derivation is option-driven, not a plain task→backend map:

| request shape | required capability |
|---|---|
| `singlepoint` | `single_point` |
| `optimize` (unconstrained) | `geometry_optimization` |
| `optimize` (`mode=transition_state`, `ts.enabled`, or structure role `transition_state`) | `transition_state` |
| `optimize` (`mode=constrained`) | `constrained_optimization` |
| `frequency` | `frequency` |
| `scan` (relaxed, single drive coordinate) | `relaxed_scan` |
| `scan` (relaxed, multi-coordinate/constraint plan) | `constrained_relaxed_scan` |
| `scan` (rigid — reserved in v1) | `rigid_scan` (declared, no implementer yet) |
| `irc` | `irc` |
| `casscf` | `casscf`; `+nevpt2` when `dynamic_correlation != none` |
| `thermochemistry` | `thermochemistry` |
| `conformer_search` | `conformer_search` |
| `md_sampling` | `md_sampling` |
| `clustering` | `clustering` |
| `censo_refine` | `censo_refine` |
| `nmr_shielding` (GIAO) | `nmr_shielding` |
| `xtb_path_search` | `xtb_path_search` |
| `orca_gradient` (EnGrad) | `orca_gradient` |

The declarative task→capability vocabulary lives in
`cccp.backends.matrix.TASK_CAPABILITY_MAP` (seven core + P2 kinds); the
deterministic ambiguity order lives in
`cccp.backends.matrix.CAPABILITY_BACKEND_PRIORITY` (orca/xtb pinned for the
core; P2 pins: `conformer_search` = crest > censo > molclus, resolving the
CensoBackend/CREST/Molclus ambiguity; `clustering` = isostat > external;
single-implementer rows for the rest).  An explicit
`request.backend` is honored strictly: if that backend does not declare the
capability the call raises `UnsupportedCapabilityError` and never substitutes
another declaring backend.

**Step ② — runtime precheck.** Checks the selected backend's required
programs against **the context passed to this call** only.  A pin
(`executables.<name>.path` in `context.config`) is authoritative and checked
strictly as an executable file — no PATH/env fallback for a pinned program;
without a pin the environment chain of `cccp.software.resolve_executable`
applies.  `context=None` (or `config=None`) is supported and means "no
configured pins": availability is judged from the environment chain only.
Selection **never** reads global/default configuration — there is no
`load_config` fallback inside the selector, so the precheck and the executor
can never diverge on which config they use.

**Selection record** (`BackendSelection`): `task`, `capability`,
`also_required`, `backend`, `reason`, `params` (final effective selection
parameters: method/basis/charge/multiplicity/electronic state + derived
option constraints), `required_programs` (`ProgramRequirement(name,
configured_path, resolved_path, available, source)`), `explicit_backend`,
`candidates`, `runtime_checked`; `to_dict()` is the provenance/debug form.
Step-① records list program names with `available=None` (unchecked);
`precheck_runtime` fills availability and sets `runtime_checked=True`.

**Error taxonomy at selection time** (all pre-launch): `TaskInputError` for
invalid envelopes (incl. `backend="auto"` and unknown backend names),
`UnsupportedCapabilityError` for unknown capabilities or capabilities with no
declaring/implementing backend, `BackendUnavailableError` when a required
program is missing in the given context (an explicit ORCA request without
ORCA surfaces here — never as a silent switch to xTB).

**Not executable yet:** selection success means a backend method is
implemented and its binary is present — it does **not** mean a public task
entry (`run_*`) is callable; Table ② owns that transition.
