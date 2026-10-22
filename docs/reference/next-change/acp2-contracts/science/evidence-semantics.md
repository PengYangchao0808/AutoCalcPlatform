# ACP2 science evidence semantics (A-03a)

Status: **frozen contract prose for schema_version 1**.
Owner: A (science track). Consumers: B (platform binding/storage) and C (evidence views).
This document is normative for the three sibling JSON Schemas:

| File | `$id` | Freezes |
|---|---|---|
| `evidence.schema.json` | `https://acp.local/acp2/science/evidence.schema.json` | per-claim status, scope, validator, source refs, missing checks, limitations; execution facts kept separate |
| `provenance.schema.json` | `https://acp.local/acp2/science/provenance.schema.json` | derivation for `electronic_structure` vs `pure_analysis`, software version, method/basis/state vs algorithm/input digest, declared conditions |
| `recipe.schema.json` | `https://acp.local/acp2/science/recipe.schema.json` | finite scientific DAG: stable steps, capability/task-kind references, typed upstream selectors, edges, required/diagnostic-only flags, validators, applicability, bounded alternatives |

All three use `$schema = https://json-schema.org/draft/2020-12/schema`, integer `schema_version = 1`, standard keywords only, and nullability through `type: ["…", "null"]` (never through omission).

## 1. Identity primitives reused from `cccp.analysis.identity`

The science contracts do **not** define a second content-identity or fingerprint algorithm. They reuse the frozen vocabulary of `src/cccp/analysis/identity.py`:

| Primitive | Location | Contract use |
|---|---|---|
| `content_sha256(path, location_root=None)` | `src/cccp/analysis/identity.py:64-77` | Returns the **bare lowercase sha256 hex** of file bytes. This is the value inside every `digest` object (`algorithm: "sha256"`, `value` = that hex string, no `sha256:` prefix). |
| `IDENTITY_SCHEMA = 2` | `src/cccp/analysis/identity.py:43` | The historical identity schema number; it is not the same namespace as the new science `schema_version`. |
| `FINGERPRINT_PREFIX = "v2:"` and `identity_fingerprint(payload)` | `src/cccp/analysis/identity.py:44,118-126` | `"v2:" + sha256(canonical_json)[:32]`. Optional `science_identity` fields in the evidence subject, provenance and recipe accept only this shape (`^v2:[0-9a-f]{32}$`). |
| `_EXECUTION_DOMAIN_KEYS` | `src/cccp/analysis/identity.py:52-54` | `output_dir`, `config`, `nproc`, `mem`, `maxcore`, `timeout_s` never enter the science identity. The evidence/provenance contracts keep execution facts in `execution` / optional execution paths, never in claim or derivation identity. |
| `ScientificIdentity` payload (`plan_identity`, `step_identities`, `items`, `steps`, `path_remaps`) | `src/cccp/analysis/identity.py:133-155` | The recipe's optional `science_identity` and the evidence subject's optional `science_identity` are opaque `v2:` fingerprints of that two-layer identity. Recovery truth stays `plan_identity` / `step_identity`; this schema does not recompute or replace it. |

`ArtifactRef` (`src/cccp/calculation/contracts.py:95-106`) and `Provenance` (`:108-119`) remain the calculation-layer types. The science contracts **adapt** them: `ArtifactRef.path/type/checksum` maps to `relative_path/role/digest`, and `Provenance.backend/method/version/input_signature` maps to `software/derivation/input`. No second `ArtifactRef` or `Provenance` implementation is introduced in code by this contract.

## 2. The four claim statuses (frozen enum)

`evidence.schema.json::$defs.ClaimStatus.enum` is exactly:

```json
["supported", "refuted", "inconclusive", "not_evaluated"]
```

Any other value — including `confirmed`, `passed`, `failed`, `unknown`, `true` — is rejected by the enum. The four statuses mean:

| Status | Meaning | Schema-enforced evidence floor |
|---|---|---|
| `supported` | The cited evidence affirms the claim within its declared scope. | Requires a `validator` with id+version and at least one `source_refs` entry; each source ref carries a sha256 digest and explicit units. |
| `refuted` | The cited evidence contradicts the claim within its declared scope. | Requires a `validator` with id+version and at least one digested `source_refs` entry. A refutation without evidence is not honest. |
| `inconclusive` | Evidence exists but is insufficient, partial or conflicting; neither support nor refutation is claimed. | No positive floor, but missing/partial checks must be listed in `missing_checks`. |
| `not_evaluated` | The check was not attempted, or its prerequisite was absent. | Requires at least one explicit `missing_checks` entry; a bare `not_evaluated` is rejected. |

