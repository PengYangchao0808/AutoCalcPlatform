# tests/ — Test Suite

## OVERVIEW
pytest test suite covering config loading, ACP core/IO/backend/workflow modules, engine routing, and legacy functional tests. conftest.py provides autouse env-var cleanup and shared fixtures.

## STRUCTURE
```
tests/
├── __init__.py                    # Package marker (empty)
├── conftest.py                    # Autouse env cleanup + sample_config fixture
├── test_config.py                 # Config merge/validate/apply (legacy cccp)
├── test_cccp.py       # Original monolithic functional tests
├── test_engine_routing.py         # Engine routing with mocks
├── test_acp_backends.py           # ACP backend capability + delegation tests
├── test_acp_cli.py                # ACP CLI smoke tests
├── test_acp_config.py             # ACP config facade tests
├── test_acp_core_models.py        # ACP core model tests
├── test_acp_io.py                 # ACP IO structure reader tests
├── test_acp_workflows_conformer.py # ACP conformer workflow tests
├── test_core_workflow.py          # Core workflow engine tests
└── baseline/                      # Reference configs for integration tests
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Config testing | `test_config.py` | `_merge_configs`, `_apply_env_overrides`, `_validate_config` |
| Functional tests | `test_cccp.py` | Original bugfix regression tests |
| Engine routing | `test_engine_routing.py` | `patch` + `MagicMock` for `GaussianInterface`/`ORCAInterface` |
| ACP backends | `test_acp_backends.py` | Capability checks, delegation, registry |
| ACP config | `test_acp_config.py` | Facade imports, env override |
| ACP workflows | `test_acp_workflows_conformer.py` | Stage function + protocol tests |

## CONVENTIONS
- All tests via `pytest` (no pytest.ini — config in `pyproject.toml`)
- Standard `assert` (no `self.assert*` — not unittest style)
- Python path: `from cccp.*` imports (package must be installed)
- `test_engine_routing.py` uses `unittest.mock.patch` and `MagicMock`
- Tests run: `pytest tests/ -v`

## ANTI-PATTERNS
- **No assertions imported** — uses raw `assert` (acceptable, but no `pytest.raises` context manager in places where it belongs)
- **test_engine_routing.py monolithic** — 806 lines, dense mocking logic in single file
- **test_cccp.py "legacy"** — 319 lines of unstructured assertions, no mocks
- **No smoke/integration tests** — all tests are unit-level; no full-pipeline end-to-end tests
