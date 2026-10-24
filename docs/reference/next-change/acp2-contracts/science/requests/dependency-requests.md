# ACP2 Plan A — External Dependency Requests (A-08)

Status: open. Every row is an artifact A consumes but does not own. A records the
exact missing item and the A tasks that cannot pass until the producer publishes
it. The machine ledger is `../cross-track-dependencies.json`; this document is
the human-readable request list and adds no second truth.

Rules A applies while these are open:

- An absent artifact yields `NOT_VERIFIED` with the gap below, never `PASS`.
- A version, digest or `run_id` is recorded only when the producer publishes it;
  A never invents one.
- A does not edit B/C files to work around a missing artifact. Requests travel
  through this directory.

| Producer | Artifact A needs | Exact missing item | Affected A tasks | Current A state |
|---|---|---|---|---|
| B00 | Final re-frozen baseline | Either the full 40-hex final baseline commit SHA **or** a reproducible patch manifest with digest. `096cdffe9a3817d8ee443d824830d87ec6e07bc2` is the historical starting point only and is **not** the final baseline. | A-00a (baseline ratification), A-29 (H7 baseline provenance) | `NOT_VERIFIED` — ledger row `B00:final-re-frozen-baseline` |
| B01 | `acceptance-result.schema.json` (`acp2.acceptance.v1`) | The single shared receipt schema, its Schema dialect, required/error fields, scenario/extension rules, and one valid + one invalid cross-validated sample. B01 must freeze the field names once: A uses `command: string[]`, separate `cwd`, `started_at`/`finished_at`, `artifacts[{path,size_bytes,sha256}]`; B currently writes `started`/`finished`, C writes `started_on`/`finished_on`. | A-01b (samples), A-08 (H2 gate), A-10a (harness writer), A-20/A-25b/A-27b (receipt emission) | `NOT_VERIFIED` — no sample delivered; A adds only `extensions.science` when B01 exists |
| B01 | `(owner, scenario, phase, run_id)` phase registry | The registry file, including the `science:*` / `experience:*` prefix decision and the namespaced phases `experience:nmr`, `experience:thermo`, `experience:selectivity`. A's current phase values are plan-declared, not registry-verified; no `run_id` is known. | A-01b, A-08, A-20, A-25b, A-26, A-29 | `NOT_VERIFIED` — see `phase_registry_gap` in the ledger |
| B05 | MCP stdio launch file + minimal read-only capabilities | The launch file (command, args, cwd, env shape) and the minimal read-only capability mapping that makes a handshake and a read-only tool listing possible. B04's premature MCP-adapter check is **not** H2 evidence. | A-08 (H2 external evidence), A-10a (future harness connection) | `NOT_VERIFIED` — H2 MCP/DSH rows stay open |
| B07 | Frozen DA deadlines / run spec | `science_deadline_seconds`, `export_budget_seconds`, `aggregate_budget_seconds`, shared `started_at`, and the queue-accounting policy. Plan prose values (13380/900/120) are not a frozen spec. | A-20 (P2/H4 budget adjudication) | `NOT_VERIFIED` — ledger row `B07:frozen-da-run-spec` |
| B08 | Four-window crash evidence | Evidence for reservation-before-persist, persisted-before-submission-start, submitted-with-lost-response, and restart replay: no duplicate directory/reservation/QC process/bsub; no permanently undispatched accepted task; only identity-valid complete results reused. | A-20 | `NOT_VERIFIED` — ledger row `B08:four-crash-window-evidence` |
| B20a-exec | DA execution receipts + authorized artifacts | Step receipts and authorized products for the A-17/A-18/A-19 scientific audit. It runs only after C T-34's approved single submission. A publishes its audit before B20b aggregates and never waits for B20b/B20c/C25. | A-20 | `NOT_VERIFIED` — ledger row `B20a-exec:da-execution-artifacts` |
| B20a-search | DA candidate-search evidence | Input class/TS seed, candidate source/count, generation steps, filtering/rejection reasons, validators and success criteria meeting `A-14b:search-contract`. A control-loop or known-candidate PASS is not search evidence. | A-14b (contract), A-20 (search adjudication) | `NOT_VERIFIED` — ledger row `B20a-search:search-run-evidence` |
| B23b | Selectivity authorized artifacts | Both qualified paths executed and exported under one `TaskRef` after C T-40a. The exact machine id is the one B publishes; A does not infer it from the numeric prefix. | A-25b | `NOT_VERIFIED` — ledger row `B23b:selectivity-authorized-artifacts` |
| C T-40a | Single selectivity research submission | C's approved single submission for the frozen A-25a selectivity fixture. It must not submit before A-25a freezes the fixture. | A-25a (ordering), A-25b (consumer chain) | `NOT_VERIFIED` — declared external id `C T-40a` |
| C T-40b | Selectivity presentation/signature step | C's presentation/signature record, which signs only after A-25b's audit report. | A-25b | `NOT_VERIFIED` — declared external id `C T-40b` |
| B25a | Release candidate | Three role wheels/sdists plus `release-manifest.json` built from the frozen candidate. A-27b verifies it in a clean environment and A-29 signs H7 from it; A never waits for B26/B25b. | A-27b, A-29 | `NOT_VERIFIED` — ledger row `B25a:release-candidate` |

## Other declared edges (not gate preconditions)

| Producer | Artifact | Exact missing item | Affected A tasks |
|---|---|---|---|
| B03 | `runtime/science-binding.v1.json` + aggregate `runtime/dependencies.json` | The runtime science-binding file reserving A's schema ids/version/digests, and the aggregate ledger that consumes A's rows. A submits rows; B owns the total ledger. | A-01a (contract-set consumer) |

## Handoff state

- H2 (`../handoff-H2.json`) records every row above as `NOT_VERIFIED` with its
  gap; it does not upgrade any row because a producer name exists.
- The P0 static science gate does **not** wait for these artifacts. Reaction and
  recipe contracts are delivered; real computation and MCP/DSH evidence stay
  separate.
