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
enforces this and the execution/private-surface rules.

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
