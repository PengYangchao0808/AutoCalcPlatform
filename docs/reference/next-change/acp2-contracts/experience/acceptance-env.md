# C acceptance environment, routing contract and evidence root

Status: **frozen by C/T-04 (C04), 2026-10-22; B00/B02a adoption by T-04a,
2026-10-22**. Owner: C track.
Canonical live file: `/opt/acp2-dev/data/acceptance-env-c.sh` (`chmod 600`).
This document is the human-readable companion to that file and to
`dsh-compatibility.json` in this directory.

> This is **C acceptance**, not a re-signing of B acceptance. B00's committed
> `runtime/baseline-lock.json` / `manifest.json` (commit `31e60cb`) and B02a's
> environment (commit `15767b9`) are adopted by digest. See
> "Evidence-root resolution" below.

## 1. Installed toolchain (isolated, no system replacement)

| Component | Frozen value | Absolute executable |
| --- | --- | --- |
| Node | `22.19.0` (`node-v22.19.0-linux-x64`) | `/opt/acp2-tools/node-v22.19.0-linux-x64/bin/node` |
| npm (bundled) | `10.9.3` | `/opt/acp2-tools/node-v22.19.0-linux-x64/bin/npm` |
| pnpm | `11.28.5` | `/opt/acp2-tools/pnpm/node_modules/.bin/pnpm` |
| DSH | revision `687f282e8625c6b8d11ff58211a90524be2e4317`, version `0.2.1-rc.2` | `/opt/acp2-tools/deepseek-harness/apps/cli/src/bin.ts` |
| Official MCP client | `@deepseek-ai/dsh-mcp-client@0.2.1-rc.2` (workspace) | `/opt/acp2-tools/deepseek-harness/packages/mcp/mcp-client` |
| tsx loader | `4.23.13` | `file:///opt/acp2-tools/deepseek-harness/node_modules/.pnpm/tsx@4.23.13/node_modules/tsx/dist/esm/index.mjs` |

Node archive `node-v22.19.0-linux-x64.tar.xz` was verified against the official
`SHASUMS256.txt` **before** extraction:

```
sha256 c0649af18e6a24f6fe5535a3e86b341dd49a8e71117c8b68bde973ef834f16f2
```

The archive and checksum file remain in `/opt/acp2-tools/downloads/`.
DSH `engines.node` is verbatim `^22.19.0 || >=24.0.0`; Node 22.19.0 satisfies it,
so the planned revision was not re-selected. `pnpm-lock.yaml` SHA-256 is
`4637cca3b89c4c8d5a147321710e597aa62ec2444a3772ab542be5690893dfe4`.

The system Node at `/home/redacted-user/.opencode/bin/node` (v20.19.0) is untouched.
Only the acceptance shell prepends `$ACP2_NODE_HOME/bin` and
`$ACP2_TOOLS/pnpm/node_modules/.bin` to `PATH`.

Install/build evidence (historical, pre-adoption root):
`/opt/acp2-dev/evidence/C/node-dsh-install/20261022T112416Z-c04/`.
New product evidence is written under the adopted
`reports/acp2_implementation/bb45a46/C/`.

## 2. Exported variables

All values are absolute. No credentials are exported.

```
ACP2_PROJECT_ROOT       repo-root            (resolves B00 project-relative roots)
ACP2_C_SOURCE            /opt/acp2-dev/C
ACP2_A_SOURCE            /opt/acp2-dev/A          (read-only; C never reads it)
ACP2_INTEGRATION_SOURCE  /opt/acp2-dev/integration
ACP2_SOURCE              $ACP2_INTEGRATION_SOURCE               (alias, one meaning)
ACP2_TOOLS               /opt/acp2-tools                 (C Node/DSH root; B02a uses .../venvs)
ACP2_DATA_ROOT           /opt/acp2-dev/data      (B02a's own root is .../data/b)
ACP2_DSH_SOURCE          /opt/acp2-tools/deepseek-harness
ACP2_DSH_LOADER          file://…/tsx@4.23.13/.../esm/index.mjs
ACP2_DSH_HOME            $ACP2_DATA_ROOT/dsh-home
ACP2_DSH_WORKSPACE       $ACP2_DATA_ROOT/research-workspace
ACP2_DSH_OVERLAYS        $ACP2_CONTRACTS/experience/dsh-overlays
ACP2_BASELINE_LOCK       $ACP2_PROJECT_ROOT/docs/reference/next-change/acp2-contracts/runtime/baseline-lock.json
ACP2_EVIDENCE            reports/acp2_implementation/bb45a46   (adopted B00 root)
ACP2_PROVISIONAL_EVIDENCE /opt/acp2-dev/evidence  (historical alias only; rejected)
ACP2_CONTRACTS           $ACP2_C_SOURCE/docs/reference/next-change/acp2-contracts
ACP2_C_PYTHON            venvs/c/bin/python
ACP2_INTEGRATION_PYTHON  venvs/integration/bin/python
ACP2_BROWSER_PYTHON      venvs/browser/bin/python
ACP2_PYTHON              $ACP2_C_PYTHON
ACP2_NODE_HOME           /opt/acp2-tools/node-v22.19.0-linux-x64
PLAYWRIGHT_BROWSERS_PATH $ACP2_TOOLS/playwright-browsers
```