`missing_checks` is always present (possibly empty) so that "nothing was missing" is an explicit statement rather than an omission.

## 3. Execution terminal state is not claim status

Execution facts live **only** in `EvidenceReport.execution`:

```json
{
  "terminal_state": "completed | failed | cancelled | not_started",
  "argv": ["..."],
  "exit_code": 0,
  "started_at": null,
  "finished_at": null
}
```

Claim objects have `additionalProperties: false` and no execution field. Consequently:

- A claim cannot carry `exit_code`, `terminal_state`, `argv`, `stdout` or a job result.
- A process that **completed** may still yield `inconclusive` or `refuted` claims. This is the canonical `completed-but-inconclusive` case.
- A process that **failed** never upgrades a claim to `supported`; it normally yields `not_evaluated` or `inconclusive` plus a blocking missing check.
- `terminal_state` is never read as a proxy for scientific support, and no mapping `completed → supported` exists in the contract.

The evidence report is a science record. Job/attempt/queue/account/price state belongs to the platform binding layer (track B) and is not restated here.

## 4. Source references, digests and explicit units

Every `ArtifactSourceRef` (used by claims) requires:

| Field | Rule |
|---|---|
| `artifact_path` | **Relative** to the run/evidence root. Absolute paths and `..` traversal are rejected by pattern. |
| `digest` | Object `{ "algorithm": "sha256", "value": "<64 lowercase hex>" }`. The value is exactly `content_sha256()` output; no prefix, no uppercase. A missing digest or digest mismatch cannot support a claim. |
| `role` | Scientific role of the artifact, e.g. `native_output`, `standardized_product`, `input_snapshot`, `irc_forward`, `irc_reverse`. |
| `units` | **Required field, explicitly declared.** A unit string (`hartree`, `kJ.mol-1`, `cm-1`, `angstrom`, `kelvin`, …) for numeric quantities. `null` is allowed only when the artifact is not a numeric quantity (e.g. a geometry log used qualitatively). It is never omitted. |
| `quantity` | Optional name of the extracted quantity. |

Host/platform locations are deliberately absent: the same rule as `_EXECUTION_DOMAIN_KEYS` keeps `output_dir`, `nproc`, `mem`, `maxcore`, `timeout_s` and config out of scientific identity.

## 5. Validator admissibility (model text is not a validator)

A validator is a machine-checkable or human-reviewable check, not a narrative:

```json
{
  "validator_id": "check.imaginary_mode_count",
  "validator_version": "1.0.0",
  "kind": "deterministic | software | human_review"
}
```

The schema enforces:

- `validator_id` matches `^(?:check|software|human)\.[a-z0-9][a-z0-9_.-]*$`.
- `kind` is one of `deterministic`, `software`, `human_review`; there is **no** `model`, `llm` or `prompt` member.
- `kind` and the id namespace agree (`deterministic` → `check.*`, `software` → `software.*`, `human_review` → `human.*`).
- `validator_version` is a non-empty version token.

Therefore free-form model output, an LLM answer, or a string such as `"the model says the barrier is low"` can never occupy the `validator` slot. `supported` and `refuted` claims must carry an admissible validator id+version.

## 6. Missing-check vocabulary (frozen)

`evidence.schema.json::$defs.MissingCheckId.enum` is the only accepted vocabulary:

| Check id | Meaning |
|---|---|
| `artifact_missing` | A required artifact was never produced or cannot be found. |
| `artifact_digest_mismatch` | The artifact exists but its digest differs from the declared digest. |
| `partial_output` | The calculation output is truncated or incomplete. |
| `missing_irc` | The IRC evidence is absent. |
| `irc_not_bidirectional` | Forward/reverse IRC did not connect the intended endpoints. |
| `imaginary_mode_missing` | No imaginary mode was reported where one was required. |
| `imaginary_mode_count` | The number of imaginary modes is not the required number. |
| `geometry_not_stationary` | The geometry is not at a stationary point within tolerance. |
| `frequency_not_computed` | Frequency/Hessian evidence is absent. |
| `convergence_not_met` | An SCF/geometry/response convergence criterion was not met. |
| `electronic_state_unresolved` | The target electronic state is not uniquely identified. |
| `basis_incomplete` | The basis (or auxiliary basis) is missing or incomplete. |
| `temperature_unspecified` | Temperature is required but was not declared. |
| `standard_state_unspecified` | Standard state is required but was not declared. |
| `reference_state_unspecified` | Reference state is required but was not declared. |
| `method_identity_missing` | A required method identity is absent. |
| `software_version_missing` | A required software version is absent. |
| `validator_unavailable` | The named validator could not be executed. |
| `external_artifact_unavailable` | An authorized external artifact is not available. |
| `run_key_unavailable` | The registered run key is not available. |
| `not_executed` | The check was intentionally not executed. |

