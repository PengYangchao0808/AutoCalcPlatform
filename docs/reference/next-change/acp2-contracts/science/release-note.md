# ACP2 Plan A — Science Release Note (H7, A-29)

Status: **provisional science handoff**. `handoff-H7.json` is signed by track A
for its own science scope, but `release_readiness.ready` is **false**: no B25a
release candidate exists and the real-run rows for H3 (Local/LSF), H4 (DA),
H6 (selectivity / C T-40b) remain `NOT_VERIFIED`. This note is not a product
release announcement and does not authorize a production deployment.

## 1. What this track delivers

- **Contract set `acp2.science` version 1 (frozen)** — eight JSON schemas, two
  contract prose documents and four example instances, delivered by H1 and
  verified byte-for-byte in A-29.
- **Capability catalog** — four built-in capabilities
  (`electronic.energy`, `geometry.optimize`, `vibration.compute`,
  `vibration.analyze`) plus three domain capabilities (`nmr.shielding`,
  `nmr.compare`, `thermochemistry.ensemble`).
- **Fifteen task kinds** with exactly one execution station under
  `src/cccp/calculation/tasks/` and fifteen exported `run_*` cores.
- **Cross-domain contracts** — recipe/identity, evidence/provenance,
  reaction/question, plugin v1/v2 upgrade, historical readers and version
  regression, delivered through H2, H5 and H6.
- **Installed-package and example probes** — A-27b exercises the packaged
  science corpus from a clean venv over a reproducible local-candidate wheel;
  A-28 runs four software-free SDK examples and the acceptance harness without
  a real QC binary.

## 2. Frozen contract set

| Item | Value |
|---|---|
| Contract set | `acp2.science` |
| Version | `1` |
| Frozen | `true` |
| Schema files | 8 (`capability`, `task-binding`, `errors`, `reaction`, `question`, `evidence`, `provenance`, `recipe`) |
| Contract prose | `semantic-rules.md`, `evidence-semantics.md` |
| Examples | 4 (`capability-energy`, `task-binding-singlepoint`, `reaction-da`, `question-activation`) |
| Handoff records | H1–H6 (digests pinned in `handoff-H7.json`) |

Schema/provider/plugin/fixture digests are re-computed from disk in A-29 and
recorded in `handoff-H7.json::contract_digests`. A digest mismatch is a gate
failure, never a warning.

## 3. What is verified (and how)

| Scope | Evidence class | Result |
|---|---|---|
| H0 baseline record | static commit + byte digests | PASS |
| H1 capability/task-binding contracts | static contract, 14 digested files | PASS |
| H2 capability registry, recipes, identity, MCP static rows | static implementation | PASS |
| H3 same-task P1 ledger | static contract tests only; six real cells `NOT_VERIFIED` | `NOT_VERIFIED` |
| H4 Diels-Alder science gate | constructed validators/checkers only; real positive rows `NOT_VERIFIED` | `NOT_VERIFIED` |
| H5 plugin conflicts and v1→v2 upgrade | constructed plugin fixtures | PASS |
| H6 cross-domain/version regression | constructed NMR/thermochemistry contracts; selectivity `NOT_VERIFIED` | `NOT_VERIFIED` |
| A-27b installed package | local-candidate wheel, clean venv, 18 passed | PASS (local candidate) |
| A-28 SDK examples | four software-free examples, harness absent-wheel exit 2 / present-wheel PASS | PASS (no real QC) |
| Reverse-import / one-station / 15 workflows / shims / assets | static scans + guard tests | PASS |

The complete command set, exit codes and evidence records are in
`replay-index.json` and `$ACP2_EVIDENCE/A/A-29/a29-20261023T053950Z/`.

## 4. Explicitly NOT_VERIFIED

- **B25a release candidate wheel** — no platform candidate exists. The A-27b
  wheel is a reproducible local candidate (`sha256:78d0e622…`) and must never be
  presented as B25a.
- **Real ORCA runs** — no single point, optimization, frequency, TS, IRC or
  gradient was launched; every QC binary row is `needs-real-evidence`.
- **H3 Local/LSF real rows** — no B10 three-entry task, B07 deadlines, B05 stdio
  launch, C15 browser record or LSF receipt.
- **H4 DA real rows** — no B20a-exec TS/IRC/energy artifact and no
  B20a-search evidence; H4 stays open.
- **H6 selectivity and C T-40b** — no C T-40a submission, B23b artifact, real
  A-25b audit or C T-40b signoff.
- **Platform jobs / receipts** — B01 `acp2.acceptance.v1` and the B01 registry are
  unpublished; no platform job id, attempt, receipt or run key is invented.
- **NMR/thermochemistry real runs** — the H6 PASS rows are constructed contract
  verification with `real_run=false`.

## 5. Release readiness and aggregation

`release_readiness.ready = false` because the required rows
`H3`, `H4`, `H6` and `B25a-release-candidate` are `NOT_VERIFIED`. A future
signer may set `ready = true` only after every required row is `PASS` with an
executed evidence reference and, where the row is a real-run row, real evidence.

Track B owns the platform aggregate release index and performs it after A-29 and
after track C's final handoff. A does not wait for that aggregation and does not
treat it as an H7 prerequisite.

## 6. Rollback / replay

- Rollback: revert the single A-29 commit; no production service, database or
  run root was touched.
- Replay: run every entry in `replay-index.json` from the recorded `cwd` with the
  recorded interpreter. The acceptance gate is the first entry; the wheel probe
  is the second. Every evidence path in the index must exist before replay.
- Evidence: `$ACP2_EVIDENCE/A/A-29/a29-20261023T053950Z/result.json` plus the
  raw logs and probe scripts in the same directory.
