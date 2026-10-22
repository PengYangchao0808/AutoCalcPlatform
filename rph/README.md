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
