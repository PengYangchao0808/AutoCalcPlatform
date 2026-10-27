# rph — Research Package Hub workspace

Standalone, **private** pnpm workspace for the ACP 2.0 experience client. It is
deliberately separate from the repository root:

- there is **no root `package.json`** in this checkout and none may be added;
- `rph/node_modules` and `rph/dist-packages` are local build outputs;
- Node `^22.19.0 || >=24.0.0` and pnpm `11.28.5` are pinned
  (`packageManager`, `engines`).

## Dependency direction (frozen)

```text
cli -> dsh-bundle -> bridge -> research
```

| Package | May depend on | Must not |
| --- | --- | --- |
| `@rph/research` | nothing (pure reference/helpers) | import bridge/dsh-bundle/cli, the platform store, or any execution API |
| `@rph/bridge` | `@rph/research` | import dsh-bundle/cli, own fetch/HTTP, spawn processes, or import platform store code |
| `@rph/dsh-bundle` | `@rph/bridge` | import cli, DSH private source paths, or vendor DSH |
| `@rph/cli` | `@rph/dsh-bundle` | start a Controller, spawn processes, or execute scientific software |

`bridge` depends only on public JSON values and the `PlatformClient` interface;
`research` never imports the platform store. `scripts/check-boundaries.mjs`
enforces this and the execution/private-surface rules:

| rule | applies to | why |
| --- | --- | --- |
| `child_process`, `spawn`, `exec`, `execFile` | every package except the named launcher | one approved client process (`acp mcp serve`) |
| `fetch`, `undici`, `node:http(s)`, `net`/`tls`/`dgram`, `ws` | every package | the platform is reached over the injected MCP stdio transport, never HTTP |
| `createServer`, `.listen` | every package | exactly one Controller; RPH never opens a listener |
| `["mcp", "serve"]` argv | only `src/mcp-stdio-launch.ts` | a second `acp mcp serve` spelling is a gate failure |
| `@rph/*` import direction | every package | only `cli -> dsh-bundle -> bridge -> research` |

Each rule has a permanent negative test in `test/boundaries.test.mjs` (synthetic
workspaces), so the gate cannot rot into documentation. The launcher allowlist is
`packages/dsh-bundle/src/mcp-stdio-launch.ts` and nothing else: adding a launcher
is a deliberate gate change. Shell/QC/LSF execution stays forbidden everywhere.

## Platform client (M3-01b)

`@rph/dsh-bundle` owns the **public adapter** that turns a real session into a
bridge bound to the server's answer:

```text
initialize/host handshake -> real tools/list -> tools/call:acp_capabilities
  -> handshake.negotiatedExtensions -> new Bridge(client, {negotiatedExtensions})
```

- `src/platform-session.ts`: `openMcpSession` / `openToolRuntimeSession` /
  `buildPlatformSession`. `product` mode fails closed on a protocol mismatch,
  an un-negotiated `acp.research.v1`, or a `tools/list` that does not cover the
  promised P1+P2 surface; `acceptance` mode records the real answer and keeps
  P2 locked. There is **no option** that can inject a negotiated extension.
- `src/mcp-stdio.ts`: the injected-transport client (no process of its own);
  `src/mcp-stdio-launch.ts` remains the only launcher (`acp mcp serve`).
- `src/platform-result.ts`: one restoration step between the verbatim transport
  result and the bridge's strict guard — it drops the frozen inert audit member
  (`data.capability_request.authorization`) and withholds absolute host paths;
  a real credential is never rewritten and is still refused by the bridge.
- No `@modelcontextprotocol/client` runtime dependency is declared here: the
  official SDK is owned by the DSH plugin (`@deepseek-ai/dsh-mcp-client`) that
  provides `ctx.tools`, and by the injected stdio transport. Adding the SDK to
  this package would create a second transport owner.


## CLI (`@rph/cli` → bin `rph`)

`src/compose.ts` is the single composition root; the CLI imports
`@rph/dsh-bundle` **only** and never re-implements the Supervisor state machine.

```bash
rph --help | --version
rph research --question <text> --recipe-digest sha256:<64 hex> [options]
rph resume --proposal <ref> --plan-digest sha256:<64 hex> [--task <job>] [--cursor <c>]
rph doctor [--connect] [--environment dev|acceptance|release] [--json]
```

