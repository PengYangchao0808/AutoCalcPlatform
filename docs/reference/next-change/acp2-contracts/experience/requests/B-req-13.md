# B-req-13 — Synchronize the B-owned plan/index/runbook before each cross-track gate

- **Request ID:** `B-req-13`
- **Owner / dispatcher:** B00 (baseline/paths), B01 (public phase/readiness registry), B03 (P2 entry + MCP extension)
- **Producer:** B00 + B01 + B03 (the three share the main plan, execution index and runbook)
- **Consumer step:** C/T-02 (this contract); gates H1, H4, H6 and every C todo
  that reads a public phase or P2 entry: T-06, T-07, T-14, T-18–T-22, T-23–T-36,
  T-40–T-42
- **Consumer track:** C (`acp2-plan-c-rph-workbench`)
- **Current revision:** B has not handed C a versioned registry or a
  synchronized index; the C baseline (`e7fa3283d040fedd7926ec50df06ae56e283c7ee`)
  has no `runtime/**`. C writes only `extensions.experience` and references B01
  by `registry_ref`. Status: `NOT_VERIFIED(b01_handoff)` +
  `NOT_VERIFIED(b03_handoff)`.
- **Expected revision:** B-published, versioned and digested changes to
  `ACP_2.0_Parallel_Plan_B_Runtime_20261020.md`,
  `ACP_2.0_Parallel_Execution_Index_20261020.md` and
  `ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md`, plus the B01
  registry. Each change lands **before** the gate it enables, not after.

## Why this is a gate, not documentation cleanup

C cannot schedule a phase that B has not registered, and cannot enter a gate on
a runbook that still describes the previous baseline/paths. If the plan, index
and runbook are updated after the gate, the gate evidence has no stable
definition to be measured against. Therefore synchronization is a precondition,
and a late/B25b-only update is a failure, not a cleanup.

## Required synchronization points

| Before | B00/B03 must have published, in main plan + index + runbook | Reject if |
| --- | --- | --- |
| **H1** (baseline/paths/enums/P2 entry) | 1. The final baseline identity (`final_debug_candidate`) and its reconstruction rule.<br>2. The absolute path roots (`$ACP2_TOOLS`, `$ACP2_DATA_ROOT`, `$ACP2_EVIDENCE`) and the fact that they are owned by B.<br>3. The public enum set (`phase`, `readiness`, `delivery_status`, result/scope) and their aliases.<br>4. The P2 entry (which scenario/phase opens P2 and who owns its execution). | Any of the four is missing, still "TBD", or appears only in one of the three documents. |
| **ADR-008 / H4** (science acceptance scope) | The frozen CDXML scope and the frozen search scope (what is accepted, what is explicitly out of scope, and the evidence rows that prove each). | CDXML or search scope is decided inside the H4 acceptance run, or differs between plan/index/runbook. |
| **B23** (P4 sub-delivery) | The P4 sub-delivery split and the **required** H4/H6 rows, each with an id, owner and acceptance. | B23 starts before the P4 split and the H4/H6 required rows are registered. |
| **B25b** (release/upgrade manual) | B25b may only consolidate content already published at the earlier gates. | B25b introduces a baseline/path/enum/CDXML/search/P4/H4/H6 decision for the first time. |

## B01 public registry — mandatory registrations

The B01 registry (the only authority for public `scenario`/`phase`/`readiness`)
must register at least:

- `phase`: `main`, `approval`, `budget`, `da-submit`, `da-evidence`,
  `aggregate`, `da-preflight`
- namespaced C rows: `experience:fixture`, `experience:live`,
  `experience:control`, `experience:search`, `experience:nmr`,
  `experience:thermo`, `experience:selectivity`

`da-preflight` and `da-submit` are **separate** phases. C must be able to invoke
B18a preflight with `--phase da-preflight` and B20a-ready/exec with
`--phase da-submit` (or the registry-registered equivalent); branching on an
output directory or a task number is forbidden. `experience:*` rows are C's
namespaced registrations — they are **not** a second public phase registry and
C may not mint any other public phase.

Registration rules:

- An undeclared `(scenario, phase)` is a usage error (exit `3`), never a silent
  default to `main`.
- `readiness` uses `contract_ready | implementation_ready | integration_ready |
  verified`; `delivery_status` uses `planned | blocked`. They are separate
  fields and separate vocabularies.
- Read aliases: `deployed → integration_ready`, `preflight → da-preflight`,
  `scenario_id → scenario`, `runtime:* → experience:*`. Aliases are normalized
  **on read**; they must not be listed as rejects, and an unknown value must be
  rejected (not coerced).
- One implementing owner per `(scenario, phase)`; shared scenarios have one
  writer and named consumers.
- Receipt identity is `(owner, scenario, phase, run_id)`. An aggregate reads
  the same `run_id`; it never picks an arbitrary child.

