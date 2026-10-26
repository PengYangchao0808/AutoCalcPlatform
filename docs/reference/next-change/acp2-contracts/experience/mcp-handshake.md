# C · Real MCP stdio handshake, negotiation matrix and tool schemas (T-11 / C07)

- **Owner:** C (experience / RPH)
- **Consumer:** T-12 (DSH plugin wiring), T-13 (model tool call), T-14 (H2 receipt)
- **Scenario:** `mcp-live` (`scripts/acceptance/acp2_experience_scenario_mcp_live.py`)
- **Primary evidence run:** `$ACP2_EVIDENCE/C/mcp-live/mcp-live-20261022T180038Z-540504/main/`
- **Receipt result at capture time:** `NOT_VERIFIED`, reason `controller_unavailable`, exit `2`
- **H2 status:** **NOT closed by this row.** T-14 owns the H2 receipt.

> Rule applied: a missing environment (B's isolated Controller) is
> `NOT_VERIFIED`; an executed failure is `FAIL`. This capture never converts a
> missing Controller into a PASS and never fabricates a live handshake.

## 0. M0-08 update (2026-10-26) — handshake closed, Controller window still open

`ADR-006` (`docs/reference/next-change/adr/ADR-006-mcp-negotiation.md`) replaced
the old "server rejects an unsupported `protocolVersion`" rule with the MCP
2025-06-18 lifecycle rule: the server answers a version it speaks and the client
decides compatibility. Consequences for this document:

| Old pin in this capture | State after M0-08 |
| --- | --- |
| §4.1 "expected live outcome is `unsupported_protocol_version`" | **CLOSED** — the server answers `2025-06-18`; the frozen client accepts it |
| §6 rows (a)–(e) required behaviour | **REPLACED** — see §6 (7 rows; `no_common` no longer exists) |
| §5.1 Finding F1 (descriptor vs live `tools/list`) | **CLOSED as a decision** — the live `tools/list` is the client contract; frozen launch descriptors are audit material (see §5.1) |
| `requests/B-req-18.md` R1 / R2 | **RESOLVED** by ADR-006 (§4.1, §5.1); the Controller-window request itself stays open |
| Top-level `initialize.extensions.acp.research.v1.negotiated` | **REMOVED** — extension negotiation is M0-09/M0-12, and `initialize` no longer claims it |

New primary evidence (real client, real transport, no Controller window needed —
a loopback Controller stub stands in and counts every request):

- `tests/test_acp2_mcp_sdk_contract.py` (`-m contract`) — the frozen
  `@deepseek-ai/dsh-mcp-client` `0.2.1-rc.2` + `@modelcontextprotocol/client`
  `2.2.0`, production options `{capabilities:{}, versionNegotiation:{mode:"auto"}}`:
  client offers `2025-11-25`, server answers `2025-06-18`, client accepts,
  `tools/list` = 8, `acp_capabilities` `status=ok`.
- `scripts/acceptance/acp2_mcp_negotiation_probe.py` — 7-row standard-handshake
  matrix (`same`, `unsupported_version`, `private_versions`, `malformed_version`,
  `server_discover`, `tools_before_init`, `notification_eof`), zero `tools/call`.
- `tests/test_acp2_runtime_mcp_process.py` — process-level rows updated in place.

Zero side effects observed in both processes: `GET /api/health` = 2 (sibling
`server/discover` probe + session), `POST /api/v1/jobs` = 0, no other Controller
request, no file under `ACP_RUN_ROOT`.

## 0b. M0-09 update (2026-10-26) — research extension negotiated in `tools/call`

`ADR-006 §D7` selected the **application-level handshake**: the research extension
is requested with a `tools/call` to the P1 discovery tool, never in `initialize`.
Recorded facts and consequences for this document:

| Observation (real client, real adapter) | Consequence |
| --- | --- |
| `initialize` result carries **no** `acp.research.v1` and no `negotiated` assertion; `capabilities.extensions["acp.negotiation.v1"].extension_handshake` points at `{carrier: "tools/call", tool: "acp_capabilities"}` | Nothing in this document may cite `initialize` as the negotiation point; the §1 B06 note is a historical capture only |
| `tools/call acp_capabilities {"research_extension": {"id": "acp.research.v1", "version": "1.0.0"}}` answers `isError: true`, `error.code = unsupported_capability`, `params.rule = extension_not_implemented`, and still returns `data.extensions[0]` with `implemented: false`, `negotiated: false`, `negotiated_by: "tools/call:acp_capabilities"` | Advertising is not delivery: the ten P2 tools are promised, not registered |
| A `tools/call` with `@1.1.0` (the old B06 value) answers `params.rule = extension_version_mismatch`; an unknown id answers `extension_not_advertised` | Version/id mismatch is refused in-band, never adopted by inference |
| `tools/list` is the **whole** model-visible surface: DSH registers every advertised tool and there is **no** per-tool negotiation flag | "Not negotiated ⇒ not callable" is guaranteed by **absence from the allowlist** (JSON-RPC `unknown_tool`), not by a runtime `negotiated` check |
| The official plugin converts an MCP `isError` result into a **thrown** tool failure (`packages/mcp/mcp-client/src/tools.ts::createExecutor`); `structuredContent` is not delivered | The ACP result's `content[0].text` is the full machine envelope, so the typed refusal is recoverable; `rph/packages/dsh-bundle` does exactly that in `platformClientFromToolRuntime` |
| DSH registers MCP tools as `mcp__<serverName>__<rawName>` (`publicToolName`) and `ctx.tools.execute({name, arguments, callId, signal})` looks up that model-visible name | The bundle adapter defaults to the `mcp__acp__` prefix (`resolveToolName` overrides it) |

Evidence: `reports/acp2_remediation_20261026/m0-09/` (`summary.json` +
`live-client/{mcp-launch.json,dsh-client.json,wire.jsonl}`),
`tests/test_acp2_mcp_sdk_contract.py` (`-m contract`),
`tests/test_acp2_mcp_research_handshake.py`, `rph/test/mcp-handshake.test.ts`.

This does **not** close the P2 rows: the ten research tools remain unregistered and
`M3` owns the real Agent line, so the T-12/P2 rows below stay `NOT_VERIFIED`.

## 1. Frozen launch descriptors (consumed read-only)

| Descriptor | Path (under `$ACP2_EVIDENCE`) | SHA-256 | Contract | Contract digest |
| --- | --- | --- | --- | --- |
| B05 | `B/B05/mcp-launch.json` | `ca807cc4b8014af265fac64c9b84ca3c4050df9bfbab3b5dc408ce068b548046` | `1.0.0` | `1944f4f06c65b13b6ef98c752aa18d8e5752139b1513ecb2c19c44bb554fc488` |
| B06 | `B/B06/mcp-launch.json` | `867d25a6e5757ded401006e2c51f3512fcce551299a3221e7b892e3b82c9a7f6` | `1.1.0` | `67073115cb0856db22d84e22215d54e4dbe2437aad964efc15d3b706641e11a4` |

Both descriptors are byte-identical except for the server contract version,
the extension block and the tool `inputSchema` revision:

```json
{
  "command": "/ACPServer/acp2-development/venvs/b/bin/python",
  "args": ["-m", "acp", "mcp", "serve", "--transport", "stdio",
           "--controller", "http://127.0.0.1:18765", "--log-level", "INFO"],
  "cwd": "/ACPServer/AutoCalcPlatform/src",
  "env": {
    "ACP_RUN_ROOT": "/ACPServer/acp2-development/data/b/run-root",
    "ACP_CONTROLLER_URL": "http://127.0.0.1:18765",
    "ACP2_MCP_LOG_LEVEL": "INFO",
    "ACP2_TOOLS": "/ACPServer/acp2-development/venvs",
    "ACP_CONFIG": "/ACPServer/acp2-development/data/b/controller-config/cccp.yaml",
    "PATH": "/ACPServer/acp2-development/venvs/b/bin:/usr/sbin:/usr/local/bin:/usr/bin:/bin"
  },
  "protocol_version": "2025-06-18",
  "transport": "stdio",
  "controller": {
    "transport": "loopback-http", "loopback_only": true,
    "start_controller": false, "shared_db": false
  }
}
```

Static checks executed by the scenario (all **PASS**, run
`launch-identity.json` / assertions `launch.*`):

- `launch_version == 1`, `transport == "stdio"`, `protocol_version == "2025-06-18"`;
- contract versions `1.0.0` (B05) / `1.1.0` (B06) and 64-hex contract digests;
- absolute executable exists (`…/venvs/b/bin/python`, Python 3.11.13) and
  `cwd` exists (`/ACPServer/AutoCalcPlatform/src`);
- `args` head is `-m acp mcp serve --transport stdio --controller <url>` and the
  tail is `--log-level INFO`; B05 and B06 agree on `command/args/cwd/env` keys;
- `ACP_RUN_ROOT` is an existing directory and `ACP_CONFIG` an existing file;
- `controller.start_controller == false`, `loopback_only == true`,
  `shared_db == false` — the adapter will not start a second Controller;
- `stdio.stdout_is_channel_only == true`, `logs_to == "stderr"`;
- the allowlist is exactly the eight P1 names
  (`acp_analyze`, `acp_cancel`, `acp_capabilities`, `acp_preflight`,
  `acp_prepare_artifact`, `acp_read_artifact`, `acp_status`, `acp_submit`) and
  every `inputSchema` is an object schema.

B06 additionally negotiates the research extension:
`initialize.extensions["acp.research.v1"] = {id, version: "1.1.0", negotiated: true}`.
*(Historical capture — superseded by M0-09/ADR-006 §D7: `initialize` no longer carries
any extension claim, and the extension is now requested through `tools/call
acp_capabilities` at `1.0.0`. See §0b.)*

## 2. Controller status (B-owned)

Pre-flight `GET http://127.0.0.1:18765/api/health`:

| Observation | Value |
| --- | --- |
| Reachability | **unreachable** — `URLError: [Errno 111] Connection refused` |
| B05 PID file (81900) | process absent |
| B06 PID file (255506) | process absent |
| B07 topology PID (365514) | process absent |
| B08 PID (448951) | process absent |
| Task count before / after | **NOT_VERIFIED** (`controller_unavailable`) |
| Verified Controller PID | **NOT_VERIFIED** (`controller_unavailable`) |

C did **not** start a Controller. Evidence: `controller.json`,
`mcp-live-summary.json#controller`.

## 3. Stdlib probe (B05 descriptor)

The sanctioned probe (`docs/reference/next-change/examples/acp_mcp_stdio_probe.py`)
was executed unchanged against `B/B05/mcp-launch.json`:

| Field | Value |
| --- | --- |
| Probe exit | `1` (probe raised) |
| Adapter exit | `3` (`MCP_EXIT_CONTROLLER_UNAVAILABLE`) |
| Protocol frame sent | `initialize` with `protocolVersion: 2025-06-18`, `clientInfo: acp2-readonly-probe/1` |
| Frames received | **none** — stdout closed before the reply |
| Adapter stderr | `Controller unavailable at http://127.0.0.1:18765: Controller unreachable … [Errno 111] Connection refused` |
| Row verdict | `NOT_VERIFIED(controller_unavailable)` — not FAIL (missing prerequisite) |

Evidence: `stdlib-probe/wire.jsonl` (sha256 `f9dd0cc9…1dfe76`),
`stdlib-probe/stderr.log` (sha256 `d1846836…35d7`),
`stdlib-probe/process.json` (`{"pid": 540513, "exit_code": 3}`).

This is the real startup path: `acp mcp serve` binds its backends through
`build_mcp_runtime(controller)`, which calls `GET /api/health` **before** serving
stdin. With the Controller absent the adapter exits `3` and no MCP session can
exist. It is therefore impossible to exercise `initialize`/`tools/list` in this
window without B.

## 4. Frozen official client handshake (B06 descriptor)

`scripts/acceptance/acp2_mcp_live_client.mjs` composes the DSH harness pieces
without a second transport:

- `@deepseek-ai/dsh-mcp-client/src/transport.ts::createTransport` (the plugin's
  stdio factory, including its credential-scrubbed parent env);
- `@modelcontextprotocol/client` `2.2.0` `Client` with the plugin's own options
  (`{capabilities: {}, versionNegotiation: {mode: "auto"}}`).

A transparent recorder on the official transport's `send`/`onmessage` seam
archives the wire; it never answers, rewrites, retries or synthesises a frame.

| Field | Value |
| --- | --- |
| `@deepseek-ai/dsh-mcp-client` | `0.2.1-rc.2` |
| `@modelcontextprotocol/client` | `2.2.0` |
| SDK `LATEST_PROTOCOL_VERSION` | `2025-11-25` |
| SDK `SUPPORTED_PROTOCOL_VERSIONS` | `2025-11-25, 2025-06-18, 2025-03-26, 2024-11-05, 2024-10-07` |
| Actual `initialize` request | `protocolVersion: "2025-11-25"` (no `protocolVersions` list emitted) |
| Negotiated protocol | **NOT_VERIFIED** (`controller_unavailable`) |
| Tool snapshot from live `tools/list` | **NOT_VERIFIED** (`controller_unavailable`) |
| Client error | `SdkError: Connection closed` (adapter exited before replying) |

Evidence: `dsh-client/dsh-client.json` (sha256 `65ca2f7b…e5438`),
`dsh-client/wire.jsonl` (sha256 `fe4493b1…6192`).

### 4.1 Compatibility resolution (M0-08, executed)

The frozen client offers **`2025-11-25`** first and does not send the server's
declarative `protocolVersions` fallback list. Before M0-08 the server turned that
into `initialize` → `-32602` `unsupported_protocol_version`, i.e. a guaranteed
handshake failure for the production client (R3 counterexample
`evidence/verification-r3-20261026/dsh.json`).

**Resolved.** `_negotiate_protocol_version` now follows the MCP 2025-06-18
lifecycle rule (ADR-006 §D1): the server answers the version it speaks
(`2025-06-18`) and the client decides whether it can continue. The frozen SDK
supports `2025-06-18`, so the production client accepts it; a client whose own
support set excludes it fails **on its own** (`-m contract` scenario
`incompatible-client`) and still creates no task.

Two further rules came out of the execution:

- the private `protocolVersions` list is **recorded only**
  (`capabilities.extensions["acp.negotiation.v1"].client_declared_versions`),
  never a gate, and is no longer required;
- `initialize` no longer carries top-level `extensions` and makes no
  `negotiated` claim; only a malformed `protocolVersion`/`protocolVersions`
  shape is `-32602`.

`server/discover` (sent by `versionNegotiation: {mode:"auto"}` on a disposable
sibling process) is answered with an ordinary `-32601`; no discover result is
implemented and both processes are side-effect free.

## 5. Eight-tool schema snapshot

Live `tools/list` could not be captured (Controller absent). The frozen
descriptor snapshot is archived as `tool-schemas.json` (sha256
`6032c1d1…22c84e1`, `source: frozen_launch_descriptor`, `live_verified: false`).

| Tool | Required | Properties | `additionalProperties` |
| --- | --- | --- | --- |
| `acp_capabilities` | — | — | `false` |
| `acp_preflight` | `workflow`, `input` | `workflow`, `input`, `method`, `project_id`, `resources` | `false` |
| `acp_submit` | `workflow`, `input`, `plan_digest` | `workflow`, `input`, `method`, `project_id`, `resources`, `plan_digest`, `proposal_digest`, `idempotency_key` | `false` |
| `acp_status` | `reference` | `reference` (`string | {kind,id}`) | `false` |
| `acp_cancel` | `reference` | `reference`, `expected_revision` | `false` |
| `acp_prepare_artifact` | `purpose` | `purpose`, `task_ref`, `job_id`, `artifact_id`, `attempt`, `generation`, `idempotency_key` | `false` |
| `acp_read_artifact` | `artifact_ref` | `artifact_ref`, `max_bytes`, `encoding` | `false` |
| `acp_analyze` | `analysis_id`, `input_refs`, `explicit` | `analysis_id`, `input_refs`, `explicit`, `parameters`, `plan_digest`, `idempotency_key` | `false` |

**Output schemas:** the server advertises no `outputSchema` for any tool (all
eight live entries in B05's archived `tools/list` omit it). A client must treat
the tool result envelope (`content` + `structuredContent` + `isError`) as the
contract, not a declared output schema.

### 5.1 Finding F1 — descriptor vs live `tools/list` drift (CLOSED as a decision, 2026-10-26)

B05's own archived probe (`B/B05/probe/wire.jsonl`, sha256
`f4d898901f9b7ce243c85553dfd1acc81e3e835f70c9d3b63d357d5389dbe1f5`) contains a
real `tools/list` from the same launch descriptor. Comparison
(`schema-drift.json`, sha256 `11cfc845…5e1d58`) shows **all eight** advertised
`inputSchema` values differ from the frozen descriptor:

- live schemas use `additionalProperties: true`; the descriptor uses `false`;
- `acp_preflight`/`acp_submit`/`acp_analyze` require only `workflow` live, while
  the descriptor requires `input` and (for submit/analyze) `plan_digest` /
  `analysis_id`+`input_refs`+`explicit`;
- `acp_prepare_artifact`/`acp_read_artifact` live properties are
  `job_id/resource_id/relative_path/attempt/idempotency_key`, while the
  descriptor defines `purpose/task_ref/...` and `artifact_ref/max_bytes/encoding`;
- `acp_capabilities` differs only in schema annotations.

**Decision (B, M0-08, ADR-006): the live `tools/list` returned by the running
adapter is the client contract.** A client (DSH) registers the live schemas, so
that surface — not a frozen JSON descriptor — defines the model-facing parameter
space. Consequences:

- the frozen B05/B06 `mcp-launch.json` `tools[].inputSchema` blocks are
  **audit material for that capture**, not a contract to reconcile the server
  to; `docs/reference/next-change/acp2-contracts/runtime/mcp-launch.template.json`
  stays a *format* template (it carries no `tools` array);
- any future descriptor must be generated from a live `tools/list` snapshot, and
  a descriptor that disagrees with the live adapter is a **descriptor bug**;
- the observation that the live schemas are the *weaker* ones (`workflow` only,
  `additionalProperties: true`) is retained as a product input to M1-01 — it is
  not a negotiation blocker and M0-08 does not change tool schemas.

Verified end to end: `tests/test_acp2_mcp_sdk_contract.py` asserts the real
client's live `tools/list` is exactly the eight P1 names with object schemas,
and `acp_capabilities` answers `status=ok`.

## 6. Negotiation matrix (seven rows, standard handshake)

Executed for the M0-08 closure by `scripts/acceptance/acp2_mcp_negotiation_probe.py`
(one fresh adapter process per row, newline JSON-RPC framing identical to the
sanctioned stdlib probe, **zero** `tools/call` frames, per-row wire/stderr/exit
archives; it is a diagnostic harness, not a product transport) and by the
process-level pytest rows in `tests/test_acp2_runtime_mcp_process.py`.

| Row | Required behaviour (post-ADR-006) | Verdict |
| --- | --- | --- |
| (a) `same` | request `2025-06-18` → same version, tools = 8 | PASS (probe + process test) |
| (b) `unsupported_version` | request `2025-11-25` with no private list (exact SDK request) → server answers `2025-06-18`, tools = 8 | PASS (probe + process test + real SDK) |
| (c) `private_versions` | private `protocolVersions` declared → recorded in `capabilities.extensions`, still the server version, never a gate | PASS (probe + process test) |
| (d) `malformed_version` | non-string `protocolVersion` → `-32602` `invalid_protocol_version`, no tool call | PASS (probe + process test) |
| (e) `server_discover` | pre-`initialize` `server/discover` → ordinary `-32601`, then `initialize` succeeds in the same session | PASS (probe + process test + real SDK sibling probe) |
| (f) `tools_before_init` | `tools/list` before `initialize` → `not_initialized` | PASS (probe + process test) |
| (g) `notification_eof` | initialized notification + stdin EOF → clean exit, no fabricated frame, no cancel | PASS (probe + process test) |

The old row (c) `no_common` (an unsupported version must fail `initialize`) is
**withdrawn**: MCP negotiation is client-decided, so an unknown version is no
longer a server error. The replacement negative is `incompatible-client` in
`scripts/acceptance/acp2_mcp_live_client.mjs`: the client pins its own
`supportedProtocolVersions` to `2025-11-25`, the server answers `2025-06-18`,
and the **client** refuses — `status: "refused"`, zero tasks.

The verdicts above are Controller-independent by construction (a loopback
Controller stub counts requests), which is why they can be PASS while the
isolated-Controller rows below stay `NOT_VERIFIED`.

## 7. No-side-effect check

| Check | Verdict | Evidence |
| --- | --- | --- |
| Controller PID alive after adapter exit | `NOT_VERIFIED` | Controller absent before the run |
| Task count before == after | `NOT_VERIFIED` | `controller.json` has no task count |
| No serving adapter process left | **PASS** | `ps` scan found 0 `acp mcp serve --transport stdio --controller http://127.0.0.1:18765`; probe child PID 540513 is gone |
| Tasks submitted by C | **PASS (structural)** | the scenario never calls `tools/call`; `cleanup.json#tasks_submitted = 0` |
| C started a Controller | **PASS (negative)** | `cleanup.json#controller_started_by_c = false` |
| Production `:8765` touched | **PASS (negative)** | `cleanup.json#production_8765_touched = false` |

`cleanup.json` and assertion `no_side_effect.no_serving_process_left` record the
real observation; the two Controller-dependent rows stay `NOT_VERIFIED`.

## 8. NOT_VERIFIED register (exact)

From the receipt extension `experience.mcp_live.not_verified_rows`:

```
controller.reachable
controller.pid
controller.task_count_before
schema.live_snapshot
probe.stdlib_exit
probe.stdlib_protocol
official.client_handshake
official.tool_allowlist
official.tool_schemas
official.capabilities_read
matrix.same
matrix.different_supported
matrix.no_common
matrix.tools_before_init
matrix.notification_eof
no_side_effect.task_count_unchanged
no_side_effect.controller_alive_after
```

**M0-08 delta (2026-10-26).** That register is the historical capture and is not
rewritten. The negotiation/allowlist/handshake rows are now covered by
Controller-independent evidence (ADR-006 §3/§4): `official.client_handshake`,
`official.tool_allowlist`, `official.tool_schemas`, `official.capabilities_read`
and the seven `matrix.*` rows have executed PASS evidence in
`tests/test_acp2_mcp_sdk_contract.py` + `scripts/acceptance/acp2_mcp_negotiation_probe.py`.
Still `NOT_VERIFIED` and requiring the isolated Controller (or a release run):
`controller.*`, `probe.stdlib_*`, `schema.live_snapshot` against B05's own
descriptor, and `no_side_effect.task_count_*` / `controller_alive_after`.

## 9. What unblocks this row

1. B keeps one isolated Controller running at `http://127.0.0.1:18765` (never
   production `:8765`) with a verified PID, start/stop command and config
   (`requests/B-req-18.md`). — **still open**
2. ~~B reconciles the launch descriptor `tools[].inputSchema` with the live
   `tools/list` (Finding F1)~~ — **resolved**: the live `tools/list` is the
   client contract (§5.1, ADR-006).
3. ~~B decides the `2025-11-25` vs `2025-06-18` negotiation path (§4.1)~~ —
   **resolved**: the server answers `2025-06-18`, the client decides (§4.1,
   ADR-006 D1/D6).

Then T-11 is a single `c_accept mcp-live 300` re-run: the same body exercises
the stdlib probe, the official-client schema snapshot, all seven matrix rows and
the task-count invariant. Until then the **Controller-dependent** rows remain
`NOT_VERIFIED` and this document must not be cited as an H2 PASS.

---

# T-12 section — official DSH MCP plugin profiles and tool discovery (C08)

- **Owner:** C (experience / RPH)
- **Scenario:** `dsh-discovery-live`
  (`scripts/acceptance/acp2_experience_scenario_dsh_discovery_live.py`)
- **Primary evidence run:**
  `$ACP2_EVIDENCE/C/dsh-discovery-live/dsh-discovery-live-20261022T200600Z-634944/main/`
- **Receipt result at capture time:** `NOT_VERIFIED`, reason
  `controller_unavailable`, exit `2` (39 PASS, 3 NOT_VERIFIED, 0 FAIL)
- **H2 status:** **NOT closed by this row.** T-14 owns the H2 receipt.

## 1. Overlays (C-owned, shipped under the bundle)

| Profile | Overlay | SHA-256 |
| --- | --- | --- |
| web | `rph/packages/dsh-bundle/profiles/web.cordis.yml` | `ca9294b0c50b60425306375b1f69376aec756fd42de52295ed5b5b2b5f7449a4` |
| headless | `rph/packages/dsh-bundle/profiles/headless.cordis.yml` | `2d8141461bcec91d772f9d3a8daae5c59c17948e9ec0e667877659007ecc4513` |

Both are a top-level Cordis patch array. The new entry uses `insert`
semantics (never a deep merge):

```yaml
- insert:
    - id: acp-mcp-acceptance
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: acp
        transport: stdio
        # command/args/cwd/env are byte-equal to B05 mcp-launch.json
        failOnStartupError: true
```

`command/args/cwd/env` were compared field-by-field against
`B/B05/mcp-launch.json` (sha256
`ca807cc4b8014af265fac64c9b84ca3c4050df9bfbab3b5dc408ce068b548046`) in both
the overlay source and the composed `--dump-config` tree: **equal**.

The web profile also ships one research preset (`rph-research`) with persona,
time context and `todo_write` only, disables the shipped shell-bearing presets
(`preset-standard`, `preset-ptc`, `preset-minimal`, `preset-cordis`), and makes
`rph-research` the default. The headless profile disables `tool-bash`,
`tool-pwsh`, `tool-jobs`, `bash-sandbox` and `pwsh-sandbox`; `shell-env` stays
because the permission-presets row injects the `shell` service (it registers
no tool).

## 2. `--dump-config` (redacted, boot-free)

| Profile | Exit | Redacted archive | SHA-256 | bytes |
| --- | --- | --- | --- | --- |
| web | 0 | `dsh-effective-config.web.redacted.yml` | `f10c51f439244921aab65e0886634c502ab6da98702442f3a2c673b802767bae` | 46604 |
| headless | 0 | `dsh-effective-config.headless.redacted.yml` | `ac78ad502ff3aa6235b03c702f8f299f99a215aa62af5e06ad1488c333bff483` | 13567 |

Only the redacted copies are archived; the unredacted dump is never written to
disk. `--dump-config` does not evaluate `!!js`, so no credential value is
expanded; the redactor still masks secret-shaped keys, bearer tokens and
`?token=` values, and the scenario asserts no live environment secret value
occurs in the archive.

## 3. Live model-visible snapshot (real registration)

`scripts/acceptance/acp2_dsh_tool_snapshot.mjs` boots the frozen profile through
`apps/cli/src/profile-boot.ts::runProfile` and reads the live `ctx.tools`
registry. For web it mounts the default preset through the public
`agentPresets.mount` + `createScope` APIs and reads `schemas(scopeOf)`; for
headless the global registry is the model-visible set. Tool origin is captured
from the first `packages/**` stack frame of `ToolRuntime.prototype.register`
(resolved to the nearest `package.json` name), so presence is registration,
not a source scan.

| Profile | Scope | Tools visible at capture | Result |
| --- | --- | --- | --- |
| web | `agent-preset:rph-research` | `working_directory`, `todo_write` | no exec, no MCP (plugin failed) |
| headless | `global` | 21 non-exec native tools (`read`/`glob`/`grep`/`todo_write`/`web_*`/delegation/…); no `bash`/`pwsh`/`job_*`/`ssh`/`terminal` | no exec, no MCP (plugin failed) |

No `mcp__acp__*` tool appears in either live snapshot because the MCP server
exits before `initialize` (next section). The snapshot is still a real
registration snapshot: the negative rows are absence after an executed
registration attempt, not a code-scan prediction.

## 4. Registration attempt — `NOT_VERIFIED(controller_unavailable)`

`failOnStartupError: true` is set in both overlays. Both entries reach
`fiber_state = FAILED`; the loader prints, and the scenario archives, the exact
stderr:

```
2026-10-23 04:06:07,495 [ERROR] acp.cli: Controller unavailable at http://127.0.0.1:18765: Controller unreachable at http://127.0.0.1:18765: [Errno 111] Connection refused
SdkError: Connection closed
```

`GET http://127.0.0.1:18765/api/health` was connection-refused before and after
the run; C did not start a Controller, submit a task, or touch `:8765`.
Therefore `registration.official_plugin`, `registration.eight_p1_tools` and
`registration.source_plugin` are `NOT_VERIFIED` with that stderr. They are not
PASS by assumption and not FAIL: the missing prerequisite is B's Controller
(`B-req-18`). When the plugin registers, the scenario asserts exactly the eight
P1 names `mcp__acp__` + `P1_TOOLS` and `source_plugin =
@deepseek-ai/dsh-mcp-client`; a failed registration with a reachable Controller
is FAIL.

## 5. Web binding

Started with `--host 127.0.0.1 --port <ephemeral> --no-open` (captured port
53061, never 8765). An HTTP GET to `127.0.0.1:<port>/` returned 401 (the
auth fence, i.e. listening); an HTTP GET to the host's non-loopback IPv4
(`10.16.5.157:<port>/`) was `ECONNREFUSED`. Loopback binding is observed, not
inferred from config.

## 6. Negatives (executed, explicit failure)

| Fixture | Observation | Verdict |
| --- | --- | --- |
| bad `command` (`/nonexistent/acp2-missing-python`) | entry `FAILED`; stderr `Error: spawn /nonexistent/acp2-missing-python ENOENT`; zero `mcp__` tools | **PASS** (explicit, no silent start) |
| incompatible protocol (C-owned stdio stub rejects every version) | client sent `initialize` `2025-11-25`; **the stub** answered `-32602`, `data.code=unsupported_protocol_version`; entry `FAILED`; zero `mcp__` tools | **PASS** (explicit) |

The `-32602` above comes from the C-owned test stub, never from B's adapter:
since M0-08/ADR-006 the real server answers `2025-06-18` instead of rejecting
the request. The row is kept because "a server that hard-rejects the client
version makes DSH fail loudly with zero tools" is still a required negative.

The stub is a test fixture written into the evidence directory; it is never
part of the shipped profile and exposes no QC capability.

## 7. Finding F1 — CLOSED by ADR-006 (M0-08); B must still strengthen schemas in M1-01

T-12 did not silently adopt either schema shape. The T-11 `schema-drift.json`
remains the recorded source: B05 descriptor vs B's archived live `tools/list`
differ for all eight tools (`additionalProperties` and required fields).

M0-08 closed the *authority* question (see the T-11 section §5.1 and ADR-006):
the live `tools/list` is the client contract, DSH registers those schemas, and a
frozen descriptor that disagrees is a descriptor bug. `f1-schema-mismatch.json`
therefore reads `status: CLOSED_DECISION`, `decided_by: B (M0-08, ADR-006)`,
`c_follow_up: none for H2`. The residual product observation — the live schemas
require only `workflow` and set `additionalProperties: true` — is carried to
M1-01 as a schema-strengthening input, not as a handshake blocker.

## 8. P2 extension policy

No additional tool is advertised. `extensions` in B05 is empty, the P2 rows
remain `NOT_VERIFIED(b03_handoff)`, and the snapshot asserts a single
`mcp__acp__` namespace. The eight P1 tools are the current contract, not a
permanent ceiling.

## 9. What unblocks T-12

Same prerequisite as T-11: B runs the isolated Controller on
`127.0.0.1:18765` and resolves F1. A single `c_accept dsh-discovery-live 300`
then exercises the same body: registration, eight-tool allowlist, no-exec and
both negatives. This document and the scenario must not be cited as an H2 PASS
until that run is green.