- `--mode product|acceptance|demo` selects the composition mode. `product` (the
  default) and `acceptance` require a model planner, the model gateway, a real
  platform session (P1+P2 surface), a durable research writer and the
  artifact/evidence path; the missing prerequisites are printed **all at once**
  and the command exits `3`. Nothing falls back to a fixture.
- `--mode demo` is a **labelled fixture**: deterministic skills, in-memory ports,
  `evidence_kind: "fixture"`, `demo: true`. It is refused outside `dev`, refused
  when a real Controller URL is configured, and it refuses `--skills model`.
- `--skills test` is demo-only; `product`/`acceptance` refuse it.
- `resume` addresses a real research saga (`acp.research.resume`); a product
  session is required and a demo has nothing to resume.
- Exit codes: `0` ran, `1` failed (typed), `2` usage, `3` incomplete composition.

Version: `packages/cli/package.json` is the single source (`CLI_VERSION`,
`MCP_CLIENT_VERSION` and the thin-slice client identity all read a package
manifest; no source keeps a second `0.0.0`).

## Portable profiles (dev / acceptance / release)

`src/runtime-profile.ts` is the only profile matrix. A profile names
**parameters**; every host value comes from the environment at start-up, so the
repository carries no absolute interpreter, checkout cwd, acceptance run root or
plaintext key:

| parameter | env | dev | acceptance | release |
| --- | --- | --- | --- | --- |
| `controller_url` | `ACP2_CONTROLLER_URL` | optional | required | required |
| `interpreter` | `RPH_INTERPRETER` | default `python3` | required | required |
| `working_directory` | `RPH_WORKING_DIRECTORY` | optional | required | required |
| `run_root` | `ACP_RUN_ROOT` | optional | required | required |
| `config_path` | `ACP_CONFIG` | optional | required | required |
| `project_id` | `RPH_PROJECT` | optional | required | required |
| `permission_scope` | `RPH_PERMISSION_SCOPE` | optional | required | required |
| `artifact_evidence_path` | `RPH_ARTIFACT_EVIDENCE_PATH` | optional | required | required |
| `research_writer` | `RPH_RESEARCH_WRITER` | optional | required | required |
| `model_provider` / `model_id` | `RPH_MODEL_PROVIDER` / `RPH_MODEL_ID` | optional | optional | required |
| `model_requests_budget` | `RPH_MODEL_REQUESTS` | default `8` | required | required |
| `controller_api_key` | `RPH_CONTROLLER_API_KEY` | optional, secret | optional, secret | optional, secret |

Only `dev` may run the demo and it is the only non-strict doctor environment.
The two `packages/dsh-bundle/profiles/*.cordis.yml` files are the **frozen live
acceptance host records** (digest-pinned by the M0-09 evidence) and are not
product profiles; portable deployment configuration lives in the matrix above.

## Composition root and the recovery ports (M3-04 exit)

`src/compose.ts` builds the Supervisor from one injected session plus injected
readers/writers; `src/research-ports.ts` adapts platform facts onto the frozen
Supervisor contract (`skills/supervisor.ts`, `SUPERVISOR_RECOVERY_SCHEMA`). It
never re-implements the state machine or a verdict.

