# B-req-06 — Workspace, CI and static-asset mount for the private `rph/` tree

- **Request ID:** `B-req-06`
- **Status:** open
- **Raised by:** C (T-05 / C02), 2026-10-22
- **Requested of:** B (repo/CI publisher and static-asset owner)
- **Severity:** blocking for T-06 (Harness) and every later `rph` build in CI
- **Consumer step:** C/T-05 and C/T-06–T-10, T-43–T-46
- **Consumer track:** C (`acp2-plan-c-rph-workbench`)

## Consumer step

C creates `rph/` as a private pnpm workspace inside the C worktree. Until B
publishes the CI job and the static-asset mount contract below, `rph` is only
buildable by hand with the frozen toolchain, and a released Workbench cannot
serve the client's static assets from a versioned path.

## Owner

B — repository layout/CI (`B00`/`B03`) and release asset publication. C never
edits the root `package.json` (there is none), CI definitions, or B's publisher
jobs.

## Current schema revision

The C baseline (`e79473d`, tree aligned with B `1a1b4b23…`) contains:

- no root `package.json`, `pnpm-workspace.yaml`, `node_modules` or
  `pnpm-lock.yaml`;
- `rph/` created by T-05 with a private root `package.json`
  (`private:true`, `packageManager:"pnpm@11.28.5"`,
  `engines.node:"^22.19.0 || >=24.0.0"`), a committed `pnpm-lock.yaml` and
  four packages `packages/{research,bridge,dsh-bundle,cli}`;
- no CI job that builds it and no static-asset manifest.

Status: `NOT_VERIFIED(b06_handoff)` for CI/mount until B delivers.

## Expected deliverable

### 1. Private workspace boundary

- The repository root keeps **no** `package.json`, no `pnpm-workspace.yaml`,
  no root `node_modules` and no root lockfile. `rph/` is an isolated workspace;
  `pnpm --dir rph …` must remain the only supported invocation.
- B's checkout/cleanup automation must not delete `rph/pnpm-lock.yaml` or
  rewrite `rph/package.json`; the lockfile is a committed artifact.
- C's `rph/scripts/check-boundaries.mjs` stays the boundary authority; B must
  not replace it with a looser scan.

### 2. pnpm cache and registry policy

- `store-dir` outside the repository (recommended
  `$ACP2_TOOLS/pnpm-store`), so CI never commits or caches into `rph/`.
- Registry access only through the pinned registry with lockfile integrity
  (`pnpm install --frozen-lockfile`); no `latest`, no range widening.
- A warmed cache must allow an offline reinstall
  (`pnpm install --offline --frozen-lockfile`) to succeed.
- Network egress for the CI job is limited to the registry; no postinstall
  script may fetch scientific software, DSH source, or credentials.

### 3. CI job

A single job (or a matrix entry) with:

```yaml
node: "22.19.0"
pnpm: "11.28.5"
workdir: rph
steps:
  - pnpm install --frozen-lockfile
  - pnpm build
  - pnpm test
  - pnpm check
```

Requirements:

- the job fails on a non-zero exit of any step; `pnpm test` prints at least one
  executed test and fails with `EMPTY TEST SET` when none ran;
- `pnpm check` runs `scripts/check-boundaries.mjs` before typecheck;
- artifacts: `rph/dist-packages/*.tgz` retained for the release job, with
  SHA-256 recorded in the release receipt;
- the job must not run with `--dir` against a different Node/pnpm pair than the
  frozen compatibility lock.

### 4. Static-asset manifest mount

C emits `docs/reference/next-change/acp2-contracts/experience/static-assets.manifest.json`
(extension `acp2.experience.static-assets.v1`, produced by T-43) listing every
client asset with `path`, `sha256`, `media_type`, `release_id`.

B mounts that manifest's assets **read-only** at a versioned URL root
(`/experience/<release_id>/`) and serves exactly the listed files:

- unknown paths return 404 (no directory listing, no fallback to the repo);
- `rph/node_modules`, `rph/dist-packages`, test output and source maps are
  never mounted;
- the mount is immutable per `release_id`; a new release is a new root;
- a digest mismatch at publish time rejects the whole release.

## Minimal input

```json
{
  "workspace": {
    "root": "rph",
    "private": true,
    "package_manager": "pnpm@11.28.5",
    "node_engine": "^22.19.0 || >=24.0.0",
    "lockfile": "rph/pnpm-lock.yaml",
    "root_package_json": false
  },
  "ci": {
    "job_id": "acp2-rph-workspace",
    "commands": [
      "pnpm install --frozen-lockfile",
      "pnpm build",
      "pnpm test",
      "pnpm check"
    ],
    "working_directory": "rph"
  },
  "static_assets": {
    "manifest": "docs/reference/next-change/acp2-contracts/experience/static-assets.manifest.json",
    "mount_root": "/experience/<release_id>/",
    "read_only": true
  }
}
```

## Expected success output

```json
{
  "workspace": {"status": "ready", "root_package_json": false, "lockfile_sha256": "sha256:<64-hex>"},
  "ci": {"job_id": "acp2-rph-workspace", "result": "pass", "node": "22.19.0", "pnpm": "11.28.5"},
  "static_assets": {
    "release_id": "2026.10.22-rph.1",
    "mount_root": "/experience/2026.10.22-rph.1/",
    "files": 0,
    "digest_verified": true
  }
}
```

The CI evidence row records the exact command list, exit code per command and
the `rph/dist-packages/*.tgz` digests.

## Failure output

Any of the following must fail closed with a machine-readable error; B must
never paper over a broken workspace:

```json
{
  "class": "invalid-state",
  "code": "rph_workspace_invalid",
  "params": {
    "rule": "root_package_json_present",
    "detail": "package.json exists at repository root"
  }
}
```

Other required rejections:

| Rule | `rule` | Trigger |
| --- | --- | --- |
| Root workspace file added | `root_package_json_present` | any root `package.json`/`pnpm-workspace.yaml`/lockfile appears |
| Lockfile drift | `lockfile_drift` | `--frozen-lockfile` install fails |
| Toolchain mismatch | `toolchain_mismatch` | Node/pnpm differ from the compatibility lock |
| Empty test set | `empty_test_set` | `pnpm test` exits 1 with `EMPTY TEST SET` |
| Boundary hit | `boundary_violation` | `scripts/check-boundaries.mjs` exits non-zero |
| Mount escape | `static_asset_path_escape` | a manifest path is absolute, contains `..`, or points outside the asset root |
| Digest mismatch | `static_asset_digest_mismatch` | served bytes do not match `sha256` |
| Unlisted file requested | `static_asset_not_listed` | path is not in the manifest (404) |

## Compatibility requirement

- B may add fields to CI output/mount metadata; it must not rename or drop the
  rules above.
- A Node or pnpm upgrade requires a new compatibility lock revision, a lockfile
  regeneration and a re-run of this request; it is never a silent CI change.
- The mount root is versioned by `release_id`; aliasing `/experience/latest` is
  optional and must not bypass digest verification.
- C keeps releasing `rph` only as a private workspace; nothing under `rph/`
  may be published to a public registry by this request.

## Evidence path

`$ACP2_EVIDENCE/C/workspace/<run_id>/` for T-05 (build/test/check/package) and
`$ACP2_EVIDENCE/C/client-release/<run_id>/` for the CI/mount rows once B
delivers. C records `NOT_VERIFIED(b06_handoff)` until then.
