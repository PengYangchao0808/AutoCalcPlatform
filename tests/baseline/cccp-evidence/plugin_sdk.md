# C09-s2 / T45 — Plugin SDK, example and contract test suite (tracked)

- **Owner:** C 路科学负责人 · **Plan:** `.omo/plans/acp-parallel-plan-3-cccp.md` todo 45
- **Main plan:** `docs/reference/next-change/ACP_Parallel_Plan_3_CCCP_20261018.md` §C09 (关联 §31.1, OS-09, M1/M4)
- **Extends:** C09-s1 / T44 (`tests/baseline/cccp-evidence/plugin_pilot_spec.md`, commit `637d8d3`)
- **Write authority:** `C直写` — `src/cccp/plugin/**` (registered in `write_registry.md` §4.8); this spec + tests are C-owned tracked evidence.
- **Base snapshot HEAD at spec time:** `3cfd43975d735ef7500dc05553139f2332d08197` (shared dirty worktree; only this slice's files are staged in the T45 commit).
- **Status:** SDK surface + example plugin + 7-class contract suite + deprecation policy delivered. External distribution entry-point discovery/installation remains out of scope (T46 only consumes the digest fields).

## 1. Invariants (unchanged from T44, now SDK-enforced)

1. **Built-in task layer is the only execution station.** The SDK consumes the built-in
   `TaskRequest` envelope, runs with the built-in `TaskContext`, and returns the built-in
   `TaskResult` envelope. There is no second task chain, no plan/scheduler type, and no
   capability name may equal a `TaskKind` value or a `BUILTIN_RESERVED_NAMES` entry.
2. **Plugins are disabled by default.** Discovery is metadata-only; `PluginHost.enable` is the
   only enable path; `load` is the only loader path (T44 boundaries untouched).
3. **No silent substitution.** A plugin failure never yields a built-in result under the
   plugin identity. `PluginFailureReport` carries an explicit operator action
   (`PluginRollbackGuidance.silent_substitution_allowed is False`).
4. **Discovery/版本摘要 are read-only and side-effect free.** Dependency availability uses
   `importlib.util.find_spec`, never an install or an import of the dependency.
5. **No reverse dependency.** `src/cccp/**` never imports the platform package
   (slice guard static + acp-free import probe).

## 2. SDK API surface (`cccp.plugin.sdk`, re-exported by `cccp.plugin`)

| Symbol | Kind | Purpose |
|---|---|---|
| `PLUGIN_SDK_VERSION` / `PLUGIN_SDK_COMPATIBILITY` | int / str | SDK contract version (tracks `PLUGIN_API_VERSION`). |
| `PluginIdentityScope` | enum | `release` / `step` / `product` consumption point. |
| `PluginVersionDigest` | frozen dataclass | Deterministic sha256 identity over metadata + resolved dependencies. |
| `lock_plugin(metadata, *, resolved_dependencies=None)` | func | Compatibility check + digest construction (raises typed mismatch/dependency errors). |
| `resolve_dependency_versions(metadata)` | func | Availability + version resolution without install/import. |
| `require_compatible_plugin(metadata, *, host_api_version=...)` | func | Typed API-range rejection. |
| `ResolvedDependency` | frozen dataclass | `name` / `version` / `source` (`distribution`/`stdlib`/`module`/`provided`). |
| `PluginOutputSpec` | frozen dataclass | Declared `units`, `atom_mapping` (`input_order`/`atom_uid`), `allows_partial`. |
| `PluginCapabilityHandler` | runtime Protocol | `capability_names()` + `output_spec_for()` + `invoke(request, *, context)`. |
| `invoke_capability(host, name, *, capability, request, context=None, clock=...)` | func | Load + run + contract-validate one capability; typed failures only. |
| `validate_result_contract(result, *, metadata, capability, request, output_spec)` | func | Envelope/units/mapping/partial checks. |
| `check_cancelled(context)` / `check_deadline(name, started_at, context, *, clock=...)` | funcs | Cooperative control helpers for handlers. |
| `PluginRollbackAction` / `PluginRollbackGuidance` / `PluginFailureReport` / `failure_report(error, *, plugin_name=None)` | types/func | Typed failure → explicit rollback/retry decision. |
| `PluginSdkDeprecation` / `SDK_DEPRECATIONS` / `SDK_DEPRECATION_WINDOW_MINOR_RELEASES` | types/data | Tracked deprecation registry + window. |

Built-in envelope types (`TaskRequest`, `TaskContext`, `TaskResult`, `TaskKind`,
`ErrorKind`, `Provenance`, `MethodSpec`, `StructureInput`, `TaskResources`,
`TaskCancelledError`, `TaskInputError`, `resolve_context`) are **re-exported** so a plugin
author imports one module; the definitions stay in `cccp.calculation`.

## 3. Version / digest locking (T46 handoff)

`PluginVersionDigest` hashes canonical JSON (sorted keys, ASCII, compact separators) of:
`digest_schema_version`, `name`, `distribution`, `plugin_version`, `api_version`,
`compatible_range`, `schema_version`, capability `(name, schema, schema_version)` rows,
declared dependencies, resolved dependency `(name, version, source)` rows, and
`science_software` `(software, version_range, verified)` rows. Changing any identity-bearing
field changes the digest; changing only a display label does not.

`PluginVersionDigest.identity_fields(scope=..., capability=...)` returns JSON-safe,
platform-neutral fields for the three consumption points:

| Field | release | step | product |
|---|---|---|---|
| `plugin_identity_scope` / `plugin_identity_scope_version` | yes | yes | yes |
| `plugin_name` / `plugin_distribution` / `plugin_version` | yes | yes | yes |
| `plugin_api_version` / `plugin_api_compatibility` / `plugin_schema_version` | yes | yes | yes |
| `plugin_digest` / `plugin_digest_schema_version` | yes | yes | yes |
| `plugin_dependencies` | yes | yes | yes |
| `plugin_capability` / `plugin_capability_schema` / `plugin_capability_schema_version` | — | with capability | required |

`PRODUCT` scope without an explicit capability is a typed `PluginContractViolationError`:
a derived product identity must name the parser/analysis capability and its schema version.
Field names/types/source are frozen here **before** the platform freezes execution identity
(T46 owns the downstream handoff document and sample values).

## 4. Example plugin (`cccp.plugin.example_xyz`)

`XyzReadonlyParserPlugin` is a real plain-XYZ parser (no program launch, stdlib only):

- metadata: name/capability `xyz_readonly_parser`, schema `xyz_structure` v1, `MIT`,
  `requires_subprocess=False`, no dependencies, `default_enabled=False`;
- handler: `capability_names()` + `output_spec_for()` +
  `invoke(request, *, context)` reading `request.structure.path`;
- result: built-in `TaskResult` with explicit `units={"coordinates":"angstrom"}`,
  `atom_order` equal to the input symbols, `provenance.input_signature` = sha256 of the text;
- partial: a truncated file keeps the valid atom prefix, `complete=False`,
  `status="failed"`, `error_kind=parse_failure`, `errors=("truncated: ...",)`;
- control: `check_deadline` + `check_cancelled` run before the read and between atom lines.

Fixtures: `tests/fixtures/xyz_readonly_example.xyz`,
`tests/fixtures/xyz_readonly_truncated.xyz`.

## 5. Seven boundary classes (per-class assertions)

| # | Boundary | Test (class) | Genuine exercise |
|---|---|---|---|
| 1 | Version mismatch | `TestVersionMismatchBoundary::test_incompatible_api_is_typed_and_carries_rollback_guidance`, `::test_incompatible_plugin_cannot_be_discovered_or_invoked` | Real `PluginVersionMismatchError` from `lock_plugin`/`PluginHost`; report `REPIN_VERSION`, `silent_substitution_allowed=False`. |
| 2 | Missing dependency | `TestMissingDependencyBoundary::test_missing_dependency_is_typed_before_loader_runs`, `::test_declared_dependency_absent_from_lock_map_is_typed` | `importlib.util.find_spec` proves absence; loader call list stays empty (check precedes load, no install). |
| 3 | Duplicate capability | `TestDuplicateCapabilityBoundary::test_duplicate_capability_across_plugins_is_rejected`, `::test_capability_reserved_for_builtin_task_is_rejected`, `::test_invoke_rejects_undeclared_capability`, `::test_pilot_and_example_discover_with_distinct_capabilities` | Real cross-plugin collision and reserved `TaskKind` capability rejection; T44 pilot + example coexist. |
| 4 | Partial output | `TestPartialOutputBoundary::test_truncated_xyz_keeps_valid_prefix_and_marks_partial`, `::test_partial_result_without_errors_is_rejected`, `::test_capability_that_disallows_partial_is_rejected` | Real truncated XYZ → prefix preserved; contract rejects error-less partial and partial when disallowed. |
| 5 | Units / mapping | `TestUnitsMappingBoundary::*` | Declared angstrom units + input atom order accepted; wrong order, non-canonical unit, count mismatch and task mismatch rejected. |
| 6 | Cancel / timeout | `TestCancellationTimeoutBoundary::*` | Stateful cancel callback stops mid-file (4 checks); `timeout_s=0.0` → typed `PluginTimeoutError`; injected clock proves elapsed-based deadline. |
| 7 | Fallback / rollback | `TestFallbackRollbackBoundary::*` | Loader failure is typed `PluginLoadError`; report says `DISABLE_PLUGIN`; host not silently disabled; no fallback result; contract violation preserves partial evidence. |

Plus `test_example_plugin_loads_through_host_and_parses_complete_xyz` (example loads through
the host and parses the complete fixture) and the digest tests
(`test_digest_is_deterministic_and_version_sensitive`,
`test_identity_fields_support_release_step_and_product_scopes`,
`test_digest_locks_resolved_dependency_versions`).

## 6. Cancellation / timeout semantics

- `TaskContext.timeout_s` is the runtime authority; a non-positive budget raises immediately.
  Otherwise `check_deadline` is elapsed-time based and called between units. The SDK does not
  create threads or kill processes — control is cooperative and deterministic.
- `check_cancelled` reads `TaskContext.is_cancelled()`; cancellation is a typed
  `TaskCancelledError` and maps to rollback action `retry`, `requires_operator=False`.
- Both helper checks run before the handler and the handler repeats them between units.

## 7. Units / atom mapping semantics

- Every plugin result MUST carry `metadata["units"]` (explicit; no implicit default).
  Declared axes must match the capability's `PluginOutputSpec`; known axes use the canonical
  spelling from `cccp.calculation.products.contracts` unit enums.
- `metadata["atom_order"]` MUST align with `symbols`/`coordinates`. `input_order` requires
  equality with the input symbols; `atom_uid` requires unique uids. Symbols and coordinates
  must be supplied together.
- `metadata["capability"]` MUST equal the invoked capability, and `result.task` MUST equal
  `request.task` (no second task chain).

## 8. Dependency policy

- `PluginMetadata.dependencies` lists importable runtime dependencies. The SDK records
  resolved versions (`distribution` via importlib metadata, `stdlib` = Python runtime,
  `module` = `unversioned`).
- Absence is a typed `PluginDependencyUnavailableError` raised **before** the loader; missing
  names are reported together. The SDK never installs or imports the dependency.
- For reproducible releases, distributions (not bare modules) SHOULD be declared so the
  digest locks a real version.

## 9. Fallback / rollback policy

`failure_report(error)` maps each typed failure to guidance; the built-in capability is
always preserved and silent substitution is always forbidden:

| Error | Action | Operator? | Notes |
|---|---|---|---|
| `PluginVersionMismatchError` | `repin_version` | yes | Re-pin to a compatible release or disable; never downgrade silently. |
| `PluginDependencyUnavailableError` | `fix_dependency` | yes | No automatic installation. |
| `PluginCapabilityConflictError` | `resolve_capability_conflict` | yes | Rename/disable one plugin. |
| `PluginContractViolationError` | `disable_plugin` | yes | `preserve_valid_partial_output=True`. |
| `PluginLoadError` / `PluginUnavailableError` | `disable_plugin` | yes | Inspect cause; no fallback result. |
| `PluginTimeoutError` / `TaskCancelledError` | `retry` | no | Retryable control outcome, not a defect. |

## 10. Deprecation policy (tracked)

- The SDK surface is versioned by `PLUGIN_SDK_VERSION` (currently `1`, equal to
  `PLUGIN_API_VERSION`).
- A deprecated symbol stays available for at least
  `SDK_DEPRECATION_WINDOW_MINOR_RELEASES` (= 1) minor release, emits a Python
  `DeprecationWarning`, and is listed in `SDK_DEPRECATIONS` (currently empty) with
  `symbol`, `deprecated_in`, `removal_not_before`, `replacement` and `note`.
- Removal requires a `PLUGIN_API_VERSION` / `PLUGIN_SDK_VERSION` major increment plus a
  `PluginVersionMismatchError` for plugins pinned to the old range — never a silent break.
- Contract tests pin both the deprecated and replacement behaviour while a deprecation is
  active; the digest schema (`PLUGIN_DIGEST_SCHEMA_VERSION`) advances only for a
  wire-shape change and is recorded per digest.
- Backwards-incompatible behaviour changes are prohibited within an API range; a plugin that
  needs them declares a new range and is treated as a version mismatch until re-pinned.

## 11. QA probes (recorded)

Harness: `/usr/bin/python3.11` (3.11.13), repo root `<repo>`.

```bash
# 1) SDK + T44 pilot contract suite
python3.11 -m pytest tests/test_cccp_plugin_sdk.py tests/test_cccp_plugin_pilot.py -q
# -> 44 passed (T44: 18, T45: 26)

# 2) architecture invariants (unique primitive / dependency direction) unaffected
python3.11 -m pytest tests/test_architecture_invariants.py -q
# -> 64 passed (with the plugin suites in the same run)

# 3) slice isolation guard (static reverse-dep + acp-free import of every cccp package)
python3.11 scripts/verify_cccp_slice_guard.py
# -> checks: 5  passed: 5  failed: 0 | RESULT: PASS

# 4) explicit reverse-import gate
grep -rn "from acp" src/cccp/plugin
# -> (no output) zero matches

# 5) lint / format (pre-commit toolchain)
python3.11 -m ruff check src/cccp/plugin tests/test_cccp_plugin_sdk.py
python3.11 -m ruff format --check src/cccp/plugin tests/test_cccp_plugin_sdk.py
# -> All checks passed! / 9 files already formatted
```

**QA happy/failure:**

| Scenario | Probe | Expected / observed |
|---|---|---|
| happy: example loads through host + parses | `test_example_plugin_loads_through_host_and_parses_complete_xyz` | `TaskResult` with `O,H,H` and angstrom units → PASS |
| happy: digest identity | `test_digest_is_deterministic_and_version_sensitive` | Stable digest; changes with version/schema/range → PASS |
| failure: version mismatch | `TestVersionMismatchBoundary::*` | Typed `PluginVersionMismatchError` + `repin_version`, no silent downgrade → PASS |
| failure: cancel/timeout | `TestCancellationTimeoutBoundary::*` | `TaskCancelledError` mid-parse; `PluginTimeoutError` on zero budget → PASS |

## 12. NOT_VERIFIED (tri-state discipline)

| Item | State | Reason |
|---|---|---|
| External distribution entry-point discovery/installation pipeline | `NOT_VERIFIED` / out of scope | T44/T45 freeze metadata + in-process loader + SDK; no external plugin package exists. |
| Real third-party plugin in a clean venv | `NOT_VERIFIED` | No external plugin exists; the example plugin is in-tree and loaded in-process. |
| ORCA `>=5.0,<7` window against a real binary | `NOT_VERIFIED` | Inherited from T44; no QC binary is launched by the SDK. |
| QCEngine / cclib / QCElemental comparison | `NOT_VERIFIED` | Not attempted in this slice; only the in-tree example is validated. |

## 13. Delivered files (this slice)

- `src/cccp/plugin/sdk.py` (new)
- `src/cccp/plugin/example_xyz.py` (new)
- `src/cccp/plugin/errors.py` (extended: capability conflict / dependency / contract / timeout)
- `src/cccp/plugin/discovery.py` (extended: duplicate + reserved capability rejection)
- `src/cccp/plugin/__init__.py` (re-exports only)
- `tests/test_cccp_plugin_sdk.py` (new, 26 cases)
- `tests/fixtures/xyz_readonly_example.xyz`, `tests/fixtures/xyz_readonly_truncated.xyz` (new)
- `tests/baseline/cccp-evidence/plugin_sdk.md` (this file)

## 14. Acceptance mapping

| T45 acceptance item | Evidence |
|---|---|
| 契约测试套件覆盖全部 7 类边界（逐类断言） | §5 table; `tests/test_cccp_plugin_sdk.py` classes 1–7. |
| 示例插件在测试中加载并通过 | §4; `test_example_plugin_loads_through_host_and_parses_complete_xyz`. |
| SDK 文档含弃用政策 | §10. |
| 版本/摘要锁定（release/step/product） | §3; digest tests; T46 field spec frozen here. |
| 复用 TaskRequest/Result/Context，无第二任务链 | §1/§2; `test_result_task_must_match_request_task`; reserved capability rejection. |
| `pytest tests/test_cccp_plugin*.py -q` green | §11 probe 1 (44 passed). |
| slice guard PASS + zero `from acp` | §11 probes 3–4. |
| QA version-mismatch → typed + rollback, no silent degradation | §5 class 1; §9. |
