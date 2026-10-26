# tests/ — Test Suite

## OVERVIEW
pytest suite covering config loading, cccp QC/task/backends layers, ACP core/IO/backend/workflow modules, FastAPI, scheduler, remote LSF execution, NMR/DP4/DP5, the ACP 2.0 runtime/science plugin stack, and the `acp_services` platform layer. **490 `test_*.py`** (485 flat in `tests/` + 5 under `tests/benchmark/nmr/`; 500 top-level `.py` once helpers are counted). Prefix families: `test_acp_*` **170** (incl. 37 `test_acp_nmr_*`), `test_services_*` **69**, `test_acp2_*` **74**, `test_cccp_*` **47**. File counts are pinned; collected-test totals drift with parametrization and are not pinned here. Real-binary tests are gated behind `--run-slow`/`--run-integration` and collection-skipped by default; everything else mocks subprocess. Real-QC binary skips carry the literal `NOT_VERIFIED` (three-state rule: PASS / FAIL / NOT_VERIFIED) — a skipped real-QC test is never a green pass.

## STRUCTURE
```
tests/
├── __init__.py                    # Package marker (empty)
├── conftest.py                    # Autouse env cleanup + sample_config + requires_* markers + --run-slow gating
├── fixtures/                      # Real ORCA/xTB excerpts (orca_{opt,optfreq}_real_sections.txt…) + acp2/science/ (external_plugin/ plugin project + plugin_upgrade/{v1,v2}) + qc/, nmr/, irc/, structure_viewer/, science_products/, legacy manifests
├── baseline/                      # ⚠ refactor-evidence/ + reference/ = audit artifacts. EXCEPTIONS (live): recovery_fixtures/ (test_recovery_fixtures_smoke.py); refactor-evidence/capability-evidence.md (parsed by test_capability_evidence_table); services-evidence/ (20 files + contract_samples/) parsed by test_services_doc_sync.py / test_services_closure_record.py
├── benchmark/                     # Only nested subpackage: nmr/ benchmark harness + 5 test_*.py
├── _acp2_external_plugin_harness.py, _acp2_external_plugin_probe.py, _acp2_upgrade_plugin_probe.py  # Un-prefixed ACP2 plugin build/probe helpers — never collected
├── test_config.py, test_cccp_*.py # cccp: config merge, software, task cores (47 files: test_cccp_task_*/contracts/batch/isolation/plugin_{sdk,pilot}/science_products*/bounded_products…)
├── test_architecture_invariants.py, test_architecture_recheck_20261021.py  # 四层架构守护（唯一基元定义/依赖方向/能力证据表）+ 2026-10-21 recheck R01–R12 regressions
├── test_grep_gates_script.py      # scripts/check_grep_gates.py 四 pin（unique_run_scan 等）
├── test_ci_workflow_contract.py   # M0-02a CI contract：matrix/前端/RPH package/conformance skeleton/markers/wall-clock
├── test_acp2_environment_readiness.py  # M0-10 三态环境 readiness、secret redaction、只读集群/远程探测门控
├── test_qc_interfaces_*.py        # ORCA/CREST/xTB/ISOSTAT interface parsing (inline output strings + real fixture)
├── test_acp_*.py                  # ACP module tests, 170 files (catalog, cli, backends, workflows, api, scheduler, intake, io, pes/batch/irc/tsmode/electronic-state…)
├── test_acp2_*.py                 # ACP 2.0, 74 files: runtime_{adapter_registry,adapter_upgrade,approvals,budget,admission,artifact,control_recovery,event_recovery,multitenant,multiproject,persistent_idempotency,mcp_backends,mcp_process,migration,recovery_runbook,science_binding,science_handoff,command_identity} / science_{recipe,registry,plugin_registry,plugin_gate,evidence_contract,provenance,schema,task_binding,inventory,installed_package,reaction_contract,stationary_point,energy_comparability,nmr_binding,cross_domain,selectivity,h4_gate,p1_gate,h6_gate,h7_gate} / experience_{budget_proxy,harness,research_loop,observations} / mcp_{sdk_contract(−m contract + _acp2_mcp_live_harness helper),research_handshake} / rph_install_surface / api/v1/v2 science routes, science_projection, research_read_routes/resource_key, controller_client_recover, preflight_fingerprint, status_projection_verified_facts / environment_readiness
├── test_services_*.py             # acp_services 平台层 + 收口门, 69 files: attempt_recovery/cgroup_attempt_recovery/standalone_recipe/packaging_roles/release_prep/worker_bootstrap/worker_contract/doc_sync/closure_record…
├── test_acp_nmr_*.py              # NMR/DP4/DP5: assignment, enumerate, equivalence, fchl, io, probability, scaling, spectra, runner (37 files)
├── test_acp_*_browser.py, test_frontend_*_browser.py  # Playwright: 4 test_frontend_*_browser + test_frontend_interaction + 2 acp_*_browser (importorskip + mark.slow)
├── test_remote_phase*.py          # Remote LSF execution (7 files; mock paramiko: FakeSFTPFile/FakeSFTPClient)
└── test_local_cleanup.py          # Scheduler local disk-cleanup retention
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Fixtures & markers | `conftest.py` | `_clean_env_vars` (autouse, deletes all CONFSEARCH_*), `sample_config`, `requires_orca/crest/xtb/isostat/shermo/censo` skipif, `NOT_VERIFIED`/`real_qc_skip_reason` (three-state evidence rule), `--run-slow`/`--run-integration` |
| Config testing | `test_config.py` | `_merge_configs`, `_apply_env_overrides`, `_validate_config` |
| Executable resolution | `test_cccp_software.py` | `resolve_executable`/`discover_all` (no execution, logic only) |
| ORCA parsing | `test_qc_interfaces_orca.py` | Inline output strings + `fixtures/orca_optfreq_real_sections.txt` (guards freq parser against format regressions) |
| Simple workflows | `test_acp_workflows_simple.py` | 69 tests — real CLI subprocess runs + input block generation (no longer largest) |
| xTB-MD→CENSO→DFT | `test_acp_workflows_xtbmd_censo_energy.py` | 54 tests, tmp_path pipeline harness |
| CENSO acceptance | `test_acp_censo_p5_acceptance.py` | 48 tests, BLAS/OpenMP env pinning, `fake_run` subprocess patch |
| Energy workflow | `test_acp_workflows_energy.py` | rank1 vs full-ensemble, Boltzmann weights, `--levels` |
| NMR/DP4/DP5 | `test_acp_nmr_*.py` | Module-level; `test_acp_nmr_probability.py` covers compute_dp4/dp5; `test_acp_nmr_qc_smoke.py` = level-1 real-QC skeleton gated by `requires_orca/crest/censo` |
| API | `test_acp_api_v1.py` | FastAPI TestClient with ACP_RUN_ROOT→tmp_path |
| Remote LSF | `test_remote_phase{1..6}.py` + `test_remote_phase1_integration.py` | Mock paramiko; phase1_integration is `@pytest.mark.integration` |
| ACP 2.0 runtime | `test_acp2_runtime_*.py` | 23 files — adapter registry/upgrade, approvals, budget, admission, control/event recovery, multitenancy, MCP backends/process, migration, science binding/handoff |
| ACP 2.0 science | `test_acp2_science_*.py` | 32 files — recipe/registry/plugin gates, evidence/provenance/schema, H4/P1/H6/H7 gates, domain contracts |
| External plugin fixture | `fixtures/acp2/science/external_plugin/` + `_acp2_external_plugin_{harness,probe}.py` | `build_backend.py` + `cccp_science_plugin.json`; upgrade pair in `plugin_upgrade/{v1,v2}` via `_acp2_upgrade_plugin_probe.py` |
| Services guards | `test_services_doc_sync.py`, `test_services_closure_record.py` | Parse `baseline/services-evidence/{migration_ledger,public_shim_inventory,handoff_registry}.md`; closure record three-state |
| Architecture recheck | `test_architecture_recheck_20261021.py` | R01–R12 pins from `reports/architecture_recheck_20261021/REPORT.md` |
| Largest files | `test_acp_structure_viewer.py` (150 test fns), `test_acp_api_structure_viewer.py` (118) | Per-surface density — add to domain files instead |
| Browser | `test_frontend_*_browser.py` + `test_frontend_interaction.py` | `pytest.importorskip("playwright")`, `pytestmark = pytest.mark.slow`; CI py3.11 only |

## CONVENTIONS
- **Naming**: flat `test_*.py`, domain-prefixed (`test_acp_*` 170, `test_services_*` 69, `test_acp2_*` 66, `test_cccp_*` 47, `test_qc_interfaces_*`, `test_remote_phase*`); the only nested package is `tests/benchmark/nmr/` (5 files). Config in `pyproject.toml` (`testpaths=["tests"]`, `python_files=["test_*.py"]`, markers `slow`/`integration`/`requires_*`) — no pytest.ini
- **Assertions**: raw `assert` (no unittest-style `self.assert*`)
- **Mocking**: `unittest.mock.patch` + `MagicMock` (80 files); `monkeypatch` (186 files) for env/config — **NO pytest-mock/mocker anywhere**
- **Subprocess mocking idiom**: `patch("cccp.qc.interfaces.censo.subprocess.run", side_effect=fake_run)` — patch at the interface-module path, never bare `subprocess.run` unless the file under test uses it
- **Real-binary gating**: `@pytest.mark.slow`/`@pytest.mark.integration` (59 deployments across 18 files, browser/slow suites included) — skipped at collection unless `--run-slow`/`--run-integration`
- **Binary-gated skipif**: `@requires_orca/crest/xtb/isostat/shermo/censo` imported as `from tests.conftest import requires_orca`; detection = `shutil.which` over `CONFSEARCH_<NAME>_PATH` (env override) or the bare binary name at conftest import. Skip reasons carry the literal `NOT_VERIFIED` via `real_qc_skip_reason` (three-state rule: a skipped real-QC test is never a green pass). To actually run real-QC tests, export the configured paths first (`export CONFSEARCH_ORCA_PATH=... CONFSEARCH_CREST_PATH=... CONFSEARCH_XTB_PATH=... CONFSEARCH_CENSO_PATH=...`) — `~/.cccp.yaml` binaries are not on `PATH` — then pass `--run-slow --run-integration`
- **CLI smoke tests**: real `subprocess.run([sys.executable, "-m", "acp.cli", …])` (no mocks); retired subcommands asserted intercepted
- **Browser tests**: `pytest.importorskip("playwright")` + `pytestmark = pytest.mark.slow`; need `pip install -e '.[browser]'` + `python -m playwright install chromium`; without playwright they record `NOT_VERIFIED`
- **CI** (`.github/workflows/ci.yml`): main job py3.10/3.11/3.12 = `compileall src tests` + `pytest tests/ -m "not slow and not contract" -q` (`--strict-markers` default-on); install `pip install -e '.[dev,api,remote]'` + `nmrglue` (qml not installable in CI). Browser steps run only on py3.11. Mandatory `frontend-module-tests` job runs a non-empty `run_all.js` suite. Separate `rph-detect` + `rph` jobs (Node 22.19.0, pnpm 11.28.5) run only when `rph/pnpm-lock.yaml` exists: `install --frozen-lockfile` → build → test → check → **package**, then upload the full offline install surface `rph/dist-packages/*` (manifests/lockfile/checksums, not only tarballs). `mcp-conformance` (Python 3.11 + Node 22.19) still runs a non-empty placeholder selection; the real `contract`-marked suite now exists (`tests/test_acp2_mcp_sdk_contract.py`, needs Node 22.19 + DSH harness) but the lane's rewiring to `-m contract` remains M0-02b. Jobs record wall-clock; split long legs instead of skipping them.
- **tmp_path** pervasive (10,918 hits/358 files); inline QC output strings as module constants

## ANTI-PATTERNS
- **Flat layout, no subpackages** — 481 files in `tests/`; only nesting is `tests/benchmark/nmr/` (5 files). Per-area grouping is by filename prefix only
- **`baseline/refactor-evidence/` + `baseline/reference/` look like fixtures but are audit history** — only `refactor-evidence/capability-evidence.md` (parsed by test_capability_evidence_table) and `recovery_fixtures/` are live; do not wire the rest into conftest
- **`baseline/services-evidence/` is both audit trail and machine-parsed gate** — 20 files + `contract_samples/`; `migration_ledger.md`, `public_shim_inventory.md`, `handoff_registry.md` are parsed by `test_services_doc_sync.py`. Never rewrite a `NOT_VERIFIED` status token to `PASS`
- **Un-prefixed `_acp2_*` helpers must stay un-prefixed** — `_acp2_external_plugin_harness.py`/`_acp2_external_plugin_probe.py`/`_acp2_upgrade_plugin_probe.py` build plugin fixtures; renaming to `test_*` would collect build scripts
- **Duplicate `sample_config` fixtures** — `test_acp_workflows_energy.py` and `test_acp_workflows_ensemble.py` define local ones shadowing conftest's
- **Standalone executables**: 14 files carry `if __name__ == "__main__"` (6 `test_remote_phase*` + 2 `_acp2_*` probes + CLI/scheduler helpers) — tests double as scripts, runnable via `PYTHONPATH=src python3 tests/test_remote_phase1.py`
