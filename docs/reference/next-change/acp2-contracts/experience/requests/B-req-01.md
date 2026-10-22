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
- the negotiated `acp.research.v1` extension id/version in initialize;
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
    "plan_digest": "sha256:d6b0c35220aec8385f0c92778da312d1c60f7023630572969fbbcd2357b70c8b",
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
