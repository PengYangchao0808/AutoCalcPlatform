# plugin/ — Science Plugin Ecosystem (ACP 2.0, 2026-10)

## ACP3 双人协作（2026-11-04）

读[架构总纲 §10](../../../docs/reference/next-change/ACP_3.0_Architecture_20261031.md)。B 主写统一科学插件生命周期、Provider 锁、共享 Renderer/Parser/Validator；A 消费目录与诊断，不另建化学插件注册总线。现行 v1 compute 约束与 metadata-only discovery 不因 V3 文档而解除；动态 compute/Standalone/原生执行按 P0—P4 切片和兼容测试交付。

本节是开发目标，不声明新增能力已交付；现行依赖/执行/安全护栏继续约束实现。


## OVERVIEW
Third-party science plugin contract (**8 .py, 3.7k lines**). Discovery is metadata-only (the plugin module is **never imported** during discovery), enablement is **disabled-by-default** (`PLUGIN_ENABLEMENT_POLICY`), execution stays at the single execution station `cccp.calculation` (`BUILTIN_EXECUTION_STATION`) — plugins extend capabilities, never the subprocess layer. Entry-point group: **`cccp.science_plugins`** (v1); manifest file `cccp_science_plugin.json` read from the installed **distribution** (`ep.dist`), JSON only.

## STRUCTURE
```
plugin/
├── metadata.py        # 1060 L: PluginMetadata (frozen discovery record), PluginCapability (kind: compute→TaskKind | pure_analysis), PluginTrustLevel, PluginLicensePolicy, PluginFixtureEvidence, PluginRecipeRef, PluginValidatorRef (check./software./human.), ApiCompatibility, ScienceSoftwareVersion, ResourceRequirements, ErrorSemantics, BuiltinIdentity, verify_fixture_evidence, api_version_in_range
├── discovery.py       # discover_plugins (explicit candidates), scan_science_plugin_entry_points, discover_science_plugins, PluginCatalog, PluginStatus (4 projections described/installed/enabled/usable), EntryPointPluginLoader, builtin_catalog, BUILTIN_RESERVED_NAMES, PLUGIN_ENABLEMENT_POLICY="disabled-by-default"
├── host.py            # PluginHost — metadata-only construction; enable() then load(); NO import-string/shell dispatch
├── sdk.py             # 1056 L: invoke_capability, validate_result_contract, lock_plugin→PluginVersionDigest (sha256 over identity+resolved dep versions), PluginIdentityScope, PluginOutputSpec (units/atom_mapping), validate_declared_recipe/validators, check_cancelled/check_deadline, failure_report→PluginRollbackGuidance (silent_substitution_allowed always False), SDK_DEPRECATIONS
├── errors.py          # 14 typed rejections (PluginNameConflictError, PluginCapabilityConflictError, PluginLicenseError, PluginVersionMismatchError, PluginFixtureIntegrityError, PluginContractViolationError, PluginDependencyUnavailableError, PluginTimeoutError, …) — all fail closed BEFORE launch
├── readonly_parser.py # pilot capability wrapping cccp.analysis.orca_parser (OrcaReadonlyParserPlugin)
├── example_xyz.py     # SDK example read-only XYZ parser returning built-in TaskResult
└── __init__.py        # pure re-export
```

## WHERE TO LOOK
| Concern | Location |
|---|---|
| Add a plugin (authoring) | `sdk.py` + `example_xyz.py` as template; `tests/fixtures/acp2/science/external_plugin/` = reference external distribution (pyproject + build_backend + manifest + fixtures/native_singlepoint.acp2n) |
| Discovery / catalog | `discovery.py` — entry-point scan + explicit candidates; 4 independent status projections |
| Trust & conflicts | `errors.py` (fail-closed pre-launch) + `metadata.py::PluginTrustLevel` + `discovery.BUILTIN_RESERVED_NAMES` |
| Version locking | `sdk.py::lock_plugin` → `PluginVersionDigest` |
| Upgrade path | fixture pair `tests/fixtures/acp2/science/plugin_upgrade/{v1,v2}` + `tests/_acp2_upgrade_plugin_probe.py` |

## CONVENTIONS
- **Metadata-only discovery**: manifest `cccp_science_plugin.json` from the distribution; the plugin module is imported only after `host.enable()` → `host.load()`; `default_enabled: false` is declarative only — the host never auto-enables
- **No shell/import-string dispatch** in `host.py`; capabilities bind to `TaskKind`/pure analysis, executed via `cccp.calculation`
- **Fail closed before launch**: name/capability/license/version/fixture/dependency errors all raise typed errors in `errors.py` before any execution
- **Rollback never silently substitutes**: `silent_substitution_allowed` is always `False`
- API versions: `PLUGIN_API_VERSION=1`, `PLUGIN_SCIENCE_VERSION=1`, `PLUGIN_SCHEMA_VERSION=1`

## ANTI-PATTERNS
- **Importing a plugin during discovery/catalog scans** — forbidden (metadata only)
- **Auto-enabling** a discovered plugin — policy is disabled-by-default, allow-list trust (`acp_services.runtime.capability_registry` on the platform side mirrors this)
- **Capabilities colliding with `BUILTIN_RESERVED_NAMES`** — conflict error, not shadowing
- **Plugin executing subprocesses directly** — execution goes through the calculation layer (`BUILTIN_EXECUTION_STATION`)

## GUARD TESTS
`tests/test_cccp_plugin_pilot.py` (T44 metadata/discovery/load frozen boundaries) · `tests/test_cccp_plugin_sdk.py` (T45 SDK boundary classes) · `tests/test_acp2_science_plugin_registry.py` (A-21b metadata-only scan) · `tests/test_acp2_science_plugin_gate.py` (A-23 conflicts/upgrade/historical read-only) · `tests/test_acp2_science_external_plugin_fixture.py` (A-22a offline wheel-built external plugin)
