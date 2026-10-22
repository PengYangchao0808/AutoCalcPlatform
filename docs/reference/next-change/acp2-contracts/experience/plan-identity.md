# Plan / Step / Approval Identity (frozen by T-03 / C01b)

Status: **frozen v1** for ACP 2.0 P2. Consumers: T-25 (research-record
persistence) and T-27 (approval binding) must use these names and rules
verbatim. The P2 resource envelope may add fields later, but it may not change
the meaning, projection or digest algorithm below.

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

`plan_definition_digest` is the canonical digest of a projection containing
**only** these keys from the Proposal:

```text
recipe_digest
science
  capability_steps
  initial_inputs
  method
  mapping
  charge
  spin_multiplicity
  solvent            (optional; omitted when null)
  temperature        (optional; omitted when null)
budget
allowed_change_scope
```

The projection is exactly:

```python
PROJECTION_FIELDS = ("recipe_digest", "science", "budget", "allowed_change_scope")
projection = drop_null_optional_fields({key: proposal[key] for key in PROJECTION_FIELDS})
```

`drop_null_optional_fields` removes any object member whose value is JSON
`null` (recursively). This is a projection step, not a digest step: after it,
the canonicalization input contains no `null`. Absent and `null` therefore mean
the same thing for optional science fields.

### 1.2 Explicit exclusions (must never change the digest)

The following are **outside** the projection. Changing any of them must leave
`plan_definition_digest` byte-identical:

- `approval_ref` and `approval_status` (and any other approval/rejection/revoke projection),
- `created_at`, `updated_at`, any other timestamp or clock value,
- `display_name`, titles, captions, UI labels, ordering hints,
- `decision`, `human_edits`, `round`, revision counters (`record_revision`, proposal `revision`),
- every `task_ref` / `TaskRef` and `evidence_ref` appended after dispatch,
- `proposal_id`, `project_id`, `question_ref`/`question_revision`, `reaction_ref`/`reaction_revision`, `hypothesis`.

Rationale: the digest is the **scientific definition** the approver signs.
Approval state, presentation and later provenance must not invalidate an
already-approved plan, and appending execution facts must not either. Project
and budget scope are bound by `approval_ref` (see §3) rather than smuggled into
the science projection.

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

PROJECTION_FIELDS = ("recipe_digest", "science", "budget", "allowed_change_scope")

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
    projection = {k: proposal[k] for k in PROJECTION_FIELDS}
    payload = _normalize(_drop_nulls(projection))
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()
```

The committed reference digest for `fixtures/proposal.valid.json` is
`sha256:d6b0c35220aec8385f0c92778da312d1c60f7023630572969fbbcd2357b70c8b`.
The assertion script recomputes it and also prints the full stability matrix.

### 1.4 Sensitivity matrix (frozen expectation)

| Mutation | Digest |
| --- | --- |
| `approval_ref` | unchanged |
| `approval_status` | unchanged |
| `display_name` | unchanged |
| `updated_at` | unchanged |
| `science.method` | **changed** |
| `science.mapping` | **changed** |
| `science.initial_inputs[0].sha256` | **changed** |
| `budget.count` | **changed** |

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
plan_definition_digest
+ project_id
+ budget
+ allowed_change_scope
```

Rules:

- Requesting approval for a different `plan_definition_digest`, project, budget
  or allowed-change scope requires a new approval. Reusing an `approval_ref`
  across any of those is a contract violation (`revision_conflict` /
  `approval_binding_mismatch`).
- An approval is **not** bound to, and must not be invalidated by, later
  appended `TaskRef`s or `evidence_ref`s.
- `allowed_change_scope.mode = "none"` means no post-approval substitution.
  `"finite_list"` permits only the enumerated finite substitutions; anything
  else needs a new proposal and approval.
- Approval status (`pending|approved|rejected|revoked`) is the approval's own
  state, never a task/job status, and never input to `plan_definition_digest`.

---

## 4. Frozen field names

`proposal.schema.json` required set:

```text
proposal_id, project_id, question_ref, question_revision, reaction_ref,
reaction_revision, hypothesis, recipe_digest, plan_definition_digest, science,
budget, approval_ref, allowed_change_scope, round, decision, created_at,
human_edits
```

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
