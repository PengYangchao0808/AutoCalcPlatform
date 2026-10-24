# B-req-07 — `runtime/dependencies.json` with an executable dependency validator

- **Request ID:** `B-req-07`
- **Owner / dispatcher:** B00 (create the file) and B03 (validate + keep it current)
- **Producer:** B00 (baseline/manifest rows), B03 (acceptance scheduling + validator)
- **Consumer step:** C/T-02 (this contract), and every C todo that reads a B row:
  T-05, T-06, T-11, T-12, T-13, T-18–T-22, T-34–T-40
- **Consumer track:** C (`acp2-plan-c-rph-workbench`)
- **Current schema revision:** no versioned `docs/reference/next-change/acp2-contracts/runtime/dependencies.json`
  is visible in the C baseline (`/ACPServer/acp2-development/C`, HEAD
  `c54823109010b651de3f0cca28da5af54a10fdfb`); `runtime/**` is absent there.
  C keeps its own rows in `experience/handoffs/dependencies-c.json`
  (`acp2.experience.dependencies-c.v1`, 6 rows, all `delivered:false`) and never
  edits the B file. Status: `NOT_VERIFIED(b00_handoff)`.
- **Expected schema revision:** B-published `acp2.dependencies.v1` (or a later
  major, versioned and digested). This request freezes the *validation* rules
  below; field names may be extended, never made optional.

## Required row shape

```json
{
  "schema": "acp2.dependencies.v1",
  "schema_version": "1.0.0",
  "source_plan": ".omo/plans/acp2-parallel-plan-b-runtime.md",
  "rows": {
    "B01:scenario-registry": {
      "producer": "B01",
      "track": "B",
      "artifact": {
        "path": "docs/reference/next-change/acp2-contracts/runtime/scenario-registry.json",
        "version": "1.0.0",
        "digest": "sha256:<64-hex>",
        "available_at": "2026-10-22T00:00:00Z"
      },
      "requires": {
        "relation": "AND",
        "depends_on": ["B00:baseline-lock"]
      },
      "readiness": "contract_ready",
      "delivery_status": "planned",
      "acceptance": {
        "at": "2026-10-23T00:00:00Z",
        "after": ["B00:baseline-lock"]
      }
    },
    "C:T-06": {
      "producer": "C",
      "track": "C",
      "artifact": {
        "path": "scripts/acceptance/acp2_experience.py",
        "version": "1.0.0",
        "digest": "sha256:<64-hex>",
        "available_at": "2026-10-24T00:00:00Z"
      },
      "requires": {
        "relation": "OR",
        "any_of": ["B01:acceptance-validator", "B01:scenario-registry"]
      },
      "readiness": "contract_ready",
      "delivery_status": "blocked",
      "acceptance": {
        "at": "2026-10-24T00:00:00Z",
        "after": ["B01:acceptance-validator", "B01:scenario-registry"]
      }
    }
  }
}
```

Two vocabularies must stay separate:

- `readiness` ∈ `contract_ready | implementation_ready | integration_ready | verified`
  — the *maturity ladder*.
- `delivery_status` ∈ `planned | blocked` — whether the producer has started/how
  it is scheduled. `planned` is **not** a maturity level and must never be
  written into `readiness`.

Aliases are read-aliases, not extra values: `deployed → integration_ready`,
`preflight → da-preflight`, `scenario_id → scenario`, `runtime:* → experience:*`.
A conflicting pair (same key with different canonical values) is rejected. An
unknown value is rejected; it is not silently accepted as an alias.

## Dependencies must be explicit AND/OR

Free-text "or" inside a requirement string is forbidden. Every edge is either:

- `{"relation": "AND", "depends_on": ["<row-id>", ...]}` — all must be ready; or
- `{"relation": "OR", "any_of": ["<row-id>", ...]}` — at least one must be ready.

`depends_on` and `any_of` are arrays of row ids, never prose, never artifact
paths. Mixing `depends_on` with `relation: "OR"` (or `any_of` with `"AND"`) is a
schema error.

## Validator requirements (must be executable, not prose)

`scripts/acceptance/acp2_runtime.py --validate-dependencies <file>` (or the B03
equivalent) must exit non-zero for each of these, with the named reason code:

| # | Rejection | Reason code |
| --- | --- | --- |
| 1 | Row has no `producer` (or an empty one) | `missing_producer` |
| 2 | `depends_on`/`any_of` names a row id that does not exist | `dangling_reference` |
| 3 | Cycle that crosses tracks (A→B→C→A, at least two tracks in the cycle) | `cross_track_cycle` |
| 4 | `acceptance.at` is earlier than the `artifact.available_at` of any row in `acceptance.after` (acceptance scheduled before its prerequisite exists) | `acceptance_before_artifact` |
| 5 | `relation` missing, unknown, or inconsistent with its key list | `relation_invalid` |
| 6 | Free-text "or" in a requirement string / non-row-id dependency | `dependency_not_row_ref` |
| 7 | `delivery_status` used as `readiness` or vice versa | `status_vocabulary_confusion` |
| 8 | Conflicting alias pair or unknown enum value | `alias_conflict` / `enum_unknown` |

Cycle detection must run over the union of A, B and C rows; a cycle wholly
inside one track is still rejected, but the *named* failure above is specifically
the cross-track case. `acceptance.after` must be a subset of the transitive
prerequisite set; an id in `after` that is not a prerequisite is
`acceptance_before_artifact` too (an unproven ordering claim).

