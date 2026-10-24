# ACP2 cross-domain compatibility: NMR and ensemble thermochemistry

**Status:** delivered by A-24a/A-24b (plan todo 43/44). **Contract set:** `acp2.science` version 1.

This document states the unit, mapping, reference and version rules two science
domains must obey when they share the ACP2 evidence/provenance contract. It is
normative for the two bound capabilities:

| Domain | Capability | Kind | Implementation seam |
| --- | --- | --- | --- |
| NMR | `nmr.shielding` | compute | `cccp.calculation.tasks.nmr_shielding.run_nmr_shielding` |
| NMR | `nmr.compare` | pure analysis | `cccp.analysis.nmr.comparison.compare_nmr_shielding` |
| Thermochemistry | `thermochemistry.ensemble` | pure analysis | `cccp.analysis.ensemble.science.build_ensemble_thermochemistry` |

The A-05b built-in catalog and the `handoff-H2.json`…`handoff-H5.json` digest
chain stay frozen at their delivered scope. These three descriptors live in the
explicit domain extension catalog (`cccp.calculation.capabilities.domains`) and
are selected with `domain_registry()`.

## 1. Shared evidence contract

Both domains emit the same frozen `EvidenceReport` (`evidence.schema.json`,
schema version 1) and `ScientificProvenance` (`provenance.schema.json`, schema
version 1). For both domains:

- execution facts (`terminal_state`, `argv`, `exit_code`) are separate from
  claim status; a completed analysis never implies `supported`;
- every `supported`/`refuted` claim carries an admissible `check.*` validator
  and at least one digested source artifact;
- partial evidence is `inconclusive` (or `not_evaluated` when no check ran) with
  an explicit missing check;
- limitations are report-level records referenced by claim id;
- source references are relative paths with bare lowercase sha256 digests and
  are verified with `verify_claim_sources(report, read_root)`.

## 2. Unit rules

| Quantity | Domain | Unit | Rule |
| --- | --- | --- | --- |
| Isotropic shielding | NMR | ppm | Recorded verbatim from the shielding table; never converted to a shift without a reference. |
| Computed/experimental chemical shift | NMR | ppm | `δ = (σ_TMS − σ) / (1 − σ_TMS/10⁶)` via the existing averaging seam; residuals and regressions stay in ppm. |
| Temperature | both | K | Positive finite value; NMR records the Boltzmann/reference temperature, thermochemistry records the ensemble temperature. |
| Gibbs energy | thermochemistry | Hartree | Supplied and reported in Hartree. |
| Derived Gibbs energy | thermochemistry | kcal/mol | Only via the existing `HARTREE_TO_KCAL` constant; the derived unit is recorded (`derived_energy_unit=kcal_per_mol`). |
| Mixing entropy | thermochemistry | kcal/(mol·K) | Only via the existing `s_mix_kcal_per_mol_kelvin` function. |
| T·S_mix | thermochemistry | kcal/mol | Only via the existing `t_s_mix_kcal_per_mol` function. |

Cross-domain rule: a quantity from one domain is never added to, subtracted
from, or compared with a quantity from the other. There is no ppm↔Hartree
conversion and no cross-domain derived number. A claim that needs both domains
must be expressed as two claims with separate units and scopes.

## 3. Mapping rules

### NMR atom mapping

- The mapping is ordered: `(atom key, element, declared label, shielding)` in
  structure order, with a declared `atom_index_base`.
- `atom_mapping_digest` is the sha256 of that ordered key. Because the shielding
  value is part of each row, a permutation of two atoms of the same element —
  where the element/label sequence is unchanged — still changes the key.
- A comparison may pin the expected digest. A mismatch raises the typed
  `NmrAssignmentInvalidatedError`; binding preflight reports
  `atom_order_unchanged=false` and refuses execution. The old assignment is
  never reused.
- Labels are unique and non-blank. A signal whose emitted label disagrees with
  the declared label at its atom index raises `NmrAtomMappingError`.
- `atom_index_base` is part of the mapping identity; a table re-keyed from
  0-based to 1-based without declaring the new base is invalid.

### Thermochemistry component mapping

- Every component has a unique `component_id`. Weights attach to ids, not to a
  sorted position; sorting by Gibbs value only selects the rank-1 component.
- The supplied weight vector is parallel to the supplied component list and is
  recorded in that order; the normalized vector is recorded with the same order.
- A component without a weight or a Gibbs value is listed in
  `components_missing`; it is never dropped from the report and no partial total
  is presented as the ensemble total.

