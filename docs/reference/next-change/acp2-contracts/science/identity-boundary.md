# ACP2 Plan A — Recipe Compilation and the Science Identity Boundary

Status: design note for A-07b (`src/cccp/calculation/recipes/compile.py`).
Baseline: `acp2/a-science`, after A-07a (`7f148ad`). Author: QCcalc Team.
Scope: pure definition compilation and identity materialization. A never
executes the DAG; Runtime (B18) owns the execution loop, persistence,
cancellation and retries.

## 1. Two identities, one hashing scheme

The compiler freezes two identities at two different moments:

| Identity | When | Function | Question it answers |
|---|---|---|---|
| **Plan-definition digest** | Before approval | `compile_recipe(...)` | "Is this the same scientific plan the reviewer approved?" |
| **Step-execution identity** | At dispatch | `materialize_step_identity(...)` | "Is this step's actual input the science we agreed to run and may reuse?" |

Both are `v2:` fingerprints produced by
`cccp.analysis.identity.identity_fingerprint`
(`"v2:" + sha256(canonical_json)[:32]`). File content is bound with
`cccp.analysis.identity.content_sha256`. No second hashing scheme, prefix or
canonicalizer is introduced. The compiled definition itself is rendered with
the shared canonical JSON helper
(`cccp.analysis.research._common.canonical_json`: sorted keys, two-space
indent, one trailing newline), so two compilations of the same recipe produce
byte-identical `definition_json` and `to_json()`.

`CompiledRecipe` exposes:

- `definition_json` — canonical JSON of the definition payload;
- `plan_definition_digest` — `v2:` fingerprint of that payload;
- `schema_digest` — `sha256:<64 hex>` over the exact canonical definition
  bytes, for byte-level audit and artifact citation.

`schema_digest` identifies the compiled definition bytes; it is **not** a
recovery key. `plan_definition_digest` is the science-approval identity.

## 2. Plan-definition digest — included and excluded

Included (a change to any of these changes the digest):

- recipe id, schema version, applicability, limits and recipe-level
  validators;
- the ordered typed `TaskRequest` definition of every step: task kind and
  resolved capability/task-kind selection, charge, multiplicity, method/basis
  level, backend, electronic state, materialized step options/parameters, and
  a path-free structure view;
- `consumes` / `produces` `OutputRef` entries on every step, including the
  full typed selector (`selector_kind`, `index`, `range`, `field`);
- every edge (including an edge-level `output_ref`) and every bounded
  alternative block with its `when`, `max_uses`, replacement target and
  nested step parameters/selectors;
