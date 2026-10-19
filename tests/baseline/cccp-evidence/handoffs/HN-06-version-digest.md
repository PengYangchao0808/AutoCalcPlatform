# HN-06 — 插件/parser 版本摘要字段规范与示例值交接（供 B 的 release/step/product identity）

| 字段 | 值 |
|---|---|
| HN 编号 | `HN-06`（C 侧 `tests/baseline/cccp-evidence/handoff_registry.md` §2 索引行） |
| owner / provider / consumer | C 路科学负责人 / C / **B（release/step/product identity 冻结与发布，B06/B07 执行身份）** |
| tracked 位置 | `tests/baseline/cccp-evidence/handoffs/HN-06-version-digest.md`（本文件）+ `handoffs/samples/version_digest_samples.json`（真实样例值） |
| 计划来源 | `.omo/plans/acp-parallel-plan-3-cccp.md` todo 46（C09-s3） |
| 形状来源（已冻结） | T45 `src/cccp/plugin/sdk.py` — `PluginVersionDigest`（canonical-JSON sha256）+ `identity_fields(scope, capability)`；T44 `src/cccp/plugin/metadata.py` 元数据字段 |
| status | `delivered`（provider 侧；**未 integrated** — B 应用并验收前不得置 `integrated`） |
| applied_commit（provider 交付提交） | 见 `handoff_registry.md` HN-06 行回填 |

---

## 0. 范围与冻结时点声明（B 必读）

1. **本 HN 交付的是字段规范 + 真实示例值**，不是 API 承诺。字段名/types/来源全部来自
   已提交的 T45 实现（`src/cccp/plugin/sdk.py`，commit `b9283e5`）；本文件只做下游消费
   说明与样例固化，**未新增任何未定 API**。
2. **字段冻结时点早于 B 的执行身份发布**：摘要 shape 在 T45 已冻结
   （`PLUGIN_DIGEST_SCHEMA_VERSION = 1`、`PLUGIN_IDENTITY_SCOPE_VERSION = 1`）；本 HN
   在 B 冻结 ExecutionEnvelope/执行身份并发布之前交付字段名/types/来源与样例值
   （计划要求：版本摘要 schema 与字段形状必须在 B 冻结执行身份前明确，评审 C7/P1-3）。
   B 消费时**不得**在 C 交付前自行推断字段形状；如需变更 digest shape，须先递增
   `PLUGIN_DIGEST_SCHEMA_VERSION` 并由 C 更新本 HN（B 不改 shape）。
3. **用途边界**：这些字段**只**供 B 的 release/step/product 身份锁定（以及科学结果中
   该身份的嵌入声明）。**不**把插件身份混入科学结果的**内容**身份
   （内容身份仍由 `cccp.calculation` 的结果契约/provenance/input_signature 负责）；
   插件身份是"谁解析/谁计算"的发布身份，不是"算的是什么"的内容摘要。
4. **首批 API/配置/插件能力对应同一 release**：`plugin_version` 与 cccp 发行版本
   （`cccp.__version__`）在内置插件上同源；B 的 release identity 应以同一发行版本号
   为轴，插件摘要作为其上的细粒度锁定。

---

## 1. 两个层次的字段（B 按消费点选择）

| 层次 | API | 用途 |
|---|---|---|
| **L1 摘要锁定**（lock） | `cccp.plugin.sdk.lock_plugin(metadata, *, resolved_dependencies=None)` → `PluginVersionDigest` | 产生 `digest`（sha256 hex）+ 完整锁定载荷（`to_dict()`）。B 的 release identity 建议嵌入 `digest` 与 `plugin_digest_schema_version`。 |
| **L2 身份字段**（identity projection） | `PluginVersionDigest.identity_fields(scope=..., capability=...)` → JSON-safe dict | 三个消费点（`release`/`step`/`product`）的**直接嵌入字段**；B 按 scope 取用。 |

`identity_fields` 的返回值**总是**含 L1 的 `plugin_digest`；L2 是 L1 的投影 + scope 标记。

---

## 2. L1 字段表 — `PluginVersionDigest.to_dict()`（摘要锁定载荷）

摘要 = sha256（64 位小写 hex）over canonical JSON（`sort_keys=True`,
`separators=(",", ":")`, `ensure_ascii=True`）of 以下**恰好这些**字段
（`_digest_payload()`，`digest` 自身除外）：

