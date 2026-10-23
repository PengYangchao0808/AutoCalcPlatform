# B25b standalone/Lab install evidence (2026-10-23)

- Branch/HEAD at start: `feat/frontend-plan1-closure` @ `672b651`
- Scenario receipt: `package-install-main-20261023T131008Z-1633496-80a5221a.json` — result `PASS`, exit `0`
- Summary: `summary.json` (validated by `--check-only`)
- Clean venvs/data root: `/tmp/opencode/b25b` (outside the repository); harness venvs were removed after the run.

## Clean install and digests

| role | wheel | install | module location | version |
|---|---|---|---|---|
| cccp | `cccp-0.3.0-py3-none-any.whl` | exit `0` | venv `site-packages` | `0.3.0` |
| worker | `acp_worker-0.3.0-py3-none-any.whl` | exit `0` | venv `site-packages` | `0.3.0` |
| web | `acp_web-0.3.0-py3-none-any.whl` | exit `0` | venv `site-packages` | `0.3.0` |
| root | `acp-0.3.0-py3-none-any.whl` | exit `0` | venv `site-packages` | `0.3.0` |

All release-manifest digests matched; root wheel digest recorded in `release-digests.json`.

## Operation smoke

- CCCP controlled singlepoint ran in the cccp-only venv with no Controller and no `acp`/`acp_services` import (`cccp-sample.json`).
- Standalone CLI `acp --help` passed in the web and root role venvs (`cli-and-compat.json`).
- Lab server started on isolated loopback port `51245` with an isolated `ACP_RUN_ROOT`; `/api/health` returned `status=ok` (`lab-health.json`).
- stdio MCP `initialize` negotiated `2025-06-18` and `tools/list` returned the eight P1 tools (`mcp-tools.json`).
- Compatibility imports (`acp.calculations.primitives`, `acp.backends`, `acp.compat.legacy.manifests`) and the store/contracts identity forwarders held from the installed wheel (`cli-and-compat.json`).

## Assets

- LICENSE in each dist-info; `3Dmol-LICENSE` with the web assets; `NOTICE.md` + `LICENSE-DP5` + DP5 model assets in the ccp package; `py.typed`; `cccp.plugin.sdk`; API schema modules; AGENTS docs.
- Sample config, acceptance/MCP schema, standalone recipe and MCP client matrix published under `release/docs/`.
- `requirements-node.txt` unchanged; B25b added no `acp run` runtime import (`version-sync.json`).

## A/C raw evidence aggregate

- A-27b: `PASS` — `<server-root>/acp2-evidence/a-science-20261022/A/A-27/a27b-20261023T042054Z` (raw evidence present; scientific/experience sign-off stays with its owner).
- A-28: `PASS` — `<server-root>/acp2-evidence/a-science-20261022/A/A-28/20261023T044051Z` (raw evidence present; scientific/experience sign-off stays with its owner).
- C33: `NOT_VERIFIED` — `<acp2-dev>/evidence/C/clean-install-live` (evidence absent at the registered path).
- C34: `NOT_VERIFIED` — `<acp2-dev>/evidence/C/operator-runbook-live` (evidence absent at the registered path).

## Static-asset mount (B-req-06)

- `NOT_VERIFIED(c_static_assets_absent)`; expected manifest `<repo>/docs/reference/next-change/acp2-contracts/experience/static-assets.manifest.json`; mount `/experience/<release_id>/`.
- No mount, directory listing or digest behaviour was fabricated because C has not published the manifest.

## Release correction

- B25a `acp_web` wheel failed at Lab startup: `OriginPolicy.__init__()` did not accept `public_origin` although `server.py` passed it. This was the same defect behind B25a `web_http_role_probe: NOT_VERIFIED(candidate_origin_policy_mismatch)`.
- B25b rebuilt the four 0.3.0 distributions from clean HEAD + the already-present `boundary.py` `public_origin` fix. Original artifacts: `release-b25a-original/`, `root-build-b25a-original/`; corrected digests: `release/release-manifest.json`, `root-build/acp-0.3.0-py3-none-any.whl`.
- No science code, tool license or version number changed; only startup parameter compatibility.

## Verification

- `pytest tests/test_services_doc_sync.py tests/test_services_standalone_recipe.py tests/test_packaging_assets.py tests/test_acp2_runtime_acceptance_harness.py -q` → 50 passed.
- `ruff check` / `ruff format --check` on changed Python files → PASS.
- `validate_acp2_contracts.py` → VALID.
- `acp2_runtime.py --check-only summary.json` → VALID.

## Cleanup / production

- Harness stopped its Lab/MCP processes and removed its venvs; no production `acp.service` restart; no access to `/var/lib/acp/runs`.
- Remaining `/tmp/opencode/b25b` inspection venvs were removed after this evidence was written.
