# H2 env reuse — `acceptance-env-c.sh` variables and fresh-shell re-check

Part of the H2 C-side closeout (T-14). Companion receipt: `H2.json` in this
directory. Evidence captures (bound into `H2.json` artifacts with sha256):

- Variable list: `<repo>/reports/acp2_implementation/bb45a46/C/h2/env-reuse-variables.txt`
- New-shell re-check: `<repo>/reports/acp2_implementation/bb45a46/C/h2/env-reuse-newshell-check.txt`

## Variable list (no credentials)

`acceptance-env-c.sh` defines no credential-bearing variables (asserted by
name scan; no `*SECRET*`/`*TOKEN*`/`*PASSWORD*`/`*API_KEY*`/`*CREDENTIAL*`
match). The delivered variables are:

| Variable | Value (fixed roots/versions/revisions) |
|---|---|
| `ACP2_ALL_SCENARIOS` | 36 frozen scenario IDs (P0–P5, verbatim registry) |
| `ACP2_LIVE_SCENARIOS` | 20 live scenario IDs |
| `ACP2_A_SOURCE` | `<acp2-dev>/A` |
| `ACP2_BASELINE_LOCK` | `<repo>/docs/reference/next-change/acp2-contracts/runtime/baseline-lock.json` |
| `ACP2_BROWSER_PYTHON` | `<acp2-dev>/venvs/browser/bin/python` |
| `ACP2_CONTRACTS` | `<acp2-dev>/C/docs/reference/next-change/acp2-contracts` |
| `ACP2_C_PYTHON` | `<acp2-dev>/venvs/c/bin/python` |
| `ACP2_C_SOURCE` | `<acp2-dev>/C` |
| `ACP2_DATA_ROOT` | `<acp2-dev>/data` |
| `ACP2_DSH_BUILD_EVIDENCE` | `$ACP2_EVIDENCE/C/node-dsh-install/20261022T112416Z-c04/07-build-and-version.txt` |
| `ACP2_DSH_BUILD_STATUS` / `_REASON` | `built` / empty |
| `ACP2_DSH_HOME` | `<acp2-dev>/data/dsh-home` |
| `ACP2_DSH_LOADER` | `file://<server-root>/acp2-tools/deepseek-harness/node_modules/.pnpm/tsx@4.23.13/node_modules/tsx/dist/esm/index.mjs` |
| `ACP2_DSH_OVERLAYS` | `$ACP2_CONTRACTS/experience/dsh-overlays` |
| `ACP2_DSH_REV` | `687f282e8625c6b8d11ff58211a90524be2e4317` |
| `ACP2_DSH_SOURCE` | `<server-root>/acp2-tools/deepseek-harness` |
| `ACP2_DSH_WORKSPACE` | `<acp2-dev>/data/research-workspace` |
| `ACP2_EVIDENCE` | `<repo>/reports/acp2_implementation/bb45a46` |
| `ACP2_INTEGRATION_PYTHON` | `<acp2-dev>/venvs/integration/bin/python` |
| `ACP2_INTEGRATION_SOURCE` | `<acp2-dev>/integration` |
| `ACP2_NODE_BIN` / `ACP2_NODE_HOME` / `ACP2_NODE_VERSION` | `.../node-v22.19.0-linux-x64/{bin/node,,}` / `22.19.0` |
| `ACP2_PNPM_BIN` / `ACP2_PNPM_VERSION` | `<server-root>/acp2-tools/pnpm/node_modules/.bin/pnpm` / `11.28.5` |
| `ACP2_PROJECT_ROOT` | `<repo>` |
| `ACP2_PROVISIONAL_EVIDENCE` | `<acp2-dev>/evidence` (historical alias only) |
| `ACP2_PYTHON` | `<acp2-dev>/venvs/c/bin/python` |
| `ACP2_SOURCE` | `<acp2-dev>/integration` (equals `ACP2_INTEGRATION_SOURCE`) |
| `ACP2_TOOLS` | `<server-root>/acp2-tools` |

The env file also delivers the shell functions `c_accept` and `route_select`
(the single routing implementation); both resolve as functions in a fresh
shell.

## Fresh-shell re-check (re-run at closeout, exit 0)

Executed in a new `bash --noprofile --norc` that only sources
`$ACP2_DATA_ROOT/acceptance-env-c.sh`:

```text
command -v node   -> <server-root>/acp2-tools/node-v22.19.0-linux-x64/bin/node   (v22.19.0)
command -v pnpm   -> <server-root>/acp2-tools/pnpm/node_modules/.bin/pnpm        (11.28.5)
command -v python -> /usr/bin/python                                          (3.11.13 host)
role interpreters: ACP2_C_PYTHON / ACP2_INTEGRATION_PYTHON / ACP2_BROWSER_PYTHON all non-empty
PASS: acp imported from <acp2-dev>/C/src/acp/__init__.py          (C role)
PASS: acp imported from <acp2-dev>/integration/src/acp/__init__.py (integration role)
PASS: playwright imported from .../venvs/browser/lib64/python3.11/site-packages/playwright/__init__.py (under sys.prefix)
OVERALL: PASS (exit 0)
```

`python` on `PATH` resolves to the host interpreter inside the naked shell;
the acceptance harness always invokes the role interpreters by absolute path
(`ACP2_PYTHON` for fixture work, `ACP2_INTEGRATION_PYTHON` for live work), so
this is consistent with every recorded `command` in `H2.json`.
