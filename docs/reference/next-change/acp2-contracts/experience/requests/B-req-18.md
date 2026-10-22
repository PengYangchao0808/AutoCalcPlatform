# B-req-18 — Keep one isolated Controller running for the C H2 window

- **Status:** open
- **Raised by:** C (T-11 / C07), 2026-10-22 (capture 2026-10-23T02:00Z)
- **Requested of:** B (owner of the single isolated Controller and the MCP
  launch descriptors)
- **Severity:** blocking for `mcp-live` (T-11), `dsh-discovery-live` (T-12),
  `model-live` (T-13) and the H2 receipt (T-14)
- **C evidence:** `$ACP2_EVIDENCE/C/mcp-live/mcp-live-20261022T180038Z-540504/main/`
  (receipt `result=NOT_VERIFIED`, `reason=controller_unavailable`, exit `2`)

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

### R1 — protocol negotiation

The frozen official client (`@deepseek-ai/dsh-mcp-client@0.2.1-rc.2`,
`@modelcontextprotocol/client@2.2.0`) actually sends
`initialize.protocolVersion = "2025-11-25"` and does **not** emit the server's
declarative `protocolVersions` fallback list. The server advertises
`LOCKED_PROTOCOL_VERSIONS = ("2025-06-18",)`. Static expectation: a live
`initialize` fails with `data.code = "unsupported_protocol_version"`.

Please decide and document one of:

- add the client-offered version to the server's supported set (with the
  process-level proof / ADR-006 update the code comment requires),
- accept the declarative `protocolVersions` list as the negotiation input and
  keep the locked default, or
- pin the client to `2025-06-18` in the DSH profile (and record that pin).

C will not change server protocol constants and will not treat a differing
version string as incompatible when both sides do support a common version.

### R2 — descriptor vs live `tools/list` schema drift

`B/B05/probe/wire.jsonl` (sha256 `f4d89890…9be1f5`, B-recorded live
`tools/list`) advertises `additionalProperties: true` and much weaker required
fields than the frozen descriptor `tools[].inputSchema`. Example: live
`acp_read_artifact` = `job_id/resource_id/relative_path/attempt/idempotency_key`;
the descriptor requires `artifact_ref`. All eight tools differ. The same
weaker schemas exist in the source the launch `cwd` executes
(`src/acp_services/entrypoints/mcp.py::_tool_specs`, both the main checkout and
B `58d81ec`).

Please state which surface is authoritative for clients and reconcile the
descriptor or the server. DSH registers the live `tools/list`, so the model
currently sees the weaker parameter surface. C records this as Finding F1 in
`experience/mcp-handshake.md`; it is not a C-side fix.

## Minimal acceptance C will run once the Controller is up

```bash
source <acp2-dev>/data/acceptance-env-c.sh
c_accept mcp-live 300          # expect 0/PASS or 2/NOT_VERIFIED, never fabricated PASS
```

Expected rows when the Controller is healthy: `initialize` and `tools/list`
negotiate a common version; eight `inputSchema` snapshots match the authoritative
surface; matrix rows (a)–(d) PASS and row (e) exits cleanly; Controller PID and
task count are unchanged before/after. Any executed mismatch is `FAIL`; any
remaining environment gap stays `NOT_VERIFIED`.
