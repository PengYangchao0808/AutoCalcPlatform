# B-req-01 — Close the launch / application API field gaps

- **Status:** open
- **Raised by:** C (T-03 / C01b), 2026-10-22
- **Requested of:** B (application API / MCP launch owner; B03/B04a)
- **Severity:** blocking for T-05 and T-11 (bridge submit path)

## Consumer step

T-05 (`MCP client fixtures`) and T-11 (`Bridge submit/status/evidence`). C's
RPH bridge calls only the versioned MCP tools B registers; it never opens a
second HTTP client.

## Owner

B — `runtime/application-api.v1.json` and the MCP tool binding (B03/B04a).

## Current schema revision

Committed at B HEAD: `acp2.runtime.application-api.v1`, contract version
`1.0.0`, sha256 `a9f7e741007029638b64e069d918746f7105c2bf99c97dc1e9c3818cdcc0d4a4`.
The P2 research extension is
`runtime/p2-research-operations.v1.json` v1.0.0, sha256
`f2126a9d92aa429e1da64e1bb04c56f71b38b57c2ce4fe8110d1289fa6198c2c`.

## Expected schema revision

`acp2.runtime.application-api.v1` **v1.1.0** (additive) that exposes, for a
research launch:

- `proposal_ref` and `plan_digest` on the launch/submit input (not only a
  free-form job payload);
- `project_id` resolved server-side (a payload claim is ignored/rejected);
- a `budget` object with an explicit unit and `timeout_s`;
- the `acp.research.v1` extension handshake through `tools/call acp_capabilities`
  (`extensions[]` with `implemented`/`negotiated`/`refusal`) — M0-09 moved this out of
  `initialize`, which now only points at the carrier;
- a stable place for the created `task_ref` (`job_id`, `attempt`,
  `release_id`) in the submit result.

C does not copy these field lists into its own schema; it references the B
contract by version + digest.

## Minimal input

```json
{
  "tool": "acp_submit",
  "arguments": {
    "proposal_ref": "prop-da-selectivity-001",
    "plan_digest": "sha256:32bd96dd1c8d2eb962a419590ff91ebf695adef1c587c147185f522be5422d9b",
    "budget": {"unit": "task_count", "count": 1, "timeout_s": 3600}
  }
}
```

## Expected success output

```json
{
  "result": "accepted",
  "task_ref": {
    "job_id": "20261022_001_DAselectivity",
    "attempt": 1,
    "release_id": "release-2026.10.22"
  },
  "approval_ref": "approval-da-selectivity-001"
}
```

## Failure output

A submit without `plan_digest`, or carrying a client-asserted project that does
not match the resolved scope, is rejected and no task is created:

```json
{
  "class": "authorization",
  "code": "access_denied",
  "params": {
    "rule": "project_scope_mismatch",
    "path": "arguments.project_id"
  },
  "input": {
    "tool": "acp_submit",
    "arguments": {
      "project_id": "proj-other",
      "proposal_ref": "prop-da-selectivity-001"
    }
  }
}
```

## Compatibility requirement

- Additive within `v1`; a renamed/removal field or a different task-ref shape
  requires a new major contract and a new MCP tool-name version.
- The eight frozen P1 tools keep their names and input schemas; the P2
  extension adds tools, never widens a P1 one.
- C's bridge fails closed when the negotiated extension is absent
  (`unsupported_capability`), never falls back to a second client protocol.

## Evidence path

`$ACP2_EVIDENCE/C/contracts/<run_id>/` plus the B contract digest cited above.

## D06 amendment (M1-04a / M1-04b, 2026-10-27)

The launch field gap above is closed with **two** digests and **one** submit
classification; neither is optional and neither may stand in for the other.

| wire field | means after M1-04 | domain (plan-identity §6) |
| --- | --- | --- |
| `proposal_ref` | the proposal the science belongs to | `proposal_digest` scope |
| `proposal_digest` | the semantic Proposal projection | `proposal_digest` |
| `plan_digest` | the **execution** plan (workflow/input/method/resources) | `execution_plan_digest` |
| `budget` | the launch bound **only** (`unit` + `count?` + `timeout_s`) | `execution_limits` / `ResourceBounds` |
| approval `science_digest` | the C `plan_definition_digest` the approver signed | `plan_definition_digest` |

Rules now enforced at submit (single `classify_submit`, `tests/test_acp2_submit_policy.py`):

1. a submit carrying **only** `budget` is **not** a legacy job: it is refused
   (`approval_required`) instead of having its budget silently dropped;
2. a complete research launch requires `proposal_ref` **and** `plan_digest`;
   a missing `plan_digest` is `input_invalid(plan_digest_required)`, a missing
   proposal reference is `access_denied(approval_required)`;
3. the approval binds the science digest as well; a submit whose proposal
   science cannot be read/verified is `access_denied(science_binding_unverifiable)`;
4. an **agent** credential (server-side credential scope or deployment policy
   profile `ACP_SUBMIT_POLICY_PROFILE=agent`) can never downgrade: omit-all,
   budget-only and proposal-less submits are denials with a typed rule;
5. a legacy `local-single` / keyed `manual-legacy` submit is explicitly allowed
   and recorded (`V1JobCreatedResponse.submit_class`).

The `budget` object itself stays `{unit, count?, timeout_s}` on the v1 wire; an
undeclared inner key is `422 input_invalid(unknown_field)` (it used to be
dropped by the normalizer). The D09 shape (`wall_seconds`, `queue_seconds`,
`input_tokens`, `output_tokens`, `core_hours`) is the **proposal/approval**
budget: `wall_seconds` → `ResourceBounds`/timeout (local termination + CCCP
`resources.timeout_s` at subprocess level + LSF `-W`), `queue_seconds` → the
queue deadline, tokens/core-hours → recorded-only (the RPH client throttle is
not a platform enforcement claim).