| 字段名 | 类型 | 来源 | 入摘要 | 说明 |
|---|---|---|---|---|
| `digest_schema_version` | int | `PLUGIN_DIGEST_SCHEMA_VERSION`（当前 `1`） | ✔ | 摘要 shape 版本；wire-shape 变更才递增，且逐摘要记录。 |
| `name` | str | `PluginMetadata.name`（T44 冻结，dotted lowercase） | ✔ | 插件唯一名；冲突在发现层拒绝（T44）。 |
| `distribution` | str | `PluginMetadata.distribution` | ✔ | 发行包名（内置插件 = `"cccp"`）。 |
| `plugin_version` | str | `PluginMetadata.version` | ✔ | 插件语义版本；内置插件 = `cccp.__version__`。 |
| `api_version` | int | `ApiCompatibility.api_version` | ✔ | 插件构建所对的 API 版本（当前 `1`）。 |
| `compatible_range` | str | `ApiCompatibility.compatible_range`（当前 `">=1,<2"`） | ✔ | 插件接受的 host API 范围。 |
| `schema_version` | int | `ApiCompatibility.schema_version` | ✔ | 元数据记录自身版本。 |
| `capabilities` | list[obj] | `PluginMetadata.capabilities` | ✔ | 每项 `{name: str, schema: str, schema_version: int}`；能力 schema 变更 → 摘要变。 |
| `declared_dependencies` | list[str] | `PluginMetadata.dependencies` | ✔ | 声明的可导入依赖名（按**导入名**声明）。 |
| `resolved_dependencies` | list[obj] | `resolve_dependency_versions()` 或调用方提供 | ✔ | 每项 `{name: str, version: str, source: "distribution"\|"stdlib"\|"module"\|"provided"}`；解析到的版本变 → 摘要变。 |
| `science_software` | list[obj] | `PluginMetadata.science_software` | ✔ | 每项 `{software: str, version_range: str, verified: bool}`；窗口/验证标记变 → 摘要变。 |
| `digest` | str | 计算得出 | ✘（自身） | sha256 hex；`to_dict()` 中附带。 |

**不入摘要**：`resources`（ResourceRequirements）、`license`、`error_semantics`、
`default_enabled`（均为发现/策略字段，不影响执行身份；license 变更不改变摘要——
B 的 release 清单若需许可轴，应在 release identity 另列，不复用本摘要）。

---

## 3. L2 字表 — `identity_fields(scope, capability=None)`（B 直接嵌入的字段）

| 字段名 | 类型 | 来源 | release | step | product |
|---|---|---|---|---|---|
| `plugin_identity_scope` | str | `"release"`/`"step"`/`"product"`（入参） | ✔ | ✔ | ✔ |
| `plugin_identity_scope_version` | int | `PLUGIN_IDENTITY_SCOPE_VERSION`（当前 `1`） | ✔ | ✔ | ✔ |
| `plugin_name` | str | 同 L1 `name` | ✔ | ✔ | ✔ |
| `plugin_distribution` | str | 同 L1 `distribution` | ✔ | ✔ | ✔ |
| `plugin_version` | str | 同 L1 `plugin_version` | ✔ | ✔ | ✔ |
| `plugin_api_version` | int | 同 L1 `api_version` | ✔ | ✔ | ✔ |
| `plugin_api_compatibility` | str | 同 L1 `compatible_range` | ✔ | ✔ | ✔ |
| `plugin_schema_version` | int | 同 L1 `schema_version` | ✔ | ✔ | ✔ |
| `plugin_digest` | str | 同 L1 `digest` | ✔ | ✔ | ✔ |
| `plugin_digest_schema_version` | int | 同 L1 `digest_schema_version` | ✔ | ✔ | ✔ |
| `plugin_dependencies` | list[obj] | 同 L1 `resolved_dependencies` | ✔ | ✔ | ✔ |
| `plugin_capability` | str | 声明的能力名（**入参** capability，须已在 metadata 声明） | —* | —* | **必填** |
| `plugin_capability_schema` | str | 该能力的 schema | —* | —* | **必填** |
| `plugin_capability_schema_version` | int | 该能力的 schema_version | —* | —* | **必填** |

\* release/step scope 传入 `capability=` 时同样携带这三字段（能力已声明时）；
不传则不含。**`product` scope 不传 capability 是 typed 错误**
（`PluginContractViolationError: PRODUCT identity requires an explicit capability`）——
派生产品身份必须指名解析/分析能力及其 schema 版本。传入未声明的能力名同样是
typed 错误。以上均为实测（见 §5/§6）。