| port | wire | installed when |
| --- | --- | --- |
| `submitIntent` | M1-02 ledger via `createSubmitIntentPort` | always (the session bridge's ledger) |
| `submit` | bridge `acp_submit` (P1) | a plan→payload mapper is injected |
| `approve` | `acp_research_approval_query_v1` | always |
| `evidence` | the single `acp2.runtime.evidence-projection.v1` | an `EvidenceReaderPort` is injected |
| `resume` | P2-R5 `acp_research_resume_v1` | the proposal ref is known |
| `persistDecision` | research writer CAS | the writer implements `persistDecision` |
| `persistCheckpoint` | research writer fenced checkpoint | the writer implements `persistCheckpoint` |
| `budgetUsage` | platform saga/receipt counters | a `BudgetUsageReaderPort` is injected |

- **The submit key is never re-derived.** `bridgeSubmitRequest` forwards
  `SubmitRequest.idempotency_key` verbatim and omits the `research`/`round`
  identity, so the bridge's legacy self-derived key path cannot come back; an
  empty key is a typed refusal (`submit_key_missing`), never a new key.
- **No port invents a fact.** A transport failure becomes a typed
  `ResearchPortError` (`evidence_unavailable`, `cursor_expired`, `fenced`, …); a
  missing reader/writer leaves the port absent and the Supervisor fails closed.
- **`product` mode sets `require_evidence_binding: true`**: the verdict comes
  only from a verified `EvidenceView` plus a persisted decision. The fail-fast
  matrix therefore also lists `evidence_reader_missing`,
  `decision_writer_missing`, `checkpoint_writer_missing` and
  `budget_usage_source_missing` — in the same one-shot exit as the rest.
- The platform exposes the evidence projection as a research-record route
  (`/research/records/{id}/evidence`); the RPH boundary forbids direct HTTP, so
  the transport is an injected `EvidenceReaderPort`. The live reader against a
  real Controller stays `NOT_VERIFIED` until that route is reachable from the
  MCP surface or an approved acceptance adapter exists.

## Doctor

`rph doctor` has two modes and a three-state vocabulary (`pass`/`fail`/`skip`; a
check that did not run is never `pass`):

- **static** — version pair, profile parameter resolution, writer, budget, model
  configuration and secret redaction. No socket, no process.
- **`--connect`** — additionally opens a real session through the named launcher
  and reports the protocol handshake, permission scope, the `acp.research.v1`
  answer and the observed P1/P2 tool surface.

A credential is rendered `<redacted:env>`; the report is scanned for the real
secret value before it is printed. The live `--connect` row against a real
Controller is `NOT_VERIFIED` until an environment with a running Controller is
available; the in-process transport path is covered by `test/m3-02-cli.test.ts`.

## Commands

```bash
pnpm --dir rph install              # writes pnpm-lock.yaml
pnpm --dir rph install --frozen-lockfile
pnpm --dir rph build                # tsc project references (root emits nothing)
pnpm --dir rph test                 # builds, then node --test over test/dist
pnpm --dir rph check                # boundary gate + typecheck
pnpm --dir rph package              # dist-packages/*.tgz
```

`test` fails with `EMPTY TEST SET` when no compiled test is found; an empty set
is never a pass.

## Offline install surface (release artifacts)

`pnpm package` writes the four private tarballs **and** a self-contained install
surface into `dist-packages/`:

```text
dist-packages/
  rph-{research,bridge,dsh-bundle,cli}-0.0.0.tgz
  rph-install.package.json   consumer manifest (identical bytes to package.json)
  package.json
  pnpm-workspace.yaml        pnpm 11 settings + @rph/* overrides
  pnpm-lock.yaml             file:-only resolutions
  rph-packages.json          name/version/size/sha256 index
  SHA256SUMS                 covers every shipped file
  README.md                  clean-host instructions
  verify-install.mjs         checksum + import-resolution check
```

Copy that directory to the target host and install without a checkout or the
public registry:

```bash
pnpm install --offline --frozen-lockfile
node verify-install.mjs
```

`verify-install.mjs` now also runs the published bin from an unrelated absolute
directory (`node_modules/.bin/rph --version`, `--help`, `doctor --json`) and
asserts that the reported version is `@rph/cli`'s own `package.json` version —
the offline proof of "no checkout, no source `PYTHONPATH`, no second version".

The packed tarballs keep `@rph/*` dependencies as plain versions (npm rewrites
`workspace:*`), so `pnpm-workspace.yaml` pins them to the local tarballs via
`overrides`. pnpm 11 ignores the `pnpm` field in `package.json`; the workspace
file is the authority. Packaging fails if a tarball digest, the lockfile or a
throwaway offline install regresses. The packages remain `private: true` — the
tarballs are distributed, not published.

## Layout

```text
rph/
  package.json          private workspace root (no root package.json above rph/)
  pnpm-workspace.yaml
  pnpm-lock.yaml
  tsconfig.json         project references only, no root emit
  tsconfig.base.json    strict ES2022 / NodeNext / declaration
  packages/{research,bridge,dsh-bundle,cli}/
  test/                 real Node tests + their tsconfig
  fixtures/             consumption fixtures (producer revision + digest)
  examples/             configuration templates and task-reference samples
  scripts/              boundary, test and packaging gates
```

The frozen DSH installation lives at `$ACP2_TOOLS/deepseek-harness`; it is
**not** copied or forked here. The bundle references only public DSH interface
strings such as `@deepseek-ai/dsh-mcp-client`.
