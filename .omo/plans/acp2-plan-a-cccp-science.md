# acp2-plan-a-cccp-science - Work Plan

## TL;DR (For humans)

**Who this is for and what changes for them:** For the science-track engineer and the platform and experience teams who consume the science layer, the project gains one frozen, versioned scientific contract set, a real capability catalog, deterministic recipe and evidence machinery, and reproducible real-computation evidence - instead of implicit assumptions spread across code and documents.

**What you'll get:** All thirty science-track tasks as an executable, phase-gated plan: contracts and pure scientific types, a read-only capability registry bound to the existing task cores, a compiled recipe DAG with stable science identity, deterministic validators, real small-molecule singlepoint/optimize/frequency evidence, a limited Diels-Alder vertical slice, an external science plugin, cross-domain NMR/thermochemistry reuse, and an independently installable SDK package - each with positive and negative tests and auditable evidence.

**Why this approach:** The existing execution stack already has one task root, one request/result envelope and one capability matrix. The plan extends those seams with descriptions, validation and evidence; it never builds a second executor. Every real run is either backed by a frozen environment and an authorized artifact manifest, or recorded honestly as not verified, so a green-looking suite cannot hide missing science.

**What it will NOT do:** It will not modify the ACP application, Services platform, frontend or RPH trees; will not add a CLI, a second task root, a new dependency or a scheduler; will not delete compatibility shims, model assets or recovery fixtures; and will not turn mocks, dry runs or unexecuted scenarios into passes.

**Effort:** XL
**Risk:** High - the work spans scientific semantics, a cross-package contract, real quantum-chemistry execution and an external plugin distribution, and several acceptance rows depend on another team's frozen environment.
**Decisions to sanity-check:** All thirty tasks are in scope under the full evidence-gated path; P0/P1 is the first delivery batch and P2-P5 continue only when their contracts and environments exist. The receipt protocol is B01's single schema, not an A-local approximation. The evidence root is immutable and platform-assigned. B Data owns platform authorization and export eligibility; CCCP validates only content and scientific identity. A signs its own H7 from B25a's candidate and never waits for B26. CDXML is explicitly deferred to a later scope and requires owner ratification if the master-plan ChemDraw entry is to remain a current promise. The small, runtime-verified export defect for the fifteenth task core is fixed inside the science track. Missing software, run keys or authorized artifacts produce `NOT_VERIFIED`, never `PASS`.

Your next move: approve this written plan for execution in a worker session, or send it through the requested dual review result. Full execution detail follows below.

---

> TL;DR (machine): XL/High - 51 implementation todos + 4 final verifiers across P0-P5; versioned science contracts, capability registry/binding, recipe identity, deterministic validators, real Local/LSF and limited Diels-Alder evidence, external plugin, cross-domain reuse, independent package; every required row is PASS or the phase gate stays open.

## Scope

### Affected user and ideal state

**Affected user:** the science-track executor who must deliver without further interview; the runtime/integration owner who consumes the contracts and owns shared files; the experience owner who builds forms, tool metadata and evidence views from the same contracts; external CCCP users and third-party plugin authors who must run science without the platform; and maintainers who rely on reproducible evidence.

| Row | Statement | Reason |
| --- | --- | --- |
| IS-1 | One versioned science contract set (capability, task binding, errors, reaction, question, evidence, provenance, recipe) exists with positive and negative fixtures and explicit version rules, consumable by the platform and experience tracks without reading the science code. | The platform and experience tracks must not invent a second scientific model; contract drift is the main integration failure mode. |
| IS-2 | Public scientific types are immutable, round-trip exactly, import no ACP or QC execution path, and preserve unknown historical fields rather than inventing defaults. | Pure-type import purity is an architecture invariant; silent defaults turn missing evidence into false proof. |
| IS-3 | A read-only capability registry separates described, installed, available and scientifically verified states, is deterministic, and exposes only the three compute capabilities plus one pure-analysis capability at first; recipe steps may reference an existing task kind through the binding until a descriptor exists; discovery never executes a plugin or starts a calculation. | "Installed" is not "scientifically valid"; software-name guessing and import-time execution are forbidden. |
| IS-4 | Each capability maps to exactly one existing task implementation; binding converts types and validates scientific preconditions only; preflight produces no artifacts and no subprocess. | The task layer is the single execution station; a second dispatch path is a hard failure. |
| IS-5 | Recipe compilation yields a typed task-request DAG whose steps reference a registered capability or an existing task kind through the binding, with a plan-definition digest frozen before approval (method, structure content, atom mapping and roles, charge/multiplicity/electronic state, `OutputRef` selectors, provider/plugin versions, comparison conditions) and a step-execution identity materialized at dispatch from actual upstream digests - never job id, queue, account or display name. | Recovery may reuse only science-identical results; platform identity must never leak into science identity, and pre-approval identity must not require bytes that do not exist yet. |
| IS-6 | Deterministic validators return supported, refuted, inconclusive or not evaluated for each claim; one imaginary frequency never proves connectivity and one IRC direction never proves a bidirectional path. | Task completion is not scientific claim support; overclaiming is the core scientific risk. |
| IS-7 | Evidence reports and scientific provenance keep execution completion separate from claim status, cite relative artifacts, digests and units, and list every missing check and limitation. | Consumers must distinguish absent evidence from contradictory evidence. |
| IS-8 | A real small-molecule singlepoint runs through the standalone science API on this server; optimize and frequency plus Local and LSF artifacts are validated from authorized manifests; a missing environment records not verified and leaves the gate open. | Real execution is an acceptance gate; mocks, dry runs and invented energies must never close it. |
| IS-9 | The limited Diels-Alder slice has frozen structures, mapping, budget and criteria, a compiled finite recipe, real transition-state, frequency, bidirectional IRC and thermochemistry evidence, and honest inconclusive outcomes when evidence is missing. | A real vertical slice is the only proof the science stack works; the symmetric example cannot support endo/exo claims. |
| IS-10 | A separately distributed third-party science plugin adds a read-only capability without changing core source; conflicts, bad versions, missing dependencies, unknown licenses and corrupt data fail safely; old artifacts stay readable after upgrade. | The plugin-ecosystem gate is only real if it is an external distribution, not another branch inside core. |
| IS-11 | NMR shielding and ensemble thermochemistry reuse the same capability and evidence contract with domain-specific provenance and limitations; a selectivity fixture genuinely distinguishes endpoints or explicitly refuses. | Cross-domain reuse prevents a second science stack; selectivity must not be claimed from two single points. |
| IS-12 | A frozen wheel installs in a clean environment and the documented SDK and plugin commands run without ACP, DSH or the source tree; documented outputs match the frozen version. | Independent distribution is the product claim and cannot rest on an editable development install. |
| IS-13 | All active workflows, compatibility shims, model assets, recovery fixtures and architecture invariants remain intact; every task has a result record with real command, exit code, assertions, scope and artifact digests. | The science track must extend, not regress, the existing product and evidence discipline. |

| GAP | Difference from the ideal state | Reason | Closed by todos |
| --- | --- | --- | --- |
| GAP-1 | The science contract directory does not exist. | The platform and experience tracks have no consumable science contract. | 3-10 |
| GAP-2 | No public research types or scientific validators exist; artifact and provenance types stop at the calculation layer. | No typed reaction, question, evidence or deterministic validation. | 9-10, 22, 31-32 |
| GAP-3 | No capability registry, task binding or recipe compiler exists; capability resolution is only the inline selection layer. | No scientific catalog, single-task binding or recipe DAG. | 11-16 |
| GAP-4 | No science acceptance tests or fixtures exist. | Contract and scientific behavior are not executable evidence. | 4, 6, 8, 10, 12, 14, 16, 18-19, 22, 24, 27-32, 35, 38-41, 43-51 |
| GAP-5 | No science acceptance harness exists. | Real runs cannot be driven with the project's three-state protocol. | 20-21, 23, 25, 27, 30, 33-36 |
| GAP-6 | No science acceptance evidence records exist. | Real runs cannot be audited or replayed. | 1, 17, 21, 23, 25-26, 33-37, 42, 46-47, 49, 51 |
| GAP-7 | The plugin manifest has only capability name and schema; no capability kind, recipe or validator reference, trust level or entry-point group; enablement is in-memory and not a sandbox. | The plugin tasks cannot be satisfied by the current pilot. | 38-42 |
| GAP-8 | The fifteenth task core and its option/result types are missing from the package-level lazy exports (runtime-verified). | The fifteen-task compatibility matrix would document an incomplete public surface. | 14 |
| GAP-9 | No NMR or thermochemistry capability descriptors or cross-domain evidence exist. | Cross-domain reuse is unproven. | 43-44, 47 |
| GAP-10 | No distinguishable-endpoint selectivity fixture or budgeted evidence exists. | Endo/exo claims cannot be made. | 45-46 |
| GAP-11 | No installed-package test or standalone SDK guide and examples exist. | The independent distribution claim is unsupported. | 48-50 |
| GAP-12 | No run keys, authorized artifact manifests or environment freeze are available to the science track. | Real runs would be untraceable or block on unavailable prerequisites. | B-issued and A-consumed: 1, 17, 21, 23, 25-26, 33-36, 46, 49; A records NOT_VERIFIED when absent |
| GAP-13 | The baseline is a feature-branch commit with a dirty worktree, untracked planning documents and a host default interpreter below the project minimum. | Starting from a moving checkout or the wrong interpreter invalidates every evidence record. | 1 |

### Must have

- All thirty science tasks A-00 through A-29 represented as executable todos with exact references, acceptance, failure assertions and evidence paths.
- A frozen baseline record: actual commit, branch, dirty-worktree inventory, worktree path, interpreter path and the three package import locations.
- Versioned schemas for capability, task binding, errors, reaction, question, evidence, provenance and recipe, plus positive and negative fixtures and a minimal first handoff package.
- Pure immutable public types for reaction, question, evidence and provenance with explicit serialization, version checks and historical adapters that preserve unknown values.
- A metadata-only capability registry with deterministic ordering and separated described/installed/available/verified projections, seeded with energy, optimization, frequency and a pure frequency-analysis capability.
- A capability-to-task binding that emits existing task requests and delegates to exactly one existing `run_*` implementation; preflight is side-effect free.
- A recipe compiler producing a typed task-request DAG whose steps reference a registered capability or an existing task kind through the binding; canonical JSON and science identity; required and diagnostic-only semantics are enforced.
- Deterministic validators for stationary point, frequency mode, IRC endpoint and connectivity, and energy comparability; each returns the four-valued claim status honestly.
- A science acceptance harness with the project's help/dry-run behavior and the 0/1/2/3 exit-code contract, emitting the shared acceptance schema.
- Real singlepoint, optimize and frequency evidence from the standalone science API, plus Local and LSF artifact validation from authorized manifests.
- A limited Diels-Alder v1 fixture and recipe, real transition-state/frequency/bidirectional-IRC/thermochemistry evidence, and a final evidence report that separates execution from claims.
- Plugin manifest and registry extensions for capability kind, validator/recipe reference, trust evidence and version digest; one externally installed read-only plugin distribution that needs no core edit; conflict and upgrade safety.
- NMR and thermochemistry capabilities bound to the same protocol, plus a selectivity fixture with distinguishable endpoints.
- A packaging request for the platform owner, an installed-package test, clean-environment wheel verification and an SDK guide with runnable examples.
- Per-task evidence records with schema, candidate revision, scenario, scope, result, reason, real command, exit code, timestamps, assertions and artifact SHA-256.
- Final handoffs H2 through H7 with explicit verified, failed and not-verified rows; no required row is silently skipped.

### Must NOT have (guardrails, anti-slop, scope boundaries)

- No edits under `src/acp/**`, `src/acp_services/**`, `frontend/**`, `rph/**`, root or nested `AGENTS.md`, `pyproject.toml`, `requirements*.txt`, CI configuration, the four version constants or default YAML. Every need is a written request to the platform owner.
- No new CLI, `__main__.py`, scheduler, queue, account/quota logic, persistence layer or second execution station.
- No second implementation of a scientific type in the platform tree; no second `run_*` dispatch path; no backend-level subprocess.
- No new third-party runtime dependency. No unapproved schema engine. No vendored scientific formulas.
- No real expensive Diels-Alder, transition-state or IRC job before the static recipe and validators exist and the platform owner has issued a registered run key.
- No unlimited candidate search, no silent budget widening, no retry that repeats completed expensive work.
- No claim of a complete mechanism, endo/exo selectivity on the symmetric fixture, experimental rates, global minimum or general accuracy beyond the frozen fixture.
- No claim that ChemDraw/CDXML input is supported while it remains outside the initial A/B/C scope; no silent best-effort parsing of unsupported structures.
- No deletion, relaxation or rewriting of existing tests, compatibility shims, historical readers, model assets, notices or recovery fixtures.
- No mock, dry run, syntax-only check, `--collect-only` or unexecuted scenario counted as pass.
- No edits to the production service, its database, its run root or global Node/Python/SSH configuration.

## Verification strategy

> A's automated checks are agent-executed. User approval, independent reproduction and real model/cluster operation are separate facts owned by the user/B/C; A never represents them as an automated PASS.

- **Test decision:** test-first for contract surfaces (schemas, examples, fixtures and round-trip tests are committed before the production module is accepted); test-with-implementation for registries, bindings, compilers and validators; real-environment checks are gated by the platform-provided interpreter, software paths, run keys and authorized manifests. Framework: `pytest`, flat files under `tests/`, raw asserts, `unittest.mock` only.
- **Interpreter:** A's development tests and harness commands run under `$ACP2_A_PYTHON`, the role-owned frozen interpreter whose import path resolves to the A worktree. If it is unset, the task records `NOT_VERIFIED` with the exact gap and does not fall back to a system interpreter. Integration evidence uses `$ACP2_INTEGRATION_PYTHON` and records the frozen candidate; wheel probes use a separately recorded clean interpreter. Bare `python3.11` is permitted only for interpreter-version probes and non-project utilities; bare `python3` is never used because the host default is 3.9.25.
- **Working directory:** every command starts with `cd "$ACP2_A_SOURCE"`; the science worktree is `acp2/a-science` created from the frozen base revision. `$ACP2_SOURCE` is not used in this plan because B/C assign it the frozen integration candidate; A uses `$ACP2_A_SOURCE` for its worktree and `$ACP2_INTEGRATION_SOURCE` when reading an integrated artifact. A command that resolves imports from any other checkout invalidates the evidence; assert `cccp.__file__`, `acp.__file__` and `acp_services.__file__` against the intended role interpreter before trusting a result.
- **Evidence root and immutability:** `$ACP2_EVIDENCE` is assigned by the platform owner and is the only evidence root. Every task writes an immutable directory `$ACP2_EVIDENCE/A/<task-id>/<subtask-id>/<run-id>/` containing `result.json` plus real argv, stdout/stderr, exit codes and input/output SHA-256. The `Evidence` path in each todo names that task root; the actual record is `<task-root>/<run-id>/result.json`. When a todo's Evidence path already includes `<run-id>`, use it verbatim as the record path. A never overwrites an existing run directory and never writes evidence relative to its own worktree. Stage summaries only reference these records; they never replace them. If `$ACP2_EVIDENCE` is unset, the task writes no PASS and records `NOT_VERIFIED` with the gap "evidence root unprovisioned".
- **Receipt protocol:** A does not define a second receipt schema. The single authority is B01's `acceptance-result.schema.json`. A references that schema by version and digest and does not restate, rename or add a second copy of its canonical fields, including `candidate_revision`, `candidate{server_revision,client_revision,harness_revision}`, `evidence_kind`, `job_id`, `attempt`, `release_id`, `submission_id`, `negotiated_protocol` and `platform`; their single canonical location is B01's. A adds only an `extensions.science` object. The receipt identifier is `(owner, scenario, phase, run_id)`; `phase` uses B01's frozen enum and registered namespace extensions, never bare `fixture`/`live`. A requests the concrete phase in the B01 registry: `main` for `science:installed-singlepoint`, `science:validate-artifacts` and `science:installed-examples`, and `da-evidence` for `science:validate-da-ts`, `science:validate-da-irc` and `science:validate-da-report`; if B01 registers a different value, the registry wins. A uses `owner="A"` and `scenario="science:<short-name>"` (for example `science:installed-singlepoint`), while the CLI may keep the short alias. A delivers one valid and one invalid sample to B01 as soon as the draft schema exists (todo 4), not at A17 or after B05; `json.tool` alone is never sufficient.