**B 消费建议（不改变字段 shape，仅惯例）：**

- **release identity**：嵌入 `identity_fields(scope=RELEASE)` 的全部字段；插件集合非空时
  按 `plugin_name` 排序拼接或逐插件登记。空插件集是合法 release（`BuiltinIdentity`
  已提供 host 侧版本，见 T44 `discovery.builtin_catalog`）。
- **step identity**：嵌入 `identity_fields(scope=STEP, capability=<本步调用的能力>)`。
- **product identity**：嵌入 `identity_fields(scope=PRODUCT, capability=<产出该产品的
  能力>)`；产品 provenance/结果契约中记录该 dict，即完成"此产品由哪个插件版本的
  哪个能力产出"的锁定。

---

## 4. 语义要点（锁定值与产物入身份的规则）

1. **锁定值语义**：`digest` 覆盖 L1 表中全部 ✔ 字段。任一身份承载字段变（插件版本、
   API/schema 版本、能力 schema、依赖解析版本、科学软件窗口/verified）→ 摘要变；
   仅展示性字段（license/resources 等）变 → 摘要不变。
2. **依赖来源四态**：`distribution`（importlib metadata 实测版本）/ `stdlib`
   （Python 运行时，`python-3.x`）/ `module`（可导入但无发行元数据 → `unversioned`）/
   `provided`（受控安装器经 `lock_plugin(resolved_dependencies=...)` 注入，不查环境）。
   **同 metadata 在不同环境解析出不同版本 → 摘要不同**——这是特性：release identity
   锁定的是"解析后的实际版本"。可复现发行优先声明 distribution（导入名=发行名）。
3. **已知缺口（T45 记录，供 B 注意）**：按导入名声明的依赖若发行名不同（如 `yaml`
   导入但发行名 `PyYAML`），解析为 `source="module", version="unversioned"`——
   摘要锁定不到真实版本；声明 `PyYAML` 则 `find_spec("PyYAML")` 失败 → typed
   `PluginDependencyUnavailableError`。**对可复现 release，B 应要求插件以
   导入名=发行名 的 distribution 声明依赖**（如 `numpy`），或以 `provided` map
   注入受控版本。此缺口在 SDK 侧文档化，不在本 HN 修复。
4. **缺失依赖**：typed `PluginDependencyUnavailableError`，在 loader 之前抛出；SDK
   永不安装/导入依赖。B 的 release 校验可复用 `lock_plugin` 作为发布前检查。
5. **空依赖/空科学软件是合法值**（内置解析器插件两者皆空）；`plugin_dependencies`
   为 `[]`，`lock.science_software` 为 `[]`。
6. **结果内嵌**：插件产出的 `TaskResult` 由 `validate_result_contract` 校验
   （envelope/units/atom_order/partial）；`identity_fields` 的 dict **不**由 SDK 自动
   写入结果 metadata——嵌入点在 B 的发布/step/product identity 写入处（本 HN 的
   consumer 职责）。C 侧承诺的是字段 shape 与生成 API 稳定。

---

## 5. 示例值（真实 SDK 生成 — 命令 + 原始输出）

生成环境：`/usr/bin/python3.11`（3.11.13），repo root `<repo>`，
`PYTHONPATH=src`。**全部样例值固化于** `tests/baseline/cccp-evidence/handoffs/samples/version_digest_samples.json`
（tracked；由下述命令生成，非手写）。

生成命令（可复跑；输出即样例 JSON 文件内容）：

