# C acceptance environment, routing contract and evidence root

Status: **frozen by C/T-04 (C04), 2026-10-22**. Owner: C track.
Canonical live file: `<acp2-dev>/data/acceptance-env-c.sh` (`chmod 600`).
This document is the human-readable companion to that file and to
`dsh-compatibility.json` in this directory.

> This is **C acceptance**, never B acceptance. B00 published no
> `runtime/baseline-lock.json`, so nothing here may be reported as a B-signed
> baseline. See "Evidence-root resolution" below.

## 1. Installed toolchain (isolated, no system replacement)

| Component | Frozen value | Absolute executable |
| --- | --- | --- |
| Node | `22.19.0` (`node-v22.19.0-linux-x64`) | `<server-root>/acp2-tools/node-v22.19.0-linux-x64/bin/node` |
| npm (bundled) | `10.9.3` | `<server-root>/acp2-tools/node-v22.19.0-linux-x64/bin/npm` |
| pnpm | `11.28.5` | `<server-root>/acp2-tools/pnpm/node_modules/.bin/pnpm` |
| DSH | revision `687f282e8625c6b8d11ff58211a90524be2e4317`, version `0.2.1-rc.2` | `<server-root>/acp2-tools/deepseek-harness/apps/cli/src/bin.ts` |
| Official MCP client | `@deepseek-ai/dsh-mcp-client@0.2.1-rc.2` (workspace) | `<server-root>/acp2-tools/deepseek-harness/packages/mcp/mcp-client` |
| tsx loader | `4.23.13` | `file://<server-root>/acp2-tools/deepseek-harness/node_modules/.pnpm/tsx@4.23.13/node_modules/tsx/dist/esm/index.mjs` |

Node archive `node-v22.19.0-linux-x64.tar.xz` was verified against the official
`SHASUMS256.txt` **before** extraction:

```
sha256 c0649af18e6a24f6fe5535a3e86b341dd49a8e71117c8b68bde973ef834f16f2
```

The archive and checksum file remain in `<server-root>/acp2-tools/downloads/`.
DSH `engines.node` is verbatim `^22.19.0 || >=24.0.0`; Node 22.19.0 satisfies it,
so the planned revision was not re-selected. `pnpm-lock.yaml` SHA-256 is
`4637cca3b89c4c8d5a147321710e597aa62ec2444a3772ab542be5690893dfe4`.

The system Node at `/home/<user>/.opencode/bin/node` (v20.19.0) is untouched.
Only the acceptance shell prepends `$ACP2_NODE_HOME/bin` and
`$ACP2_TOOLS/pnpm/node_modules/.bin` to `PATH`.

Install/build evidence:
`<acp2-dev>/evidence/C/node-dsh-install/20261022T112416Z-c04/`.

## 2. Exported variables

All values are absolute. No credentials are exported.

```
ACP2_C_SOURCE            <acp2-dev>/C
ACP2_A_SOURCE            <acp2-dev>/A          (read-only; C never reads it)
ACP2_INTEGRATION_SOURCE  <acp2-dev>/integration
ACP2_SOURCE              $ACP2_INTEGRATION_SOURCE               (alias, one meaning)
ACP2_TOOLS               <server-root>/acp2-tools
ACP2_DATA_ROOT           <acp2-dev>/data
ACP2_DSH_SOURCE          <server-root>/acp2-tools/deepseek-harness
ACP2_DSH_LOADER          file://…/tsx@4.23.13/.../esm/index.mjs
ACP2_DSH_HOME            $ACP2_DATA_ROOT/dsh-home
ACP2_DSH_WORKSPACE       $ACP2_DATA_ROOT/research-workspace
ACP2_DSH_OVERLAYS        $ACP2_CONTRACTS/experience/dsh-overlays
ACP2_EVIDENCE            <acp2-dev>/evidence   (provisional)
ACP2_CONTRACTS           $ACP2_C_SOURCE/docs/reference/next-change/acp2-contracts
ACP2_C_PYTHON            venvs/c/bin/python
ACP2_INTEGRATION_PYTHON  venvs/integration/bin/python
ACP2_BROWSER_PYTHON      venvs/browser/bin/python
ACP2_PYTHON              $ACP2_C_PYTHON
ACP2_NODE_HOME           <server-root>/acp2-tools/node-v22.19.0-linux-x64
PLAYWRIGHT_BROWSERS_PATH $ACP2_TOOLS/playwright-browsers
```

