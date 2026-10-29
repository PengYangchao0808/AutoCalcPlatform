# Installed RPH product entry (F05 / Phase B)

`rph research` now defaults to `createProductParts` in dsh-bundle. Tests retain
`dependencies.parts` and `createTransport` seams; the installed bin requires
neither. Demo keeps its labelled fixture and Controller refusal.

One negotiated MCP session supplies the model Planner, gateway usage, durable
proposal/record writer, evidence projection reader, budget reader and explicit
singlepoint mapper. The bridge remains free of HTTP/process/store access.
The additive operations and authorization are defined in
[`research-consumption.v1.json`](../runtime/research-consumption.v1.json).
The existing `acp.research.v1@1.0.0` handshake remains the visibility gate;
product clients also require all ten consumer tools in the observed tools/list.
The eight base tools and ten legacy P2 tools keep their existing contracts.
No new dotted wire identifier is introduced.

## Configuration and first supported scenario

Use the portable profile environment variables documented in rph/README.md.
The provider credential belongs to the Controller, never the science file or RPH.
`RPH_CONTROLLER_API_KEY` is forwarded as the named launcher's `ACP_API_KEY` env
reference; the key is never an argv/tool argument or a doctor output value.

The explicit science input example is
`rph/examples/singlepoint.product.json`. It contains `science_seed`,
`initial_input`, `submission` and `planning_budget`. Confirm the input bytes,
charge, multiplicity, method/basis, resources and budgets before running:

```sh
rph doctor --connect --json
rph research --science singlepoint.product.json --research stable-session-seed \
  --question 'Compute the single-point energy of this confirmed H2 geometry' \
  --recipe-digest sha256:<published-recipe-digest> --json
rph resume --proposal <returned-proposal-ref> --plan-digest <execution-plan-digest> --json
```

A model candidate must validate against the typed seed, one `electronic.energy`
step and the confirmed singlepoint submission. Resource parameters must explicitly
match the confirmed `ncores`/`memory_gb`. Input bytes must match the declared hash.
The launch task count/deadline must equal the plan. `basis:null` explicitly selects the basis-free r2SCAN-3c composite case; other methods require an explicit basis. The first mapper refuses geometric constraints, substitutions, multiple inputs and labelled/reference electronic states. Missing values refuse before
submission. The Controller freezes the validated candidate and returns separate
science, execution and proposal digests. The execution digest goes to submit;
science attribution uses the science digest. Approval remains a separate human
operation; this factory never approves its own plan.

Client recovery metadata (`task_ref`, `report`, `checkpoint`) is stored in
`proposal.rph_state` through expected-revision CAS and current project/attempt
guards. These are client recovery checkpoints, not authoritative worker files.
Record decisions use the existing ResearchWriter evidence gate and CAS append.
The default submit-intent port uses the session bridge ledger; Controller submit
idempotency remains authoritative. A product proposal additionally stores its
validated model plan, explicit science configuration and stable session seed.
`resume` reads that proposal and the authoritative approval, checks the execution
and science digest bindings, and rehydrates the existing Supervisor. Before a
task exists it reuses the saved plan without a model call; with a task checkpoint
it observes the same attempt/release and continues waiting/evidence/decision
without submitting again. Legacy proposals continue through the existing Saga
resume operation. It never creates a second Saga for a direct product submit.

## Doctor and evidence limits

`doctor` retains the v1 diagnostic checks and adds `readiness[]` facts with
`ok/warn/fail` and a `hint`: model, identity/permissions, software, storage and
execution. Local `profile_paths` stays a separate fact; remote storage cannot mask
a missing local profile path. `ready` is true only when all five product categories
are ok and no diagnostic fails. Static mode does not claim remote verification. `--connect` calls
read-only Controller readiness and resolves ORCA through cccp.software; it never
executes a provider or QC binary. Model credential configuration does not prove
provider reachability. Singlepoint envelope availability still requires per-plan
preflight/admission. Old Controllers lacking consumer tools are a typed refusal.

Evidence reads require a published exact report reference (id/path/digest),
current attempt, receipt and task result. Missing evidence produces a stopped or
inconclusive outcome; it never uses model text as evidence. The reader shares the
existing platform projection with the research-record evidence route.
Budget usage is receipt/ledger-based. Unmeasured cumulative wall/queue fields
are explicitly lower bounds; token/core-hour hard enforcement stays deferred.

Phase B tests use **injected fake model/transport**. No real model, QC, LSF,
production restart, or Candidate-Full claim follows from these tests. Phase C
must verify one real trajectory, report publication, approval continuation,
process/MCP interruption and stale-attempt recovery with the actual artifacts.
