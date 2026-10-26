# P2 Operation Map — research resources and approvals over the official MCP surface

Status: **implemented by T-09 / C06a (2026-10-22)** for the C track
(`.omo/plans/acp2-plan-c-rph-workbench.md`). This document is the consumer
contract for `rph/packages/bridge/src/index.ts`; it is machine-checked by
`scripts/acceptance/acp2_experience_scenario_bridge_contract.py`.

`bridge-contract` is **fixture evidence**. It proves the mapping and the
negative guards offline; it does **not** close H2 and does not claim that the
P2 tools are live on a running Controller.

## 1. Provenance and negotiation

- B03 file: `docs/reference/next-change/acp2-contracts/runtime/p2-research-operations.v1.json`
- schema: `acp2.runtime.p2-research-operations.v1`
- sha256: f2126a9d92aa429e1da64e1bb04c56f71b38b57c2ce4fe8110d1289fa6198c2c
- owner: B (`status: delivered`, `contract_version: 1.0.0`)
- consumed blob: `git -C $ACP2_PROJECT_ROOT show HEAD:docs/reference/next-change/acp2-contracts/runtime/p2-research-operations.v1.json`

The P1 allowlist stays exactly eight tools and never widens:
`acp_capabilities`, `acp_preflight`, `acp_submit`, `acp_status`,
`acp_cancel`, `acp_prepare_artifact`, `acp_read_artifact`, `acp_analyze`.
P2 adds ten tool names; the combined allowlist is 18 names.

Negotiation rule (B03 `client_path.negotiation`, carrier moved by M0-09/ADR-006 D7):

1. The client calls the P1 discovery tool `acp_capabilities` with
   `research_extension: {"id": "acp.research.v1", "version": "1.0.0"}`. The
   carrier is **`tools/call`, never `initialize`**: the frozen DSH client sends
   `capabilities: {}` and discards a top-level `initialize` extensions block, so
   an `initialize` claim would be unreadable.
2. A client that did not negotiate the extension sees only the P1 allowlist;
   calling a P2 tool is refused with class `unsupported`, code
   `unsupported_capability`, `params.rule = extension_not_negotiated` (or
   `unknown_tool` at the JSON-RPC layer, because an unregistered research tool is
   not in `tools/list`).
3. A negotiated id with a different version is refused with the same code and
   `params.rule = extension_version_mismatch`; the bridge never guesses a
   compatible shape. An extension the server advertises but has not implemented
   answers `implemented=false` / `negotiated=false` with
   `params.rule = extension_not_implemented` — advertising is never delivery.
4. There is no fallback HTTP client, no shell/SSH tool and no untyped
   passthrough. A Data interface that is not a live DSH tool is not a closure.

The bridge refuses all of this locally (`assertOperationAllowed`) before any
`tools/call`, so an un-negotiated client cannot even reach the server with a
P2 name.

## 2. P1 operation mapping

| P1 operation | tool | long task | notes |
| --- | --- | --- | --- |
| `discover` | `acp_capabilities` | no | capability discovery, never a flattened model tool |
| `validate` | `acp_preflight` | no | zero side effects; DA calls must pass `da-preflight` |
| `submit` | `acp_submit` | yes | reference-only; retry with the same `idempotency_key` |
| `status` | `acp_status` | no | no connection state; poll the persisted reference |
| `evidence` | `acp_analyze` | yes | explicit analysis; requires `explicit: true` from the caller |
| `prepare` | `acp_prepare_artifact` | yes | idempotent, budget-admitted |
| `read` | `acp_read_artifact` | no | never fetches, never implies `prepare` |

Why `evidence` maps to `acp_analyze`: the frozen eight-tool table has no
separate evidence tool. `acp_analyze` is the only explicit analysis entry and
it requires `explicit: true`; `acp_read_artifact` only returns an
already-prepared artifact and never computes. Mapping evidence to
`acp_read_artifact` would silently turn "analyse" into "read" and mapping it to
`acp_submit` would launch new science. `acp_analyze` is therefore the only
correct tool, and the bridge forwards `explicit` from the request instead of
filling it in.

DA dispatch discipline: `resolveDaPhase` requires an explicit
`da-preflight` (validate) or `da-submit` (submit). A missing phase is
`invalid-parameter` / `phase_dispatch` with `rule = phase_required`; a
mismatch is `rule = phase_mismatch`; a phase on an unrelated operation is
`rule = phase_not_applicable`.

## 3. P2 operation map

Every row below names a tool that exists in B03's committed table. The
`tool/version` cell is `name@toolVersion`; `error codes` is a
comma-separated list of B03 `error_classes`.