Interpreters are provisional: B02a delivered no role interpreters, so
`venvs/{c,integration,browser}` were created by C/T-01. Their imports are
asserted to resolve inside the matching source (`venvs/c` → `$ACP2_C_SOURCE`,
`venvs/integration` → `$ACP2_INTEGRATION_SOURCE`, `venvs/browser` → its own
`sys.prefix`). This is `NOT_VERIFIED(b02a_handoff)`.

## 3. Scenario registry and `ACP2_LIVE_SCENARIOS`

`acceptance-env-c.sh` is the **single source** for both lists:

* `ACP2_ALL_SCENARIOS` — the frozen 36-scenario enum from
  `ACP_2.0_Parallel_Plan_C_RPH_Workbench_20261020.md` §3.3.
* `ACP2_LIVE_SCENARIOS` — the 21 live-by-default scenarios:

```
mcp-live dsh-discovery-live model-live
dsh-local-singlepoint-live dsh-lsf-singlepoint-live dsh-optfreq-live
same-task-browser-live client-recovery-live
approval-budget-live research-loop-live research-resume-live
fault-boundaries-live da-research-live
plugin-discovery-live schema-form-live plugin-upgrade-live
cross-domain-live multi-project-live experience-upgrade-live
clean-install-live operator-runbook-live
```

Everything else is fixture-by-default. `evidence-browser-live` is deliberately
**fixture-by-default**: it runs live only with an explicit `--mode live`.
T-06/T-07 must consume `ACP2_LIVE_SCENARIOS` verbatim and assert it equals their
frozen enum; they must not retype a second list.

## 4. `route_select()` — the only routing implementation

```
route_select <scenario> <timeout> [--phase V] [--mode V] [--subcase V]
            [--manifest V] [--out V] [--timeout V] [--dry-run]
```

Rules:

1. Fewer than two positionals → `3` (checked before reading scenario/timeout).
2. Each value-taking flag (`--phase`, `--mode`, `--subcase`, `--manifest`,
   `--out`, `--timeout`) checks that a value exists **before** reading `$2`;
   missing value → `3` immediately, no loop. `--dry-run` is boolean.
3. Unknown flag, unknown scenario, non-positive timeout, or a `--mode` other
   than `fixture`/`live` → `3`.
4. `--dry-run` always selects `$ACP2_C_PYTHON` + `$ACP2_C_SOURCE` (dry-run wins
   over `--mode live`).
5. Otherwise live = explicit `--mode live`, OR the scenario is in
   `ACP2_LIVE_SCENARIOS` and `--mode` is not `fixture`. Live selects
   `$ACP2_INTEGRATION_PYTHON` + `$ACP2_INTEGRATION_SOURCE`.
6. Sets `ACP2_SELECTED_PYTHON`, `ACP2_SELECTED_INTERPRETER`,
   `ACP2_SELECTED_SOURCE`, `ACP2_PRODUCT_SOURCE`, `ACP2_SELECTED_MODE`
   (`fixture|live`), plus the parsed `ACP2_RS_*` globals used by `c_accept`.

Verified routing matrix (isolated probe, interpreter stubs, no product import):

| argv | selected source | mode |
| --- | --- | --- |
| `baseline 60 --dry-run` | C | fixture |
| `evidence-browser-live 60 --mode fixture` | C | fixture |
| `mcp-live 60 --dry-run` | C | fixture |
| `mcp-live 60 --mode live --dry-run` | C | fixture (dry-run wins) |
| `mcp-live 60` | integration | live |
| `mcp-live 60 --mode live` | integration | live |
| `mcp-live 60 --mode` / `--phase` / `--subcase` | — | exit 3 in ~6 ms |
| unknown scenario / unknown flag / one positional | — | exit 3 |

`scripts/acceptance/acp2_route_probe.sh` contains no routing logic: it sources
this environment and prints only
`interpreter=<path> source=<path> mode=<fixture|live>`, or `exit=<code>`.

## 5. `c_accept <scenario> <timeout> [flags...]`

Order of checks:

1. `ACP2_CONTRACTS` must be non-empty, and the evidence root must resolve
   (section 6), otherwise `2`.
