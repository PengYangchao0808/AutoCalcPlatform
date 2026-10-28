# Plan / Step / Approval Identity (frozen by T-03 / C01b)

Status: **frozen v1** for ACP 2.0 P2 (typed by fix 1). Consumers: T-25
(research-record persistence) and T-27 (approval binding) must use these names
and rules verbatim. The P2 resource envelope may add fields later, but it may
not change the meaning, projection or digest algorithm below.

The three identities are deliberately separate. Collapsing them is a defect:

| Layer | Question it answers | When materialized | Mutable? |
| --- | --- | --- | --- |
| `plan_definition_digest` | *What science was approved?* | When the Proposal is frozen | **No** — any scientific change is a new proposal |
| `step_identity` | *What exact bytes/parameters/implementation will this step run?* | Immediately before that step is dispatched | Per step; never reused across a different materialization |
| `approval_ref` | *Which human approval authorizes this exact plan?* | At approval request, bound to one `plan_definition_digest` | Status may change; binding may not |

A DSH chat session, a Workbench snapshot and a UI label are none of these.

---

## 1. `plan_definition_digest`

### 1.1 Projection (what is hashed)

`plan_definition_digest` is the canonical digest of **exactly** these JSON
paths from the Proposal. No other Proposal member is included:

```text
recipe_digest

science.capability_steps        (array; order-significant)
science.initial_inputs          (array; order-significant)
science.method
science.mapping
science.charge
science.multiplicity
science.allowed_substitutions   (array; may be empty)

budget.model_requests
budget.input_tokens
budget.output_tokens
budget.task_refs
budget.recipe_steps
budget.qc_invocations
budget.wall_seconds
budget.queue_seconds
```

The projection is built leaf-by-leaf; it is **not** "the whole `science` and
`budget` objects". That is what keeps optional presentation/basis fields out:

```python
SCIENCE_FIELDS = (
    "capability_steps", "initial_inputs", "method", "mapping",
    "charge", "multiplicity", "allowed_substitutions",
)
BUDGET_FIELDS = (
    "model_requests", "input_tokens", "output_tokens", "task_refs",
    "recipe_steps", "qc_invocations", "wall_seconds", "queue_seconds",
)

def plan_definition_projection(proposal: dict) -> dict:
    return {
        "recipe_digest": proposal["recipe_digest"],
        "science": {k: proposal["science"][k] for k in SCIENCE_FIELDS},
        "budget": {k: proposal["budget"][k] for k in BUDGET_FIELDS},
    }
```

`drop_null_optional_fields` then removes any object member whose value is JSON
`null` (recursively). This is a projection step, not a digest step: after it,
the canonicalization input contains no `null`. Absent and `null` therefore mean
the same thing for an optional member (e.g. `science.capability_steps[].tool`).

`budget.unit_basis` and `budget.notes` are **excluded**: they describe how the
count was chosen, not the science being approved. `science` has
`additionalProperties: false`, so no unlisted science field can enter the
projection.

### 1.2 Explicit exclusions (must never change the digest)

The following are **outside** the projection. Changing any of them must leave
`plan_definition_digest` byte-identical:

- `approval_ref` and `approval_status` (and any other approval/rejection/revoke projection),
- `created_at`, `updated_at`, any other timestamp or clock value,
- `display_name`, titles, captions, UI labels, ordering hints,
- `decision`, `human_edits`, `round`, revision counters (`record_revision`, proposal `revision`),
- every `task_ref` / `TaskRef` and `evidence_ref` appended after dispatch,
- `proposal_id`, `project_id`, `question_ref`/`question_revision`, `reaction_ref`/`reaction_revision`, `hypothesis`,
- `allowed_change_scope` (approval-facing summary; the scientific substitution set is `science.allowed_substitutions`, which **is** in the digest),
- `budget.unit_basis`, `budget.notes`.