- **Budget before execution:** the run spec carries total, phase, scenario and per-unit caps, and the harness refuses to submit when any cap is exhausted. Counts are separated into top-level TaskRef, Recipe step, QC invocation, attempt, bsub/LSF submission and (for the experience track) model request. Queue wait, scientific runtime, client request and overall acceptance deadlines are separate fields; a tool timeout preserves the task reference for reconciliation and never auto-resubmits.
- **Lint baseline:** the platform owner records the full pre-existing Ruff JSON diagnostics (rule, path, line/column and a stable fingerprint), not only counts or rule distributions. A compares changed files and new fingerprints against that baseline; a non-zero baseline command is expected and is not itself a failure. Resolution of old diagnostics is a separate task and never a gate on the science track. Full-repository cleanup is never a condition of a science task.
- **Cross-track ledger fields (R3-03/R4-03):** `science/cross-track-dependencies.json` uses artifact-level rows with `producer`, `consumer`, `artifact_id`, `version`, `digest`, `readiness` (`contract_ready | implementation_ready | integration_ready | verified`) and an explicit `and`/`or` precondition list. A never models a whole B/C task as a single readiness bit. `phase` values are B01's frozen enum plus its registered namespace extensions; A does not add bare `fixture`/`live` phase values.
- **Failure evidence:** negative tests are first-class. A contract that rejects an invalid fixture must assert the specific error and must not normalize, coerce or silently fill the invalid value.
- **No fake success:** `--help` and `--dry-run` may exit zero but never produce `PASS`. A missing prerequisite writes `NOT_VERIFIED` with the exact missing item and exit code 2. A locally constructed counterexample is labelled as constructed, never as a real run. `$ACP2_ARTIFACT_MANIFEST` is the platform-provided absolute path to the authorized artifact manifest; it is never synthesized by the science track.
- **Architecture guards:** run `tests/test_architecture_invariants.py`, `tests/test_cccp_isolation.py` and `tests/test_recovery_fixtures_smoke.py` at the start, after each phase, and in the final wave.
- **Main-plan acceptance gate 6 (plugin ecosystem):** wrong plugin versions, untrusted code, missing dependencies, unknown licenses, capability conflicts and historical artifacts after an upgrade must fail safely and remain readable. This is proven by the plugin conflict/upgrade suite and H5, not by a document claim.
- **Remote-node import budget:** new production modules may import only the standard library plus dependencies already present on remote compute nodes (`requirements-node.txt`: numpy, rdkit, pyyaml). They must not make scipy, matplotlib or openpyxl module-level imports. Any additional import need becomes a `science/requests/` dependency request to the platform owner; otherwise a later platform dispatch onto an LSF node would fail outside this track's tests.

### Evidence record contract

Every task's immutable `result.json` uses B01's canonical schema and field names; A does not restate or rename them. A's additions live only under `extensions.science`: `contract_version`, `assertions`, `prerequisites`, `native_work_root`, `interpreter`, `software_version`, `fixture_id` and `run_key`. If B01 already defines any of these, B01's location wins and the extension omits it. `candidate_revision`, `candidate{server_revision,client_revision,harness_revision}`, `evidence_kind`, `job_id`, `attempt`, `release_id`, `submission_id`, `negotiated_protocol` and `platform` keep B01's single location and spelling. Null semantics are fixed: a standalone CCCP run has null platform identifiers and requires the native work root/interpreter/software; a fixture record sets `evidence_kind=fixture` and carries no live platform identifiers; a live run requires the platform identifiers; an aggregate record carries null/absent platform identifiers, references child records and makes no new scientific claim. `FAIL` is never downgraded. `NOT_VERIFIED` names the missing interpreter, binary, license, cluster window, run key or authorized artifact.


## Execution strategy

### Cross-track prerequisites from the 2026-10-21 review

A consumes these platform/experience handoffs; it does not re-implement them:

- **B00 re-frozen baseline (Q04):** `$ACP2_BASE_REV` is the platform owner's final re-frozen debug baseline, not automatically `096cdffe...`. B00 publishes either a full commit SHA or a reproducible patch manifest with digest; A verifies the worktree actually contains the pending business fixes and records the patch/diff digest. A never claims the old HEAD is the final baseline.
- **B01 receipt protocol (Q01/R2-02):** B01's `acceptance-result.schema.json` is the single protocol. A references it by version and digest, adds only `extensions.science`, and delivers one valid and one invalid sample when the draft schema is published (todo 4), before B05/A17. Scenario IDs use `science:*`.
- **B04/B05 order (Q03):** H2 consumes B05's stdio launch file and minimal read-only capabilities. B04's initial close covers only the single Controller, HTTP/CLI client and second-Controller rejection; A does not treat a premature MCP-adapter check as H2 evidence. B06 then delivers the full use-case bindings.
- **Early test candidates (Q04):** B publishes an H2 test candidate and a P1 test candidate before their real tests. A records the actual server, client and harness revisions; a single `candidate_revision` is not accepted as proof of all three.
- **Scope decision (Q05):** A executes the full P0-P5 evidence-gated plan. P0/P1 is the first delivery batch; P2-P5 continue only when their listed contracts and environments exist. A does not idle when a phase is pending: it continues static contract/fixture work and records `NOT_VERIFIED` for any blocked real-run row. A's final H7 is not conditioned on B26.
- **B08 crash windows (Q06):** A-20 consumes the platform evidence for the four crash windows (reservation before persist, persisted before submission start, submitted with lost response, restart replay). A verifies that no duplicate directory, reservation, QC process or bsub was created and no accepted task is permanently undispatched.
- **Global dependency ledger (R2-06):** B00/B03 (the platform owner's designated ledger steps) create and aggregate the total `runtime/dependencies.json`, including the logical-ID-to-todo mapping and validation for missing producers, dangling references and cross-track cycles. A submits its `science/cross-track-dependencies.json` rows to that aggregate and does not claim ownership of the total ledger.
- **A-12a boundary (Q07):** B Data owns authorization, revocation, freeze and export of an immutable input snapshot. A's harness checks the platform binding and export eligibility. CCCP production code validates only content digest, artifact role, schema/generation as opaque provenance, and scientific identity; it never infers permission or current-generation authority.
- **Cross-track dependency ledger (Q08):** `science/cross-track-dependencies.json` records artifact-level rows as defined in the Verification strategy (task ID, product/artifact ID, producer, consumer, phase, version, digest, readiness and explicit and/or precondition). It is generated/checked against the plan and includes only A's external edges; B owns the total ledger.
- **DA verdict split (Q09/R4-05).** scenario `result`, scientific claim `status` and H4 closure are three independent records. A scenario can PASS while its claim is `inconclusive`; H4 closes only with the required real positive evidence. A C-side stop-adding-compute deadline is not itself a scientific failure; A reads one frozen run spec with `science_deadline_seconds`, `export_budget_seconds`, `aggregate_budget_seconds`, a shared `started_at` and the queue-accounting policy, and judges the actual completed operations and the reserved evidence-export window. Only an actual overrun or missing required operation fails the scenario.
- **C-owned requests (Q10, Q13, Q16):** C's `p2-closeout` self-reference, the live DSH tool-allowlist evidence and the final shared resource window are requests to C/B; A supplies the science artifacts and consumes the returned evidence.

### R3 decisions (A-owned, 2026-10-21)

- **R3-01 Recipe is a scientific definition, not an execution engine.** A owns `StepRef` (the typed record `{step_id, capability_or_task_kind, output roles}`)/`OutputRef` semantics and the typed DAG definition; B owns the execution loop, persistence, retries and cancellation. A never adds a loop, second dispatcher or hidden sub-task payload. The handoff to B18 is the compiled definition plus output-reference selectors, not a running orchestrator.
- **R3-06 two-stage identity.** A freezes the plan-definition digest before approval (recipe, initial inputs, method, versions, parameters, dependency selectors and allowed bounded alternatives). Step execution identity is materialized at dispatch from actual upstream digests, parameters and implementation version. A approval digest excludes mutable management fields (`approval_ref`, approval status, timestamps); unknown scientific fields are not silently ignored for execution or recovery.
- **R3-07 three validation layers.** JSON structure (type/required/enum/bounds/refs), CCCP scientific semantics (mapping/roles/method/state/units/applicability) and Runtime platform constraints (project/permission/enablement/resources) are distinct. Unknown execution fields are rejected for new execution requests; historical reads preserve unknown fields but gain no execution/recovery eligibility; unknown major versions never execute. The UI subset uses standard `minimum/maximum` and nullable types; custom UI-only keys are not expected to be enforced by a general validator.
- **R3-09 role-owned interpreters.** A's development tests use `$ACP2_A_PYTHON` and assert `cccp.__file__` resolves inside the A worktree; platform integration evidence uses `$ACP2_INTEGRATION_PYTHON` and the frozen candidate; wheel probes use a separately recorded clean interpreter. The live artifact-validation rows 23/25 use A's harness under `$ACP2_A_PYTHON` as science-artifact validation and do not claim A executed the frozen integration service. Reusing `$ACP2_PYTHON` for every role is not accepted.
- **R3-10 static/live decoupling.** Static validators (todo 22), the artifact-snapshot consumer (todo 24) and plugin manifest/registry (todos 38-42) depend on contracts and synthetic real-format fixtures, not on a successful real singlepoint or DA positive. Their live rows remain `NOT_VERIFIED` until the corresponding real environment exists.
- **R3-11 DA capability split.** ADR-008 distinguishes the execution/validation chain (known starting geometry or candidate, frozen method) from research candidate search (user input, bounded proposals). A labels the source of every starting structure; a known TS is never presented as a search success. Search capability is not proven by the known-candidate chain or by a control-loop PASS. A owns `science/requests/search-contract.md` (input categories, candidate source/count, generation steps, screening/rejection reasons, validators and success criteria); A-20 records search as a separate row. If search is in H4 scope and unexecuted, H4 stays open; if it is deferred, the owner must ratify the scope reduction. Budget is derived from representative step timings before the full chain is frozen.
- **R3-12 CDXML scope.** A declares CDXML outside the initial supported input scope; B/C must not claim a complete ChemDraw input chain until an owner and parser are added. The supported initial formats are the existing structure/reaction inputs, with unsupported features rejected explicitly.
- **R3-14 early packaging preview.** Todo 2 records the distribution/top-level package ownership and a minimal wheel smoke plan early; the full independent package remains P5.
- **B18 execution handoff (R3-01):** A delivers the compiled recipe definition plus `OutputRef` selectors to B18; B18a/b/c own the execution loop, persistence, retries and recovery. A registers this edge in the cross-track ledger with readiness and never executes the DAG itself.
- **R3-04 / R4-01 / R5-02 selectivity producer.** A-25/A-26 use the single registered reference `owner=C, scenario=experience:cross-domain-live, subcase=selectivity` (not a separate `selectivity-live` scenario). A provides the two-path fixture and criteria (A-25a); C T-40a makes the single research submission; B23b executes and exports the authorized artifacts; A-25b audits them; C T-40b presents and signs. A records the request in `science/requests/selectivity-producer.md` and the cross-track ledger, and cannot close the selectivity row alone. `subcase` is display metadata, not public identity: A references the record only when `(owner, scenario, phase, run_id)` is unique, with the registered `experience:selectivity` phase
; if two subcases share one public key, A records `NOT_VERIFIED` instead of merging them.
- **D01 approval vs automation.** A's checks are agent-executed; user approval and independent reproduction are separate facts owned by the user/B/C. A never simulates a human approval as an automated PASS.
- **D10/D11 science-field rules.** Provenance requires a method/software identity only for results that depend on an electronic-structure method; pure analysis records algorithm/version/input digest instead. Ensemble weights use a declared raw-or-normalized input policy with explicit tolerance and recorded normalization provenance.

### Parallel execution waves

Waves are phase-aligned. Within a wave, dispatch a todo only when every dependency in the matrix is complete; independent todos in the same wave may run concurrently. A phase gate is a hard barrier: the next phase's real-run todos do not start until the gate's required static rows pass, but later-phase static contract work may begin once its listed dependencies are satisfied.

- **Wave 1 - Frozen baseline:** todos 1-2.
- **Wave 2 - Contract foundations:** todos 3, 5, 7.
- **Wave 3 - Contract fixtures and public types:** todos 4, 6, 8, 9.
- **Wave 4 - Type completion and registry:** todos 10, 11.
- **Wave 5 - Binding and recipe:** todos 12, 13.
- **Wave 6 - Recipe identity and P0 gate:** todos 14, 15, 16, 17.
- **Wave 7 - P1 fixtures and harness:** todos 18, 20.
- **Wave 8 - P1 standalone and static science:** todos 19, 21, 22, 24.
- **Wave 9 - P1 artifact/LSF validation and gate:** todos 23, 25, 26.
- **Wave 10 - P2 static Diels-Alder:** todos 27-32.
- **Wave 11 - P2 real Diels-Alder and gate:** todos 33-37.
- **Wave 12 - P3 plugins:** todos 38-42.
- **Wave 13 - P4 cross-domain and selectivity:** todos 43-47.
- **Wave 14 - P5 distribution and final gate:** todos 48-51.

### Dependency matrix

> `Depends on` is the authoritative dispatch column. `Blocks` is a non-normative convenience view and may include downstream tasks; F1 checks `Depends on` against each todo's `Blocked by` and never treats `Blocks` as a scheduling source.

| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | - | 2-51 | none |
| 2 | 1 | 5 | none |
| 3 | 1 | 4, 11 | 5, 7 |
| 4 | 3 | 17 | 6, 8 |
| 5 | 1, 2 | 6 | 3, 7 |
| 6 | 5 | 9 | 4, 8 |
| 7 | 1 | 8 | 3, 5 |
| 8 | 7 | 9, 10 | 4, 6 |
| 9 | 6, 8 | 10 | none |
| 10 | 9 | 27 | none |
| 11 | 4 | 12, 13 | 10 |
| 12 | 11 | 14 | 10 |
| 13 | 11 | 14 | 10 |
| 14 | 12, 13 | 17 | none |
| 15 | 14 | 16 | none |
| 16 | 8, 15 | 29 | none |
| 17 | 14 | 18, 20 | none |
| 18 | 17 | 19, 21 | 20 |
| 19 | 18 | 21 | 20 |
| 20 | 17 | 21 | 18, 19 |
| 21 | 19, 20 | 22, 23, 26 | none |
| 22 | 17 | 23, 26 | none |
| 23 | 21, 22, 24 | 25, 26 | none |
| 24 | 17 | 23, 25, 26 | 22 |
| 25 | 23, 24 | 26 | none |
| 26 | 21, 23, 24, 25 | none | none |
| 27 | 10, 17 | 28 | none |
| 28 | 27 | 29, 30 | none |
| 29 | 16, 28 | 30 | none |
| 30 | 29 | 31, 32 | none |
| 31 | 28, 30 | 33, 36 | none |
| 32 | 30, 31 | 33, 34, 36 | none |
| 33 | 31, 32 | 34, 35, 37 | none |
| 34 | 33 | 36, 37 | none |
| 35 | 30, 33 | 36 | 34 |
| 36 | 33, 34, 35 | 37 | none |
| 37 | 33, 34, 35, 36 | none | none |
| 38 | 12, 17 | 39, 40 | none |
| 39 | 38 | 40, 41, 42 | none |
| 40 | 39 | 41, 42 | none |
| 41 | 40 | 42 | none |
| 42 | 39, 40, 41 | 43 | none |
| 43 | 42 | 44, 46, 47 | 45 |
| 44 | 43 | 46, 47 | 45 |
| 45 | 42 | 46 | 43, 44 |
| 46 | 43, 44, 45 | 47 | none |
| 47 | 43, 44, 45, 46 | 48 | none |
| 48 | 47 | 49, 50 | none |
| 49 | 48 | 50, 51 | none |
| 50 | 49 | 51 | none |
| 51 | 49, 50 | none | none |

### Phase gates

- **Baseline and evidence roots:** `$ACP2_A_SOURCE` is the science worktree, `$ACP2_A_PYTHON` is A's role-owned frozen interpreter and `$ACP2_EVIDENCE` is the required platform-issued evidence root; its location is chosen by the platform owner (the index's illustrative mapping is `reports/acp2_implementation/<candidate-label>/`). A never creates a second evidence root, never writes into the platform's per-run capture and never writes evidence relative to its own worktree.
 All `science/*.md`, `science/examples/**`, `science/requests/**` and `science/handoff-H*.json` paths are relative to `docs/reference/next-change/acp2-contracts/science/` (Plan A line 52).
- **P0 gate (todo 17):** static capability contracts, registry and binding; reaction/recipe research types are listed as in-progress unless already delivered. Real computation is explicitly not run.
- **P1 gate (todo 26):** real singlepoint plus optimized/frequency artifacts and Local/LSF reconciliation. If LSF is unavailable, H3 stays open.
- **P2 gate (todo 37):** three independent records: (a) scenario `result` - did the required operation, assertions and evidence delivery complete; (b) scientific claim `status` - `supported / refuted / inconclusive / not_evaluated` per claim, bound to validator and inputs; (c) H4 closure - requires a real valid TS (one imaginary mode + mapped mode), bidirectional IRC with both endpoints, and comparable energies, plus honest negative cases. A PASS on a "correctly reports inconclusive" negative scenario does not replace the required positive evidence. If the budget ends without a valid positive, H4 stays open and A continues independent work. Search is part of P2/H4 by default: A-20 consumes `B20a-search`; a missing or `NOT_VERIFIED` producer keeps H4 open, and any deferral requires owner-ratified `search_scope=DEFERRED` synchronized across B/C and the main plan. A control-loop or known-candidate PASS never substitutes for search.
- **P3 gate (todo 42):** one external plugin distribution discovered, enabled, invoked and safely rejected on conflicts.
- **P4 gate (todo 47):** NMR/thermochemistry reuse plus selectivity evidence and version regression. A required selectivity row that is `NOT_VERIFIED` keeps H6/P4 open; it is not labelled verified.
- **P5 gate (todo 51):** A signs its own science H7 from B25a's candidate plus A-27b/A-28 verification; A does not wait for B26 or B25b. B aggregates after A-29 and C35.
- **Scenario names are inputs, not contracts.** A's receipt IDs are `science:installed-singlepoint`, `science:validate-artifacts`, `science:validate-da-ts`, `science:validate-da-irc`, `science:validate-da-report`, `science:installed-examples`; the CLI may keep the short alias. The B/C references are read from the platform registry: `dsh-optfreq-live`, `da-runtime`, `da-research-live`, and `owner=C, scenario=experience:cross-domain-live, subcase=selectivity`. If a name/subcase is absent, the row records `NOT_VERIFIED` and A never invents a platform scenario.