Until the registry exists and is registered in the plan/index/runbook, every C
row that names a public phase is `NOT_VERIFIED(phase_registry)` and must not be
counted as PASS.

## Minimal input

```json
{
  "request": "B-req-13",
  "consumer": "C/T-02",
  "observed": {
    "c_baseline_head": "e7fa3283d040fedd7926ec50df06ae56e283c7ee",
    "runtime_dir_present": false,
    "b01_registry_present": false,
    "b03_mcp_extension_present": false,
    "c_write_surface": "extensions.experience only"
  },
  "required_by_gate": {
    "H1": ["baseline", "paths", "public_enums", "p2_entry"],
    "H4": ["cdxml_scope", "search_scope"],
    "B23": ["p4_sub_delivery", "h4_h6_required_rows"],
    "B25b": ["consolidation_only"]
  }
}
```

## Expected success output

```json
{
  "request": "B-req-13",
  "result": "PASS",
  "registry": {
    "owner": "B01",
    "version": "1.0.0",
    "digest": "sha256:<64-hex>",
    "phases": ["main", "approval", "budget", "da-preflight", "da-submit", "da-evidence", "aggregate"],
    "namespaced": ["experience:fixture", "experience:live", "experience:control", "experience:search", "experience:nmr", "experience:thermo", "experience:selectivity"]
  },
  "synchronized_documents": [
    "ACP_2.0_Parallel_Plan_B_Runtime_20261020.md",
    "ACP_2.0_Parallel_Execution_Index_20261020.md",
    "ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md"
  ],
  "synchronized_before": {"H1": true, "H4": true, "B23": true},
  "b25b_scope": "consolidation_only"
}
```

## Failure output

The following must be rejected with exit `1` and the named codes. The first
three are schedule/sync failures; the last two are registry failures.

```json
{
  "request": "B-req-13",
  "result": "FAIL",
  "input": {
    "plan_updated_at_gate": "H4",
    "index_updated_at_gate": "B25b",
    "runbook_updated_at_gate": "B25b",
    "h1_public_enums_present": false,
    "cdxml_scope": "decided_inside_h4",
    "search_scope": "decided_inside_h4",
    "p4_sub_delivery_published_before_b23": false,
    "b25b_introduces": ["baseline", "cdxml_scope"],
    "registry": {
      "phases": ["main", "approval", "budget", "da-submit", "approval", "aggregate"],
      "namespaced": ["experience:fixture", "experience:live"],
      "aliases_in_rejects": ["preflight", "deployed"],
      "unknown_phase": "da-preflight"
    }
  },
  "rejected": [
    {"code": "sync_after_gate", "gate": "H1", "detail": "public enums are still TBD in the plan at H1; the index/runbook are updated only at B25b"},
    {"code": "sync_after_gate", "gate": "H4", "detail": "CDXML and search scope are decided inside the H4 run instead of before ADR-008"},
    {"code": "sync_after_gate", "gate": "B23", "detail": "P4 sub-delivery and H4/H6 required rows are not registered before B23"},
    {"code": "b25b_first_decision", "detail": "B25b introduces baseline and cdxml_scope for the first time"},
    {"code": "phase_unregistered", "phase": "da-preflight", "detail": "da-preflight is missing from the B01 registry and is treated as unknown"},
    {"code": "phase_unregistered", "phase": "experience:control", "detail": "C may not open control/search/nmr/thermo/selectivity without a B01 namespaced entry"},
    {"code": "phase_duplicate", "phase": "approval", "detail": "duplicate phase entry in the registry"},
    {"code": "alias_conflict", "alias": "preflight", "detail": "a read alias is listed as a reject; aliases must normalize, not reject"}
  ],
  "expected": "H1-synchronized baseline/paths/public enums/P2 entry, H4-frozen CDXML+search scope before ADR-008, P4+H4/H6 rows frozen before B23, B25b consolidation only, and a B01 registry containing all seven public phases plus all seven experience:* rows with read-only aliases"
}
```

## Compatibility

- Adding a phase/scenario is allowed only by the owner named in the runbook's
  frozen registry section, with a new registry version/digest. Removing one, or
  changing an existing meaning, requires the same major-version discipline as the
  acceptance-result contract.
- C's `registry_ref` is the only coupling: C does not copy the enum, and a
  missing registry is recorded as `NOT_VERIFIED(b01_handoff)` /
  `NOT_VERIFIED(phase_registry)`, never as a locally invented value.
- This request does not ask B to change any C-owned file. C's row list stays in
  `experience/handoffs/dependencies-c.json`.

## Evidence path

C-side consumption evidence: `$ACP2_EVIDENCE/C/contracts/<run_id>/`
(`assert_contracts.py`, `result.json`). B-side synchronization evidence:
`$ACP2_EVIDENCE/B/<scenario>/<run_id>/`, referenced by the plan/index/runbook
receipts at the gate that consumes it.