## Minimal input

```json
{
  "request": "B-req-07",
  "consumer": "C/T-02",
  "observed": {
    "c_baseline_head": "c54823109010b651de3f0cca28da5af54a10fdfb",
    "runtime_dir_present": false,
    "c_rows_file": "docs/reference/next-change/acp2-contracts/experience/handoffs/dependencies-c.json"
  },
  "required_rows_for_c": [
    "B00:baseline-lock",
    "B00:manifest/evidence_root",
    "B01:scenario-registry",
    "B01:acceptance-validator",
    "B02a:tools-root",
    "B02a:role-interpreters",
    "B02a:browser-venv",
    "B03:mcp-extension"
  ]
}
```

## Expected success output

A B-published file that validates cleanly, contains the eight C-facing rows
above, and for each row reports the producer, versioned artifact + digest,
`readiness`, `delivery_status`, explicit `requires.relation`, and
`acceptance.at/after`. Example validation transcript:

```json
{
  "request": "B-req-07",
  "result": "PASS",
  "validated": true,
  "rows": 43,
  "cross_track_cycles": 0,
  "dangling_references": 0,
  "acceptance_before_artifact": 0,
  "delivery_status_vs_readiness_conflicts": 0,
  "digest": "sha256:<64-hex>"
}
```

## Failure output

Concrete rejected document. Every one of the four required rejections is
represented; the validator must exit `1` and name all triggered codes (not just
the first).

```json
{
  "request": "B-req-07",
  "result": "FAIL",
  "input": {
    "rows": {
      "C:T-06": {
        "artifact": {"path": "scripts/acceptance/acp2_experience.py", "version": "1.0.0", "digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000", "available_at": "2026-10-24T00:00:00Z"},
        "requires": {"relation": "OR", "depends_on": ["B01:missing-row"]},
        "readiness": "planned",
        "delivery_status": "planned",
        "acceptance": {"at": "2026-10-23T00:00:00Z", "after": ["B01:late-artifact"]}
      },
      "B01:late-artifact": {
        "producer": "B01",
        "artifact": {"path": "docs/reference/next-change/acp2-contracts/runtime/scenario-registry.json", "version": "1.0.0", "digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111", "available_at": "2026-10-25T00:00:00Z"},
        "requires": {"relation": "AND", "depends_on": ["C:T-07"]},
        "readiness": "planned",
        "delivery_status": "planned",
        "acceptance": {"at": "2026-10-26T00:00:00Z", "after": ["C:T-07"]}
      },
      "C:T-72": {
        "producer": "C",
        "artifact": {"path": "scripts/acceptance/acp2_experience.py", "version": "1.0.0", "digest": "sha256:2222222222222222222222222222222222222222222222222222222222222222", "available_at": "2026-10-24T00:00:00Z"},
        "requires": {"relation": "AND", "depends_on": ["A:T-01"]},
        "readiness": "contract_ready",
        "delivery_status": "planned",
        "acceptance": {"at": "2026-10-27T00:00:00Z", "after": ["A:T-01"]}
      },
      "A:T-01": {
        "producer": "A",
        "artifact": {"path": "docs/reference/next-change/acp2-contracts/science/proposal.schema.json", "version": "1.0.0", "digest": "sha256:3333333333333333333333333333333333333333333333333333333333333333", "available_at": "2026-10-26T00:00:00Z"},
        "requires": {"relation": "AND", "depends_on": ["C:T-72"]},
        "readiness": "implementation_ready",
        "delivery_status": "planned",
        "acceptance": {"at": "2026-10-28T00:00:00Z", "after": ["C:T-72"]}
      }
    }
  },
  "rejected": [
    {"code": "missing_producer", "row": "C:T-06", "detail": "row has no producer/owner"},
    {"code": "dangling_reference", "row": "C:T-06", "detail": "requires.depends_on names B01:missing-row which is absent"},
    {"code": "relation_invalid", "row": "C:T-06", "detail": "relation=OR used with depends_on; OR requires any_of"},
    {"code": "status_vocabulary_confusion", "row": "C:T-06", "detail": "readiness='planned' is a delivery_status value"},
    {"code": "cross_track_cycle", "rows": ["C:T-72", "A:T-01"], "detail": "C→A→C crosses tracks and has no entry point"},
    {"code": "acceptance_before_artifact", "row": "C:T-06", "detail": "acceptance.at 2026-10-23T00:00:00Z precedes B01:late-artifact available_at 2026-10-25T00:00:00Z"}
  ],
  "expected": "all rows produced by their owner, every edge a row id, cross-track cycles absent, every acceptance.at >= the available_at of each prerequisite artifact, readiness and delivery_status in separate vocabularies"
}
```

## Compatibility and follow-up

- The validator may add reason codes; it may never remove or rename the eight
  above without a major schema bump.
- C's `experience/handoffs/dependencies-c.json` is the authoring surface for C
  rows until B accepts them. B owns the merge; C does not edit the B file.
- If B ships a different file name, it must be registered in the B manifest and
  reflected here by a `B-req` amendment, not by a silent C-side rename.

## Evidence path

C-side evidence for consuming this contract:
`$ACP2_EVIDENCE/C/contracts/<run_id>/` (assertions in
`assert_contracts.py` + `result.json`). B-side validation evidence belongs to
`$ACP2_EVIDENCE/B/<scenario>/<run_id>/`.