- initial inputs: effective structure content (inline coordinates/symbols or
  the file's SHA-256), structure role, atom mapping, charge, multiplicity,
  method, electronic state, standard state, comparison conditions and
  provider/plugin versions;
- topological order of the declared DAG.

Excluded, and why:

| Excluded item | Reason |
|---|---|
| `approval_ref`, approval status, evidence refs, timestamps | Routine approval/evidence append must not invalidate approved science. |
| Recipe `title` / display name | Presentation metadata, not science. |
| Cached `Recipe.science_identity` | Derived value; including it would make the digest self-referential. |
| Job id, queue, account, price, scheduler status | Platform state; the recipe schema forbids these tokens at every depth. |
| Execution resources (`nproc`, `mem`, `maxcore`, `timeout_s`), work/output directories | Machine/runtime concerns; the existing ACP identity canonicalizer already drops these execution-domain keys. |
| File locations | A path is replaced by `content_sha256`; moving a file never changes the science, changing its bytes always does. |

Management metadata is accepted as an `ApprovalMetadata` argument and stored
on the `CompiledRecipe`, but it is never inserted into the definition payload.
`Recipe.title` is likewise ignored by the definition payload, so a display-name
change leaves both `plan_definition_digest` and `definition_json` unchanged.

## 3. Step-execution identity — materialized only at dispatch

`materialize_step_identity(compiled, step_id, upstream_digests,
provider_versions)` is the dispatch-time gate. Its payload contains:

- the plan-definition digest (so a changed definition always changes every
  step identity);
- step id, task kind, binding selection, binding id, implementation id and
  the implementation version resolved from the dispatch provider map;
- the same path-free science input view (method, structure content, role,
  atom mapping, charge/multiplicity/electronic state, standard state,
  comparison conditions), with provider versions replaced by the versions
  actually present at dispatch;
- materialized parameters (the typed options by default, or an explicit
  `materialized_parameters` mapping);
- every dependency's stable address, upstream step, product kind, resolved
  selector and **actual** content digest;
- the provider/plugin version map.

It excludes job id, display name, approval metadata, artifact paths and
scheduler state by construction. `_json_safe` rejects a `Path` in dispatch
parameters: a location must be presented as its content digest.

### Dependency addresses

`output_ref_address(ref)` returns
`<from_step>::<product_kind>::<canonical selector JSON>`. B18 computes the
digest of the product selected by the reference and passes a mapping from that
address to a SHA-256 digest. A bare structural edge (no `output_ref`) adds the
upstream step's whole-result dependency (`product_kind = "step_result"`) only
when no `consumes` entry already selects a product from the same upstream; the
explicit selector is always the more precise dependency.

### Unresolved selectors are hard errors

The identity is never materialized by skipping a dependency. A selector is
considered resolved only when it is coherent for its kind:

| `selector_kind` | Required materialization |
|---|---|
| `whole_product` | no `index`, `range` or `field` |
| `index` | `index` set; no `range`/`field` |
| `slice` | `range` set with `start < end`; no `index`/`field` |
| `field` | `field` set; no `index`/`range` |
| `conformer`, `mode`, `frame` | `index` or `field` set; no `range` |

An incoherent selector raises `UnresolvedOutputRefError` with
`reason="unresolved_output_ref_selector"`; a coherent selector with no
supplied digest raises the same error with
`reason="missing_upstream_digest"`. A malformed digest raises
`InvalidUpstreamDigestError`. Therefore a changed upstream byte changes the
step execution identity, while an absent upstream can never be silently
treated as "same".

## 4. Joining the existing plan/step identity (no second recovery truth)

The ACP recovery contract remains authoritative:
`acp.calculations.identity.compute_identity` produces `plan_identity` and
`step_identities`, and checkpoint adoption/reuse keys on those values. The
recipe identities do not replace, shadow or re-key them.

The join contract for B18:

1. **Definition → plan identity input.** When B18 builds its
   `CalculationPlan`, it includes `CompiledRecipe.plan_definition_digest` as a
   science field of the plan payload. A changed recipe definition therefore
   changes the existing `plan_identity` through the normal `compute_identity`
   path. B18 does not store a second plan key.
2. **Dispatch → step identity input.** Before executing or adopting a step,
   B18 materializes `materialize_step_identity(...)` from the actual upstream
   digests and provider versions and carries
   `StepExecutionIdentity.execution_identity` as a science field of that
   step's identity payload. The persisted checkpoint still stores only the
   existing `plan_identity` / `step_identity` values; the recipe identity is
   an input to them, not an alternative key.
3. **Adoption rule.** A completed result may be adopted only when both the
   existing `step_identity` and the materialized recipe
   `execution_identity` match. A mismatch is a conservative recompute, never
   an exception or a silent reuse.
4. **No new checkpoint.** A creates no checkpoint, `resume_source.json`,
   retry counter or persistence file. `identity_schema` stays
   `cccp.analysis.identity.IDENTITY_SCHEMA`; the compiled definition stores
   its own schema/version as contract metadata only.

Because the plan-definition digest is computed before approval and excludes
approval/evidence/display fields, appending evidence or approving the plan
cannot change the science key. Because the step identity is computed from
actual bytes at dispatch, it cannot be frozen over products that did not exist
at approval time.

## 5. Unknown-field policy

- **New execution.** `compile_recipe` parses mapping payloads through the
  closed `Recipe.from_dict` contract; unknown recipe fields raise
  `RecipeSchemaError`. Initial inputs and approval metadata are also closed
  (`UnknownScientificFieldError` / `CompileInputError`). Step `parameters` are
  checked against the typed options class of the resolved task kind, so an
  unknown scientific parameter (for example a misspelled
  `stabilty_check`) is rejected instead of being silently ignored by the
  legacy `TaskRequest.from_dict` S2 read rule.
- **Historical read.** `read_historical_recipe(payload)` deep-copies the
  payload, reports every unknown top-level and nested step/edge/alternative/
  validator field, and hard-codes `execution_eligible=False` and
  `recovery_eligible=False`. Tolerating an unknown field for reading never
  grants execution or recovery eligibility.

## 6. Non-claims

- `compile_recipe` does not execute, dispatch or persist anything; it imports
  no `cccp.calculation.tasks` module and starts no subprocess.
- The existence of a `TaskRequest` in the compiled definition is a typed
  intent, not evidence that a QC binary is installed or that a run occurred.
- Real singlepoint/optimize/frequency execution remains `needs-real-evidence`
  in `baseline.md` §8.7.
