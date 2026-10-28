# C09-s1 / T44 — Trusted plugin pilot: frozen metadata & boundaries (tracked)

- **Owner:** C 路科学负责人 · **Plan:** `.omo/plans/acp-parallel-plan-3-cccp.md` todo 44
- **Main plan:** `docs/reference/next-change/ACP_Parallel_Plan_3_CCCP_20261018.md` §C09 (关联 §31.1, OS-09, M1/M4)
- **Write authority:** `C直写` — `src/cccp/plugin/**` (registered in `write_registry.md` §4.8); this spec + tests are C-owned tracked evidence.
- **Base snapshot HEAD at spec time:** `c874676b73295dd097437cc7142df1d764af4fc3` (shared dirty worktree; only this slice's files are staged in the T44 commit).
- **Status:** 样板规格 + 最小骨架 + 定向测试已交付；外部 entry-point 发现/安装管线 **不在本片**（T45 SDK 面）。

## 1. Capability template choice (recorded decision)

**Chosen:** `cccp.analysis.orca_parser` (plan T18) — exposed through the pilot adapter
`cccp.plugin.readonly_parser`.

**Why this one:**

1. **Read-only by construction** — `OrcaOutputParser.parse(path)` / `parse_text(text)` is
   "path in → data out"; it launches no ORCA process, so the built-in task layer
   (`cccp.calculation`) remains the only execution station.
2. **Clear Analysis seam** — one aggregate record (`OrcaCalculation`) with a stable
   duck-typed surface; no task-state or scheduler coupling.
3. **Already deduplicated** — frequency science delegates to
   `cccp.calculation.frequency_parse.parse_orca_frequency_text`; the pilot must not add a
   second parse mirror.
4. **Cheapest trustworthy failure surface** — unreadable/missing files and partial output are
   tolerated (all-default record), so the pilot tests boundaries rather than QC binaries.

**Rejected alternative:** `cccp.calculation.frequency_parse` directly. It is a pure function
over text, but the public seam is narrower (one output kind) and it sits *inside* the task
layer; using it as the plugin entry would blur the "Analysis capability vs task station"
boundary the pilot is meant to freeze.

**Not pluginized:** every other scientific path (tasks, backends, QC interfaces, NMR, PES,
sampling) stays built-in. T44 adds **one** capability template, not a pluginization program.

## 2. Frozen metadata fields

`cccp.plugin.metadata.PluginMetadata` is a frozen (`slots=True`) dataclass. Metadata exists
even when the external plugin set is empty (see §3). Required fields:

| Field | Type | Meaning | Pilot value |
|---|---|---|---|
| `name` | dotted lowercase | Stable plugin id; conflict-checked (§5) | `orca_readonly_parser` |
| `distribution` | str | Owning distribution name | `cccp` |
| `version` | str | Distribution version that ships the plugin | `cccp.__version__` |
| `api` | `ApiCompatibility` | API/schema compatibility range (§2.1) | `api_version=1`, `compatible_range=">=1,<2"`, `schema_version=1` |
| `capabilities` | tuple `PluginCapability` | Declared capabilities + emitted schema | `readonly_parser` / `orca_calculation` / v1 |
| `science_software` | tuple `ScienceSoftwareVersion` | Validated software version windows + tri-state `verified` | `orca >=5.0,<7`, `verified=False` (NOT_VERIFIED) |
| `resources` | `ResourceRequirements` | Declared needs; never auto-granted | no subprocess, no network |
| `license` | str | Must be non-empty | `MIT` |
| `dependencies` | tuple str | Importable runtime deps (empty = stdlib/cccp only) | `()` |
| `error_semantics` | `ErrorSemantics` | Pre-launch typed / partial-tolerant / **never overrides built-in** | all `True` |
| `default_enabled` | bool | Declarative only — the host **never** auto-enables from it | `False` |

Hard validations (`PluginMetadataError`): name pattern, non-empty distribution/version/license,
≥1 capability with unique names, and `error_semantics.never_overrides_builtin is True`.

### 2.1 API compatibility range

`api_version_in_range(version, expression)` supports comma-separated `>=`, `>`, `<=`, `<`,
`==` integer tokens. Empty/malformed expressions **never match** (fail closed).

## 3. Built-in identity & explicit empty set

`builtin_catalog()` returns the zero-external-plugin representation. It is serialisable and
non-ambiguous — an empty plugin tuple is explicit, not `None`:

```json
{
  "builtin": {
    "distribution": "cccp",
    "version": "<cccp.__version__>",
    "api_compatibility": ">=1,<2",
    "execution_station": "cccp.calculation"
  },
  "plugins": [],
  "plugin_count": 0,
  "is_empty": true,
  "enablement_policy": "disabled-by-default"
}
```

`execution_station` is frozen to `cccp.calculation`: no plugin may become a second execution
chain. Version digest consumers (T46) read `builtin.version` + each
`plugins[].version`/`api.schema_version`; T44 only freezes the shapes.

## 4. Discovery boundary (`cccp.plugin.discovery.discover_plugins`)

**Discovery is inert.**

- Reads `PluginMetadata` records passed as `PluginCandidate`s. No filesystem install, no
  `pip`, no entry-point enumeration side effects.
- **Never imports a plugin module** and **never calls `candidate.loader`**. A test spies on the
  loader and asserts zero calls.
- Validates in order: reserved-name conflict → duplicate name → API-range mismatch. Any failure
  raises a typed error (fail closed; no partial catalog).
- Returns `PluginCatalog(builtin=..., plugins=<explicit tuple>)`. A candidate with
  `loader=None` is still discoverable; it only fails at load time (§7).

## 5. Name-conflict rejection (never override built-in)

Reserved names are frozen in `BUILTIN_RESERVED_NAMES`:

- every active `TaskKind` value (`singlepoint`, `optimize`, `frequency`, `scan`, `irc`,
  `casscf`, `thermochemistry`, `conformer_search`, `md_sampling`, `clustering`,
  `censo_refine`, `nmr_shielding`, `xtb_path_search`, `orca_gradient`,
  `xtb_constrained_opt`);
- package/stations: `cccp`, `cccp.calculation` (plugin ids are CCCP-scoped; the platform
  distribution id is not part of the plugin dispatch surface);
- parser seams the pilot wraps: `orca_parser`, `orca_output_parser`, `frequency_parse`.

A collision raises `PluginNameConflictError(name, reason="reserved built-in name ...")`
**before** any loader is considered. Two candidates with the same name raise the same type with
`reason="duplicate plugin name"`. `PluginHost` construction performs the same discovery, so a
conflicting candidate cannot even create a host — built-in behaviour is structurally
unoverridable.

## 6. Enablement boundary (`PluginHost`)

- Every plugin is **disabled by default**; `enabled_names()` is empty on a fresh host.
- `default_enabled` is declarative metadata only; the host **ignores it for auto-enable**.
  Explicit `host.enable(name)` is the single enable path.
- `enable()` on an undiscovered name raises `PluginUnavailableError(reason="not discovered")`.
- `disable(name)` is idempotent and evicts any cached instance, so re-enable re-runs the loader.

## 7. Loading boundary (`PluginHost.load`)

Order of checks (each with a distinct typed error):

| Condition | Result |
|---|---|
| name not discovered | `PluginUnavailableError` — `not discovered` |
| discovered but not enabled | `PluginDisabledError` |
| enabled, `loader is None` | `PluginUnavailableError` — `no in-process loader supplied` |
| enabled loader raises | `PluginLoadError` (original exception preserved as `.cause`) |
| enabled loader returns | instance cached per host; repeated `load` returns the same object |

Loaders are **Python callables supplied with the candidate**, never import strings, module
paths, or shell commands. There is no arbitrary import/shell dispatch in the boundary. The
pilot loader constructs `OrcaReadonlyParserPlugin`, a thin adapter over the built-in
`OrcaOutputParser`; it does not replace or shadow it.

## 8. Missing / unavailable boundary

| Scenario | Typed outcome |
|---|---|
| No external plugins installed | valid `builtin_catalog()` with `plugins == ()` |
| `PluginHost.builtin_only()` + enable any name | `PluginUnavailableError` |
| Candidate declares API `>=2,<3` against host API 1 | `PluginVersionMismatchError` (at discovery) |
| Enabled candidate without loader | `PluginUnavailableError` |
| Loader raises (missing dependency at load time) | `PluginLoadError` with `.cause` |
| Metadata missing license/capability/override flag | `PluginMetadataError` |

## 9. Error taxonomy

All extend `PluginError`; base classes preserve legacy catchability
(`ValueError` / `LookupError` / `RuntimeError`):

- `PluginMetadataError(PluginError, ValueError)`
- `PluginNameConflictError(PluginError, ValueError)`
- `PluginVersionMismatchError(PluginError, ValueError)`
- `PluginDisabledError(PluginError, RuntimeError)`
- `PluginUnavailableError(PluginError, LookupError)`
- `PluginLoadError(PluginError, RuntimeError)`

## 10. Packaging side (uses T56 first-batch build spec)

Reference: `tests/baseline/cccp-evidence/build_spec_first_batch.md`.

**No packaging change is required for the plugin package in this batch.**

- `[tool.setuptools.packages.find] include = ["cccp*", ...]` already discovers
  `cccp.plugin` and its submodules because `src/cccp/plugin/__init__.py` exists. T56 Build A
  empirically showed `cccp*` discovery picks up newly created `cccp.analysis` packages with no
  pyproject edit; the same rule applies here.
- The plugin package ships **no package data** and **no new runtime dependency**; therefore no
  `[tool.setuptools.package-data]` key and no `requirements-node.txt` increment.
- T56's only first-batch delta remains the `"cccp.analysis.nmr" = ["model_assets/*"]` rule
  (§1b of that spec), unrelated to this slice.
- If a later plugin ships data assets, it must extend T56's package-data request rather than
  add a second asset locator.

## 11. QA probes (recorded)

Harness: `/usr/bin/python3.11` (3.11.13), repo root `/ACPServer/AutoCalcPlatform`.

```bash
# 1) directed pilot tests (18 cases)
python3.11 -m pytest tests/test_cccp_plugin_pilot.py -q
# -> 18 passed in 0.18s

# 2) import + boundary probe
python3.11 -c "import cccp.plugin as p; c=p.builtin_catalog(); assert c.is_empty and c.builtin.execution_station=='cccp.calculation'; h=p.PluginHost.with_builtin_pilot(); assert h.enabled_names()==(); h.enable(p.ORCA_READONLY_PARSER_NAME); from pathlib import Path; from cccp.analysis.orca_parser import OrcaOutputParser; t=Path('tests/fixtures/orca_optfreq_real_sections.txt').read_text(); assert h.load(p.ORCA_READONLY_PARSER_NAME).parse_text(t)==OrcaOutputParser().parse_text(t); print('PASS')"
# -> PASS

# 3) slice isolation guard (static reverse-dep + acp-free import of cccp.plugin)
python3.11 scripts/verify_cccp_slice_guard.py
# -> checks: 5  passed: 5  failed: 0 | RESULT: PASS
#    static-reverse-dependency PASS; public-api-import imports cccp.plugin acp-free
```

**Scenario coverage (QA happy/failure):**

| Scenario | Probe | Expected / observed |
|---|---|---|
| happy: pilot parses sample like built-in | `test_enabled_pilot_parses_like_builtin` | `==` on `OrcaCalculation` for real ORCA excerpt → PASS |
| failure: disabled by default | `test_pilot_is_disabled_by_default_and_load_is_rejected` | `PluginDisabledError` → PASS |
| failure: reserved-name conflict | `test_discovery_rejects_reserved_builtin_task_name` + `test_host_construction_cannot_override_builtin` | `PluginNameConflictError`, built-in not overridden → PASS |
| failure: duplicate name | `test_discovery_rejects_duplicate_plugin_names` | `PluginNameConflictError` → PASS |
| failure: incompatible API | `test_incompatible_api_range_is_rejected` | `PluginVersionMismatchError` → PASS |
| failure: missing loader | `test_enabled_candidate_without_loader_is_unavailable` | `PluginUnavailableError` → PASS |
| failure: loader raises | `test_loader_failure_is_typed_with_cause` | `PluginLoadError` with `.cause` → PASS |
| failure: discovery auto-exec | `test_discovery_never_executes_a_loader` | loader calls `[]` → PASS |

## 12. NOT_VERIFIED (tri-state discipline)

| Item | State | Reason |
|---|---|---|
| ORCA `>=5.0,<7` window against a real ORCA binary | `NOT_VERIFIED` | No QC binary launched in this slice; `science_software[0].verified=False` records it. |
| External distribution entry-point discovery/installation pipeline | `NOT_VERIFIED` / out of scope | T44 freezes metadata + in-process loader boundary; T45 owns SDK/example/contract suite. |
| Real third-party plugin package in a clean venv | `NOT_VERIFIED` | No external plugin exists yet; empty-set catalog is the verified representation. |

## 13. Delivered files (this slice)

- `src/cccp/plugin/__init__.py` (re-exports only)
- `src/cccp/plugin/errors.py`
- `src/cccp/plugin/metadata.py`
- `src/cccp/plugin/discovery.py`
- `src/cccp/plugin/host.py`
- `src/cccp/plugin/readonly_parser.py`
- `tests/test_cccp_plugin_pilot.py`
- `tests/baseline/cccp-evidence/plugin_pilot_spec.md` (this file)

## 14. Acceptance mapping

| T44 acceptance item | Evidence |
|---|---|
| 样板规格文档（元数据/启用/加载/冲突/缺失/unavailable） | §2–§9 |
| 最小实现骨架（可导入、默认关闭） | `src/cccp/plugin/**`; §4–§7; disabled-by-default tests |
| `pytest tests/test_cccp_plugin*.py -q` green | §11 probe 1 (18 passed) |
| slice guard PASS | §11 probe 3 (5/5 checks) |
| packaging side refers to T56 spec | §10 |