## Todos

- [x] 1. A-00a: Freeze the science baseline, worktree and ownership boundary
  What to do / Must NOT do: Read the root and nested knowledge bases, Plan A, the execution index, the review and the acceptance runbook. Consume B00's final re-frozen baseline: accept either a full commit SHA or a reproducible patch manifest with digest, and treat `096cdffe...` as the historical reference, not an assumed final base. Record `git rev-parse HEAD`, `git status --short`, the current branch, the frozen base revision, every dirty path, the patch/diff digest, the worktree path, `$ACP2_A_PYTHON`, the absolute `$ACP2_EVIDENCE` root and the three package import locations. Create or reuse the science worktree so it actually contains the pending business fixes; write `science/baseline.md` and `science/path-map.md`. Do not edit, clean, reset or stash any existing working-tree changes; do not start from the old HEAD and assume the fixes are carried.
  Closes: GAP-13
  Parallelization: Wave 1 | Blocked by: none | Blocks: 2-51
  References: `AGENTS.md` (root), `src/cccp/AGENTS.md`, `tests/AGENTS.md`; `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:11-52`; `docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md:33-77`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_20261021.md:79-87`; `docs/reference/next-change/ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md:483-526`.
  Acceptance criteria (agent-executable): `git rev-parse HEAD` matches B00's published base SHA or the patch manifest reproduces that tree; the patch/diff digest and every dirty path are recorded; `acp2/a-science` is the active worktree; `"$ACP2_A_PYTHON" -c "import cccp, acp, acp_services; print(cccp.__file__, acp.__file__, acp_services.__file__)"` resolves inside `$ACP2_A_SOURCE`; `baseline.md` names the base, patch, delivery and code-candidate revisions and the absolute evidence root; `git status --short` after setup shows no new product-file edits from this task.
  QA scenarios (name the exact tool + invocation): happy: `cd "$ACP2_A_SOURCE" && git rev-parse HEAD && git diff --stat` and confirm the patch digest and import paths; failure: point `$ACP2_BASE_REV` at the old `096cdffe...` HEAD while B00's patch is unapplied and confirm the guard writes `NOT_VERIFIED` and creates no science files. Evidence `$ACP2_EVIDENCE/A/A-00/<run-id>/result.json`
  Commit: Y | `chore(cccp): lock ACP2 science baseline and ownership boundary`
  Recommended task executor category: deep-low - baseline and cross-track ownership reasoning that the executor can settle from the cited documents.

- [x] 2. A-00b: Inventory workflows, task kinds, existing APIs and compatibility surface
  What to do / Must NOT do: Build the frozen inventory: the fifteen active workflows, the fifteen task kinds and their mapping (including cases where names differ), the request/result/context contracts, the task cores, the capability matrix and selection seam, the plugin layer, the scientific products and the compatibility forwarders. Write the mapping and the not-implemented list into `science/baseline.md`; generate `science/cross-track-dependencies.json` with A's external edges (task ID, product ID, producer, consumer, phase, precondition, artifact_id, version, digest, readiness `contract_ready|implementation_ready|integration_ready|verified`, and explicit and/or conditions) and check it for missing IDs and cycles. Add `science/packaging-preview.md` with the current distribution/top-level package ownership, files/assets, dependency closure and a minimal wheel smoke plan; reuse the existing `packaging/README.md`, role build configurations and `scripts/build_release.py`, and record what already exists before proposing any gap; flag any missing package-data or import-boundary risk early. Record which rows are static facts and which need later real evidence. Do not re-plan existing packaging capability as a blank-slate task. Do not reopen already-closed debug defects; do not infer a one-to-one workflow-to-task mapping.
  Closes: GAP-1
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 5
  References: `src/acp/workflows/registry.py`; `src/cccp/calculation/requests.py:54-72`; `src/cccp/calculation/tasks/__init__.py:5-37`; `src/cccp/calculation/selection.py:188-288`; `src/cccp/backends/matrix.py:82-310`; `src/cccp/plugin/metadata.py:155-242`; `tests/test_architecture_invariants.py:91-123`; `packaging/README.md`; `scripts/build_release.py`; `reports/architecture_repair_20261022/REPORT.md`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R5_20261022.md:174-186`.
  Acceptance criteria (agent-executable): the inventory table has exactly fifteen active workflows and fifteen task kinds; every workflow row has an explicit mapping or an explicit not-a-task-kind reason; every mismatched name is called out; `cross-track-dependencies.json` parses, every referenced ID exists, the readiness/AND-OR schema is used, and a cycle checker reports none; the ledger enumerates the P2/P4 external edges (`B20a-exec`, `B20a-search`, `B23b`, `C T-40a`, `C T-40b`, `B08`) with artifact-level readiness and digest; `packaging-preview.md` names each top-level package/file/asset owner and the wheel smoke command, records the existing three role configs plus `scripts/build_release.py`, and proposes only the science delta;
 `"$ACP2_A_PYTHON" -m pytest tests/test_architecture_invariants.py tests/test_cccp_isolation.py tests/test_recovery_fixtures_smoke.py -q` exits 0.
  QA scenarios (name the exact tool + invocation): happy: run the guard suites and validate the cross-track ledger plus the packaging preview; failure: add a dependency on a nonexistent B product or a package-data path missing from the preview and confirm the ledger/preview validator rejects it. Evidence `$ACP2_EVIDENCE/A/A-00/<run-id>/result.json`

  Commit: Y | `docs(cccp): record ACP2 workflow/task compatibility inventory`
  Recommended task executor category: unspecified-low - mechanical inventory with a few cross-file lookups.

- [x] 3. A-01a: Author the capability, task-binding and error schemas
  What to do / Must NOT do: Under `docs/reference/next-change/acp2-contracts/science/`, write `capability.schema.json`, `task-binding.schema.json` and `errors.schema.json`. Freeze the scientific fields: schema, schema version, capability id, implementation id, input schema, output schema, preconditions, completion checks, limitations, compatibility, provenance requirements and execution semantics. Separate the three validation layers: JSON structure (type/required/enum/minimum/maximum/refs), CCCP scientific semantics, and Runtime platform constraints. Add `schema_dialect` (the JSON Schema dialect and supported keyword subset) and a `ui_subset_version`; use standard `minimum`/`maximum` and nullable types rather than custom `range`/`nullable` keywords. Define unknown-field policy: new execution requests reject unsupported execution fields; historical reads preserve unknown fields but gain no execution/recovery eligibility; unknown major versions never execute. Define error records for unknown major version, duplicate capability id, unknown implementation, disabled provider, unavailable binary and invalid scientific input. In the task-binding schema, reference the existing request/result/context serialization and state explicitly that the context is never serialized. Do not add platform job status, account, queue or price fields.
  Closes: GAP-1
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 4, 11
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:87-95`; `src/cccp/calculation/requests.py:1761-1881`; `src/cccp/calculation/results.py:1386-1560`; `src/cccp/calculation/context.py:33-120`; `src/cccp/calculation/contracts.py:95-119`; `docs/reference/current/ACP_CCCP_Task_API_DevDoc.md:171-400`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R3_20261021.md:223-243`.
  Acceptance criteria (agent-executable): all three files parse with `json.load`; each declares `$schema`, a stable `$id` and a positive schema version; the capability schema requires every frozen field, declares `schema_dialect` and `ui_subset_version`, and rejects unknown major versions; the task-binding schema references the existing schema versions 1 and contains no `TaskContext` field; the error schema enumerates the six defined error codes; the unknown-field and three-layer policy is documented and covered by a positive/negative corpus.
  QA scenarios (name the exact tool + invocation): happy: `"$ACP2_A_PYTHON" -c "import json,pathlib; [json.load(p.open()) for p in pathlib.Path('docs/reference/next-change/acp2-contracts/science').glob('*.schema.json')]"`; failure: mutate a schema version to an unknown major and confirm the contract test (todo 4) rejects it. Evidence `$ACP2_EVIDENCE/A/A-01/<run-id>/result.json`
  Commit: Y | `docs(cccp): add ACP2 capability and task-binding schemas`
  Recommended task executor category: deep-high - a cross-package contract whose field semantics must stay compatible with the existing envelopes.

- [x] 4. A-01b: Add capability examples, positive/negative fixtures, contract test and first handoff
  What to do / Must NOT do: Add `science/examples/capability-energy.json` and `science/examples/task-binding-singlepoint.json`; add the real fixture files `tests/fixtures/acp2/science/contracts/capability_valid.json`, `capability_unknown_version.json`, `capability_duplicate_id.json`, `capability_unsupported_provider.json`, `capability_unknown_execution_field.json` and `historical_read_unknown_field.json` (never literal braces in a path). Deliver `tests/test_acp2_science_schema.py` first, using only standard-library JSON parsing plus a restricted assertion helper that declares its supported keyword set and rejects unsupported schema constraints rather than silently skipping them; no third-party schema engine. When B01's draft `acceptance-result.schema.json` is available, also deliver `science/examples/receipt-valid.json` and `receipt-invalid.json` and validate them through the shared validator; if the draft is not yet published, record `NOT_VERIFIED` and deliver immediately when it is, without waiting for B05 or A17. Publish `science/handoff-H1.json` in its minimal form with file digests, field owners, positive/negative fixtures and compatibility scope. Do not treat a described capability as installed or available.
  Closes: GAP-1, GAP-4
  Parallelization: Wave 3 | Blocked by: 3 | Blocks: 17
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:87-95`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R2_20261021.md:64-74`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R3_20261021.md:223-243`; `tests/AGENTS.md:40-47`; `pyproject.toml:24-39`; `src/cccp/plugin/metadata.py:89-123`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_schema.py -k capability -q` exits 0; the valid fixture is accepted; each negative fixture produces its named error (`capability_unknown_version` → `unknown_major_version`, `capability_duplicate_id` → `duplicate_capability_id`, `capability_unsupported_provider` → `unknown_implementation`); an unknown execution field is rejected for execution; a historical payload with an unknown field remains readable but is not eligible for execution or recovery; `handoff-H1.json` lists all contract files with SHA-256 and the required/optional/default/enum/unit/rejection/serialization fields for each; when B01's draft exists, the two receipt samples are present and the shared validator accepts the valid one and rejects the invalid one; no test imports `jsonschema`.
  QA scenarios (name the exact tool + invocation): happy: run the `-k capability` test and print the fixture verdicts; failure: change the valid fixture capability id to a reserved task name and confirm the test rejects it. Evidence `$ACP2_EVIDENCE/A/A-01/result.json`
  Commit: Y | `test(cccp): add ACP2 capability contract fixtures and handoff`
  Recommended task executor category: unspecified-high - multi-file test and fixture work with a clear contract.

- [x] 5. A-02a: Author the reaction and question schemas plus semantic rules
  What to do / Must NOT do: Write `reaction.schema.json` and `question.schema.json`; freeze structure references, species, atom mapping, bond changes, charge, multiplicity, stereochemistry, conditions and declared input uncertainty. The question schema freezes question type, hypothesis references, required evidence types and comparison conditions, and excludes model roles, cost and job status. Write `science/semantic-rules.md` covering stable atom ids on both sides, one-to-one maps, element and stoichiometry conservation, explicit unknown stereo/conditions, and the distinction between cannot-interpret-the-input and representable-but-not-applicable. Declare CDXML outside the initial supported input scope and align the supported list with the experience track: MOL/SDF/SMILES plus the existing structure upload; CDXML is `DEFERRED`. Unsupported features are rejected explicitly rather than silently dropped. B/C must not claim a complete ChemDraw/CDXML chain until an owner and parser are added. Do not add LLM roles, budgets or platform state.
  Closes: GAP-1
  Parallelization: Wave 2 | Blocked by: 1, 2 | Blocks: 6
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:97-105`; `src/cccp/analysis/pes/atom_mapping.py:26-87,114`; `src/cccp/analysis/pes/bond_changes.py`; `src/cccp/calculation/contracts.py:36-40,283-313`.
  Acceptance criteria (agent-executable): both schemas parse; the semantic rules document contains one rule per invariant with a rejecting fixture named; the reaction schema requires stable atom identity and mapping uniqueness; the question schema rejects any field named role, cost, price or job status.
  QA scenarios (name the exact tool + invocation): happy: parse both schemas and grep the rule table for all five invariants; failure: add a `job_status` field to a copy of the question schema and confirm the contract test rejects it. Evidence `$ACP2_EVIDENCE/A/A-02/result.json`
  Commit: Y | `docs(cccp): add ACP2 reaction and question schemas`
  Recommended task executor category: deep-high - scientific identity semantics that must not be coerced.

- [x] 6. A-02b: Add reaction/question examples, positive/negative fixtures and tests
  What to do / Must NOT do: Add `science/examples/reaction-da.json`, `science/examples/question-activation.json` and the `tests/fixtures/acp2/science/contracts/reaction/` positive and negative set: valid simple reaction, duplicate map, missing product, charge mismatch and ambiguous stereo. Deliver `tests/test_acp2_science_reaction_contract.py` before declaring the task done. Assert that unknown stereochemistry stays unknown, that a net reaction never claims a single elementary step, and that conservation violations are rejected.
  Closes: GAP-1, GAP-4
  Parallelization: Wave 3 | Blocked by: 5 | Blocks: 9
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:97-105`; `src/cccp/analysis/pes/atom_mapping.py:114`; `tests/test_cccp_task_irc.py:91-109`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_reaction_contract.py -q` exits 0; each negative fixture asserts its own error; the valid example round-trips through JSON without map or charge changes; no fixture contains an invented stereo assignment.
  QA scenarios (name the exact tool + invocation): happy: run the contract test and print the accepted/rejected fixture table; failure: remove the product structure from the valid example and confirm the test rejects it. Evidence `$ACP2_EVIDENCE/A/A-02/result.json`
  Commit: Y | `test(cccp): add ACP2 reaction and question contract fixtures`
  Recommended task executor category: unspecified-high - multi-file contract fixtures and assertions.

- [x] 7. A-03a: Author the evidence, provenance and recipe schemas
  What to do / Must NOT do: Write `evidence.schema.json`, `provenance.schema.json` and a minimal `recipe.schema.json`, plus `science/evidence-semantics.md` describing the four claim statuses, execution-versus-claim separation, source/digest requirements, explicit units and the missing-check vocabulary. Freeze per-claim statuses `supported`, `refuted`, `inconclusive`, `not_evaluated`, plus validator id/version, source references, missing checks, units and limitations. Freeze provenance derivation: for electronic-structure results require method/software/basis/state; for pure analysis use algorithm/version/input digest instead of inventing a quantum method. Include temperature, standard state and reference state where applicable. The recipe schema must include steps, typed references, edges, required/diagnostic-only flags, validators, applicability and bounded alternatives. Do not map execution terminal states to claim support; do not put queue, account or job status in the recipe.
  Closes: GAP-1
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 8
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:107-115`; `src/cccp/calculation/contracts.py:95-119`; `src/cccp/analysis/identity.py:118-151`; `docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md:92-98`.
  Acceptance criteria (agent-executable): all three schemas parse; the evidence schema rejects an unknown claim status; the recipe schema contains no field whose name matches job, queue, account, price or status; the provenance schema requires a software version and, for electronic-structure results, a method identity, while pure-analysis results require algorithm/version/input digest instead of an invented quantum method.
  QA scenarios (name the exact tool + invocation): happy: parse the schemas and assert the four claim statuses plus the forbidden-field list; failure: add `job_status` to a recipe copy and confirm rejection. Evidence `$ACP2_EVIDENCE/A/A-03/result.json`
  Commit: Y | `docs(cccp): add ACP2 evidence, provenance and recipe schemas`
  Recommended task executor category: deep-high - the execution-versus-claim separation is the central scientific contract.

- [x] 8. A-03b: Add evidence/provenance fixtures, tests and the H1 update
  What to do / Must NOT do: Add the contract fixtures: negative cases (missing IRC, corrupt digest, partial output, unknown evidence status) and positive/historical cases (completed-but-inconclusive, historical schema that must stay readable through the adapter). Deliver `tests/test_acp2_science_evidence_contract.py`; assert that a missing source or digest mismatch cannot be supported, that model text cannot impersonate a validator, that an unknown evidence status is rejected, and that completed-but-inconclusive and historical payloads are accepted. Update `science/handoff-H1.json` with the new files, versions and compatibility range; do not rewrite the previously published minimal fields.
  Closes: GAP-1, GAP-4
  Parallelization: Wave 3 | Blocked by: 7 | Blocks: 9, 10
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:107-115`; `src/cccp/calculation/results.py:41-58`; `src/cccp/calculation/contracts.py:95-119`; `docs/reference/next-change/ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md:498-522`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_schema.py tests/test_acp2_science_evidence_contract.py -q` exits 0; the four negative fixtures each fail for the intended reason; the two positive/historical fixtures load successfully; the handoff file validates as JSON and keeps the earlier entries.
  QA scenarios (name the exact tool + invocation): happy: run both test modules, print the four negative fixture verdicts plus the model-text impersonation assertion; failure: change one claim from inconclusive to supported without a source and confirm the test fails. Evidence `$ACP2_EVIDENCE/A/A-03/result.json`
  Commit: Y | `test(cccp): add ACP2 evidence and provenance contract fixtures`
  Recommended task executor category: unspecified-high - multi-file fixtures plus a versioned handoff update.

