# B-req-04 — B must ingest C's harness results and the launch/DSH versions into its ledgers

- **Request ID:** `B-req-04`
- **Owner:** B00 / B03 (dispatcher + ledger owners)
- **Producer:** C/T-14 (H2 C-side closeout; receipt publisher)
- **Consumer step:** B00/B03 ingest + H2 total sign-off; any B lane that
  consumes C harness evidence (B04–B06 controller/MCP gates, B07 queues)
- **Consumer track:** C (`acp2-plan-c-rph-workbench`, T-14)
- **Current schema revision:** C has published
  `experience/handoffs/H2.json` (`acp2.acceptance.v1`, owner `C`, scenario
  `experience:h2`, phase `aggregate`, evidence_kind `aggregate`,
  run_id `h2-c-closeout-20261022T223404Z-704517`). B has no ledger row that
  ingests it, and no record of the launch/DSH versions C validated against.
  Status: `NOT_VERIFIED(b_ingest)`.
- **Expected schema revision:** B-published `runtime/dependencies.json` (v1+)
  rows referencing C's H2 receipt by versioned path + digest, plus recorded
  `dsh_revision` (`687f282e8625c6b8d11ff58211a90524be2e4317`), launcher
  (`tsx@4.23.13` loader path from `ACP2_DSH_LOADER`), Node `22.19.0` /
  pnpm `11.28.5`, and the MCP launch identity artifacts C captured.

## Why B must ingest this now

H2's C side is deliberately `NOT_VERIFIED`: 7 of 10 P0 scenarios PASS, and
three live scenarios (`mcp-live`, `dsh-discovery-live`, `model-live`) are
blocked on `controller_unavailable` (and for `model-live`,
`no_model_credential`). Total H2 sign-off belongs to B, and B cannot sign off
(or re-open) what it has not ingested. Two things must enter B's ledger:

1. **C's harness results as first-class evidence.** The ten per-scenario rows
   (result/scope/reason/command/cwd/exit_code/candidate
   {server_revision, client_revision, harness_revision}/timestamps/
   artifacts+sha256/usage plus interpreter, product_import, harness_path,
   test_revision) live verbatim in `H2.json → extensions.experience.rows`,
   each row pointing at its authoritative receipt under `$ACP2_EVIDENCE/C/`.
2. **The launch/DSH version set C actually validated against** — DSH source
   revision, tsx loader, Node/pnpm versions, and the frozen MCP launch
   identity (`launch-identity.json`, `tool-schemas.json` in the `mcp-live`
   evidence). Without these pinned in B's ledger, a later DSH/Node change can
   silently invalidate C's bridge/tool-allowlist evidence.

Additionally, C observed a validator boundary B should close on its side:

3. **Aggregate child cross-check.** `acp2_runtime.validate_receipt` verifies
   artifact digests and aggregate phase coverage but does not re-read the
   child receipts named by `sub_results[].receipt`. A fabricated aggregate
   whose `sub_results[].result` values were flipped to PASS is accepted by
   `--verify-receipt` alone (C recorded this in
   `$ACP2_EVIDENCE/C/h2/verify.log`, tamper-proof section). B's ingest must
   cross-check every `sub_results` child against the receipt file on disk
   (result equality + digest) before promoting any aggregate row, the same way
   C's `check_h2_rows.py` does for the C side.

## Minimal input

```json
{
  "request": "B-req-04",
  "user": "C/T-14",
  "h2_receipt": "docs/reference/next-change/acp2-contracts/experience/handoffs/H2.json",
  "h2_result": "NOT_VERIFIED",
  "gap_scenarios": ["mcp-live", "dsh-discovery-live", "model-live"],
  "dsh_revision": "687f282e8625c6b8d11ff58211a90524be2e4317",
  "node_version": "22.19.0",
  "pnpm_version": "11.28.5",
  "loader": "file:///opt/acp2-tools/deepseek-harness/node_modules/.pnpm/tsx@4.23.13/node_modules/tsx/dist/esm/index.mjs"
}
```

