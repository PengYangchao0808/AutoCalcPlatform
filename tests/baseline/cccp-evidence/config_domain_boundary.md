# C07-s4 — Config domain boundary: science vs Services platform (T36)

- **plan:** `.omo/plans/acp-parallel-plan-3-cccp.md` todo 36 (C07-s4)
- **write authority:** `tests/baseline/cccp-evidence/write_registry.md` §4.7 —
  `src/cccp/config.py` = `C直写`; `src/acp/core/config.py` =
  `B补丁`; `src/acp/scheduler/remote/config.py` and `config/defaults.yaml` = B-owned
- **provider base commit:** `b64dec6` (C producer base, branch `feat/frontend-plan1-closure`)
- **delivered by:** commit `refactor(cccp): split platform config domain from science config with equivalence proof`
- **review anchor:** 评审 C6 (three-layer freeze), plan-3 todo 36

## 1. The three frozen layers

| # | Layer | Owner | Entry points | Returned fields |
|---|-------|-------|--------------|-----------------|
| 1 | **CCCP science-config parsing** | C (`src/cccp/config.py`) | `get_science_defaults()`, `load_science_config()` | Science sections only: `executables`, `resources`, `theory`, `thermo`, `mrrho_settings`, `censo`, `nmr`, `protocols`, `mechanism`, `optimization_control`, plus any unknown science keys. **No `cluster`.** |
| 2 | **Services platform-config parsing** | B (`acp.scheduler.remote.config`, `acp_services.contracts.config_validation`, `acp init` wizard) | `load_platform_section()` (CCCP reader — opaque), `RemoteExecutionConfig.from_config_dict(cluster)` / `RemoteNode.from_config_dict` (authoritative parser) | `cluster` only (`enabled`, `execution_mode`, `type`, `queue`, `walltime`, `extra_flags`, `nodes`, `local`, `local_retention`, …) — passed through **verbatim**, never parsed by CCCP |
| 3 | **Legacy full-config compatibility assembly** | C (transitional) | `load_config()`, `_get_default_config()`, `_validate_config()` | Layer 1 ∪ Layer 2, unchanged six-source merge order |

`cccp.config.PLATFORM_CONFIG_SECTIONS = frozenset({"cluster"})` is the single
machine-readable declaration of the boundary; `split_config_domains(config)`
partitions any merged config at that key.

## 2. Public entry-point contract (returned fields + transitional contract)

| Entry point | Domain | Returned fields | Contract |
|---|---|---|---|
| `load_config(...)` | legacy full | science ∪ platform | **Unchanged** six-source merge; platform `cluster` merged passively. Transitional compatibility entry — existing callers keep working byte-identically (see §4). |
| `_get_default_config()` | legacy full | science ∪ platform | Builds from `get_science_defaults()` ∪ `get_platform_defaults()`; equal to the pre-split literal. |
| `get_science_defaults()` | science | science sections | New; no platform keys. |
| `get_platform_defaults()` | platform | `{"cluster": {...}}` | New; **transitional CCCP-side baseline only** — the authoritative platform defaults/YAML/init/RemoteNode validation are owned by Services. |
| `load_science_config(...)` | science | science sections | New science-minimal entry: same six-source merge, then strips `cluster`. Imports no Services code. |
| `load_platform_section(...)` | platform | `{"cluster": ...}` | New opaque pass-through reader; hand the mapping to the Services parser. |
| `split_config_domains(config)` | boundary | `(science, platform)` | New pure partition; platform is a verbatim shallow copy. |
| `_validate_config(config)` | legacy full | — | Runs `_validate_science_config` then `_validate_platform_section`; output unchanged. |

## 3. Compatibility rules (frozen)

1. **Passive pass-through only.** CCCP never parses remote `nodes`, never
   validates `execution_mode`/`type`/`walltime`, and never coerces platform
   fields. Unknown platform keys survive unchanged.
2. **No reverse-load.** `src/cccp/**` (incl. `config.py`) must not import
   `acp` / `acp_services` / `paramiko`, statically or lazily. Guarded by
   `tests/test_cccp_config_domains.py::TestScienceMinimalServicesFree`
   (AST scan + child interpreter that blocks those roots) and by
   `tests/test_cccp_isolation.py` / `scripts/verify_cccp_isolation.py`.