- [x] 9. A-04a: Implement the reaction and question public types
  What to do / Must NOT do: If the frozen baseline has no equivalent, create `src/cccp/analysis/research/reaction.py` and `question.py` with frozen dataclasses, explicit `to_dict`/`from_dict`, schema-version checks and a package `__init__.py` that exports only. Reuse the existing structure, electronic-state and method types; do not create a second atom or spin model. Historical reading uses a recognizable schema adapter and preserves unknown fields instead of filling defaults.
  Closes: GAP-2
  Parallelization: Wave 3 | Blocked by: 6, 8 | Blocks: 10
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:117-125`; `src/cccp/calculation/contracts.py:36-119,283-313`; `src/cccp/analysis/structure/models.py`; `src/cccp/analysis/pes/atom_mapping.py:26-87`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_reaction_contract.py -q` exits 0; a positive example round-trips byte-identically under canonical JSON; importing the new module does not import `cccp.calculation.tasks`, `cccp.qc` or `acp`; the module has no executable side effects.
  QA scenarios (name the exact tool + invocation): happy: round-trip the valid reaction example and compare canonical JSON; failure: feed a reaction with a duplicate map and assert the type raises before construction. Evidence `$ACP2_EVIDENCE/A/A-04/result.json`
  Commit: Y | `feat(cccp): add public reaction and question science types`
  Recommended task executor category: unspecified-high - standard pure-type implementation with strict serialization.

- [x] 10. A-04b: Implement evidence/provenance types, adapters and import-purity test
  What to do / Must NOT do: Add `src/cccp/analysis/research/evidence.py` and `provenance.py`; wire `ArtifactRef` and `Provenance` from the calculation contracts without redefining them. Provide version adapters for historical payloads; unknown values stay unknown. Add an import-purity test that proves loading the research package does not pull task execution, QC interfaces or platform modules.
  Closes: GAP-2
  Parallelization: Wave 4 | Blocked by: 9 | Blocks: 27
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:117-125`; `src/cccp/calculation/contracts.py:95-119`; `src/cccp/analysis/identity.py:132-151`; `tests/test_cccp_isolation.py`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_evidence_contract.py tests/test_cccp_isolation.py -q` exits 0; the import-purity assertion lists no `cccp.qc` or `cccp.calculation.tasks` module in `sys.modules`; a historical payload with a missing field remains missing rather than defaulted.
  QA scenarios (name the exact tool + invocation): happy: import the package in a fresh interpreter and print the forbidden-module diff; failure: add a top-level task import to the module and confirm the purity test fails. Evidence `$ACP2_EVIDENCE/A/A-04/result.json`
  Commit: Y | `feat(cccp): add evidence and provenance science types`
  Recommended task executor category: unspecified-high - pure-type implementation plus a precise import-purity check.

- [x] 11. A-05a: Build the capability contracts and read-only registry
  What to do / Must NOT do: Create `src/cccp/calculation/capabilities/contracts.py` and `registry.py`, plus `tests/test_acp2_science_registry.py` for the generic registry behavior (deterministic ordering, unknown capability, duplicate implementation, incompatible state). Define the scientific descriptor and a read-only query surface with deterministic ordering. Separate capability from implementation and expose distinct described/installed/available/verified projections. Reuse `calculation/selection.py` and `cccp.software.resolve_executable`; do not create a second software resolver or a capability-to-backend selection algorithm. Do not seed the built-in catalog yet; todo 12 does that.
  Closes: GAP-3
  Parallelization: Wave 4 | Blocked by: 4 | Blocks: 12, 13
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:127-135`; `src/cccp/calculation/selection.py:188-288,326-415`; `src/cccp/backends/matrix.py:31-80`; `src/cccp/software.py`; `docs/reference/current/ACP_CCCP_Task_API_DevDoc.md:760-783`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_registry.py -k "order or unknown or duplicate or state" -q` exits 0; two catalog calls with the same descriptors produce identical serialized ordering; querying an unknown capability returns an explicit unsupported result rather than raising an untyped error; the module imports no task module at discovery.
  QA scenarios (name the exact tool + invocation): happy: build the registry twice with two test descriptors and compare serialized catalogs; failure: add two implementations with the same id and confirm a typed conflict error. Evidence `$ACP2_EVIDENCE/A/A-05/result.json`
  Commit: Y | `feat(cccp): add read-only scientific capability registry`
  Recommended task executor category: deep-low - the shape is specified and the existing selection seam is the authority.

- [x] 12. A-05b: Seed and test the built-in capability catalog
  What to do / Must NOT do: Add `capabilities/builtin.py` registering `electronic.energy`, `geometry.optimize`, `vibration.compute` and the pure-analysis `vibration.analyze`. The first three bind to existing task kinds; `vibration.analyze` consumes existing frequency or Hessian products through the analysis layer and must never start a calculation. Other families remain explicitly unsupported. Extend `tests/test_acp2_science_registry.py` covering the four seeded capabilities, deterministic order, conflict ids, unknown provider and incompatible state.
  Closes: GAP-3, GAP-4
  Parallelization: Wave 5 | Blocked by: 11 | Blocks: 14
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:127-135`; `src/cccp/analysis/frequency_products.py:29-133`; `src/cccp/analysis/orca_parser.py`; `src/cccp/calculation/requests.py:54-72`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_registry.py -q` exits 0; the catalog contains exactly those four capability ids at first; `vibration.analyze` declares no subprocess and its resolution path does not import `cccp.calculation.tasks.frequency`; an unsupported family returns `described=false`.
  QA scenarios (name the exact tool + invocation): happy: print the catalog and assert the four ids plus their execution semantics; failure: make `vibration.analyze` resolve to `run_frequency` and confirm the spy test fails. Evidence `$ACP2_EVIDENCE/A/A-05/result.json`
  Commit: Y | `feat(cccp): seed built-in science capabilities`
  Recommended task executor category: deep-high - the analysis-versus-compute distinction is a hard scientific boundary.

- [x] 13. A-06a: Bind capabilities to the existing task API
  What to do / Must NOT do: Create `capabilities/task_binding.py` to convert capability inputs into the existing `TaskRequest` and validate scientific preconditions only. The binding also validates a direct reference to an existing `TaskKind` from the fifteen-task inventory (not only the three compute capabilities), so a recipe step can name an existing task kind without inventing a capability. Execution delegates to `run_singlepoint`, `run_optimize` or `run_frequency` for the seeded capabilities and never adds a new execution path. Keep the existing schema versions and payload types; add fields only through a versioned compatibility path. Write a spy test proving each capability reaches exactly one task implementation and that preflight produces no artifact directory and no subprocess.
  Closes: GAP-3, GAP-4
  Parallelization: Wave 5 | Blocked by: 11 | Blocks: 14
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:137-145`; `src/cccp/calculation/tasks/singlepoint.py:54`; `src/cccp/calculation/tasks/optimize.py:339`; `src/cccp/calculation/tasks/frequency.py:62`; `src/cccp/calculation/requests.py:1761-1881`; `tests/test_cccp_task_singlepoint.py:38-96`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_task_binding.py tests/test_cccp_task_singlepoint.py tests/test_cccp_task_optimize.py tests/test_cccp_task_frequency.py tests/test_architecture_invariants.py -q` exits 0; the spy records exactly one call per capability; preflight leaves `tmp_path` empty; an unsupported capability raises a typed unsupported error rather than degrading to another task.
  QA scenarios (name the exact tool + invocation): happy: bind each of the three compute capabilities and print the single delegated call; failure: make `electronic.energy` call `run_frequency` and confirm the spy test fails. Evidence `$ACP2_EVIDENCE/A/A-06/result.json`
  Commit: Y | `feat(cccp): bind science capabilities to existing task cores`
  Recommended task executor category: deep-high - the single-execution-station invariant is the core risk.

- [x] 14. A-06b: Publish the fifteen-task compatibility matrix and repair the package export gap
  What to do / Must NOT do: Add `science/task-compatibility-matrix.md` covering all fifteen task kinds, their capabilities, option/payload types and execution status. Add the missing `run_xtb_constrained_opt`, `XtbConstrainedOptOptions` and `XtbConstrainedOptPayload` entries to the `cccp.calculation` lazy export table and a regression test. Do not change any task behavior; do not rename or remove an existing export.
  Closes: GAP-4, GAP-8
  Parallelization: Wave 6 | Blocked by: 12, 13 | Blocks: 17
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:137-145`; `src/cccp/calculation/__init__.py:283-321,323-455`; `src/cccp/calculation/requests.py:1500,1719-1735`; `src/cccp/calculation/results.py:1240,1309-1324`; `tests/test_architecture_invariants.py:471-559`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -c "import cccp.calculation as c; assert hasattr(c,'run_xtb_constrained_opt') and hasattr(c,'XtbConstrainedOptOptions') and hasattr(c,'XtbConstrainedOptPayload')"` exits 0; the matrix has fifteen rows and every row names its task kind and execution status; `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_task_binding.py tests/test_architecture_invariants.py -q` exits 0.
  QA scenarios (name the exact tool + invocation): happy: import the three repaired symbols from the package root; failure: remove the new lazy entry and confirm the regression test fails. Evidence `$ACP2_EVIDENCE/A/A-06/result.json`
  Commit: Y | `fix(cccp): expose all fifteen task cores and record compatibility`
  Recommended task executor category: deep-low - a narrow export repair plus a documentation matrix; the defect is already verified.

- [x] 15. A-07a: Implement recipe contracts, validation and required/diagnostic semantics
  What to do / Must NOT do: Create `src/cccp/calculation/recipes/contracts.py` and `validation.py`. Define scientific steps with stable `step_id`, references to a registered capability or an existing `TaskKind` from the fifteen-task inventory (recorded through the binding), typed `OutputRef` selectors for upstream products, edges, required and diagnostic-only flags, validators, applicability and bounded alternatives. Reject cycles, dangling dependencies, failure propagation from a required upstream to a downstream step, and any diagnostic step treated as required. Do not include scheduler, account or central job status; do not add an execution loop or second dispatcher.
  Closes: GAP-3
  Parallelization: Wave 6 | Blocked by: 14 | Blocks: 16
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:147-155`; `docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md:92-98`; `src/cccp/calculation/contracts.py:36-119`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -c "from cccp.calculation.recipes.validation import validate_recipe; print(validate_recipe.__name__)"` succeeds; a cycle, a dangling reference and a missing required step each raise a typed error; a diagnostic-only step cannot satisfy a required downstream dependency.
  QA scenarios (name the exact tool + invocation): happy: validate a three-step linear recipe and a valid diagnostic branch; failure: mark a required edge diagnostic and confirm validation fails. Evidence `$ACP2_EVIDENCE/A/A-07/result.json`
  Commit: Y | `feat(cccp): add scientific recipe contracts and validation`
  Recommended task executor category: deep-high - dependency and required-step invariants drive recovery correctness.

- [x] 16. A-07b: Compile recipes and define the science identity boundary
  What to do / Must NOT do: Create `recipes/compile.py` producing a typed `TaskRequest` DAG with canonical JSON and a schema digest. A compiles the definition only; B18 owns the execution loop, persistence, retries and cancellation, and A must not add a second dispatcher. Freeze two identities. The plan-definition digest covers the Recipe, initial inputs, method, versions, parameters, `OutputRef` selectors and allowed bounded alternatives, and is computed before approval; it excludes `approval_ref`, approval status, display name and timestamps so routine approval/evidence append does not invalidate the approved science. The step execution identity is materialized at dispatch from actual upstream content digests, materialized parameters and implementation/provider versions; an unresolved `OutputRef` selector is an error at dispatch, not a silent skip. Science identity must cover method, structure content, atom mapping and roles, charge/multiplicity/electronic state, materialized dependency digests, provider/plugin versions and comparison conditions; it must exclude job id and display name. Reuse `analysis/identity.py` primitives and write `science/identity-boundary.md` describing how the recipe identities join the existing plan/step identity without creating a second recovery truth. Add `tests/test_acp2_science_recipe.py`.
  Closes: GAP-3, GAP-4
  Parallelization: Wave 6 | Blocked by: 8, 15 | Blocks: 29
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:147-155`; `src/cccp/analysis/identity.py:43-126`; `src/cccp/calculation/requests.py:1761-1881`; `tests/test_recovery_fixtures_smoke.py`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_recipe.py tests/test_recovery_fixtures_smoke.py -q` exits 0; compiling the same recipe twice yields byte-identical canonical JSON and plan-definition digest; changing mapping, method, charge, multiplicity, electronic state, standard state, provider/plugin version or an `OutputRef` selector changes the definition digest; changing only `approval_ref`, approval status, evidence refs or display name does not; a changed upstream byte changes the step execution identity at dispatch; an unresolved `OutputRef` selector fails at dispatch; a recipe with an unknown scientific field is rejected for execution rather than silently ignored.
  QA scenarios (name the exact tool + invocation): happy: compile twice and diff digests; failure: change only the display name and confirm the identity is unchanged, then change the mapping and confirm it changes. Evidence `$ACP2_EVIDENCE/A/A-07/result.json`
  Commit: Y | `feat(cccp): compile recipes with deterministic science identity`
  Recommended task executor category: deep-high - identity correctness is argued from invariants and crosses the recovery contract.

- [x] 17. A-08: Close the P0 static science gate and publish the handoff
  What to do / Must NOT do: Verify the H1 capability/task contracts, the registry and the binding; if the recipe round-trip has been delivered, verify it, otherwise list the research types and recipes as still in progress. Reuse the receipt samples delivered in todo 4 and confirm they still pass the shared validator; do not create a second sample set. Consume B05's launch file and minimal read-only capabilities; do not treat B04's premature MCP-adapter check as H2 evidence. Record the actual server, client and harness revisions and the H2 test candidate. Add compatibility notes and the unsupported-capability list. Put dependency, F4-amendment and shared-file requests into `science/requests/`. Publish `science/handoff-H2.json` separating static pass, real computation not run, and external MCP/DSH evidence references. Freeze the contract version. Do not claim real execution; do not include a test file that has not been delivered in the required command; do not make reaction/recipe pre-research a hard precondition of this gate.
  Closes: GAP-1, GAP-3, GAP-4, GAP-12
  Parallelization: Wave 6 | Blocked by: 14 | Blocks: 18, 20
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:157-165`; `docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md:145-154`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_20261021.md:25-39,60-87`; `docs/reference/next-change/ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md:483-522`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_schema.py tests/test_acp2_science_registry.py tests/test_acp2_science_task_binding.py tests/test_architecture_invariants.py tests/test_cccp_isolation.py -q` exits 0; the shared protocol validator accepts one A sample and rejects one invalid A sample; `handoff-H2.json` validates and lists each row with result and scope, the H2 candidate and the separate server/client/harness revisions; `science/cross-track-dependencies.json` has no missing ID or cycle; the unsupported list names every family not registered.
  QA scenarios (name the exact tool + invocation): happy: run the gate command and validate the handoff plus the protocol samples; failure: mark a real-QC row PASS while its command was not executed and confirm the record validator rejects it. Evidence `$ACP2_EVIDENCE/A/A-08/<run-id>/result.json`
  Commit: Y | `chore(cccp): close ACP2 P0 science gate`
  Recommended task executor category: deep-low - a gate-summary task whose facts are already produced.

- [x] 18. A-09a: Freeze the small-molecule singlepoint fixture
  What to do / Must NOT do: Choose a small closed-shell molecule; deliver `tests/fixtures/acp2/science/singlepoint/input.xyz`, `request.json`, `expected-science.json` and `fixture-manifest.json` with original coordinates, charge, multiplicity, atom order, method/basis actually available in the target environment and allowed tolerances. Separate parse correctness, normal termination, energy presence and unit correctness. Do not invent a reference energy before the real run; do not default an invalid spin.
  Closes: GAP-4, GAP-12
  Parallelization: Wave 7 | Blocked by: 17 | Blocks: 19, 21
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:169-177`; `src/cccp/calculation/tasks/singlepoint.py:54`; `tests/test_cccp_task_singlepoint.py:74-96`; `tests/AGENTS.md:40-47`.
  Acceptance criteria (agent-executable): all four named fixture files parse; `tests/fixtures/acp2/science/singlepoint/invalid_electrons.json` and `tests/fixtures/acp2/science/singlepoint/missing_structure.json` exist and fail for their named reason; the manifest records input SHA-256 and the scientific parameters; `expected-science.json` contains no numeric reference energy before a real run.
  QA scenarios (name the exact tool + invocation): happy: recompute the input digest and compare it with the manifest; failure: edit one coordinate and confirm the digest check fails. Evidence `$ACP2_EVIDENCE/A/A-09/result.json`
  Commit: Y | `test(cccp): freeze ACP2 singlepoint science fixture`
  Recommended task executor category: unspecified-high - fixture design with scientific parameters and digests.

- [x] 19. A-09b: Add the fixture contract test and the platform request
  What to do / Must NOT do: Deliver `tests/test_acp2_science_fixtures.py` and run `-k singlepoint`; assert digest stability, required scientific fields, invalid-electron-count rejection, missing-structure rejection and wrong-method rejection. Write `science/requests/runtime-fixture-singlepoint.md` with the fields the platform owner needs; do not present a task-request JSON as an MCP submit body.
  Closes: GAP-4, GAP-12
  Parallelization: Wave 8 | Blocked by: 18 | Blocks: 21
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:169-177`; `docs/reference/next-change/ACP_2.0_Parallel_Plan_B_Runtime_20261020.md:167-176`; `src/cccp/calculation/requests.py:1906-2001`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_fixtures.py -k singlepoint -q` exits 0; four negative cases each fail for the named reason (invalid electron count, missing structure, wrong method, incompatible multiplicity); the request document lists fixture digest, run-key request, budget and the expected scenario id.
  QA scenarios (name the exact tool + invocation): happy: run the fixture test and print each negative reason; failure: flip the multiplicity to an incompatible value and confirm the test rejects it. Evidence `$ACP2_EVIDENCE/A/A-09/result.json`
  Commit: Y | `test(cccp): validate ACP2 singlepoint fixture contract`
  Recommended task executor category: unspecified-high - test and platform-request authoring.