2. Fewer than two arguments → `3`.
3. `route_select` decides the interpreter/source; usage errors → `3`.
4. `run_id` = `$ACP2_RUN_ID`, else `<scenario>-<UTC timestamp>-<pid>`.
5. Output leaf = `$ACP2_EVIDENCE/C/<scenario>/<run_id>/<phase>[/<mode>][/<subcase>]`
   (default phase `main`). A **non-empty leaf** is refused with `3`; sibling
   phases/modes/subcases under the same `run_id` are allowed.
6. Harness = `$ACP2_SELECTED_SOURCE/scripts/acceptance/acp2_experience.py`.
   If that file is absent but the C revision harness exists, the C harness is
   used (the live lane imports the product from integration, while the C
   revision remains the harness contract owner). If neither exists → `2`
   `NOT_VERIFIED(harness_not_delivered)`.
7. On success `exec`s the harness with `--scenario`, `--timeout`, `--out` and
   the forwarded flags (`--mode`, `--phase`, `--subcase`, `--manifest`,
   `--dry-run`).

`ACP2_RS_FORWARD` carries the forwarded flags; `--out`/`--timeout` are captured
by `c_accept` so the harness receives exactly one of each.

## 6. Evidence-root resolution (B00 handoff)

`c_accept` resolves the evidence root in this order:

1. **B-bound mode** — if `$ACP2_CONTRACTS/runtime/baseline-lock.json` exists,
   `$ACP2_EVIDENCE` must equal its `evidence_root` exactly; otherwise `2`.
   The run is not stamped provisional.
2. **Provisional mode** — if no lock exists, `$ACP2_EVIDENCE` must equal
   `<acp2-dev>/evidence`, the provisional root frozen in
   `experience/ownership.md`. Every run root receives `not_verified.json`
   containing `NOT_VERIFIED(b00_handoff)`, and `ACP2_NOT_VERIFIED` is exported.
   This is never presented as B acceptance.

Both branches were exercised: matching lock → proceeds to the harness check,
mismatched lock → `2`; missing lock + non-provisional root → `2`.

## 7. `dsh_acceptance <web|headless> [app args...]`

A profile/overlay-aware wrapper around the frozen DSH source entry:

* runs `node --import "$ACP2_DSH_LOADER" "$ACP2_DSH_SOURCE/apps/cli/src/bin.ts"
  --profile <web|headless> [--patch <overlay>...] <args...>`;
* `cd`s into `$ACP2_DSH_WORKSPACE` (never the ACP production source), sets
  `DSH_HOME=$ACP2_DSH_HOME`, and passes
  `TSX_TSCONFIG_PATH=$ACP2_DSH_SOURCE/tsconfig.json` so tsx resolves the DSH
  workspace `paths` when cwd is outside the checkout;
* applies every `experience/dsh-overlays/*.yml` as `--patch`, in filename
  order (C-owned overlay drop; T-05 populates it);
* if the recorded build failed (`ACP2_DSH_BUILD_STATUS != built`), exits `2`
  with the recorded reason instead of pretending to work;
* wrong/absent profile → `3`.

Smoke evidence: `dsh_acceptance headless --help` exits 0 (21 stdout lines);
`dsh_acceptance web --dump-default-config` exits 0 (1229 lines, no boot).

## 8. Negative checks proven

* Node archive copy with one flipped byte → checksum verification exits non-zero
  (`checksum_mismatch`) and extraction is not attempted.
* Temporary `package.json` with `engines.node = ">=24.0.0"` against Node 22.19.0
  → engines gate exits non-zero and the install path stops.

## 9. Not verified / open

* `NOT_VERIFIED(b00_handoff)` — no `runtime/baseline-lock.json`, `manifest.json`
  or B-signed evidence root.
* `NOT_VERIFIED(b02a_handoff)` — tools root, data root and role interpreters are
  C-provisional; B02a delivered nothing. Node/pnpm/DSH install itself is fully
  verified and does not depend on B02a.
* `c_accept <scenario>` product PASS is **not** claimed here: the Harness
  (`scripts/acceptance/acp2_experience.py`) is delivered by T-06/T-07. At freeze
  time `c_accept` correctly returns `2` `NOT_VERIFIED(harness_not_delivered)`.
* No `route.json` is written by this todo; T-07 owns the formal route record.
* Chromium is not installed; `playwright install` was not run (T-08 owns it).