Each entry is `{ "check_id": <enum>, "reason": <non-empty string>, "blocking": <boolean?> }`. Prose is not a check id; new ids require a `schema_version` bump.

## 7. Scope and limitations

Every claim carries a `scope`:

```json
{
  "subject": "activation_barrier",
  "applicability": "system_specific",
  "conditions": "gas phase, 298.15 K, conformer set A",
  "not_in_scope": ["other conformers", "solvent effects"]
}
```

`applicability` is one of `universal`, `system_specific`, `ensemble_specific`, `conformer_specific`, `condition_specific`. `conditions: null` is an explicit statement that no additional scope restriction is claimed, not an omission.

Report-level `limitations[]` entries have a stable `limitation_id`, a description and optional `affected_claim_ids`. Claims may reference limitation ids in their own `limitations[]`. A limitation must never be used to hide a missing check: missing evidence is recorded in `missing_checks`.

## 8. Provenance derivation

Provenance is separate from evidence. It records **how a result was derived**, not whether a claim is supported.

Both result kinds require:

- `software` with a non-empty `software_version` (a missing version is rejected);
- `input.artifacts[]` with relative paths and sha256 digests;
- `conditions` with the three keys `temperature_kelvin`, `standard_state`, `reference_state` **present and explicitly nullable** (a condition that does not apply is `null`, never omitted).

### `electronic_structure`

`derivation` requires `method` (at least `method_id`; optional `method_family`, `dispersion`, `hamiltonian`), `basis` (at least `basis_id`), and `electronic_state` (at least `charge` and `multiplicity`; optional `state_label` and `spin_mode` from `cccp.calculation.contracts.SpinMode`). A result that omits the method, basis or electronic state is rejected.

### `pure_analysis`

`derivation` requires `algorithm`, `algorithm_version` and `input_digest` (sha256). It **must not invent a quantum method**: `additionalProperties: false` rejects `method`/`basis`/`electronic_state`, and an explicit `not`/`anyOf` guard rejects an object that carries any of those keys. A pure analysis (e.g. an imaginary-mode check over an existing frequency output) derives from existing products, not from a fictitious quantum calculation.

## 9. Recipe boundary: science DAG, never a scheduler

`recipe.schema.json` describes a finite scientific DAG. It contains:

- `steps[]` with stable `step_id` (pattern `^[a-z0-9][a-z0-9_.-]*$`);
- exactly one non-null reference per step: `capability_ref` (registered capability) **or** `task_kind` (one of the 15 frozen `TaskKind` strings);
- typed `OutputRef` selectors (`whole_product`, `index`, `slice`, `field`, `conformer`, `mode`, `frame`) for upstream products;
- `edges[]` with `from_step` / `to_step` and an optional typed `output_ref`;
- `required` and `diagnostic_only` flags (mutually exclusive; a diagnostic-only step can never satisfy a required dependency);
- `validators[]` that reference admissible validator ids and versions;
- `applicability` (`supported_reaction_classes`, `preconditions`, `unsupported_when`);
- bounded `alternatives[]` (`max_uses >= 1`, plus recipe-level `limits.max_alternatives`, `max_steps`, `max_edges`).

### Forbidden field names (encoded negative guard)

The recipe has **no** field named `job`, `queue`, `account`, `price` or execution `status` at any depth. This is encoded as a `propertyNames` guard applied to every object schema:

```json
"propertyNames": { "$ref": "#/$defs/SafeFieldName" },
"SafeFieldName": {
  "type": "string",
  "pattern": "^[a-z][a-z0-9_]*$",
  "not": { "pattern": "(job|queue|account|price|status)" }
}
```

Consequences:

- `job_status` matches the lowercase snake_case pattern but also matches the `not` pattern, so both the `propertyNames` guard and `additionalProperties: false` reject it.
- `queue`, `account`, `price`, `status`, `job_id`, `job_state`, `queue_position`, `account_id`, `price_model` are all rejected because the `not` pattern matches the token anywhere in the name.
- `JobStatus`, `Queue` and similar uppercase spellings fail the lowercase snake_case pattern as well.
- The guard applies to top-level recipe keys, step keys, edge keys, selector keys, validator references, alternative keys and the keys inside `parameters`.