## Expected success output

A B-published `runtime/dependencies.json` fragment containing at minimum:

```json
{
  "rows": {
    "C:h2-aggregate": {
      "producer": "C/T-14",
      "artifact": "docs/reference/next-change/acp2-contracts/experience/handoffs/H2.json",
      "readiness": "integration_ready",
      "delivery_status": "planned",
      "requires": [
        "ten P0 rows present with result/scope/reason/command/cwd/exit_code/candidate/timestamps/artifacts+sha256/usage",
        "every row carries interpreter/product_import/harness_path/test_revision",
        "sub_results cross-checked against child receipt files (result equality + sha256)"
      ]
    },
    "C:launch-dsh-versions": {
      "producer": "C/T-04,T-11,T-12",
      "readiness": "verified",
      "requires": ["dsh_revision=687f282e8625c6b8d11ff58211a90524be2e4317", "node=22.19.0", "pnpm=11.28.5", "tsx loader pinned"]
    }
  }
}
```

B's H2 sign-off row may only say PASS after C's three gap scenarios are PASS
and re-aggregated; until then B's row mirrors `NOT_VERIFIED` with the same gap
list.

## Failure output

Minimum one accepted failure. B's ingest must reject an H2 aggregate whose
child rows contradict the receipts on disk — exactly the fabrication C proved
is currently accepted by `--verify-receipt` alone:

```json
{
  "request": "B-req-04",
  "result": "FAIL",
  "reason": "aggregate children contradict receipt files",
  "detail": "H2.json sub_results lists mcp-live=PASS while $ACP2_EVIDENCE/C/mcp-live/.../experience_mcp-live-....json records NOT_VERIFIED(controller_unavailable)",
  "expected": "ingest re-reads every sub_results receipt, compares result/exit_code and digest, and rejects the aggregate"
}
```

Second failure mode: signing off H2 on B's side while the C side is open:

```json
{
  "request": "B-req-04",
  "result": "FAIL",
  "reason": "H2 total sign-off with C side open",
  "detail": "B row readiness=verified while C gaps mcp-live/dsh-discovery-live/model-live are NOT_VERIFIED",
  "expected": "B mirrors NOT_VERIFIED until all ten P0 scenarios are PASS and re-aggregated by C"
}
```

## Compatibility requirement

- `H2.json` stays a C-owned artifact; B consumes it read-only by versioned
  path + digest and never edits `extensions.experience.*`.
- The ten row keys (`baseline` … `model-live`) and the gap list format
  (`<scenario>=NOT_VERIFIED(<reason>)`) are stable; B may add fields around
  them but must not rename or re-order semantics.
- DSH/Node/pnpm/loader versions must be recorded exactly as C validated them;
  a change on B's side requires a new C re-run of T-04/T-11/T-12 scenarios and
  a fresh H2 aggregate.
- C will not treat any B sign-off as valid while its own aggregate says
  `NOT_VERIFIED` (the receipt's `experience.h2_c_side_open` extension is the
  machine-readable flag).

## Evidence path

- H2 receipt: `docs/reference/next-change/acp2-contracts/experience/handoffs/H2.json`
  (C worktree `/opt/acp2-dev/C`)
- Env reuse: `docs/reference/next-change/acp2-contracts/experience/handoffs/H2-env-reuse.md`
- Closeout evidence: `reports/acp2_implementation/bb45a46/C/h2/`
  (`build.log`, `verify.log` incl. tamper-proof section, `row-diff-check.log`,
  `ten-row-summary.md`, env-reuse captures, `build_h2_receipt.py`,
  `check_h2_rows.py`)
- B-side expected: `docs/reference/next-change/acp2-contracts/runtime/dependencies.json`
  rows `C:h2-aggregate` and `C:launch-dsh-versions`, plus the B-owned H2
  sign-off record.