| operation | row | tool/version | controller method | application use case | permission/principal | error codes | wiring task |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `proposal.create` | P2-R1 | `acp_research_proposal_create_v1@1.0.0` | `create_proposal` | Create the versioned Proposal/ResearchRecord resource that carries a round, recipe digest and plan-definition digest | scope `write` as the resolved principal; project comes from the server-resolved scope; `idempotency_key` accepted | authorization, invalid-parameter, conflict, not-prepared, retryable | T-25 |
| `proposal.read` | P2-R1 | `acp_research_proposal_read_v1@1.0.0` | `read_proposal` | Read one revision of the research record/proposal and its digest for the current round | scope `read` on the resolved project; a foreign project is not resolvable | authorization, invalid-parameter, conflict, not-prepared, retryable | T-25 |
| `proposal.update_cas` | P2-R1 | `acp_research_proposal_update_cas_v1@1.0.0` | `update_proposal_cas` | Compare-and-set a record revision so a concurrent round cannot overwrite another | scope `write`; `expected_revision` required; mismatch is `revision_conflict` | authorization, invalid-parameter, conflict, not-prepared, retryable | T-25 |
| `approval.request` | P2-R2 | `acp_research_approval_request_v1@1.0.0` | `request_approval` | Request human approval for a frozen `plan_digest` and show the pending card | scope `write` as the requesting principal; binds `plan_digest`, never a mutable plan | authorization, invalid-parameter, conflict, retryable | T-27 |
| `approval.query` | P2-R2 | `acp_research_approval_query_v1@1.0.0` | `query_approval` | Query one approval or a bounded approval list with an authoritative snapshot revision | scope `read` on the resolved project; a client can query but never approve here | authorization, invalid-parameter, conflict, retryable | T-27 |
| `approval.approve` | P2-R3 | `acp_research_approval_decide_v1@1.0.0` | `decide_approval` | Approve exactly the frozen plan digest, binding the verified human approver | approver identity comes from the authenticated Controller channel only; a payload `approver`, `role` or `tenant` is refused with `access_denied` | authorization, invalid-parameter, conflict, retryable | T-28 |
| `approval.reject` | P2-R3 | `acp_research_approval_decide_v1@1.0.0` | `decide_approval` | Reject the frozen plan digest with a reason, stopping dispatch | same verified principal rule; `decision` must be `reject` | authorization, invalid-parameter, conflict, retryable | T-28 |
| `approval.revoke` | P2-R3 | `acp_research_approval_revoke_v1@1.0.0` | `revoke_approval` | Revoke a previous approval so it can never authorize a submit | same verified principal rule plus `expected_revision`; a revoked approval cannot authorize a submit | authorization, invalid-parameter, conflict, retryable | T-28 |
| `research.wait` | P2-R4 | `acp_research_wait_v1@1.0.0` | `wait_research` | Wait a bounded time for task/approval/budget events and return an opaque cursor plus authoritative snapshot | scope `read` on the resolved project; cursor is bound to the principal; `max_wait_s <= 60`, `limit <= 200` | authorization, invalid-parameter, not-prepared, retryable | T-32 |
| `research.events` | P2-R4 | `acp_research_wait_v1@1.0.0` | `wait_research` | Read the current event window without a long poll (bounded query, zero wait) | same project-scoped read and bounded-query rule | authorization, invalid-parameter, not-prepared, retryable | T-32 |
| `research.snapshot` | P2-R4 | `acp_research_snapshot_v1@1.0.0` | `snapshot_research` | Read the authoritative snapshot and `snapshot_revision` for a set of refs | scope `read` on the resolved project; a foreign ref is not resolvable | authorization, invalid-parameter, not-prepared, retryable | T-32 |
| `research.resume` | P2-R5 | `acp_research_resume_v1@1.0.0` | `resume_research` | Resume a research round from C's resumability semantics plus B resource facts, never a second attempt | scope `write` plus a valid unrevoked approval bound to `plan_digest`; a chat session is never authority for task facts | authorization, invalid-parameter, conflict, retryable, not-prepared | T-32 |

ResearchRecord note: B03 publishes one typed versioned project resource per
Proposal/ResearchRecord. C's ResearchRecord (round, decision, step identities,
task/evidence refs) is stored through the `proposal.create/read/update_cas`
rows; the bridge does not open a second persistence path, a SQLite table or a
file backdoor.

## 4. Bridge guards (what the code refuses, not what it hopes)

| Guard | Behaviour |
| --- | --- |
| Operation mapping | One operation maps to exactly one official tool; an unknown operation is `invalid-parameter` / `unknown_operation` |
| Extension | P2 without `acp.research.v1` is `unsupported_capability` / `extension_not_negotiated` |
| Tool result | `isError` and `structuredContent` are returned by reference, never rewritten; `outcome` is derived only from `isError` |
| Long task | The projection carries `reference` only; no synthetic progress/status field |
| Large artifact | The projection carries `reference` and `digest` only; no content copy |
| `prepare` / `read` | `read` on an unprepared `(job, attempt, resource)` returns a structured `not_prepared` error without calling the tool; `read` never triggers `prepare` |
| Idempotency | The same key with a changed `workflow`/`input`/`method`/`resources` is `conflict` / `revision_conflict` with `rule = idempotency_key_reuse` |
| Raw names | A raw tool name containing `mcp__acp__` is rejected; the prefix is stripped exactly once |
| P2 identity | Payload `approver`, `approver_ref`, `principal`, `requested_by`, `role`, `tenant` is refused with `access_denied` |
| P2 decision | `approval.approve` must carry `decision: approve`, `approval.reject` must carry `decision: reject`; a mismatch is `decision_mismatch` |
| P2 waiting | `max_wait_s > 60` or `limit > 200` is `bound_exceeded` |

## 5. Verification

`scripts/acceptance/acp2_experience_scenario_bridge_contract.py` runs the
workspace gates (`install --frozen-lockfile`, `build`, `test`, `check`,
`check-boundaries.mjs`), then:

1. reads only the committed B03 blob (`git show HEAD:<path>`) and records its
   sha256;
2. asserts this document's sha256 equals the blob's and that the P2 allowlist
   is 18 names (8 P1 + 10 P2);
3. parses the table above and asserts every `tool/version` exists in B03 with
   the same version, every listed error class is B03's, and the doc has exactly
   the operations the compiled bridge exposes (from `rph/scripts/dump-bridge-contract.mjs`);
4. asserts the test log covers the negotiation rule and the six boundary
   cases (`isError` promotion, raw prefix, prepare/read, long-task reference,
   artifact reference/digest, mutated method).

`evidence_kind = fixture`; the receipt never claims H2 closure.