There is no iteration/scheduler construct in the schema: no loop, dispatch, retry, queue or scheduling keyword exists, and the recipe never executes; compilation and execution remain the job of the existing task layer and track B's binding layer.

## 10. Serialization and versioning

- Files are UTF-8 JSON, 2-space indent, sorted keys and a trailing newline (deterministic output).
- `schema_version` is an integer `1` in all three contracts. A breaking change requires a new version and a migration note; consumers must not silently reinterpret version 1.
- Unknown fields are rejected (`additionalProperties: false`) except the explicit dynamic bag `parameters`. `parameters` accepts only flat scalar values or arrays of scalars (nested objects are rejected), and its names are still guarded by `propertyNames`; no scheduler/dispatch structure can be smuggled inside a parameter value. Historical/hand-written payloads are not coerced; an adapter must map them explicitly.
- Optional object-valued fields (`Edge.output_ref`, `Step.applicability`) are non-nullable: omit the field to mean "not declared". Every genuinely nullable field uses `type: ["…", "null"]` (for example `parameters`, `science_identity`, `units`, `conditions`), never an omitted key.

## 11. Positive and negative examples per status

The digest values below are illustrative; every real value must be the 64-character lowercase sha256 returned by `content_sha256()`.

### 11.1 `supported`

Positive (accepted): a software validator and one digested native output.

```json
{
  "claim_id": "c-scf-converged",
  "statement": "The SCF converged for the target electronic state.",
  "status": "supported",
  "scope": { "subject": "scf_convergence", "applicability": "system_specific", "conditions": null },
  "validator": { "validator_id": "software.orca", "validator_version": "5.0.4", "kind": "software" },
  "source_refs": [
    {
      "artifact_path": "native/sp.out",
      "digest": { "algorithm": "sha256", "value": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" },
      "role": "native_output",
      "units": null
    }
  ],
  "missing_checks": []
}
```

Negative (rejected): the same claim with an empty source list violates the `supported` then-branch (`minItems: 1`) even though a validator is present. Omitting `validator` is rejected for the same status. A support verdict without a digested source is not evidence.

```json
{
  "claim_id": "c-scf-converged",
  "statement": "The SCF converged for the target electronic state.",
  "status": "supported",
  "scope": { "subject": "scf_convergence", "applicability": "system_specific", "conditions": null },
  "validator": { "validator_id": "software.orca", "validator_version": "5.0.4", "kind": "software" },
  "source_refs": [],
  "missing_checks": []
}
```

### 11.2 `refuted`

Positive (accepted): a deterministic validator and the frequency artifact that contradicts the claim.

```json
{
  "claim_id": "c-true-minimum",
  "statement": "The optimized geometry is a true minimum.",
  "status": "refuted",
  "scope": { "subject": "stationary_point_kind", "applicability": "conformer_specific", "conditions": "conformer A" },
  "validator": { "validator_id": "check.imaginary_mode_count", "validator_version": "1.0.0", "kind": "deterministic" },
  "source_refs": [
    {
      "artifact_path": "freq/freq.out",
      "digest": { "algorithm": "sha256", "value": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" },
      "role": "native_output",
      "units": "cm-1"
    }
  ],
  "missing_checks": []
}
```

Negative (rejected): the same claim without a `validator` key leaves a refutation with no check identity and violates the `refuted` then-branch.

```json
{
  "claim_id": "c-true-minimum",
  "statement": "The optimized geometry is a true minimum.",
  "status": "refuted",
  "scope": { "subject": "stationary_point_kind", "applicability": "conformer_specific", "conditions": "conformer A" },
  "source_refs": [
    {
      "artifact_path": "freq/freq.out",
      "digest": { "algorithm": "sha256", "value": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" },
      "role": "native_output",
      "units": "cm-1"
    }
  ],
  "missing_checks": []
}
```

### 11.3 `inconclusive`

Positive (accepted): the execution completed and one direction of the IRC exists, but the reverse path is missing; no support/refutation is claimed.

```json
{
  "claim_id": "c-ts-connects-reactant",
  "statement": "The transition state connects the intended reactant and product.",
  "status": "inconclusive",
  "scope": { "subject": "irc_connectivity", "applicability": "system_specific", "conditions": null },
  "source_refs": [
    {
      "artifact_path": "irc/forward.out",
      "digest": { "algorithm": "sha256", "value": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" },
      "role": "irc_forward",
      "units": null
    }
  ],
  "missing_checks": [
    { "check_id": "missing_irc", "reason": "reverse IRC output is absent", "blocking": true }
  ]
}
```