`ACP2_TOOLS` intentionally diverges from B02a: B02a's
`data/b/acceptance-env.sh` sets `ACP2_TOOLS=/opt/acp2-dev/venvs`
(B's Python role-venv root), while C keeps `/opt/acp2-tools` for the
T-04 Node/pnpm/DSH/browser toolchain. The roots are not unified; the three role
interpreters still point into B02a's venv root.

Role interpreters are B02a handoff (commit `15767b9`, readiness
`integration_ready`): B02a's `role-interpreters.json` declares these exact paths
and asserts every module resolves inside the matching source
(`venvs/c` → `$ACP2_C_SOURCE`, `venvs/integration` → `$ACP2_INTEGRATION_SOURCE`,
`venvs/browser` → its own `sys.prefix`). C created the venvs during T-01
bootstrap; B02a verified them. Chromium is **not** installed (C/T-08 owns it and
B02a is not accepted on Chromium launch).

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

1. `ACP2_CONTRACTS` must be non-empty, and the adopted B00 evidence root must
   resolve (section 6), otherwise `2`.
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

`c_accept` accepts `approval-budget-live 600 --phase approval` (T-27's required
invocation). The leaf remains `<run_id>/approval`, but the harness normalizes
the written public phase to `experience:fixture` and records
`evidence_kind=fixture`, because the approval content is fixture-only (Node
module suite + digest matrix, no Controller/DSH/model/QC/LSF/browser process).
`--phase budget` stays `NOT_VERIFIED(b16_handoff)` and also records fixture
evidence; `--phase aggregate` stays `NOT_VERIFIED` while that child is
`NOT_VERIFIED`. A fixture-only scenario never claims a live phase, and
`--mode live` is a usage error (`3`) for it. See `harness-contract.md` §4.

## 6. Evidence-root resolution (adopted B00 baseline)

`c_accept` reads B00's committed `runtime/baseline-lock.json` — first
`$ACP2_CONTRACTS/runtime/baseline-lock.json` when present, otherwise
`$ACP2_BASELINE_LOCK` (the project-level contracts path). It resolves and
asserts:

1. Absolute `evidence_root` → must equal `$ACP2_EVIDENCE` exactly.
2. Project-relative `evidence_root` → `$ACP2_PROJECT_ROOT/<value>` (normalized)
   must equal `$ACP2_EVIDENCE` exactly.
3. `evidence_roots.C`, when present, must equal the resolved root + `/C`
   (resolved absolute or project-relative the same way).
4. On any mismatch `c_accept` prints the reason and returns `2`; it never fakes
   a PASS.
5. On success it exports `ACP2_EVIDENCE_BINDING=b00_project_root` and writes no
   provisional `not_verified` stamp.

Adopted values: `evidence_root=reports/acp2_implementation/bb45a46`,
`$ACP2_EVIDENCE=reports/acp2_implementation/bb45a46`,
`evidence_roots.C=reports/acp2_implementation/bb45a46/C` →
`reports/acp2_implementation/bb45a46/C`.
A missing lock fails closed with `2`. The old provisional root
`/opt/acp2-dev/evidence` is retained only as
`$ACP2_PROVISIONAL_EVIDENCE` documentation and is rejected.

Exercised branches: real lock + adopted root → proceeds to the harness check;
old provisional root → `2` evidence-root mismatch; lock copy without
`evidence_root` → `2`; relative lock against a wrong `ACP2_PROJECT_ROOT` → `2`;
wrong `evidence_roots.C` → `2`; absolute-value lock → proceeds to the harness
check; missing lock → `2`.

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

* B00 handoff adopted (commit `31e60cb`): `baseline-lock.json`,
  `manifest.json` and the absolute evidence root are verified; no stale B00
  handoff gate remains.
* B02a environment adopted (commit `15767b9`): tools root, data root, role
  interpreters and browser venv are `integration_ready`. `ACP2_TOOLS`
  intentionally diverges from B02a's venv root (section 2).
* `NOT_VERIFIED(harness_not_delivered)` — `scripts/acceptance/acp2_experience.py`
  is delivered by C T-06/T-07. At adoption time `c_accept` correctly returns `2`.
* `NOT_VERIFIED(chromium_not_installed)` — Chromium install/launch is C T-08;
  B02a only verified the browser venv can import playwright.
* `c_accept <scenario>` product PASS is **not** claimed here.
* No `route.json` is written by this todo; T-07 owns the formal route record.