Rationale: the digest is the **scientific definition** the approver signs.
Approval state, presentation and later provenance must not invalidate an
already-approved plan, and appending execution facts must not either. Project
and budget scope are bound by `approval_ref` (see §3) in addition to the
digest's own coverage of the budget numbers.

> `proposal_id`/`project_id` are excluded from the digest but are part of the
> approval binding. Two projects can have identical scientific definitions and
> still must not share an approval.

### 1.3 Canonicalization (byte-for-byte reproducible)

The digest algorithm is the B-owned canonical digest
(`runtime/application-api.v1.json#canonical_digest`, vectors in
`runtime/canonical-digest-vectors.v1.json`, implementation owner B16). C
implements the same steps locally for contract verification; it does not add a
second runtime implementation.

1. Reject a non-object root (`root_not_object`).
2. Reject JSON `null` anywhere after the projection step (`null_in_digest_input`).
3. Reject JSON floats anywhere (`non_canonical_number`). Integers only. A
   quantity value is an integer or a plain-decimal string; no exponent, no
   leading zeros. Quantities are objects whose keys are exactly `{value, unit}`;
   they are normalized to the dimension base unit
   (`Decimal(precision=28, ROUND_HALF_EVEN)`) and emitted as `{unit, value}`.
4. Normalize every string to Unicode NFC.
5. Sort object keys by Unicode code point.
6. Serialize with `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
   → UTF-8 bytes, no BOM, no trailing newline.
7. `digest = "sha256:" + lowercase hex(sha256(bytes))`.

Arrays are order-significant by default. No projection field is declared a
set field, so array reordering is a scientific change. (Do not "sort
capability_steps" or "sort upstream_digests" to make a comparison pass.)

Reference implementation used by the T-03 assertion script:

```python
import hashlib, json, unicodedata

SCIENCE_FIELDS = (
    "capability_steps", "initial_inputs", "method", "mapping",
    "charge", "multiplicity", "allowed_substitutions",
)
BUDGET_FIELDS = (
    "model_requests", "input_tokens", "output_tokens", "task_refs",
    "recipe_steps", "qc_invocations", "wall_seconds", "queue_seconds",
)

def _drop_nulls(value):
    if isinstance(value, dict):
        return {k: _drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_nulls(v) for v in value]
    return value

def _normalize(value):
    if value is None:
        raise ValueError("null_in_digest_input")
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        raise ValueError("non_canonical_number")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in sorted(value.items())}
    raise TypeError(type(value))