```bash
cd <repo>
PYTHONPATH=src /usr/bin/python3.11 - <<'PY'
import json, sys
from cccp.plugin.example_xyz import example_metadata, EXAMPLE_CAPABILITY_NAME
from cccp.plugin.readonly_parser import readonly_parser_metadata
from cccp.plugin.metadata import PluginMetadata, PluginCapability
from cccp.plugin.sdk import lock_plugin, PluginIdentityScope as S

samples = {
    "generator": {
        "python": sys.version.split()[0],
        "cccp_version": __import__("cccp").__version__,
        "note": "All values below are real SDK output (lock_plugin + identity_fields), not hand-written.",
    },
    "examples": {},
}
ex = lock_plugin(example_metadata())
samples["examples"]["xyz_readonly_parser"] = {
    "digest": ex.digest,
    "lock": ex.to_dict(),
    "identity_release": ex.identity_fields(scope=S.RELEASE),
    "identity_step": ex.identity_fields(scope=S.STEP, capability=EXAMPLE_CAPABILITY_NAME),
    "identity_product": ex.identity_fields(scope=S.PRODUCT, capability=EXAMPLE_CAPABILITY_NAME),
}
pil = lock_plugin(readonly_parser_metadata())
samples["examples"]["orca_readonly_parser"] = {
    "digest": pil.digest,
    "lock": pil.to_dict(),
    "identity_release": pil.identity_fields(scope=S.RELEASE),
    "identity_step": pil.identity_fields(scope=S.STEP, capability="readonly_parser"),
    "identity_product": pil.identity_fields(scope=S.PRODUCT, capability="readonly_parser"),
}
dep_meta = PluginMetadata(
    name="vendor.sample_parser", distribution="sample-dist", version="1.3.0",
    capabilities=(PluginCapability(name="sample_parse", schema="sample", schema_version=2),),
    license="MIT", dependencies=("numpy", "math"),
)
dep = lock_plugin(dep_meta)
samples["examples"]["vendor.sample_parser_deps"] = {
    "note": "Constructed metadata (deps numpy=distribution, math=stdlib) to demonstrate resolved-dependency rows.",
    "digest": dep.digest, "lock": dep.to_dict(),
    "identity_product": dep.identity_fields(scope=S.PRODUCT, capability="sample_parse"),
    "identity_release_no_capability": dep.identity_fields(scope=S.RELEASE),
}
prov = lock_plugin(dep_meta, resolved_dependencies={"numpy": "2.4.6", "math": "python-3.11"})
samples["examples"]["vendor.sample_parser_provided"] = {
    "note": "lock_plugin(resolved_dependencies=...) — source=provided, digest differs from environment resolution.",
    "digest": prov.digest,
    "resolved_dependencies": prov.to_dict()["resolved_dependencies"],
    "digest_differs_from_env_resolution": prov.digest != dep.digest,
}
samples["behaviour_checks"] = {
    "deterministic_same_metadata_same_digest": lock_plugin(dep_meta).digest == dep.digest,
    "product_scope_without_capability": "PluginContractViolationError: PRODUCT identity requires an explicit capability",
}
print(json.dumps(samples, sort_keys=True, indent=2, ensure_ascii=True))
PY
```

### 5.1 关键原始输出摘录（与样例 JSON 一致）

**示例插件 `xyz_readonly_parser`（T45 example，无依赖、无科学软件）：**

```json
{
  "digest": "7564e154cde3bdc97cc7e4e8befd3dc2d782b13a9cad99ef92928215d115bf6a",
  "lock": {
    "api_version": 1,
    "capabilities": [{"name": "xyz_readonly_parser", "schema": "xyz_structure", "schema_version": 1}],
    "compatible_range": ">=1,<2",
    "declared_dependencies": [],
    "digest_schema_version": 1,
    "distribution": "cccp",
    "name": "xyz_readonly_parser",
    "plugin_version": "0.2.0",
    "resolved_dependencies": [],
    "schema_version": 1,
    "science_software": []
  },
  "identity_product": {
    "plugin_capability": "xyz_readonly_parser",
    "plugin_capability_schema": "xyz_structure",
    "plugin_capability_schema_version": 1,
    "plugin_identity_scope": "product",
    "plugin_identity_scope_version": 1,
    "plugin_name": "xyz_readonly_parser",
    "plugin_distribution": "cccp",
    "plugin_version": "0.2.0",
    "plugin_api_version": 1,
    "plugin_api_compatibility": ">=1,<2",
    "plugin_schema_version": 1,
    "plugin_digest": "7564e154cde3bdc97cc7e4e8befd3dc2d782b13a9cad99ef92928215d115bf6a",
    "plugin_digest_schema_version": 1,
    "plugin_dependencies": []
  }
}
```

**试点插件 `orca_readonly_parser`（T44 pilot，含科学软件窗口）：**

```json
{
  "digest": "f5d152c78870c82e66ecc117de25c36eaa9952e10e61ccb80b6a3e9f07f525ae",
  "science_software": [{"software": "orca", "verified": false, "version_range": ">=5.0,<7"}]
}
```

（`verified: false` = NOT_VERIFIED 三态纪律：窗口已声明但未在本环境用真实二进制确认，
**不是** pass。）

