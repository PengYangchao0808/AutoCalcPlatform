# B-req-18 — Keep one isolated Controller running for the C H2 window

- **Status:** open — Controller window only. **R1 and R2 are RESOLVED (2026-10-26, M0-08 / ADR-006).**
- **Raised by:** C (T-11 / C07), 2026-10-22 (capture 2026-10-23T02:00Z)
- **Requested of:** B (owner of the single isolated Controller and the MCP
  launch descriptors)
- **Severity:** blocking for `mcp-live` (T-11), `dsh-discovery-live` (T-12),
  `model-live` (T-13) and the H2 receipt (T-14)
- **C evidence:** `$ACP2_EVIDENCE/C/mcp-live/mcp-live-20261022T180038Z-540504/main/`
  (receipt `result=NOT_VERIFIED`, `reason=controller_unavailable`, exit `2`)

## Resolution summary (M0-08, 2026-10-26)

| Item | Status | Where |
| --- | --- | --- |
| R1 — protocol negotiation | **RESOLVED** | ADR-006 D1/D2/D3/D6; `src/acp_services/entrypoints/mcp.py::_negotiate_protocol_version` |
| R2 — descriptor vs live `tools/list` | **RESOLVED** (decision) | ADR-006; `experience/mcp-handshake.md` §5.1 |
| Isolated Controller window | **STILL OPEN** — unchanged by M0-08 |

R1 needed no client pin and no new supported version: the server now answers the
version it speaks (`2025-06-18`) and the frozen client accepts it because the SDK
supports that version. R2 is decided in favour of the **live** `tools/list` as
the client contract; frozen launch descriptors are audit material and a future
descriptor must be generated from a live snapshot. C needs no profile change and
no server schema change for either item.

## Consumer step

T-11 / C07 (`mcp-live`), then T-12 / C08 and T-13 / C09. C consumes B05/B06's
frozen `mcp-launch.json` and performs the real stdio handshake with the frozen
official DSH client. It must not start a second Controller or submit a task.

## Current observation

At capture time `GET http://127.0.0.1:18765/api/health` returns
`URLError: [Errno 111] Connection refused`. Every recorded B PID is dead
(B05 `81900`, B06 `255506`, B07 `365514`, B08 `448951`). B05/B06 cleanup
receipts explicitly SIGTERM'd their Controllers.

Because `acp mcp serve` calls `build_mcp_runtime(controller)`
→ `GET /api/health` **before** serving stdin, the adapter exits `3`
(`MCP_EXIT_CONTROLLER_UNAVAILABLE`) and no `initialize`/`tools/list` can occur.
C therefore recorded every Controller-dependent row as
`NOT_VERIFIED(controller_unavailable)` — never PASS.

## Requested deliverable

Please provide, and keep alive for the agreed H2 window:

1. **One** isolated Controller bound to loopback:
   - preferred URL `http://127.0.0.1:18765` (production `:8765` untouched);
   - run root `…/data/b/run-root`; config `…/data/b/controller-config/cccp.yaml`;
   - the B05/B06 `mcp-launch.json` `args` unchanged.
2. A start record matching `acp2.controller-start-record.v1` with:
   `pid`, `argv`, `cwd`, `pythonpath`, `release`/`candidate_revision`, `port`,
   `run_root`, `db_path`, `config`, `production_touched=false`.
3. The exact **start and stop** commands C may cite (C will not execute them),
   and the expected lifetime/owner of the window.
4. Confirmation that the Controller's `contract_version`/`contract_digest`
   still match the B05 (`1.0.0` / `1944f4…`) and B06 (`1.1.0` / `670731…`)
   descriptors used for the handshake.

## Two additional decisions C cannot make

### R1 — protocol negotiation — RESOLVED (2026-10-26)

The frozen official client (`@deepseek-ai/dsh-mcp-client@0.2.1-rc.2`,
`@modelcontextprotocol/client@2.2.0`) actually sends
`initialize.protocolVersion = "2025-11-25"` and does **not** emit the server's
declarative `protocolVersions` fallback list. The server advertised
`LOCKED_PROTOCOL_VERSIONS = ("2025-06-18",)` and used to reject that request with
`data.code = "unsupported_protocol_version"`.

**Decision (ADR-006 D1–D3/D6):** follow the MCP 2025-06-18 lifecycle rule — the
server answers a version it speaks (`2025-06-18`) and the client decides
compatibility. Only a malformed `protocolVersion`/`protocolVersions` shape is
`-32602`; the private `protocolVersions` list is recorded, never a gate. No new
supported version is added and **no client pin is required** in the DSH profile.

Evidence: `tests/test_acp2_mcp_sdk_contract.py` (real SDK/transport: client
offers `2025-11-25`, server answers `2025-06-18`, client accepts, `tools/list` =
8, zero tasks) and the 7-row `scripts/acceptance/acp2_mcp_negotiation_probe.py`.
C's static expectation in §4.1 of `mcp-handshake.md` is superseded.

### R2 — descriptor vs live `tools/list` schema drift — RESOLVED (2026-10-26)

`B/B05/probe/wire.jsonl` (sha256 `f4d89890…9be1f5`, B-recorded live
`tools/list`) advertises `additionalProperties: true` and much weaker required
fields than the frozen descriptor `tools[].inputSchema`. Example: live
`acp_read_artifact` = `job_id/resource_id/relative_path/attempt/idempotency_key`;
the descriptor requires `artifact_ref`. All eight tools differ.

**Decision (ADR-006):** the **live `tools/list`** is the client contract — it is
what DSH registers and what the model sees. The frozen B05/B06 descriptors are
audit material for that capture; a future descriptor must be generated from a
live `tools/list` snapshot, and a descriptor that disagrees with the adapter is a
descriptor bug. The weaker live required-fields remain a product input to M1-01
(schema strengthening), not a handshake blocker. C needs no change.

Finding F1 in `experience/mcp-handshake.md` is updated accordingly
(`CLOSED as a decision`).

## Minimal acceptance C will run once the Controller is up

```bash
source /ACPServer/acp2-development/data/acceptance-env-c.sh
c_accept mcp-live 300          # expect 0/PASS or 2/NOT_VERIFIED, never fabricated PASS
```

Expected rows when the Controller is healthy: `initialize` answers
`2025-06-18` for the client's `2025-11-25` offer and the client accepts it; eight
live `inputSchema` snapshots stand as the client contract; matrix rows (a)–(g)
PASS (row (g) exits cleanly); Controller PID and task count are unchanged
before/after. Any executed mismatch is `FAIL`; any remaining environment gap
stays `NOT_VERIFIED`.