def plan_definition_digest(proposal: dict) -> str:
    projection = {
        "recipe_digest": proposal["recipe_digest"],
        "science": {k: proposal["science"][k] for k in SCIENCE_FIELDS},
        "budget": {k: proposal["budget"][k] for k in BUDGET_FIELDS},
    }
    payload = _normalize(_drop_nulls(projection))
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()
```

The committed reference digest for `fixtures/proposal.valid.json` is
`sha256:32bd96dd1c8d2eb962a419590ff91ebf695adef1c587c147185f522be5422d9b`.
The assertion script recomputes it and also prints the full stability matrix.

### 1.4 Sensitivity matrix (frozen expectation)

| Mutation | Digest |
| --- | --- |
| `approval_ref` | unchanged |
| `approval_status` | unchanged |
| `display_name` | unchanged |
| appended `task_refs`/`evidence_refs` (post-dispatch) | unchanged |
| `science.method` | **changed** |
| `science.mapping` | **changed** |
| `science.initial_inputs[0].sha256` | **changed** |
| `science.charge` | **changed** |
| `science.multiplicity` | **changed** |
| `budget.model_requests` | **changed** |
| `budget.wall_seconds` | **changed** |

---

## 2. `step_identity`

`step_identity` is materialized **before dispatch** of the step it names, and
must cover:

1. the actual upstream artifact **byte digests** (not paths, not "latest"),
2. the fully materialized parameters (resolved defaults included; an unresolved
   default must block dispatch), and
3. the exact implementation/release version that will execute the step.

A `step_identity_ref` record is:

```json
{
  "step_index": 0,
  "step_identity": "<resolved cache/adoption identity>",
  "implementation_version": "<release id>",
  "upstream_digests": ["sha256:<64 hex>"],
  "materialized_parameters_digest": "sha256:<64 hex>"
}
```

Rules:

- The same `step_identity` may be reused for adoption **only** when all three
  ingredients are identical. Any mismatch forces re-execution; a partial or
  diagnostic result is never adopted as a normal result.
- Re-running the same plan in a new round does not silently inherit a stale
  `step_identity`; it is regenerated from the actual upstream bytes.
- `step_identity` is per step and is not a project/plan identifier.

---

## 3. `approval_ref`

An approval binds exactly this tuple:

```text
plan_definition_digest        (the science; D06 domain 1)
+ execution_plan_digest       (the frozen workflow/input/method/resources; D06 domain 2)
+ project_id
+ budget
+ allowed_change_scope
```

**D06 amendment (M1-04a).** The approval record carries two independent digests:
`science_digest` (this document's `plan_definition_digest`) and `plan_digest`
(the platform's `execution_plan_digest`). Neither is derived from, compared
with, or substituted for the other:

- a change to either digest invalidates the approval (`revision_conflict` /
  `approval_binding_mismatch`);
- an approval requested without a Proposal body binds only the execution digest
  and reports `binding_status = "execution-only"` — that gap is explicit and is
  never presented as a full science binding;
- the Workbench/RPH displays both digests; the platform returns the execution
  digest in preflight/approval responses so no client needs to compute it;
- a client must not send the C science digest as the submit `plan_digest`
  (the pre-D06 mistake). The wire name `plan_digest` means
  `execution_plan_digest` on submit; the science digest travels in the approval.

`budget` and the scientific substitution set are already inside
`plan_definition_digest`; binding them again is a defence-in-depth consistency
check, not a second source of truth. `allowed_change_scope` is the
approval-facing summary and is outside the digest.

Rules:

- Requesting approval for a different `plan_definition_digest`, project, budget
  or allowed-change scope requires a new approval. Reusing an `approval_ref`
  across any of those is a contract violation (`revision_conflict` /
  `approval_binding_mismatch`).
- An approval is **not** bound to, and must not be invalidated by, later
  appended `TaskRef`s or `evidence_ref`s.
- `allowed_change_scope.mode = "none"` means no post-approval substitution.
  `"finite_list"` permits only the enumerated finite substitutions in
  `science.allowed_substitutions`; anything else needs a new proposal and
  approval.
- Approval status (`pending|approved|rejected|revoked`) is the approval's own
  state, never a task/job status, and never input to `plan_definition_digest`.

---

## 4. Frozen field names

`proposal.schema.json` required top-level set:

```text
proposal_id, project_id, question_ref, question_revision, reaction_ref,
reaction_revision, hypothesis, recipe_digest, plan_definition_digest, science,
budget, approval_ref, allowed_change_scope, round, decision, created_at,
human_edits
```

`proposal.science` required set (typed, `additionalProperties: false`):

```text
capability_steps, initial_inputs, method, mapping, charge, multiplicity,
allowed_substitutions
```

- each `capability_steps` item: `{step_index, capability, version}` plus
  optional `tool` and `parameters`;
- each `initial_inputs` item: `{input_id, sha256, format}` plus optional `role`.

`proposal.budget` required set (all integer, `minimum: 0`,
`additionalProperties: false`):

```text
model_requests, input_tokens, output_tokens, task_refs, recipe_steps,
qc_invocations, wall_seconds, queue_seconds
```

Optional `budget.unit_basis` (e.g. `per_scenario`) and `budget.notes` are
display/basis metadata and are excluded from the digest.

`research-record.schema.json` required set:

```text
research_id, project_id, round, decision, plan_definition_digest,
step_identity_refs, task_refs, evidence_refs, missing_validation, human_edits,
approval_ref, created_at, record_revision
```

`task_ref` is an object `{project_id, job_id, attempt, release_id}`. A bare
string array is not a TaskRef list and must be rejected. Every
`task_ref.project_id` must equal the record's `project_id`.

`round` is an integer. A string (`"1"`) is a type error, not a coercion
opportunity.

## 5. Non-goals (explicit)

- A Proposal is **not** a task/job state machine. It has no `job_id`, `attempt`,
  lifecycle, queue position, `running`/`queued` field, and no job status enum.
- A DSH session/chat is **not** a ResearchRecord. `session_id`, `chat_id`,
  `messages`, `transcript`, `conversation` and `model_role` are rejected.
- The Workbench read-only view model is not a second scientific authority and
  must not compute or upgrade a claim/decision.

---

## 6. D06 digest-domain registry (frozen contract rows)

Every identity that was historically called "the digest" is registered here with
one owner, one canonical input/field order, one version, the **single**
validation implementation (platform / CCCP / RPH) and the legacy wire field it
replaces. A domain whose validator is not registered **may not be marked
frozen** — `recipe_definition_digest` stays `not-frozen` until A registers a
cross-side verifier (M2-05).

The machine-readable twin of this table is
`src/acp_services/runtime/digest_domains.py` (`DIGEST_DOMAINS`); a test asserts
that the two sets agree row for row, so neither side may drift.

| domain | owner | version | unique validator | side | status |
| --- | --- | --- | --- | --- | --- |
| `plan_definition_digest` | C-RPH science / B16 canonicalization | `1.0.0` | `src/acp_services/runtime/canonical_digest.py::proposal_plan_definition_digest` | platform | frozen |
| `execution_plan_digest` | B | `1.0.0` | `src/acp_services/runtime/canonical_digest.py::plan_digest` | platform | frozen |
| `proposal_digest` | B | `4` | `src/acp_services/runtime/canonical_digest.py::proposal_digest` | platform | frozen |
| `submit_request_digest` | B | `2` | `src/acp_services/runtime/idempotency.py::canonical_request_digest` | platform | frozen |
| `frozen_plan_body_digest` | B | `2` | `src/acp_services/runtime/attempt_freeze.py::frozen_group` | platform | frozen |
| `recipe_definition_digest` | A (CCCP) | `0.0.0-pending` | — (no cross-side verifier registered) | cccp | not-frozen |
| `science_criteria_digest` | A (CCCP) | `1.0.0` | `src/cccp/analysis/research/science_criteria.py::criteria_definition_digest` | cccp | frozen |
| `step_execution_identity` | B worker + A science | `2` | `src/acp_services/runtime/receipt_identity.py::identity_digest` | platform | frozen |
| `contract_digest` | B | `1.0.0` | `scripts/acceptance/validate_acp2_contracts.py::contract_digest_for` | platform | frozen |
| `artifact_digest` | B | `1.0.0` | `src/acp_services/data/artifacts.py::normalize_artifact_digest` | platform | frozen |
| `release_digest` | B | `1.0.0` | `scripts/build_release.py::_wheel_facts` | platform | frozen |

Rules for adding a domain:

1. register the owner, the canonical field order (order-significant) and the
   version before any producer writes the digest;
2. register the *unique* validator; a second implementation is a defect even if
   it happens to agree (B16 `canonical_digest` is the only canonicalizer, B08
   `canonical_request_digest` the only request-digest implementation);
3. record the legacy wire field and its mapping so an old client keeps a defined
   meaning (`plan_digest` on submit is the execution digest; a pre-D06 approval
   without `science_digest` is `execution-only`);
4. a domain with no validator is `not-frozen` and must never appear as frozen in
   a BOM, an approval or a release statement.