**构造样例 `vendor.sample_parser`（依赖解析演示）：**

```json
{
  "digest": "2f7253158de4ee12e549cc6da6b91fe25e17b369edd7b21ab1b885fb9031157b",
  "resolved_dependencies": [
    {"name": "math", "source": "stdlib", "version": "python-3.11"},
    {"name": "numpy", "source": "distribution", "version": "2.4.6"}
  ],
  "provided_map_digest_differs": true,
  "deterministic": true
}
```

### 5.2 release/step/product 三投影对照（同一插件）

| scope | capability 入参 | `plugin_identity_scope` | capability 三字段 | 其余 11 个 base 字段 |
|---|---|---|---|---|
| release | 不传 | `"release"` | 不含 | 全含（含 `plugin_digest`） |
| step | 传（已声明） | `"step"` | 含（`plugin_capability*`） | 全含 |
| product | 传（必须） | `"product"` | 含（必填；缺失 → typed 错误） | 全含 |

（实测：release 不传 capability 时输出键集恰为 11 个 base 字段；step/product 含全部
14 字段。见样例 JSON。）

---

## 6. QA（对样例生成的交叉核对）

| 探针 | 命令/方法 | 结果 |
|---|---|---|
| 样例可复跑、确定性 | §5 生成命令两次运行 digest 一致；`behaviour_checks.deterministic_same_metadata_same_digest = true` | PASS |
| 字段全覆盖核对 | 脚本核对样例 JSON 中 lock 键集 = §2 表 13 字段；identity base 键集 = §3 表 11 字段；capability 三字段仅出现在传 capability 的投影；dep/cap/science_software 行形状逐一等于 §2 子表 | PASS（`failures: NONE`） |
| digest 格式 | 全部样例 digest = 64 位小写 hex | PASS |
| PRODUCT 缺 capability | 实测 `PluginContractViolationError: PRODUCT identity requires an explicit capability` | PASS（typed，非静默） |
| 未声明 capability | T45 契约测试 `test_identity_fields_support_release_step_and_product_scopes` 覆盖（typed 错误） | PASS（T45 套件内） |
| 提供方 map 与环境解析摘要不同 | `vendor.sample_parser_provided.digest_differs_from_env_resolution = true` | PASS（预期行为，§4.2） |
| 字段歧义 | 无：所有文档字段在真实输出中出现且类型一致；若后续 SDK 变更 shape → 递增 `PLUGIN_DIGEST_SCHEMA_VERSION` 并修订本 HN + 复跑样例 | — |

---

## 7. 不承诺 / 边界（Must NOT 对应）

- **不承诺未定 API**：本 HN 只描述 T45 已实现的 `lock_plugin` / `identity_fields` /
  `PluginVersionDigest` / `ResolvedDependency`。外部插件发行管线（entry-point 发现、
  安装）仍是 `NOT_VERIFIED`（T44/T45 记录），本 HN 不为其字段背书。
- **不混入内容身份**：`plugin_*` 字段是发布/执行身份轴；科学结果的内容身份
  （结构/能量/单位/输入签名）仍由 `cccp.calculation` 结果契约负责，两者并列记录、
  不互相替代。
- **B 不改 shape**：字段名/嵌套/类型由 C 侧 SDK 单点定义；B 消费 JSON dict 原样
  嵌入。shape 变更走 `PLUGIN_DIGEST_SCHEMA_VERSION` / `PLUGIN_IDENTITY_SCOPE_VERSION`
  递增 + 本 HN 修订。

---

## 8. 验收映射

| T46 验收项 | 证据 |
|---|---|
| 字段规范 + 示例值 | §2/§3 字段表 + §5 真实样例（samples JSON + 命令）。 |
| B 可直接照接（字段名/types/来源） | §2/§3 含类型与来源列；§3 含 B 消费建议。 |
| 字段冻结时点早于 B 执行身份发布 | §0.2 声明；shape 冻结于 T45（`b9283e5`），本 HN 在 B 冻结前交付。 |
| QA 用样例插件生成摘要并核对 | §6；样例含 T44 pilot + T45 example 两个真实插件 + 构造依赖样例。 |
| 锁定值与产物入身份 | §4.1/§4.6 语义；digest 变更规则实测。 |
| 不承诺未定 API | §7；外部发行管线保持 NOT_VERIFIED。 |
