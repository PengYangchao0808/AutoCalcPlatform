# tests/ — Test Suite

## OVERVIEW
pytest suite covering config loading, ACP core/IO/backend/workflow modules, FastAPI, scheduler, remote LSF execution, and NMR/DP4/DP5. **60 test files, 1047 test functions** (conftest excluded). Real-binary tests are gated behind `--run-slow`/`--run-integration` and collection-skipped by default; everything else mocks subprocess.

## STRUCTURE
```
tests/
├── __init__.py                    # Package marker (empty)
├── conftest.py                    # Autouse env cleanup + sample_config + requires_* markers + --run-slow gating (129 L)
├── fixtures/                      # Verbatim real ORCA 5.x Opt Freq excerpt (orca_optfreq_real_sections.txt)
├── baseline/                      # ⚠ Audit artifact, NOT test fixtures: SHA256SUMS.txt + reference/ configs from 2026-05-25 post-migration verification. No test reads it.
├── test_config.py, test_cccp_software.py     # Legacy cccp: config merge, executable resolution
├── test_engine_routing.py         # Legacy engine routing w/ patch+MagicMock (806 L monolithic)
├── test_qc_interfaces_*.py        # ORCA/CREST/xTB/ISOSTAT interface parsing (inline output strings + real fixture)
├── test_acp_*.py                  # ACP module tests (catalog, cli, backends, workflows, api, scheduler, intake, io)
├── test_acp_nmr_*.py              # NMR/DP4/DP5: assignment, enumerate, equivalence, fchl, io, probability, scaling, spectra, runner
├── test_remote_phase{1..6}.py     # Remote LSF execution (mock paramiko: FakeSFTPFile/FakeSFTPClient)
└── test_local_cleanup.py          # Scheduler local disk-cleanup retention
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Fixtures & markers | `conftest.py` | `_clean_env_vars` (autouse, deletes all CONFSEARCH_*), `sample_config`, `requires_orca/crest/xtb/isostat/shermo` skipif, `--run-slow`/`--run-integration` |
| Config testing | `test_config.py` | `_merge_configs`, `_apply_env_overrides`, `_validate_config` |
| Executable resolution | `test_cccp_software.py` | `resolve_executable`/`discover_all` (no execution, logic only) |
| ORCA parsing | `test_qc_interfaces_orca.py` | Inline output strings + `fixtures/orca_optfreq_real_sections.txt` (guards freq parser against format regressions) |
| Simple workflows | `test_acp_workflows_simple.py` | 66 tests — largest; real CLI subprocess runs + input block generation |
| xTB-MD→CENSO→DFT | `test_acp_workflows_xtbmd_censo_energy.py` | 52 tests, tmp_path pipeline harness |
| CENSO acceptance | `test_acp_censo_p5_acceptance.py` | 48 tests, BLAS/OpenMP env pinning, `fake_run` subprocess patch |
| Energy workflow | `test_acp_workflows_energy.py` | rank1 vs full-ensemble, Boltzmann weights, `--levels` |
| NMR/DP4/DP5 | `test_acp_nmr_*.py` (10 files) | Module-level; `test_acp_nmr_probability.py` covers compute_dp4/dp5 |
| API | `test_acp_api_v1.py` | FastAPI TestClient with ACP_RUN_ROOT→tmp_path |
| Remote LSF | `test_remote_phase{1..6}.py` | Mock paramiko; phase1_integration is `@pytest.mark.integration` |

## CONVENTIONS
- **Naming**: flat `test_*.py`, domain-prefixed (`test_acp_*`, `test_qc_interfaces_*`, `test_remote_phase*`). Config in `pyproject.toml` (`testpaths=["tests"]`) — no pytest.ini
- **Assertions**: raw `assert` (no unittest-style `self.assert*`)
- **Mocking**: `unittest.mock.patch` + `MagicMock` (24 files) — **NO pytest-mock/mocker anywhere**; `monkeypatch` (11 files) for env/config
- **Subprocess mocking idiom**: `patch("cccp.qc.interfaces.censo.subprocess.run", side_effect=fake_run)` — patch at the interface-module path, never bare `subprocess.run` unless the file under test uses it
- **Real-binary gating**: `@pytest.mark.slow`/`@pytest.mark.integration` (8 tests across backends/interfaces) — skipped at collection unless `--run-slow`/`--run-integration`
- **Binary-gated skipif**: `@requires_orca` etc. imported as `from tests.conftest import requires_orca`; detection = `shutil.which` at conftest import
- **CLI smoke tests**: real `subprocess.run` on the installed `acp` entrypoint (no mocks)
- **tmp_path** pervasive (875 hits/37 files); inline QC output strings as module constants

## ANTI-PATTERNS
- **Flat layout, no subpackages** — 60 files in one dir; per-area grouping is by filename prefix only
- **`baseline/` looks like fixtures but is audit history** — no test references it; do not wire it into conftest
- **`test_engine_routing.py` monolithic** — 806 lines of dense `patch`/`MagicMock` in a single file
- **Duplicate `sample_config` fixtures** — some files (e.g. energy workflow) define local ones shadowing conftest's
- **Standalone executables**: 3 `main()` runners (`test_remote_phase1/2`, `phase1_integration`) + `if __name__ == "__main__"` blocks in 6 more files (mostly `pytest.main([__file__, "-v"])` re-invocation) — tests double as scripts, runnable via `PYTHONPATH=src python3 tests/test_remote_phase1.py`