- [x] 20. A-10a: Deliver the science acceptance harness skeleton
  What to do / Must NOT do: Create `scripts/acceptance/acp2_science.py` implementing `--help`, `--dry-run`, the scenario/flag contract from Plan A section 1.3 (`science:installed-singlepoint`, `science:validate-artifacts`, `science:validate-da-ts`, `science:validate-da-irc`, `science:validate-da-report`, `science:installed-examples`, with short CLI aliases), structured result writing and the exit codes `0=PASS`, `1=FAIL`, `2=NOT_VERIFIED`, `3=usage`. Use B01's `acceptance-result.schema.json` and the shared validator; add only an `extensions.science` area. Reuse the valid/invalid samples from todo 4 and assert the harness writer's output is equivalent to the canonical sample. `--dry-run` checks paths and budgets only and never creates a work root or a job. Emit `command` as an array plus separate `cwd`, `started_at`/`finished_at` and `artifacts[{path,size_bytes,sha256}]`. Do not start a controller, a scheduler or a QC subprocess.
  Closes: GAP-5, GAP-6
  Parallelization: Wave 7 | Blocked by: 17 | Blocks: 21
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:54-73`; `scripts/acceptance/real_environment.py:25-36,60-111,294-333`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_20261021.md:25-39`; `docs/reference/next-change/ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md:498-522`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" scripts/acceptance/acp2_science.py --help` exits 0 and lists every scenario and required flag; `--scenario installed-singlepoint --dry-run` without a run spec exits 3; with a syntactically valid placeholder run spec exits 0 but writes no PASS and creates no work root; the shared validator accepts a valid sample and rejects an invalid sample with the wrong time field or artifact digest; a usage error exits 3.
  QA scenarios (name the exact tool + invocation): happy: run `--help`, the validator samples, then `--dry-run` with a fixture and assert no work root is created; failure: omit `--out` and confirm exit 3 with no result file. Evidence `$ACP2_EVIDENCE/A/A-10/<run-id>/result.json`
  Commit: Y | `feat(cccp): add ACP2 science acceptance harness`
  Recommended task executor category: unspecified-high - a new harness with a strict CLI/evidence contract.

- [x] 21. A-10b: Implement and run the standalone singlepoint scenario
  What to do / Must NOT do: Implement `science:installed-singlepoint` through the public CCCP API only; require a run spec containing the interpreter/source or wheel digest, executable software and version, an independent native work root, total/phase/scenario caps, separate TaskRef/step/QC/attempt/bsub counts, queue/compute/client/total deadlines and an approved run key. Preflight the budget and refuse to run when any cap is exhausted or any required field is missing. Record the actual server, client and harness revisions and the H2 test candidate. Record the real command, native output, task request/result and software version. Independently parse the native output and compare energy and units. Do not touch the central jobs database, SSH or a production run root.
  Closes: GAP-5, GAP-6, GAP-8, GAP-12
  Parallelization: Wave 8 | Blocked by: 19, 20 | Blocks: 22, 23, 26
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:54-73,179-187`; `src/cccp/calculation/tasks/singlepoint.py:54-271`; `src/cccp/calculation/context.py:33-120`; `src/cccp/software.py`; `reports/architecture_remediation_20261020/real_environment/m1-singlepoint.json`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_20261021.md:159-172`.
  Acceptance criteria (agent-executable): with a platform-approved run spec and an H2 test candidate, `"$ACP2_A_PYTHON" scripts/acceptance/acp2_science.py --scenario science:installed-singlepoint --fixture tests/fixtures/acp2/science/singlepoint/fixture-manifest.json --run-spec "$ACP2_EVIDENCE/A/A-10/run-spec.json" --out "$ACP2_EVIDENCE/A/A-10/probe" --timeout 600` exits 0 and records a real native output; an exhausted budget is rejected before submission; without a run spec it exits 3; with a missing binary it exits 2 and writes `NOT_VERIFIED`; the normalized energy matches the independent parser within the declared tolerance; the receipt records the three revisions in B01's `candidate{...}` object.
  QA scenarios (name the exact tool + invocation): happy: run the approved scenario and re-parse the native output; failure: point the run spec at a nonexistent interpreter or binary and confirm exit 2 plus no PASS. Evidence `$ACP2_EVIDENCE/A/A-10/result.json`
  Commit: Y | `feat(cccp): run standalone ACP2 singlepoint acceptance`
  Recommended task executor category: deep-low - real-environment execution whose decisions are settling the run and parsing the output.

- [x] 22. A-11a: Freeze the optimize/frequency fixture and stationary-point validators
  What to do / Must NOT do: Add `tests/fixtures/acp2/science/optfreq/` with the initial structure, optimize request, frequency request, source references and scientific checks. Add `src/cccp/analysis/research/validators.py` with deterministic checks for optimization convergence, frequency membership of the optimized structure, method/state consistency, and the distinction among no check, missing evidence and contradiction. Build the static validators against the TaskResult/frequency-product contract and synthetic real-format outputs; do not wait for a real singlepoint. Freeze low-frequency thresholds and numeric tolerances before running; write `tests/test_acp2_science_stationary_point.py`. Real artifacts are validated only in todo 23.
  Closes: GAP-2, GAP-4
  Parallelization: Wave 8 | Blocked by: 17 | Blocks: 23, 26
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:189-197`; `src/cccp/analysis/tsmode/validation.py:21-90`; `src/cccp/analysis/frequency_products.py:29-133`; `src/cccp/calculation/results.py:169-275`; `tests/test_cccp_task_frequency.py:34-117`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_stationary_point.py -q` exits 0; an unconverged optimization is never reported as a minimum; a frequency result computed from the wrong structure is rejected; an absent Hessian yields `inconclusive` or `not_evaluated`, never `supported`; thresholds are module constants cited by the fixture.
  QA scenarios (name the exact tool + invocation): happy: validate a synthetic real-format converged minimum and frequency product; failure: mark the optimization unconverged and confirm the validator returns refuted, not supported. Evidence `$ACP2_EVIDENCE/A/A-11/result.json`
  Commit: Y | `feat(cccp): add stationary-point science validators`
  Recommended task executor category: deep-high - scientific correctness is argued from convergence and mode invariants.

- [x] 23. A-11b: Validate real optimize/frequency artifacts through the shared scenario
  What to do / Must NOT do: Implement and use `science:validate-artifacts` against the platform-prepared authorized snapshot for the registered `dsh-optfreq-live` run; confirm that ID is present in the platform registry before use. Consume the manifest reader delivered in todo 24. Verify the frequency is on the optimized structure, with the same method and electronic state, and record convergence and all modes. Record the actual server, client and harness revisions and the P1 test candidate. Do not run a second expensive job; do not accept a task id as scientific evidence without the snapshot and native output.
  Closes: GAP-5, GAP-6, GAP-8, GAP-12
  Parallelization: Wave 9 | Blocked by: 21, 22, 24 | Blocks: 25, 26
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:189-197`; `docs/reference/next-change/ACP_2.0_Parallel_Plan_B_Runtime_20261020.md:225-235`; `docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md:124-129`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_20261021.md:109-125`; `docs/reference/next-change/ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md:498-522`.
  Acceptance criteria (agent-executable): with an authorized snapshot, `"$ACP2_A_PYTHON" scripts/acceptance/acp2_science.py --scenario science:validate-artifacts --fixture tests/fixtures/acp2/science/optfreq/fixture-manifest.json --input-manifest "$ACP2_ARTIFACT_MANIFEST" --out "$ACP2_EVIDENCE/A/A-11/validation" --timeout 300` exits 0 and produces a stationary-point report; with a mismatched structure digest it exits 1; with no snapshot it exits 2; the receipt records the three revisions in B01's `candidate{...}` object.
  QA scenarios (name the exact tool + invocation): happy: validate the authorized opt/freq artifacts and print convergence plus imaginary-mode count; failure: alter one artifact digest in a copy of the manifest and confirm `FAIL`, not `NOT_VERIFIED`. Evidence `$ACP2_EVIDENCE/A/A-11/<run-id>/result.json`
  Commit: Y | `test(cccp): validate real ACP2 optimize/frequency artifacts`
  Recommended task executor category: deep-low - artifact consumption and scientific cross-check with a clear fixture.