3. **Final assembly stays upper-layer.** CCCP only emits the platform
   section; Services owns the authoritative schema/validation and performs
   the final platform assembly. `_validate_platform_section` exists solely
   so the legacy full-config entry keeps its exact pre-split output for
   `cluster.local_retention`.
4. **Protocols stay on the Python entry.** No YAML `protocols` section is
   generated; `protocols` remains a science key (ANTI #4).

## 4. Equivalence proof (diff EMPTY)

Snapshotter: `.omo/evidence/acp-parallel-plan-3-cccp/t36-config-snapshot.py`
(deterministic; isolated temp `HOME` + cwd; canonical sorted JSON).

```bash
python3.11 .omo/evidence/acp-parallel-plan-3-cccp/t36-config-snapshot.py t36-config-snapshot-before.json
# ...apply T36 split...
python3.11 .omo/evidence/acp-parallel-plan-3-cccp/t36-config-snapshot.py t36-config-snapshot-after.json
diff t36-config-snapshot-before.json t36-config-snapshot-after.json   # -> empty
```

16 scenarios, **byte-identical before/after**:

| # | Scenario | Covers acceptance item |
|---|---|---|
| S0 | built-in Python defaults (`_get_default_config`) | defaults source |
| S1 | `config/defaults.yaml` only | source 1 |
| S2 | `~/.cccp.yaml` (user home) | source 2 |
| S3 | `./cccp.yaml` (local dir) | source 3 |
| S4 | explicit `--config` | source 4 |
| S5 | `CONFSEARCH_*` env overrides | source 5 |
| S6 | CLI `overrides` applied last | source 6 |
| S7 | all six sources at once — later wins (`nproc` → 5) | **six-source priority order** |
| S8 | unknown top-level / nested / platform / science keys | **unknown fields** |
| S9 | `execution_mode: remote` + `nodes[]` in legacy `~/.conformer_search.yaml` | **remote node + legacy config file** |
| S10 | `~/.cccp.yaml` alongside legacy — new wins, no merge | legacy fallback |
| S11 | legacy local `./conformer_search.yaml` fallback | legacy fallback |
| F1–F4 | `_get_default_config`, `_merge_configs`, `_apply_env_overrides`, `_validate_config` | function-level equality |

Evidence files: `.omo/evidence/acp-parallel-plan-3-cccp/t36-config-snapshot-before.json`,
`...-after.json`. Both snapshot at
`sha256:9599daf4bd0e98d30107fb66091adbc2058cf9a5baf7f3e414d4489146daf375`
(138899 bytes — **identical hash ⇒ byte-identical output**, not merely an
empty textual diff). Snapshotter
`t36-config-snapshot.py` `sha256:08c817d002d8b68ef4d288fdadafdbb9458283cdecfac9040b2299d2da046a04`.

## 5. Science-minimal install does not require Services

```bash
python3.11 -m pytest tests/test_cccp_config_domains.py::TestScienceMinimalServicesFree -q
# AST scan: cccp/config.py imports no acp/acp_services/paramiko
# child interpreter blocks acp/acp_services/paramiko then runs load_science_config()
#   -> "ISOLATION_OK", no leaked modules
python3.11 scripts/verify_cccp_isolation.py --no-acp   # PASS (15/15 tasks, no acp leak)
```

## 6. Legacy full-config entry equivalence statement

`load_config()` — the legacy CCCP entry point — returns the **same
dictionary** (same keys, same values) as before the split for identical
inputs, proven byte-identically by §4. The science and platform pieces are
now sourced from `get_science_defaults()` / `get_platform_defaults()` and
validator functions `_validate_science_config` / `_validate_platform_section`
instead of a single literal/function, but the assembled output is unchanged.
No configuration merge result changed; no YAML `protocols` section was added.

## 7. B 请求 (changes requested from B — NOT applied by C)

Per write_registry §4.7 these files are B-owned; C only requests.

| # | File (B-owned) | Requested change | Reason | Home |
|---|---|---|---|---|
| BR-1 | `config/defaults.yaml` | Keep `cluster` as the sole platform section; update the two sync comments that name `cccp.config._get_default_config() cluster.local` / `cluster.local_retention` to point at `cccp.config.get_platform_defaults()` (the new platform-domain entry). Do not move science keys into `cluster`. | Domain entry renamed; comment-only + sync-source alignment, values unchanged. | B40a (B07-6), pending |
| BR-2 | `src/acp/core/config.py` | Add ACP-facade forwards: `get_science_defaults`, `get_platform_defaults`, `load_science_config`, `load_platform_section` (thin delegation to `cccp.config`, matching existing facade style). | Makes the domain boundary explicit at the ACP public facade; `acp/core/config.py` is `B补丁` in §4.7. No behavior change to existing facade functions. | B40a (B07-6), pending |
| BR-3 | `src/acp/scheduler/remote/config.py` | No code change required. Optional adoption: read the platform mapping via `cccp.config.load_platform_section()["cluster"]` instead of `load_config().get("cluster", {})` (behavior-identical today). | Confirm the authoritative platform parser consumes the new opaque platform reader. | B40a (B07-6), pending |
| BR-4 | authoritative platform defaults ownership | Treat `cccp.config.get_platform_defaults()` as a **transitional baseline only**; the authoritative platform defaults remain `config/defaults.yaml` `cluster` + Services validators. Record this ownership in the platform docs. | Prevents CCCP's baseline from being read as authority. | B40a (B07-6), pending |

No B-owned file was edited by C. `git status` shows no change to
`config/defaults.yaml`, `src/acp/core/config.py`, or
`src/acp/scheduler/remote/config.py` from this task.

## 8. B 绑定记录 (joint-batch binding)

Joint batch per plan-3 todo 36 dependency: **B-T36–T39** (plan-2 items 36–39,
`acp-parallel-plan-2-services.md`), i.e. `B07-2` … `B07-5`. Live status
checked at task start: **all `[x]` (landed)**.

**Binding status: BOUND.**

| B todo | Commit (SHA) | Test record (from B evidence) |
|---|---|---|
| B36 (B07-2 unified init) | `edf65791b3eed4fc0eab2b4fa3fc95bd24376a08` | `python3.11 -m pytest tests/test_services_init_unified.py -q` (see `.omo/evidence/acp-parallel-plan-2-services/task-36-*.md`) |
| B37 (B07-3 Paramiko-free node validation) | `5534b354dbce8f7c0bd289c946271a887c18c1c9` | `python3.11 -m pytest tests/test_config.py tests/test_acp_init_config_model.py tests/test_services_config_validation.py -q` → 120 passed (`task-37-*.md`) |
| B38 (B07-4 config plan/apply) | `2d6646d06409ffe8155963da2f7fb371de06d907` | `python3.11 -m pytest tests/test_services_config_apply.py -q` → 23 passed (`task-38-*.md`) |
| B39 (B07-5 action gating) | `9e4d21abc79cc2e4b60813c1cd97c9daad75c191` | `python3.11 -m pytest tests/test_services_action_gating.py -q` → 26 passed (`task-39-*.md`) |

C-side joint-batch verification on the bound batch:

```
python3.11 -m pytest tests/test_config.py tests/test_cccp_config_domains.py \
  tests/test_acp_config.py tests/test_acp_init_config_model.py -q            -> 44 passed
python3.11 -m pytest tests/test_services_config_validation.py \
  tests/test_services_config_apply.py -q                                     -> 55 passed
python3.11 scripts/verify_cccp_isolation.py --no-acp                         -> PASS (15/15)
```

### Residual PENDING (does NOT block the batch binding)

The **authoritative platform defaults/YAML/init/RemoteNode** re-statement
(BR-1…BR-4) lands with **B40a (B07-6)**, which is `[ ]` (pending) in plan-2.
This is the platform-defaults/instruction half of the joint work; the
science/platform *code* boundary and its equivalence proof are complete and
bound above. Exact waiting item: **B40a (B07-6) — role deps + models/notice +
node deps alignment**, which is the natural home for the platform-YAML
authority update.

## 9. Reproduction

```bash
# equivalence
python3.11 .omo/evidence/acp-parallel-plan-3-cccp/t36-config-snapshot.py /tmp/before.json   # (pre-change checkout)
python3.11 .omo/evidence/acp-parallel-plan-3-cccp/t36-config-snapshot.py /tmp/after.json
diff /tmp/before.json /tmp/after.json      # empty
# contracts + guards
python3.11 -m pytest tests/test_cccp_config_domains.py tests/test_config.py -q
python3.11 scripts/verify_cccp_isolation.py --no-acp
```