Negative (rejected): the validator id `llm:consensus` fails the validator id pattern (no `check.`/`software.`/`human.` namespace). Model consensus cannot stand in for a validator, even on an inconclusive claim.

```json
{
  "claim_id": "c-ts-connects-reactant",
  "statement": "The transition state connects the intended reactant and product.",
  "status": "inconclusive",
  "scope": { "subject": "irc_connectivity", "applicability": "system_specific", "conditions": null },
  "validator": { "validator_id": "llm:consensus", "validator_version": "1", "kind": "deterministic" },
  "source_refs": [],
  "missing_checks": [
    { "check_id": "missing_irc", "reason": "reverse IRC output is absent", "blocking": true }
  ]
}
```

### 11.4 `not_evaluated`

Positive (accepted): the check was skipped because the authorized artifact and run key were unavailable.

```json
{
  "claim_id": "c-barrier-height",
  "statement": "The activation barrier is below the experimental upper bound.",
  "status": "not_evaluated",
  "scope": { "subject": "activation_barrier", "applicability": "condition_specific", "conditions": "298.15 K" },
  "source_refs": [],
  "missing_checks": [
    { "check_id": "external_artifact_unavailable", "reason": "authorized experimental manifest not provided", "blocking": true },
    { "check_id": "run_key_unavailable", "reason": "no registered run key for the live scenario", "blocking": true }
  ]
}
```

Negative (rejected): an empty `missing_checks` list on a `not_evaluated` claim violates the then-branch (`minItems: 1`). "Not evaluated" must say what was missing.

```json
{
  "claim_id": "c-barrier-height",
  "statement": "The activation barrier is below the experimental upper bound.",
  "status": "not_evaluated",
  "scope": { "subject": "activation_barrier", "applicability": "condition_specific", "conditions": "298.15 K" },
  "source_refs": [],
  "missing_checks": []
}
```

### 11.5 Unknown status (rejected)

```json
{ "status": "confirmed" }
```

This is rejected by `ClaimStatus.enum`. The same applies to `passed`, `failed`, `true` and any localized synonym.

## 12. `completed-but-inconclusive` (the separation in one record)

```json
{
  "execution": { "terminal_state": "completed", "argv": ["orca", "irc.inp"], "exit_code": 0 },
  "claims": [
    {
      "claim_id": "c-ts-connects-reactant",
      "statement": "The transition state connects the intended reactant and product.",
      "status": "inconclusive",
      "scope": { "subject": "irc_connectivity", "applicability": "system_specific", "conditions": null },
      "source_refs": [],
      "missing_checks": [
        { "check_id": "missing_irc", "reason": "reverse IRC output is absent", "blocking": true }
      ]
    }
  ]
}
```

The process completed successfully (`terminal_state: completed`, `exit_code: 0`), yet the scientific claim stays `inconclusive`. A consumer must never collapse these two facts.

## 13. Rejection matrix (what each negative example violates)

| Negative case | Violated contract |
|---|---|
| `status: "confirmed"` | `ClaimStatus.enum` |
| `supported` with no source refs | `Claim.allOf[0].then.required` + `source_refs.minItems` |
| `supported` source ref without `digest` | `ArtifactSourceRef.required` |
| `refuted` without validator | `Claim.allOf[1].then.required` |
| validator id `llm:consensus` / free model prose | `Validator.validator_id.pattern` and `Validator.allOf` namespace/kind agreement |
| `not_evaluated` with empty `missing_checks` | `Claim.allOf[2].then.properties.missing_checks.minItems` |
| evidence claim carrying `exit_code`/`terminal_state` | `Claim.additionalProperties: false` |
| provenance without `software_version` | `SoftwareIdentity.required` |
| `electronic_structure` without `method`/`basis`/`electronic_state` | `ElectronicStructureDerivation.required` |
| `pure_analysis` with `method`/`basis`/`electronic_state` | `PureAnalysisDerivation.additionalProperties: false` + explicit `not` guard |
| `pure_analysis` without `algorithm`/`algorithm_version`/`input_digest` | `PureAnalysisDerivation.required` |
| recipe instance with `job_status` (any depth) | top-level/step `propertyNames` → `SafeFieldName.not` + `additionalProperties: false` |
| recipe copy whose schema declares a `job_status` property | recursive forbidden-field scan over the schema document (no such key is allowed) |
| recipe step with both `capability_ref` and non-null `task_kind` | `Step.oneOf` |
| step with `required: true` and `diagnostic_only: true` | `Step.allOf` mutual-exclusion branches |
| alternative without `max_uses` | `Alternative.required` (bounded alternative) |