- [x] 24. A-12a: Implement the artifact-snapshot consumer at the platform boundary
  What to do / Must NOT do: Split the responsibility. B Data owns authorization, revocation, freeze and export of an immutable input snapshot. The A harness validates the platform binding and export eligibility against that snapshot. `src/cccp/analysis/research/artifact_manifest.py` validates only content digest, artifact role, schema/generation as opaque provenance, and scientific identity (structure/method/units/mapping); it never decides permission, revocation or whether a generation is current, and never imports `acp_services`. Build the static consumer against positive and negative synthetic snapshots; do not wait for a real singlepoint. Reject path escapes, digest mismatches and structural mismatches in CCCP; reject a missing/ineligible export in the harness. Never read the central jobs database, SSH or a production task path directly. Add `tests/test_acp2_science_artifact_validation.py`.
  Closes: GAP-5, GAP-6
  Parallelization: Wave 8 | Blocked by: 17 | Blocks: 23, 25, 26
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:199-207`; `docs/reference/next-change/ACP_2.0_Parallel_Plan_B_Runtime_20261020.md:225-235`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_20261021.md:109-115`; `src/acp_services/data/contracts.py`; `src/acp_services/data/resource_read.py`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_artifact_validation.py -q` exits 0; a path escaping the read root, a bad digest and a structural mismatch each fail in CCCP with a distinct typed error; a missing or ineligible export fails in the harness; no authorization, revocation or current-generation decision exists in `src/cccp/**`; a valid snapshot resolves every artifact relative to its declared root.
  QA scenarios (name the exact tool + invocation): happy: validate a valid snapshot with three artifacts and print their digests; failure: remove the platform export eligibility and confirm the harness rejects it while CCCP still validates the content. Evidence `$ACP2_EVIDENCE/A/A-12/<run-id>/result.json`
  Commit: Y | `feat(cccp): consume authorized science artifact snapshots`
  Recommended task executor category: deep-high - the authorization/science boundary is an architecture invariant.

- [x] 25. A-12b: Cross-check Local and LSF scientific results and provenance
  What to do / Must NOT do: Reuse the delivered real-environment harness interface to inspect the baseline, then consume the platform Local/LSF run records and the experience track MCP call records. Consume B07's frozen `max_queue_wait_seconds`, `max_run_seconds`, `max_prepare_seconds` and `cleanup_seconds`; derive the harness timeout from those values instead of keeping an independent fixed value. Record `$ACP2_INTEGRATION_SOURCE` and `$ACP2_INTEGRATION_PYTHON` for any live integrated record, distinct from A's own source/interpreter. Verify the native output, actual method and software version, task result, artifact SHA-256, full scientific body and auxiliary frequency products, not merely the existence of a manifest. Test a cold cache, a cache eviction/rebuild, a cross-attempt replacement, a controller restart and an out-of-order/stale response; separate queue wait, scientific runtime, client request and overall acceptance deadlines. B11 must distinguish `completed_science_run` from `control_cancel_run` with different TaskRefs; A does not require a cancelled run to produce a completed scientific result, but requires the correct terminal state, stop confirmation and receipt. Compare the same environment and input within the frozen tolerance; do not assert numeric equality across different methods or versions. Record the actual server, client and harness revisions. With no LSF configuration, record `NOT_VERIFIED` and keep the row open.
  Closes: GAP-5, GAP-6, GAP-12
  Parallelization: Wave 9 | Blocked by: 23, 24 | Blocks: 26
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:199-207`; `docs/reference/next-change/ACP_2.0_Parallel_Plan_B_Runtime_20261020.md:213-223`; `scripts/acceptance/real_environment.py:282-291`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R2_20261021.md:76-100`; `docs/reference/next-change/ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md:397-422`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" scripts/acceptance/real_environment.py --help` exits 0; the Local row has a matching native output, manifest and complete scientific body; the LSF row is `PASS` only with real bsub/bjobs/submission/receipt evidence and the four frozen deadline values, otherwise `NOT_VERIFIED` with the exact missing item; a cold-cache rebuild and a cross-attempt replacement are exercised and the stale response is rejected; the completed and cancelled runs use different TaskRefs and the cancelled one is not required to have a completed scientific result; no row claims numeric equality across differing methods; the three revisions are recorded in B01's `candidate{...}` object.
  QA scenarios (name the exact tool + invocation): happy: compare Local artifacts, read the full scientific body and print the digest table; failure: present a cached artifact from an older attempt and confirm the cross-check rejects it. Evidence `$ACP2_EVIDENCE/A/A-12/<run-id>/result.json`
  Commit: Y | `test(cccp): cross-check ACP2 Local and LSF science provenance`
  Recommended task executor category: deep-low - evidence reconciliation with explicit environment gates.

- [x] 26. A-13: Close the P1 science and same-task gate
  What to do / Must NOT do: Verify that CLI, HTTP/Workbench and MCP/DSH read the same task and attempt returned by the first submit. Check the same task result, product schema, manifest generation/digest and scientific validation. Add negative checks for completed-but-missing-energy, an old attempt and an old generation. Confirm the existing active workflow contract is intact. Publish `science/handoff-H3.json` with per-combination Local/LSF status; do not mark an unexecuted combination verified.
  Closes: GAP-5, GAP-6, GAP-12
  Parallelization: Wave 9 | Blocked by: 21, 23, 24, 25 | Blocks: none. H3 gates the P2 real-run todos through the phase-gate rule; it is not a dependency of the static Diels-Alder fixture.
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:209-217`; `docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md:145-154`; `docs/reference/next-change/ACP_2.0_Parallel_Plan_B_Runtime_20261020.md:202-211`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_artifact_validation.py tests/test_acp2_science_task_binding.py tests/test_architecture_invariants.py -q` exits 0; the same-task matrix references one first-submit task id and attempt across all three entries; `handoff-H3.json` records each combination as `PASS`, `FAIL` or `NOT_VERIFIED` with evidence.
  QA scenarios (name the exact tool + invocation): happy: reconcile the shared task reference and print the attempt/digest table; failure: use a second task id from a fresh CLI submit and confirm the matrix rejects the claim of sameness. Evidence `$ACP2_EVIDENCE/A/A-13/result.json`
  Commit: Y | `chore(cccp): close ACP2 P1 science gate`
  Recommended task executor category: deep-low - gate reconciliation whose facts come from the platform and experience tracks.

- [x] 27. A-14a: Freeze the limited Diels-Alder v1 fixture
  What to do / Must NOT do: Freeze the first candidate as gas-phase, neutral, closed-shell, single-elementary butadiene plus ethene to cyclohexene, with explicit single-conformation/sampling boundaries. Deliver `tests/fixtures/acp2/science/diels_alder/v1/` with `reaction.json`, `question.json`, `reactants.xyz`, `product.xyz`, `fixture-manifest.json` and `acceptance.json`, including exact mapped structures, bond changes and atom order. State the only allowed questions: connectivity of this candidate path to the specified endpoints and the activation energy under the frozen method/conditions; state that this symmetric example cannot support endo/exo selectivity. Freeze method, basis, solvent, temperature, standard state, candidate cap and resource budget before any real run. Derive the budget from representative step timings before freezing the full chain; if no timing evidence exists, record `unmeasured` and keep H4 open. State whether queue wait counts toward total wall time and the in-flight stop/export-window rule in the fixture manifest.
  Closes: GAP-4, GAP-9
  Parallelization: Wave 10 | Blocked by: 10, 17 | Blocks: 28. Static definition may proceed after the research types and the P0 gate; the real Diels-Alder run still waits for H3 and a registered run key.
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:221-229`; `src/cccp/analysis/pes/atom_mapping.py:114`; `src/cccp/analysis/pes/bond_changes.py`; `docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md:230-231`.
  Acceptance criteria (agent-executable): all six fixture files parse; a direct Python assertion checks unique maps, conserved elements and charge, the absence of any endo/exo request, the frozen budget/tolerance and byte-stable atom order; the run spec records `science_deadline_seconds`, `export_budget_seconds`, `aggregate_budget_seconds`, a shared `started_at` and the queue-accounting policy; the frozen budget cites measured representative step timings or records `unmeasured` with the H4-open consequence; queue-wait inclusion, in-flight stop and export-window rules are stated; the DA fixture test selection is delivered and run in todo 28, not here.
  QA scenarios (name the exact tool + invocation): happy: run the direct mapping/conservation assertion and print the mapped reaction; failure: add an unmapped atom and confirm the assertion fails. Evidence `$ACP2_EVIDENCE/A/A-14/result.json`
  Commit: Y | `test(cccp): freeze ACP2 Diels-Alder v1 science fixture`
  Recommended task executor category: deep-high - atom mapping and scientific scope definition are correctness-critical.

- [x] 28. A-14b: Add DA mapping/conservation tests and the ADR input request
  What to do / Must NOT do: Extend `tests/test_acp2_science_fixtures.py` with exact mapping, element/charge conservation, duplicate-map, missing-product and ambiguous-stereo rejection. Write `science/requests/ADR-008-input.md` with the scientific scope, pre-registered criteria and explicit limitations for the platform owner's shared ADR. Separate execution-chain validation (known starting geometry or candidate, frozen method) from research candidate search (user input, bounded proposals); label the source of every starting structure and do not present a known TS as a search success. Write `science/requests/search-contract.md` with input categories, candidate source/count, generation steps, screening/rejection reasons, validators and success criteria; search is a separate evidence row and is not proven by a control-loop PASS. Record whether queue wait counts toward total wall time, the in-flight stop rule and the reserved evidence-export window. Do not claim this fixture supports a selectivity conclusion.
  Closes: GAP-4, GAP-9, GAP-12
  Parallelization: Wave 10 | Blocked by: 27 | Blocks: 29, 30
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:221-229`; `docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md:230`; `docs/reference/next-change/ACP_2.0_Parallel_Plan_B_Runtime_20261020.md:316-328`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R3_20261021.md:304-321`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R5_20261022.md:144-158`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_fixtures.py -k diels_alder -q` exits 0; the ADR input names the method, basis, solvent, temperature, standard state, candidate cap, resource budget, execution-chain/search split, starting-structure source, queue/stop/export rules and all out-of-scope questions; `search-contract.md` names the input category, candidate source/count, generation steps, screening/rejection reasons, validators and success criteria; no fixture field requests endo/exo.
  QA scenarios (name the exact tool + invocation): happy: run the DA fixture tests and print the five assertions; failure: change one product element and confirm conservation fails. Evidence `$ACP2_EVIDENCE/A/A-14/result.json`
  Commit: Y | `test(cccp): validate ACP2 Diels-Alder scope and mapping`
  Recommended task executor category: unspecified-high - fixture tests plus a concise ADR input.

- [x] 29. A-15a: Author the limited DA recipe and compile fixture
  What to do / Must NOT do: Add `diels_alder/v1/recipe.json` covering bounded structure preparation/conformation, candidate paths, transition-state optimization, frequency, bidirectional IRC, endpoint checks and energy/thermochemistry comparison, each marked required with bounded alternatives. Compile it to a typed task-request DAG using only a registered capability or an existing task kind through the binding (for example the existing optimize task in transition-state mode, the existing IRC task and the existing thermochemistry task); record parameter/dependency digests. A compiles only; B18 executes the DAG and owns the loop, persistence and retries. Do not create a named-reaction execution engine; do not guess a transition-state mode by array position.
  Closes: GAP-3, GAP-9
  Parallelization: Wave 10 | Blocked by: 16, 28 | Blocks: 30. Static compilation only; the real recipe execution waits for H3 and the registered run key.
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:231-239`; `src/cccp/calculation/recipes/contracts.py`; `src/cccp/calculation/recipes/compile.py`; `src/cccp/analysis/tsmode/mode_mapping.py`.
  Acceptance criteria (agent-executable): the recipe parses and compiles through `recipes.compile`; the DAG contains the required optimize/frequency/IRC/thermochemistry steps; every step references a registered capability or an existing task kind through `task_binding`, and no step names an unregistered execution path; the compiled request set round-trips; no field contains queue, account or job status.
  QA scenarios (name the exact tool + invocation): happy: compile the recipe and print the step/dependency table; failure: remove the IRC step and confirm the required-completion validation fails. Evidence `$ACP2_EVIDENCE/A/A-15/result.json`
  Commit: Y | `feat(cccp): add limited Diels-Alder scientific recipe`
  Recommended task executor category: deep-high - recipe composition determines what scientific conclusions are possible.

- [x] 30. A-15b: Test DA recipe compilation, native mode provenance and upstream blocking
  What to do / Must NOT do: Add `tests/test_acp2_science_da_recipe.py`; assert the compiled DAG, required/diagnostic flags, bounded alternatives and native transition-state mode mapping (native printed index plus provenance, never positional guessing). Assert that a failed required upstream blocks its dependents and that frequency/IRC cannot be optional while connectivity is claimed. Do not add platform-state fields.
  Closes: GAP-3, GAP-4, GAP-9
  Parallelization: Wave 10 | Blocked by: 29 | Blocks: 31, 32
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:231-239`; `src/cccp/analysis/tsmode/mode_mapping.py`; `src/cccp/analysis/tsmode/source.py`; `tests/test_cccp_task_irc.py`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_da_recipe.py tests/test_acp2_science_recipe.py tests/test_cccp_task_irc.py -q` exits 0; a removed required upstream marks its dependents blocked; an inexplicit mode index is rejected; a scan step also compiles through the same task-kind reference (without being required in the DA recipe); the recipe contains no platform-state key.
  QA scenarios (name the exact tool + invocation): happy: run the three suites and print the blocked-step table; failure: mark frequency optional and confirm the connectivity claim is rejected. Evidence `$ACP2_EVIDENCE/A/A-15/result.json`
  Commit: Y | `test(cccp): validate ACP2 Diels-Alder recipe DAG`
  Recommended task executor category: deep-high - upstream/blocking semantics and mode identity are recovery-critical.

- [x] 31. A-16a: Implement the transition-state and frequency validator
  What to do / Must NOT do: Extend `analysis/research/validators.py` with deterministic checks for optimization convergence, exactly one imaginary frequency for a first-order saddle candidate, the mapped mode, and the distinction between a local saddle candidate and proven connectivity. State each claim's scope precisely: a nonconverged optimization refutes the proposition that this optimization converged, not a stronger proposition that no stationary point exists in the region; missing evidence is `inconclusive`/`not_evaluated`, not `refuted`. Reuse the existing frequency parser and tsmode validation; do not copy formulas. Add wrong-mode, multiple-imaginary and missing-frequency fixtures.
  Closes: GAP-2, GAP-4
  Parallelization: Wave 10 | Blocked by: 28, 30 | Blocks: 33, 36
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:241-249`; `src/cccp/analysis/tsmode/validation.py:54-90`; `src/cccp/analysis/frequency_products.py:32-133`; `src/cccp/calculation/results.py:169-275`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_da_validators.py -k "ts or frequency or mode" -q` exits 0; one imaginary frequency yields `inconclusive` for connectivity, not `supported`; an unmapped mode is rejected; multiple significant imaginary modes are `refuted` for a first-order saddle.
  QA scenarios (name the exact tool + invocation): happy: validate a real TS candidate and print mode/imaginary data; failure: supply the wrong mode index and confirm the validator refutes it. Evidence `$ACP2_EVIDENCE/A/A-16/result.json`
  Commit: Y | `feat(cccp): add deterministic transition-state validators`
  Recommended task executor category: deep-high - scientific claim status is argued from mode and convergence invariants.

- [x] 32. A-16b: Implement IRC endpoint/connectivity and energy-comparability validators
  What to do / Must NOT do: Extend the validators for bidirectional IRC completion, endpoint geometry and bond-topology mapping, atom-order stability, and electronic-state/method/reference-state comparability. Allow the two IRC directions to be swapped only when the mapping proves the same target reaction. Distinguish `not_evaluated`, `inconclusive` and `refuted`. Add wrong-endpoint, one-sided-IRC, wrong-charge and incomparable-energy fixtures, labelling constructed data separately from real output.
  Closes: GAP-2, GAP-4
  Parallelization: Wave 10 | Blocked by: 30, 31 | Blocks: 33, 34, 36
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:241-249`; `src/cccp/analysis/pes/atom_mapping.py:114`; `src/cccp/analysis/pes/bond_changes.py`; `src/cccp/calculation/irc_endpoints.py`; `src/cccp/qc/thermo_normalize.py`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_da_validators.py tests/test_acp2_science_stationary_point.py tests/test_cccp_task_irc.py -q` exits 0; one successful IRC direction cannot support bidirectional connectivity; an atom-order permutation does not create a false connection; energies from different methods or reference states are refused.
  QA scenarios (name the exact tool + invocation): happy: validate a two-direction path and print endpoint mapping; failure: drop one direction and confirm the report becomes inconclusive. Evidence `$ACP2_EVIDENCE/A/A-16/result.json`
  Commit: Y | `feat(cccp): add IRC and energy-comparability validators`
  Recommended task executor category: deep-high - endpoint identity and comparability are the highest-risk scientific judgments.

- [x] 33. A-17: Validate real DA transition-state candidates and frequencies
  What to do / Must NOT do: First validate the reactant/product geometries and the small budget; then consume the B20a-exec artifacts (`22b`; R5-01 semantic candidate `B.B20a.exec`) and the platform-executed frozen recipe candidates/transition-state optimization/frequency results, using the platform-registered `da-runtime`/`da-research-live` IDs from the registry (not an assumed name). Do not treat `B20a-ready` as execution evidence; consume `B20a-exec` and do not wait for `B20b`, `B20c` or C35. Keep every candidate selection/rejection reason and the bounded search range, and label whether the starting structure is a user input, a frozen known candidate, or a generated candidate. A known starting TS proves the execution/validation chain, not research search capability. Keep three records separate: the scenario `result` (did validation complete with valid artifacts), the scientific claim `status` (may be `inconclusive`), and H4 closure (still open without a real positive candidate). If no qualified transition state is found, publish the honest `inconclusive` claim with the raw failure; never substitute an example structure. Do not start a second DA job.
  Closes: GAP-5, GAP-6, GAP-9, GAP-12
  Parallelization: Wave 11 | Blocked by: 31, 32 | Blocks: 34, 35, 37
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:251-259`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_20261021.md:127-139`; `docs/reference/next-change/ACP_2.0_Parallel_Plan_B_Runtime_20261020.md:316-328`; `docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md:124-129`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R5_20261022.md:59-91,144-158`.
  Acceptance criteria (agent-executable): with the registered `da-runtime`/`da-research-live` artifacts, `"$ACP2_A_PYTHON" scripts/acceptance/acp2_science.py --scenario science:validate-da-ts --fixture tests/fixtures/acp2/science/diels_alder/v1/fixture-manifest.json --input-manifest "$ACP2_ARTIFACT_MANIFEST" --out "$ACP2_EVIDENCE/A/A-17/validation" --timeout 300` exits 0 when the validation completes, even if the claim is `inconclusive`; the stage report separates scenario result from claim status and H4 readiness; a missing/invalid artifact is `FAIL` or `NOT_VERIFIED`, not a scientific `inconclusive`; no example TS is used as a substitute.
  QA scenarios (name the exact tool + invocation): happy: validate the real candidate and print scenario result, claim status and H4 readiness separately; failure: point at an old attempt artifact and confirm rejection; no candidate found must leave H4 open, not PASS. Evidence `$ACP2_EVIDENCE/A/A-17/<run-id>/result.json`
  Commit: Y | `test(cccp): validate real ACP2 Diels-Alder TS candidates`
  Recommended task executor category: deep-low - real artifact validation with a frozen recipe and criteria.

- [x] 34. A-18: Validate real bidirectional IRC and endpoint assignment
  What to do / Must NOT do: Consume the platform forward/reverse IRC results for the current transition-state identity, including native steps, termination reasons and final structures. Consume the `B20a-exec` artifacts (`22b`; R5-01 semantic candidate `B.B20a.exec`); `B20a-ready` is only the execution interface and is not execution evidence. Do not wait for `B20b`, `B20c` or C35. Perform any frozen endpoint optimization; if it was not performed, state that endpoints may not be minima. Use stable atom mapping and bond changes to verify both target endpoints, not visual similarity. Verify IRC and TS share method/state and that cross-attempt provenance is traceable. Record scenario result and claim status separately. One-sided success, a different reactant, a too-short path or mapping ambiguity stays uncertain and leaves H4 open.
  Closes: GAP-5, GAP-6, GAP-9, GAP-12
  Parallelization: Wave 11 | Blocked by: 33 | Blocks: 36, 37
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:261-269`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_20261021.md:127-139`; `src/cccp/calculation/tasks/irc.py:111`; `src/cccp/calculation/irc_endpoints.py`; `src/cccp/analysis/pes/atom_mapping.py:114`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R5_20261022.md:59-91`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" scripts/acceptance/acp2_science.py --scenario science:validate-da-irc --fixture tests/fixtures/acp2/science/diels_alder/v1/fixture-manifest.json --input-manifest "$ACP2_ARTIFACT_MANIFEST" --out "$ACP2_EVIDENCE/A/A-18/validation" --timeout 300` exits 0 for a valid two-direction path; `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_da_validators.py -k endpoint -q` exits 0; a one-sided path is recorded as an `inconclusive` claim and does not close H4.
  QA scenarios (name the exact tool + invocation): happy: validate the real two-direction path and print endpoint mappings; failure: delete the reverse direction and confirm connectivity is not supported. Evidence `$ACP2_EVIDENCE/A/A-18/<run-id>/result.json`
  Commit: Y | `test(cccp): validate real ACP2 bidirectional IRC`
  Recommended task executor category: deep-low - artifact validation against a frozen endpoint rule.

- [x] 35. A-19a: Build thermochemistry and activation-energy comparability
  What to do / Must NOT do: Reuse the existing thermochemistry normalization, Shermo adapter and ensemble thermochemistry; do not copy constants or standard-state formulas. Consume the `B20a-exec` artifacts (`22b`; R5-01 semantic candidate `B.B20a.exec`) and the A-17 audited identities; A-18 endpoint identity is reconciled in A-19b; `B20a-ready` and `B20b` are not science inputs.
 Check method, electronic state, temperature, standard state, reference state and stoichiometry across reactant, TS and product. Separate electronic barriers from free-energy barriers. If thermochemistry is incomplete, report only the electronic barrier and never invent a free-energy barrier. Add `tests/test_acp2_science_energy_comparability.py`.
  Closes: GAP-2, GAP-4, GAP-9
  Parallelization: Wave 11 | Blocked by: 30, 33 | Blocks: 36
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:271-279`; `src/cccp/qc/thermo_normalize.py`; `src/cccp/qc/shermo_adapter.py`; `src/cccp/analysis/ensemble/thermo.py:27-114`; `src/cccp/calculation/tasks/thermochemistry.py:51`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R5_20261022.md:59-91`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_energy_comparability.py tests/test_cccp_task_thermochemistry.py -q` exits 0; mixing electronic and free energies is rejected; different standard states/methods cannot be subtracted; a missing reactant component is rejected; a missing thermochemistry step yields no free-energy claim.
  QA scenarios (name the exact tool + invocation): happy: compare a valid electronic barrier and a valid free-energy barrier; failure: subtract energies from different standard states and confirm refusal. Evidence `$ACP2_EVIDENCE/A/A-19/result.json`
  Commit: Y | `feat(cccp): add thermochemistry comparability checks`
  Recommended task executor category: deep-high - unit/reference-state correctness drives the headline scientific result.

- [x] 36. A-19b: Assemble the final EvidenceReport, provenance and report scenario
  What to do / Must NOT do: Generate the claim-by-claim report from actually completed steps; keep execution facts separate from claim status; include conformation/sampling limits, tolerances, model boundaries and failed candidates. Attach `ScientificProvenance` with model, method, basis, software and plugin versions, temperature and standard/reference states. Implement `science:validate-da-report`. Record three separate fields: scenario result, per-claim status and H4 readiness. Do not write any model-generated natural-language conclusion back as a validated fact; do not let a completed scenario imply a supported claim.
  Closes: GAP-2, GAP-5, GAP-6, GAP-9
  Parallelization: Wave 11 | Blocked by: 33, 34, 35 | Blocks: 37
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:271-279`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_20261021.md:127-139`; `src/cccp/analysis/research/evidence.py`; `src/cccp/analysis/research/provenance.py`; `docs/reference/next-change/ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md:498-522`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" scripts/acceptance/acp2_science.py --scenario science:validate-da-report --fixture tests/fixtures/acp2/science/diels_alder/v1/fixture-manifest.json --input-manifest "$ACP2_ARTIFACT_MANIFEST" --out "$ACP2_EVIDENCE/A/A-19/report" --timeout 300` exits 0 for a valid report; every claim has status, validator id/version, source refs and limitations; a report with a missing source cannot be `supported`; a completed task with insufficient evidence is `inconclusive`; the report records whether the positive H4 evidence exists.
  QA scenarios (name the exact tool + invocation): happy: validate the report and print the claim-status table plus H4 readiness; failure: set a claim to supported without a validator source and confirm the report validator rejects it. Evidence `$ACP2_EVIDENCE/A/A-19/<run-id>/result.json`
  Commit: Y | `feat(cccp): assemble ACP2 Diels-Alder evidence report`
  Recommended task executor category: deep-high - execution/claim separation is the core scientific contract.

- [x] 37. A-20: Run the DA failure/reverse-case gate and publish H4
  What to do / Must NOT do: Use fixed negative artifacts to check missing frequency, wrong endpoint, corrupt digest, old provider version and mismatched standard state. Consume the platform injected failures (worker interruption, publication failure, recovery, late receipt) and the four B08 crash windows (reservation before persist, persisted before submission start, submitted with lost response, restart replay); verify that no duplicate directory, reservation, QC process or bsub was created, no accepted task is permanently undispatched, and only results with valid identity and complete facts are reused. Confirm publication retry does not relaunch expensive QC. A blocked required step, rejected approval or rejected budget cannot produce a completed science chain. Publish A-20 before B20b aggregates; A-20 is the science signoff B20b consumes, so never wait for B20b, B20c or C25. Keep H4 closure separate from both scenario results and claim status: H4 closes only when the required real positive evidence exists (valid TS with one imaginary mode and mapped mode, bidirectional IRC with both endpoints, comparable energies); a correctly reported `inconclusive` negative case proves honesty but does not replace the positive. Consume `science/requests/search-contract.md` and `B20a-search` (the B-plan search producer, with its artifact ID, readiness and digest) as a separate row from the known-candidate execution chain. Search is in P2/H4 scope by default: a search `PASS` requires the `B20a-search` producer and evidence satisfying the contract's success criteria; `NOT_VERIFIED`, missing artifacts, or a control-loop/known-candidate PASS leaves H4 open. A deferral is a scope reduction only after the owner ratifies `search_scope=DEFERRED` and B/C/main-plan records are synchronized; without that, H4 remains open. Record every H4 row as `PASS`, `FAIL` or `NOT_VERIFIED`; if the budget ends without a valid positive, save the real result, keep H4 open and continue independent work. Do not substitute negative-case robustness for the required real positive evidence.
  Closes: GAP-5, GAP-6, GAP-9, GAP-12
  Parallelization: Wave 11 | Blocked by: 33, 34, 35, 36 | Blocks: none in A; external B20b consumes A-20
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:281-289`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_20261021.md:99-107,127-139`; `docs/reference/next-change/ACP_2.0_Parallel_Plan_B_Runtime_20261020.md:316-328,388-408`; `.omo/plans/acp2-plan-c-rph-workbench.md:543-547`; `docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md:131-143`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R5_20261022.md:59-91,144-158`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_da_validators.py tests/test_acp2_science_energy_comparability.py tests/test_acp2_science_recipe.py tests/test_recovery_fixtures_smoke.py -q` exits 0; `handoff-H4.json` records scenario result, claim status and H4 closure separately; a required `NOT_VERIFIED` or `inconclusive` positive row keeps H4 open; the search row is recorded separately from the known-candidate chain; `B20a-search` is cited with its artifact ID, readiness and digest, and its `search-contract.md` success criteria must be satisfied for a search `PASS`; missing or `NOT_VERIFIED` `B20a-search` keeps H4 open; a deferral is accepted only with owner-ratified `search_scope=DEFERRED` and synchronized B/C/main-plan records;
 the B08 four-window evidence shows no duplicate directory/reservation/QC/bsub and no permanently undispatched task; no recovery case reuses an artifact whose identity changed.
  QA scenarios (name the exact tool + invocation): happy: run the H4 suite, reconcile the B08 crash records and validate the handoff; failure: flip a required unverified positive row to PASS and confirm the handoff validator rejects it. Evidence `$ACP2_EVIDENCE/A/A-20/<run-id>/result.json`
  Commit: Y | `chore(cccp): close ACP2 Diels-Alder science gate`
  Recommended task executor category: deep-low - gate consolidation with explicit three-state rules.

- [x] 38. A-21a: Extend the plugin manifest for capability kind, validators, recipes and trust evidence
  What to do / Must NOT do: Extend `src/cccp/plugin/metadata.py` inside the existing manifest: capability kind, implementation id, recipe/validator references, science-version range, distribution digest, verified fixture evidence, a trust-level field and a declared-license policy. This is static contract work: it depends on the capability schema/registry and P0 contracts, not on a successful DA result. Define a typed `PluginLicenseError` and an explicit accepted-license policy (unknown or empty license fails closed; the platform owner may narrow it). Add entry-point candidate discovery to the existing `src/cccp/plugin/discovery.py` for the `cccp.science_plugins` group (version 1), using `importlib.metadata.entry_points` metadata-only scanning: metadata is read without importing plugin code, and loaders are invoked only after explicit enablement. Write `science/requests/ADR-007-input.md` with the trust-level, manifest-field, compatibility and rollback input the platform owner needs for the shared ADR. Provide backward-compatible adapters for old metadata and a legacy negative test. Keep `never_overrides_builtin` enforced. Do not create a second manifest module; do not claim the in-process host is a sandbox.
  Closes: GAP-7
  Parallelization: Wave 12 | Blocked by: 12, 17 | Blocks: 39, 40
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:293-301`; `src/cccp/plugin/metadata.py:155-242`; `src/cccp/plugin/discovery.py:37-46`; `tests/test_cccp_plugin_pilot.py:96-132`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_cccp_plugin_pilot.py -q` exits 0; an old metadata object still loads through the compatibility adapter; a metadata object claiming to override a built-in is rejected; an unknown or empty license raises the typed `PluginLicenseError`; trust level defaults to the lowest and cannot be self-declared as validated without fixture evidence. The new plugin registry suite is delivered and run in todo 39, not here.
  QA scenarios (name the exact tool + invocation): happy: serialize an extended manifest and reload it through the compatibility adapter; failure: declare `trusted` with no fixture digest and confirm rejection. Evidence `$ACP2_EVIDENCE/A/A-21/result.json`
  Commit: Y | `feat(cccp): extend plugin manifest for scientific capabilities`
  Recommended task executor category: deep-high - manifest compatibility and trust semantics cross the plugin contract.

- [x] 39. A-21b: Integrate capability/recipe/validator discovery and invocation boundaries
  What to do / Must NOT do: Extend discovery, host and SDK so a plugin can declare capability, recipe and validator references, but discovery remains metadata-only and enablement remains explicit. Scientific execution stays in the existing task layer; a pure-analysis plugin cannot spawn a process. Update the catalog projection so described, installed, enabled and verified are separate. Add `tests/test_acp2_science_plugin_registry.py`.
  Closes: GAP-7
  Parallelization: Wave 12 | Blocked by: 38 | Blocks: 40, 41, 42
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:293-301`; `src/cccp/plugin/discovery.py:110-162`; `src/cccp/plugin/host.py:29-119`; `src/cccp/plugin/sdk.py:518-676`; `tests/test_cccp_plugin_sdk.py:257-305`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_plugin_registry.py tests/test_cccp_plugin_pilot.py tests/test_cccp_plugin_sdk.py tests/test_architecture_invariants.py -q` exits 0; discovery loads no plugin module; the entry-point group is scanned for metadata only and an installed fixture is discovered without loading; a plugin cannot override a built-in capability; a validator reference that is not declared is rejected.
  QA scenarios (name the exact tool + invocation): happy: discover a plugin with one capability and one validator without executing it; failure: make discovery import the plugin and confirm the spy test fails. Evidence `$ACP2_EVIDENCE/A/A-21/result.json`
  Commit: Y | `feat(cccp): integrate plugin science discovery boundaries`
  Recommended task executor category: deep-high - discovery/invocation boundaries are a security and architecture contract.

- [x] 40. A-22a: Build the external science plugin distribution fixture
  What to do / Must NOT do: Under `tests/fixtures/acp2/science/external_plugin/`, add a minimal independently installable distribution with its own `pyproject.toml`, version, license, metadata, entry point in the A-21-delivered `cccp.science_plugins` group (version 1) and a fixed native input. Implement a read-only validator or data converter; do not copy the implementation into core. Do not modify the root `pyproject.toml`.
  Closes: GAP-7
  Parallelization: Wave 12 | Blocked by: 39 | Blocks: 41, 42
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:303-311`; `src/cccp/plugin/metadata.py:109-123`; `src/cccp/plugin/discovery.py:51-62`; `tests/test_cccp_plugin_sdk.py:311-332`.
  Acceptance criteria (agent-executable): the fixture distribution builds with the platform-provided build tool (`python -m build` or `pip wheel --no-deps`, with the actual argv recorded); the metadata declares a stable entry-point group and a license; the plugin source imports no `cccp.calculation.tasks` and no platform module; the fixed native input and expected output are included.
  QA scenarios (name the exact tool + invocation): happy: build the fixture wheel in an isolated directory and print its metadata; failure: remove the license and confirm metadata validation fails. Evidence `$ACP2_EVIDENCE/A/A-22/result.json`
  Commit: Y | `test(cccp): add external ACP2 science plugin fixture`
  Recommended task executor category: unspecified-high - a small distribution with its own packaging and metadata.

- [x] 41. A-22b: Install, enable and verify the external plugin without core edits
  What to do / Must NOT do: In an isolated virtual environment, install the fixture distribution, confirm it is not loaded while disabled, then enable it and invoke the capability on the fixed input. Verify the digest lock, capability schema, output units and atom order. Record the build/install command, package digest and actual invocation. Do not install into the shared integration environment; do not auto-enable from metadata.
  Closes: GAP-7
  Parallelization: Wave 12 | Blocked by: 40 | Blocks: 42
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:303-311`; `src/cccp/plugin/sdk.py:415-459,518-599`; `tests/test_cccp_plugin_sdk.py:257-332`; `pyproject.toml:75-98`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_plugin_registry.py -k external_distribution -q` exits 0 in the isolated environment; a disabled plugin raises a typed disabled error; an enabled plugin returns a valid task result; the distribution digest matches the installed metadata; no core source changed.
  QA scenarios (name the exact tool + invocation): happy: install, enable and invoke the fixture in a clean venv; failure: disable it and confirm the invocation is refused, then uninstall and confirm discovery reports it absent. Evidence `$ACP2_EVIDENCE/A/A-22/result.json`
  Commit: Y | `test(cccp): verify external ACP2 plugin install and invocation`
  Recommended task executor category: deep-low - isolated environment and plugin boundary verification.

- [x] 42. A-23: Validate plugin conflicts, upgrades and historical read-only access for H5
  What to do / Must NOT do: Test duplicate capability names, duplicate implementation ids, incompatible API ranges, missing dependencies, unknown/empty licenses, corrupt fixture data and untrusted/unverified trust levels. Keep a v1 artifact, install v2, confirm new tasks use v2 explicitly while v1 artifacts remain readable without rerunning. Freeze the metadata-to-capability-to-recipe-to-provenance chain. Publish `handoff-H5.json` and the plugin compatibility/rollback matrix. Explicitly prove that discovery never calls a loader and an unverified plugin cannot be auto-enabled or silently substituted; do not claim process-level sandboxing, because process/permission limits belong to the platform owner.
  Closes: GAP-7, GAP-12
  Parallelization: Wave 12 | Blocked by: 39, 40, 41 | Blocks: 43
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:313-321`; `src/cccp/plugin/errors.py:13-141`; `tests/test_cccp_plugin_pilot.py:154-268`; `tests/test_cccp_plugin_sdk.py:177-305`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_plugin_registry.py tests/test_cccp_plugin_pilot.py tests/test_cccp_plugin_sdk.py tests/test_packaging_assets.py -q` exits 0; each conflict is a distinct typed error; an untrusted or unverified plugin cannot be auto-enabled or auto-loaded, and discovery never calls its loader; after uninstalling v2, v1 artifacts still read; `handoff-H5.json` lists each compatibility row with result and evidence.
  QA scenarios (name the exact tool + invocation): happy: install v2, read a v1 artifact and print the compatibility matrix; failure: install a plugin with a duplicate capability and confirm safe rejection. Evidence `$ACP2_EVIDENCE/A/A-23/result.json`
  Commit: Y | `chore(cccp): close ACP2 plugin ecosystem gate`
  Recommended task executor category: deep-low - compatibility testing with explicit failure taxonomy.

- [x] 43. A-24a: Bind NMR shielding to the science protocol
  What to do / Must NOT do: Select the existing NMR shielding/comparison capability and give it a typed descriptor and task binding without building a new NMR engine. Add molecule/atom mapping, experimental-spectrum source, solvent/reference/scaling conditions and model-asset version to provenance; convert deterministic checks into an evidence report and report limitations when the reference or coverage is insufficient. Preserve existing assets and notices.
  Closes: GAP-9
  Parallelization: Wave 13 | Blocked by: 42 | Blocks: 44, 46, 47
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:325-333`; `src/cccp/calculation/tasks/nmr_shielding.py:55`; `src/cccp/analysis/nmr/` (assignment, scaling, error model, assets); `pyproject.toml:86-98`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_cccp_task_nmr_shielding.py tests/test_packaging_assets.py -q` exits 0; the descriptor declares atom-mapping and reference conditions; an atom-order change invalidates the old assignment; a missing model asset yields a typed unavailable/inconclusive result. The shared cross-domain test is delivered in todo 44, which consumes this descriptor.
  QA scenarios (name the exact tool + invocation): happy: bind the NMR capability and validate a real shielding fixture; failure: permute atom order and confirm the old assignment is rejected. Evidence `$ACP2_EVIDENCE/A/A-24/result.json`
  Commit: Y | `feat(cccp): bind NMR shielding to the science protocol`
  Recommended task executor category: deep-high - atom mapping and reference-state provenance are domain-critical.

- [x] 44. A-24b: Bind ensemble thermochemistry and add the cross-domain tests
  What to do / Must NOT do: Give the existing ensemble thermochemistry capability a typed descriptor and binding; add temperature, standard state and component weights to provenance; declare the weight policy explicitly. Accept raw finite positive weights with a non-zero sum and normalize them with a recorded tolerance, or accept an explicitly normalized set within that tolerance; record which form was supplied and the resulting normalized weights. There is no third "silently normalize anything" path. Add the cross-domain test covering valid, missing, partial and corrupt data for both NMR and thermochemistry, and write `science/cross-domain-compatibility.md` with the unit/mapping/reference/version compatibility rules. This joint test consumes the NMR descriptor from todo 43 and follows the matrix dependency `Depends on: 43`; do not dispatch it before 43 completes. Do not duplicate the existing thermochemistry formulas or constants.
  Closes: GAP-9
  Parallelization: Wave 13 | Blocked by: 43 | Blocks: 46, 47
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:325-333`; `src/cccp/analysis/ensemble/thermo.py:27-114`; `src/cccp/qc/thermo_normalize.py`; `src/cccp/calculation/results.py:731-766`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R3_20261021.md:449-468`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_cross_domain.py tests/test_cccp_task_thermochemistry.py -q` exits 0; negative/non-finite/zero-sum weights are rejected; an explicitly normalized set outside the declared tolerance is rejected; raw weights are normalized and the policy/tolerance/provenance are recorded; a missing component is reported as incomplete rather than silently dropped; the same evidence contract is used for both domains.
  QA scenarios (name the exact tool + invocation): happy: validate raw weights summing to 0.8 and confirm they are normalized with recorded provenance; failure: set `weights_mode=normalized` with sum 0.8 and confirm rejection. Evidence `$ACP2_EVIDENCE/A/A-24/result.json`
  Commit: Y | `feat(cccp): bind ensemble thermochemistry to the science protocol`
  Recommended task executor category: deep-high - weight normalization and cross-domain provenance are correctness-critical.

- [x] 45. A-25a: Freeze the selectivity fixture with distinguishable endpoints
  What to do / Must NOT do: Choose a simple Diels-Alder or other limited selectivity case whose endpoints are stereochemically distinguishable with the available method and budget. Freeze structures, atom mapping, endo/exo definitions and comparable paths before running. State the required conformation/TS coverage and the missing-path policy. Write `science/requests/selectivity-producer.md` with the two-path inputs, acceptance criteria, run-key request, budget and the single registered reference `owner=C, scenario=experience:cross-domain-live, subcase=selectivity`. Deliver `tests/test_acp2_science_selectivity.py` with the endpoint-distinguishability test; the evidence assertions are added in todo 46. Do not reuse the symmetric A-14 fixture; do not equate two single-point minima with a selectivity prediction.
  Closes: GAP-10
  Parallelization: Wave 13 | Blocked by: 42 | Blocks: 46
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:335-343`; `src/cccp/analysis/pes/atom_mapping.py:114`; `tests/fixtures/acp2/science/diels_alder/v1/`; `docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md:129`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R3_20261021.md:170-180`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_selectivity.py -k endpoint -q` exits 0; the fixture contains two distinguishable endpoint sets with stable maps; the manifest records the frozen definitions, budget, tolerance and missing-path policy; the producer request names the two paths, run key, budget and `experience:cross-domain-live`/`selectivity`; the symmetric A-14 structures are not reused; the endpoint test asserts a stereochemical difference rather than an atom-order difference.
  QA scenarios (name the exact tool + invocation): happy: validate the fixture and print the two endpoint signatures; failure: replace one endpoint with the other coordinates and confirm the distinguishability test fails. Evidence `$ACP2_EVIDENCE/A/A-25/result.json`
  Commit: Y | `test(cccp): freeze ACP2 selectivity fixture`
  Recommended task executor category: deep-high - stereochemical identity and endpoint distinguishability are subtle.

- [x] 46. A-25b: Produce the selectivity evidence and bounded real-run report
  What to do / Must NOT do: Consume the platform-executed limited multi-hypothesis recipe and its artifacts within the frozen budget; A does not submit the job. This row requires the registered `owner=C, scenario=experience:cross-domain-live, subcase=selectivity` reference with two distinguishable paths, a run key and a budget. The handoff chain is A-25a → C T-40a → B23b → A-25b → C T-40b; the C40a→B23b edge is owned by C/B. Use the B plan's `B23b` artifact ID (the B dependency table may show `23b`); record the exact machine ID B publishes and do not rely on the numeric `23` prefix alone.
 A-25a must exist before C T-40a, B23b must produce the authorized artifacts before A-25b, and C T-40b signs after A-25b. The B23b artifact edge is `integration_ready` (artifacts exported), not B23 whole-task `verified`. `subcase` is display metadata; A requires a unique `(owner, scenario, phase, run_id)` with the registered `experience:selectivity` phase, and records `NOT_VERIFIED` if two subcases share one public key.
 Consume the B/C cross-domain budget table covering `nmr`, `thermo` and `selectivity`, including per-subrow TaskRef/step/QC counts and the cumulative total; reuse existing authorized artifacts where available, and never reset the cumulative budget by changing `run_id`. If the reference or B23b artifacts are absent, record `NOT_VERIFIED` and keep A-26/H6 open rather than silently completing P4. If either side lacks a qualified path, report `inconclusive`, not a relative preference. Produce a bounded selectivity EvidenceReport with all limitations and no experimental quantitative claim. Do not invent a platform scenario name.
  Closes: GAP-10, GAP-12
  Parallelization: Wave 13 | Blocked by: 43, 44, 45 | Blocks: 47
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:335-343`; `docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md:129`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R3_20261021.md:170-180`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R5_20261022.md:93-113`; `tests/test_acp2_science_energy_comparability.py`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_selectivity.py tests/test_acp2_science_energy_comparability.py -q` exits 0; both hypotheses have valid evidence before any preference is stated; one missing path yields `inconclusive`; the report records the budget and coverage, including per-subrow TaskRef/step/QC counts and the cumulative total for `nmr`/`thermo`/`selectivity`, plus reused artifacts; the `experience:cross-domain-live`/`selectivity` record has a unique public `(owner, scenario, phase, run_id)` and B23b `integration_ready` artifacts, otherwise the result is `NOT_VERIFIED` and H6 remains open.
  QA scenarios (name the exact tool + invocation): happy: produce the report from both qualified paths; failure: remove one path and confirm the report refuses a preference. Evidence `$ACP2_EVIDENCE/A/A-25/result.json`
  Commit: Y | `feat(cccp): produce bounded ACP2 selectivity evidence`
  Recommended task executor category: deep-high - the conclusion is only valid if both competing paths are qualified.

- [x] 47. A-26: Run cross-domain/version regression and publish H6
  What to do / Must NOT do: Check units, mapping, references, schema upgrades and derived identity across singlepoint, optfreq, DA, NMR and thermochemistry. Test old schema read-only, explicit rejection of unsupported fields and conservative recovery after a model/plugin digest change. Rerun only changed science paths. Confirm scientific output does not depend on account tier or project permission. Consume the C T-40b selectivity signoff before publishing H6; if it is absent, H6 remains open. For P4, consume per-subcase rows by their unique phase rather than a parent aggregate that cannot distinguish `nmr`/`thermo`/`selectivity`. Publish `handoff-H6.json` with the applicability/verification matrix.
  Closes: GAP-9, GAP-10, GAP-12
  Parallelization: Wave 13 | Blocked by: 43, 44, 45, 46 | Blocks: 48
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:345-353`; `src/cccp/analysis/identity.py:43-151`; `tests/test_recovery_fixtures_smoke.py`; `tests/test_architecture_invariants.py`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_cross_domain.py tests/test_acp2_science_selectivity.py tests/test_acp2_science_evidence_contract.py tests/test_recovery_fixtures_smoke.py tests/test_cccp_isolation.py -q` exits 0; an old schema remains readable; an unsupported new field is rejected; a digest change forces conservative recomputation; the matrix has no unverified row labelled verified; the `nmr`/`thermo`/`selectivity` budget allocation is recorded and existing authorized artifacts are reused where possible; P4 consumes per-subcase rows keyed by the unique phases `experience:nmr`, `experience:thermo` and `experience:selectivity`, requires the C T-40b selectivity signoff, and rejects a parent-only aggregate that cannot distinguish them.
  QA scenarios (name the exact tool + invocation): happy: run the cross-domain suite and print the version matrix; failure: change a plugin digest but reuse the old result and confirm the reuse guard fails. Evidence `$ACP2_EVIDENCE/A/A-26/result.json`
  Commit: Y | `chore(cccp): close ACP2 cross-domain gate`
  Recommended task executor category: deep-low - regression consolidation with explicit version rules.

- [x] 48. A-27a: Audit standalone dependencies and deliver the packaging request
  What to do / Must NOT do: Audit the public science imports and prove the independent package does not import ACP, ACP Services or DSH. Consume the existing `packaging/README.md`, role build configurations and `scripts/build_release.py`; request only the delta needed for the science package and do not re-create working role packaging. Write `science/requests/packaging.md` and an exact patch for the platform owner covering package inclusion, runtime dependencies, science assets/licenses, Python support range and package data. Do not edit `pyproject.toml`, versions, CI or the root package; do not split the root package yourself.
  Closes: GAP-11, GAP-12
  Parallelization: Wave 14 | Blocked by: 47 | Blocks: 49, 50
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:357-366`; `pyproject.toml:24-39,75-98`; `packaging/README.md`; `packaging/roles/cccp/pyproject.toml`; `scripts/build_release.py`; `scripts/acceptance/check_release_artifacts.py`; `scripts/acceptance/release_role_probe.py`; `tests/test_packaging_assets.py`; `src/cccp/analysis/nmr/model_assets/`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R5_20261022.md:174-186`.
  Acceptance criteria (agent-executable): the dependency audit finds no `acp`, `acp_services` or DSH import in the science import closure; the request contains an exact patch and a rollback note; the asset/license list includes the NMR model assets and notices; the request reuses the existing role build configs, `scripts/build_release.py`, `check_release_artifacts.py` and `release_role_probe.py`, and asks only for the science delta rather than a blank-slate rebuild; a dry-run of the patch applies cleanly in a scratch copy.
  QA scenarios (name the exact tool + invocation): happy: run the import-closure audit and print the forbidden-module result; failure: add a temporary `acp` import to a scratch copy and confirm the audit fails, then remove it. Evidence `$ACP2_EVIDENCE/A/A-27/result.json`
  Commit: Y | `docs(cccp): request ACP2 independent packaging changes`
  Recommended task executor category: unspecified-high - dependency and asset auditing with a precise patch request.

- [x] 49. A-27b: Verify the installed package in a clean environment
  What to do / Must NOT do: After the platform owner delivers the actual wheel, install it in a clean environment with no source path or editable install, run from a directory outside the repository, and execute pure imports, fixture parsing and a small independent probe. The test may use `$ACP2_A_PYTHON` only as the test runner; it must create or spawn a separate clean interpreter for the probe and record that interpreter path. Assert module `__file__` comes from the installed package. Without software, import and discovery must still work and execution must fail with a typed unavailable error. Add `tests/test_acp2_science_installed_package.py`.
  Closes: GAP-11, GAP-12
  Parallelization: Wave 14 | Blocked by: 48 | Blocks: 50, 51
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:357-366`; `docs/reference/next-change/ACP_2.0_Parallel_Plan_B_Runtime_20261020.md:376-387`; `tests/test_packaging_assets.py`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_installed_package.py tests/test_packaging_assets.py -q` exits 0; the clean-environment probe imports all science modules from the wheel in a separately recorded clean interpreter; without ORCA, import and discovery succeed and execution raises the typed unavailable error; no source-tree path appears in `sys.path` during the probe.
  QA scenarios (name the exact tool + invocation): happy: install the delivered wheel in a fresh venv and run the probe from `/tmp`; failure: remove the wheel asset directory and confirm the packaging test fails. Evidence `$ACP2_EVIDENCE/A/A-27/result.json`
  Commit: Y | `test(cccp): verify ACP2 wheel in a clean environment`
  Recommended task executor category: deep-low - clean-environment installation and import verification.

- [x] 50. A-28: Write the runnable science SDK and plugin guide
  What to do / Must NOT do: Write `science/user-guide.md` covering installation, import without software, configuring software, singlepoint/optimize/frequency, reading reports, units/mapping, failure interpretation, resource limits and cleanup. Add runnable `science/examples/` for a public-API singlepoint, recipe compilation, report reading and external plugin integration. Keep standalone science use separate from platform-controlled research. Do not use `python -m cccp`; do not claim an example ran unless it ran in the frozen environment.
  Closes: GAP-11
  Parallelization: Wave 14 | Blocked by: 49 | Blocks: 51
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:368-376`; `docs/reference/next-change/ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md:107-118,265-299`; `src/cccp/analysis/research/`.
  Acceptance criteria (agent-executable): every documented command is executed from the clean environment and its real output is captured; the examples contain no placeholder path; the docs distinguish import/discovery without software from execution requiring software; `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_installed_package.py -k documented_examples -q` exits 0 for the software-free examples; `"$ACP2_A_PYTHON" scripts/acceptance/acp2_science.py --scenario installed-examples --out "$ACP2_EVIDENCE/A/A-28/examples" --timeout 300` exits 0 when the installed environment is present, otherwise exits 2 with the missing prerequisite.
  QA scenarios (name the exact tool + invocation): happy: run the documented software-free examples in order and compare outputs; failure: change an example to import a non-installed module and confirm the documented-example test fails. Evidence `$ACP2_EVIDENCE/A/A-28/result.json`
  Commit: Y | `docs(cccp): add ACP2 science SDK and plugin guide`
  Recommended task executor category: writing - user-facing documentation with executable examples.

- [x] 51. A-29: Close the final science release gate and publish H7
  What to do / Must NOT do: Verify the H0 baseline and H1-H6 revisions, schema/provider/plugin/fixture digests and each row scope; update the support matrix. Run the required science regression and the independent wheel probe; rerun only changed or still-doubtful paths. Check no reverse import, one execution station, fifteen active workflows, compatibility shims, historical readers and model assets. Ensure every actual step has a command, native evidence and a PASS/FAIL/NOT_VERIFIED result. Sign A's own science H7 from B25a's candidate plus A-27b/A-28 verification; do not wait for B26, B25b or C35. B aggregates H7 after A-29 and C35. Publish `handoff-H7.json`, the release note, capability/version/limitation matrix and replay index. Do not claim a required row passed when it was not executed; do not restart or release the production service.
  Closes: GAP-6, GAP-11, GAP-12
  Parallelization: Wave 14 | Blocked by: 49, 50 | Blocks: none
  References: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md:378-386`; `docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md:145-156,206-212`; `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_20261021.md:41-58`; `tests/test_architecture_invariants.py`; `tests/test_cccp_isolation.py`; `tests/test_recovery_fixtures_smoke.py`; `tests/test_packaging_assets.py`.
  Acceptance criteria (agent-executable): `"$ACP2_A_PYTHON" -m pytest tests/test_acp2_science_*.py tests/test_architecture_invariants.py tests/test_cccp_isolation.py tests/test_recovery_fixtures_smoke.py tests/test_packaging_assets.py -q` exits 0; `handoff-H7.json` has one row for the H0 baseline and one per H1-H6 item, each with result, scope, revision and evidence; no required row is `NOT_VERIFIED` when the handoff claims release readiness; the handoff does not reference B26/B25b/C35 as a prerequisite.
  QA scenarios (name the exact tool + invocation): happy: run the full science gate and validate the handoff; failure: mark a required unexecuted row as PASS and confirm the handoff validator rejects it. Evidence `$ACP2_EVIDENCE/A/A-29/result.json`
  Commit: Y | `chore(cccp): close ACP2 P5 science release gate`
  Recommended task executor category: deep-low - final consolidation with explicit release-honesty rules.

## Final verification wave

> F1 and F2 run in parallel after ALL todos; F3 uses the platform-scheduled shared resource window; F4 runs last and consumes F1-F3. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.

- [x] F1. Plan compliance audit
  Verify every one of the fifty-one implementation rows has an immutable evidence directory, the exact command from its acceptance criteria, the required PASS/FAIL/NOT_VERIFIED result, and a commit on `acp2/a-science`. Confirm no row skipped a named dependency, no phase gate closed with a required NOT_VERIFIED row, and no A-owned file is missing from the commit list. Recompute the GAP-1 through GAP-13 closure matrix and the IS-1 through IS-13 delivery matrix against the actual todo rows, not the draft. Validate `science/cross-track-dependencies.json` (no missing ID, no cycle), the B01 protocol samples, and the separate server/client/harness revisions. Cross-check the dependency matrix against the actual evidence timestamps and ensure no run directory was overwritten. Evidence `$ACP2_EVIDENCE/A/F1/<run-id>/result.json`.
  Recommended task executor category: deep-low - audit against the written plan, not a re-implementation.

- [x] F2. Code quality and architecture review
  Compare every touched file against B00's full Ruff JSON baseline (rule, path, line/column and stable fingerprint, not only counts): no new diagnostic fingerprint and no weakened rule/exclude. Run Ruff on the changed files only; the baseline command may be non-zero because of old diagnostics, and full-repository cleanup is a separate task, not this gate. Confirm no new dependency, no bare `except Exception`, PEP 604 annotations in new code, `__init__.py` export-only, no ACP/DSH import in `src/cccp`, no task import in the research pure types, no second `run_*` implementation, and no backend subprocess. Run `tests/test_architecture_invariants.py`, `tests/test_cccp_isolation.py`, `tests/test_recovery_fixtures_smoke.py` and `tests/test_packaging_assets.py`. Reject any change that weakened an existing gate. Evidence `$ACP2_EVIDENCE/A/F2/<run-id>/result.json`.
  Recommended task executor category: deep-low - quality and invariant review with concrete commands.

- [x] F3. Real manual QA
  From a clean shell and outside-repository directory, run the documented software-free SDK example, the harness `--help`/`--dry-run`, and the installed-wheel probe. For the real singlepoint and artifact-validation checks, first verify the immutable receipts from the frozen candidate; resubmit only when the platform owner schedules a new resource window and records why the existing evidence cannot be reused. Do not duplicate a real submission merely to re-prove an unchanged candidate. Confirm stdout/stderr separation, exit codes, artifact digests, and that `--dry-run` created no work root or job. Confirm a missing prerequisite yields exit 2 and no PASS, and record the actual server/client/harness revisions. Evidence `$ACP2_EVIDENCE/A/F3/<run-id>/result.json`.
  Recommended task executor category: deep-low - operator-style end-to-end verification.

- [x] F4. Ideal-state fidelity
  Runs last, after F1-F3. Check every IS-1 through IS-13 row against delivered behavior, one by one, using the success-criteria table and the F1-F3 results. A shortfall becomes a new implementation row, never a note. Confirm the scientific claims are no broader than the frozen fixtures, the execution-versus-claim distinction survives in every report, and the fifteen active workflows and compatibility surfaces still work. Evidence `$ACP2_EVIDENCE/A/F4/<run-id>/result.json`.
  Recommended task executor category: deep-low - 1:1 fidelity check against the ideal-state ledger.

## Commit strategy

- Branch: `acp2/a-science`, created from the frozen base revision; one commit per todo, with the todo id in the message (for example `feat(cccp): bind NMR shielding to the science protocol`).
- Commit only A-owned paths: `src/cccp/**`, `tests/test_acp2_science_*.py`, `tests/fixtures/acp2/science/**`, `scripts/acceptance/acp2_science.py`, `docs/reference/next-change/acp2-contracts/science/**`, and this plan. Never commit another track's files, the primary worktree's dirty changes, secrets, run roots or job databases.
- Evidence lives under `$ACP2_EVIDENCE` and is committed only when the platform owner explicitly asks; otherwise it stays as an auditable external artifact.
- Never run `git reset`, `git clean`, `git stash` on shared work, force-push, rebase shared branches or merge into `main`. The platform owner builds the integration candidate from the delivered commits.
- If a shared-file change is needed, write `science/requests/<topic>.md` with the exact patch, reason, tests, compatibility and rollback; do not apply it in this track.

## Success criteria

> One row per IS row. The plan is complete only when every IS row has a delivering todo and a proving QA scenario; F4 checks the delivered behavior against these rows 1:1, and a shortfall becomes new `- [ ] N.` rows, never a note.

| IS | Delivering todo(s) | Proving QA scenario | Evidence |
| --- | --- | --- | --- |
| IS-1 | 3-8, 17 | capability/reaction/evidence contract tests plus the H1/H2 handoff validation | `$ACP2_EVIDENCE/A/A-08/result.json` |
| IS-2 | 9-10 | round-trip and import-purity assertions in the research type tests | `$ACP2_EVIDENCE/A/A-04/result.json` |
| IS-3 | 11-12 | deterministic catalog, conflict and analysis-versus-compute spy tests | `$ACP2_EVIDENCE/A/A-05/result.json` |
| IS-4 | 13-14 | single-delegation spy, empty preflight directory, fifteen-task matrix | `$ACP2_EVIDENCE/A/A-06/result.json` |
| IS-5 | 15-16 | canonical JSON twice, identity sensitivity to mapping/method/state, job-name independence | `$ACP2_EVIDENCE/A/A-07/result.json` |
| IS-6 | 22, 31-32 | stationary-point, TS-mode and IRC negative fixtures | `$ACP2_EVIDENCE/A/A-16/result.json` |
| IS-7 | 7-8, 36 | claim-status and provenance assertions on the final report | `$ACP2_EVIDENCE/A/A-19/result.json` |
| IS-8 | 18-26 | real singlepoint, opt/freq artifact validation and Local/LSF cross-check | `$ACP2_EVIDENCE/A/A-13/result.json` |
| IS-9 | 27-37 | DA recipe compile, real TS/frequency/IRC/thermochemistry and H4 gate | `$ACP2_EVIDENCE/A/A-20/result.json` |
| IS-10 | 38-42 | external distribution install/enable/conflict/upgrade tests | `$ACP2_EVIDENCE/A/A-23/result.json` |
| IS-11 | 43-46 | cross-domain suite plus the selectivity two-path evidence test | `$ACP2_EVIDENCE/A/A-26/result.json` |
| IS-12 | 48-50 | clean-venv wheel probe and documented-example execution | `$ACP2_EVIDENCE/A/A-27/result.json` |
| IS-13 | 1-2, 17, 26, 37, 47, 51 | architecture guards, recovery fixtures, packaging assets and H7 handoff | `$ACP2_EVIDENCE/A/A-29/result.json` |