### Cross-domain mapping rule

NMR atom labels and thermochemistry component ids are different identity
spaces. A label such as `C1` is not a component id, and a component id is not an
atom label; neither domain may consume the other's mapping.

## 4. Reference rules

### NMR

- Required: method, basis, solvent/solvent model, TMS reference per required
  nucleus, scaling mode (`goodman` or `ols`) and error-model identity.
- A required nucleus without a TMS reference produces
  `reference_state_unspecified` and an `inconclusive` report. There is no
  gas-phase or placeholder substitution for a missing required reference.
- The reference source and values are recorded in provenance; the reference
  state label is `TMS:<source>` only when every required nucleus is bound.
- A solvent/model change is a new reference condition and requires a new
  comparison; an old assignment/calibration is not reused across it.

### Thermochemistry

- Required: a positive finite temperature and a declared standard state
  (`1atm` or `1M`). The supplied token is normalized through the existing
  `normalize_standard_state`; the supplied and normalized forms are both
  recorded.
- An unspecified standard state produces `standard_state_unspecified` and a
  `not_evaluated` conditions claim; the normalized default is recorded but not
  claimed.
- The ensemble protocol records the weight mode, tolerance, supplied sum and
  normalization factor; a weight-set change is a new condition and requires a
  new evaluation.

Cross-domain rule: NMR reference conditions do not define a thermochemistry
standard state, and a thermochemistry standard state does not define an NMR
reference. A combined study records both condition sets separately.

## 5. Version rules

| Identity | Version | Rule |
| --- | --- | --- |
| Capability contract | `acp2.science.capability` v1 | Unknown major versions are rejected and never executed. |
| Evidence report | schema version 1 | Only version 1 is readable; a new major requires a new reader. |
| Provenance | schema version 1 | Same closed-reader rule; pure analysis forbids quantum method/basis/state fields. |
| NMR comparison algorithm | `NMR_COMPARISON_VERSION = "1"` | Recorded in `provenance.derivation.algorithm_version`; a semantic change bumps it. |
| Ensemble weight policy | `ENSEMBLE_WEIGHT_POLICY_VERSION = "1"` | Recorded in provenance and in the weight record; a policy change bumps it. |
| Model assets | content-addressed `sha256:<digest>` | Covers the six NOTICE-listed files; any asset change changes the version and invalidates reuse. |
| Domain extension catalog | `DOMAIN_EXTENSION_VERSION = 1` | Lives outside the frozen built-in catalog; the built-in snapshot is never rewritten. |

Additional rules:

- Unknown or unsupported fields are rejected for execution; historical payloads
  are read-only and never become execution- or recovery-eligible.
- A changed model-asset version, algorithm version or contract major is
  conservative: reuse is refused and the path is re-evaluated.
- Provenance identity never includes job id, queue, account, display name or
  permission; those are platform facts, not science identity.

## 6. Missing, partial and corrupt data

| Case | NMR result | Thermochemistry result |
| --- | --- | --- |
| Valid, complete | `supported`; all claims supported | `supported`; all claims supported |
| Missing model asset | `unavailable`; `nmr.model_assets=not_evaluated` + `artifact_missing` | not applicable (no model asset) |
| Missing required reference | `inconclusive`; `reference_state_unspecified` | `inconclusive`/`not_evaluated`; `standard_state_unspecified` |
| Partial peak/signal coverage | `inconclusive`; `partial_output`; unmatched entries listed | — |
| Missing component weight/Gibbs | — | `incomplete`; `components_missing`; no total |
| Corrupt atom mapping/label | typed `NmrAtomMappingError` | — |
| Duplicate component id | — | typed `WeightPolicyError` |
| Non-finite shielding/Gibbs/weight | typed input/`WeightPolicyError` | typed `WeightPolicyError` |
| Weights outside normalized tolerance | — | typed `WeightPolicyError`; never silently renormalized |

No case maps a missing or partial input to `supported`. Negative tests assert
the specific typed error or missing check; a validator never normalizes,
coerces or silently fills the invalid value.

## 7. Non-mixing rule

A claim from one domain cannot be used as evidence in the other. The shared
contract shares *shape and status semantics*, not scientific meaning: an NMR
`nmr.calibration` claim does not support a thermochemistry total, and a
thermochemistry `total_gibbs` claim does not support an NMR assignment.
Downstream consumers must filter claims by `claim_id` namespace and by
`scope.subject`; the two namespaces (`nmr.*` and `thermochemistry.*`) are
disjoint.
