# 项目化学网络契约（B0：结构版本网络）

> **2026-11-04 双人开发同步**：当前 HTTP/B0/B1 仍以本文为唯一契约；未来物种/结构/独立 TS、三类图、Chemical IR 和证据回写由[架构总纲 §10](ACP_3.0_Architecture_20261031.md)约束。A 主写网络领域/持久化适配/API/UI，B 评审身份与科学验证。§1 的“无分子身份”、§9/§10 末尾 B1 未完成是 B0 时点记录，已由 §8 的 B1 工作区实施记录更新；live 与反应边仍未验收。本次未重跑 B1，不提升历史证据。

**日期**：2026-11-03
**状态**：**B0 + B1 均已实现（后端 + 前端 + 持久化，本机可复验）**；live 验收（真实 Controller + 真实 DSH 面板）**未做**；PES/IRC/Batch 边未做
**上位计划**：[ACP-NEXT 插件计划](ACP_Frontend_DSH_Pluginization_Plan_20261102.md) §13.10.2 / §13.10.3
**用户决策**：2026-11-03 选择「B0 先出图，再补分子身份」，取代原「直接做项目化学网络」

本文件是 `GET /api/v2/projects/{project_id}/chemistry-network` 的**唯一契约**。实现、前端标签与验收一律以本文件为准；改动先改本文件。

---

## 1. 为什么有 B0

侦察结论（`src/acp/scheduler/structure_source_store.py:257`、`src/acp_services/store/naming.py:64`、`src/acp/results/structure_policy.py:32`、`src/acp/api/v1_routes.py:1438-1472`）：

**B0 侦察时尚无持久化分子身份；B1 已新增身份实现（§8），本段保留 B0 的设计原因。**

| 候选键 | 实际语义 | 能否作为分子节点 |
|---|---|---|
| `source_uid` = `ss_`+sha256(`job_id\n相对路径`) | 任务目录内的**文件位置** | 否：同一分子换路径即新节点 |
| `content_checksum` / `geometry_hash` = `sha256(原始 XYZ 文本)` | 单次几何的**字节哈希** | **B0 采用**：保守、不跨分子误并（空白/注释不同会分裂，可接受） |
| `tasks.molecule_key` = `" ".join(name.split())` | **显示名**键，注释明写 “NOT structure identity” | 否 |
| canonical SMILES / InChIKey | 仅 `/api/v1/molecule/resolve` 的**临时响应**，从未持久化 | 否 |
| `Provenance.molecule_hash` | **无生产者**（死字段） | 否 |

现有归并权威只有 `acp_services/store/molecule_groups.py::apply_group_merge`（**手工** API，`POST /molecule-groups/merge`）；`suggest_group_merges` 只读且 “never auto-applied”。

因此 B0 **不做任何分子级归并**。节点是「结构版本」。

## 2. 节点身份（唯一规则）

```
node_id = content_checksum        # 形如 "sha256:<64 hex>"
```

- 来源唯一：`structure_source_index.content_checksum`（由索引器写入，`src/acp/scheduler/structure_source_indexer.py`），**不由网络端点计算**。
- 同一 `content_checksum` 的多个任务/文件收敛为**一个节点**，`task_ids[]` 列出全部引用者。
- `content_checksum` 缺失（`NULL`/空）的记录**不得**退化成文件名或名称身份；这条记录被跳过并计入 `skipped`，原因 `missing_structure_hash`。
- **禁止**在 B0 里做：名称相似合并、SMILES 文本比较、InChIKey 前缀截断、几何容差合并。看 §7。

## 3. 边的种类

只使用**已持久化**的关系；不推算、不猜。`relation` 为封闭枚举，未知值不得出现在响应里。

| relation | 语义 | 权威来源 |
|---|---|---|
| `lineage` | 任务上下游（`source_job_id` / 产物引用） | `GET /api/v2/tasks/{id}/lineage` 同源的 `TaskEvidenceBuilder`（`src/acp_services/runtime/lineage.py`） |
| `shared_structure` | 两个任务引用同一结构版本 | 由 `content_checksum` 相同直接得出（非独立查询） |

**边的方向**：`lineage` 边记 `from_task → to_task`（上游 → 下游）；`shared_structure` 边不指向任务，而由节点的 `task_ids[]` 表达，**不重复发行**。实现时若 `shared_structure` 无独立信息，则不输出该边（宁可少边，不可造边）。

**B0 明确不包含**的边：PES「A → TS → B」三元组、IRC 端点绑定、Batch 父子关系 —— 这些需要从结果产物读取，属于 B1 与「边数据源」后续批次。

### 3.1 后续化学边来源（已侦察，尚未实现；不属于已落地的 B1 身份层）

侦察结论：**大多数“化学关系”并没有被记录成结构身份**，只能记录到任务/帧级别。下列为可用的、只读的候选来源；实现前必须逐条确认，不得补造。

| 候选边 | 已记录到什么程度 | 权威位置 | 能否在 B1 直用 |
|---|---|---|---|
| 任务上游 `uses` | `spec.input` 中的 `source.source_job_id` / `source.asset_id` / `source_job_id` / `from.source_job_id` | `src/acp/api/v2_routes.py:1633-1658`（`_extract_upstream_refs`，纯 DB 读） | ✅ 已是 B0 的 `lineage` 边 |
| 证据 DAG（`uses`/`produces`/`derives`/`revision`/`reuses`） | 每任务一份 `EvidenceProjection`，nodes+typed edges，纯本地读（1 次 store 读 + 1 次本地 manifest 读） | `src/acp_services/runtime/lineage.py:96-108,571-644`；路由 `v2_routes.py:1812-1852` | ✅ **B1 首选**，比 `/lineage` 更丰富且无昂贵扫描 |
| PES 候选 | 只记录「宿主 job_id + `view_type` + `frame_index`」与**目标**结构 `structure_path`；端点证据是**帧序号**，无 A/B 结构身份 | `src/acp/results/frame_candidates.py:224-269`、`src/acp/calculations/pes/review.py:621-645`、`src/cccp/analysis/pes/path_selection.py:469-503` | ⚠️ 只能表达「任务 → 候选结构」，不能表达 A→TS→B |
| IRC | `ts_source` = TS 任务的 `job_id` + `product_id`（`irc_ts_source_v1` 十字段锁定）；端点只记录 `direction`；`IrcResult(irc_id, ts_id, …)` 在 `acp` 侧**无写入者** | `src/acp/api/submission_contract.py:80-91`、`src/acp/calculations/irc/source.py:52-66`、`src/acp/calculations/primitives/irc.py:241-254` | ⚠️ 可得「IRC 任务 → TS 任务」，端点→结构需另行解析 |
| Batch 项 | 宿主任务的 `source_job_id` 已在 `spec.input`（即 `lineage` 已覆盖）；**产物无 `candidate_id`、无 source task** | `src/acp/calculations/batch/loaders.py:137-216`、`batch/engine.py:1018-1043` | ⚠️ 父子仅到任务级 |

**两条硬性排除**：

1. **禁止用 `/lineage` 的 downstream**：它按 `spec_json LIKE '%id%'` **全表扫描**（`v2_routes.py:1728-1761`），在项目级网络里不可用；B0/B1 只允许 upstream。
2. **禁止在 GET 路径上触发远端抓取**：`_job_read_root` 对终态任务会同步 `fetch_catalog`（`v1_routes.py:791-805`），`/tasks/{id}/results`、远程 `/tasks/{id}/tree` 因此有副作用。网络端点只用 DB 与本地 manifest，或用文档化的 cache-only reader（`acp_services/data/read_root.py`、`resource_read.py`）。

## 4. 端点

```
GET /api/v2/projects/{project_id}/chemistry-network
    ?limit=<int, 默认 200, 1..500>
    &cursor=<opaque string>
```

- 路由位置：`src/acp/api/v2_routes.py`（已挂载的 `v2_router`）。
- 门禁顺序（照 `v2_routes.py:565` 的既有规范）：`_manager(request)` → `_scoped_project_or_404(request, project_id)` → `manager.projects.get_project(project_id) is None` 时 404。未知与越权项目**同为 404**，`detail` 串一致（`Project not found: {project_id}`）。
- 纯投影：新模块 `src/acp/api/chemistry_projection.py`，只吃内存数据，**不得 import DB/server**（照 `science_projection.py:1-14` 的约束）；路由负责取记录。

### 响应（`src/acp/api/v2_schemas.py`）

```python
class V2ChemistryNetworkNode(BaseModel):
    node_id: str            # identity_kind=molecule_v1 时是 "mol_<24hex>"；否则 "sha256:<hex>"
    identity_kind: str       # 每节点自己的身份层级（混排时必须逐节点标注）
    scheme: str = ""         # 分子身份方案（"mol_identity_v1"），未识别时 ""
    task_ids: list[str] = Field(default_factory=list)
    molecule_names: list[str] = Field(default_factory=list)   # 仅展示，非身份
    canonical_smiles: str = ""       # 仅 identity_kind=molecule_v1 时有值
    inchikey: str = ""               # 元数据，永不参与身份判定
    formula: str = ""
    charge: int | None = None
    multiplicity: int | None = None
    atom_count: int | None = None

class V2ChemistryNetworkEdge(BaseModel):
    relation: str           # "lineage"（见 §3 封闭枚举）
    from_task: str
    to_task: str
    depth: int = 1

class V2ChemistryNetworkResponse(BaseModel):
    project_id: str
    nodes: list[V2ChemistryNetworkNode] = Field(default_factory=list)
    edges: list[V2ChemistryNetworkEdge] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(default_factory=list)  # [{"reason": ..., "task_id": ...}]
    truncated: bool = False
    next_cursor: str | None = None
    # 整图身份层级：
    #   "molecule_v1"          —— 全部节点都有分子身份
    #   "structure_content_v1" —— 全部节点只有结构内容哈希（B0 行为）
    #   "mixed"               —— 部分节点已识别、部分未识别（必须显式暴露，不得静默降级为整图分子网络）
    identity_kind: str = "structure_content_v1"
    identified_nodes: int = 0     # 有分子身份的节点数
    unidentified_nodes: int = 0   # 只有内容哈希的节点数
```

模型加进 `v2_schemas.py::__all__`。

**节点归并规则（B1 起）**：

1. 节点的分组键取 `structure_source_index.molecule_identity_key`；该键为 NULL 的行**不得**与有身份的行合并，而是各自回落到 `content_checksum` 分组。
2. 同一 `molecule_identity_key` 的所有结构文件收敛为一个节点，`task_ids` 列全部引用者。
3. 节点元数据（`canonical_smiles`/`charge`/`multiplicity`/`inchikey`）取自 `structure_identities`（`acp_services/store/molecule_identity.py`），**不重复计算**。
4. 整图 `identity_kind` 按 §5 的 `identified_nodes`/`unidentified_nodes` 推出；只要存在未识别节点就必须是 `mixed`，前端据此提示，不得宣称分子网络。

## 5. 副作用与预算（硬约束）

- **cache-only**：只做 `store`/`tasks`/`structure_source_index` 的 `SELECT`。禁止 `_job_read_root`、禁止 `fetch_catalog`、禁止 enqueue prepare、禁止写 store。
- 守卫测试必须断言 `manager.store.counts()` 前后不变（照 `tests/test_acp2_v2_science_routes.py:105-111`）。
- 上限：单次响应节点 ≤ `limit`，边 ≤ 4×`limit`；超限置 `truncated=true` 并给 `next_cursor`，**不静默截断**。
- 请求不得触发索引器扫描；索引缺失就如实少节点（`skipped` 记账）。

**实现期澄清（2026-11-03，B0 落地时确认）**：

1. **允许的例外——只读结构源存储的惰性构造**：路由用 `StructureSourceStore(manager.store.db_path)` 的进程级惰性实例读取索引；其构造函数会执行该存储自有的幂等 `CREATE TABLE IF NOT EXISTS` / migrate。这**不**启动索引器扫描、**不**触碰 `jobs` 计数，是本契约下唯一允许的非纯 SELECT 行为。选择它是因为仓库里的 `v2_structure_sources._get_stores` 会调用 `indexer.ensure_started()`，那才是被禁止的扫描。
2. **HTTP 层不可达的截断**：路由每请求最多取 `limit` 条源，因此节点上限不会触发；`truncated` 只可能来自边预算（`MAX_EDGE_LOOKUPS = 500`）与查询预算（≤2×5 页）。所以“不静默截断”的承诺在 HTTP 上由 `truncated`/`next_cursor` 兑现，不存在被吞掉的节点。
3. **测试分层**：纯投影 9 条可在任何平台跑；路由级 7 条依赖 `acp.api.server`，而 `acp_services.runtime.attempt_freeze` 在 Windows 上 `import fcntl` 失败——路由用例以显式平台 skip 记账（绝不记为绿），**CI 是首次真实执行**。

## 6. 前端契约

- 模块：`frontend/js/modules/reaction-network/`（复用，不新建第二套面板）。
- 数据源：新增可插拔 `deps.networkSource`（默认读本端点），**保留** legacy `mechanism-studies` 读取作为 project 缺省时的回退，并标注来源。
- 研究区主槽：`frontend/js/shell/recipes.js` 的 `research` 配方 `main` 由 `agent-workspace` 改为 `reaction-network`；`agent-workspace` 保留可被用户固定回槽。
- 布局迁移：`layout-preferences.js` 照 UI-04 的“跳过并重推默认”规则，丢弃 `research.main === "agent-workspace"` 的旧存档。
- UI 文案必须写明身份层级，**不得**自称分子网络：
  - zh：「结构版本网络 · 未做分子归集」
  - en：`Structure-version network · no molecule merging`
  - 横幅由 `identity_kind` 决定：`structure_content_v1` → 上述文案；`molecule_v1` → 才可显示「分子网络」；`mixed` → 「分子网络（含未识别结构）」/ `Molecule network · some structures unidentified`（必须同时显示 `identified_nodes`/`unidentified_nodes` 计数）。
- 交互：点节点 → `commands.openStructure({structureRef})`（定位分子工作区）+ `setFocus({networkNodeId})`；点 lineage 边 → `commands.openTask(...)`。
- 四态：`未连接`（Controller 不可达）/ `空态`（项目无结构）/ `就绪` / `错误+重试`。**不得**用 fixture 冒充 live。

## 7. 禁止清单（违反即为科学缺陷）

1. 用名称、SMILES 文本、InChIKey 前缀、显示名做节点合并。
2. 把 `molecule_key` 或 `molecule_name` 当作身份键。
3. 在没有 `content_checksum` 时用文件名/时间戳补一个身份。
4. 把 B0 的图宣传成“分子网络”或用于驻点/端点判定。
5. 为凑出好看的图而添加未持久化的推测边。

## 8. B1 分子身份层（**已实现**，B1a–B1d）

设计口径（实现前定的，保持有效）：

- 身份键 = `mol_` + sha256(canonical JSON)[:24]，payload 仅含 `scheme` / 异构 canonical SMILES / `charge` / `multiplicity`；几何、文件名、显示名、InChIKey **一律不进键**；
- 无法判定身份（键态不明、键感知失败）时返回 `key=None` + 封闭原因码，**不猜**，该结构节点回落到 `content_checksum`；
- 归并仍然**手工**（沿用 `apply_group_merge`），身份层只回答“是不是同一分子”，不接受自动归并。

实施落点与证据：

| 批次 | 落点 | 证据 |
|---|---|---|
| B1a 科学层 | `src/cccp/analysis/structure/identity.py`（`SCHEME="mol_identity_v1"`、`compute_molecule_identity`、`identity_key_payload`） | 20 条单测；另行用真实乙醇（SMILES / 两种 xyz：不同构象+原子倒序）验证三者同键，(R)/(S)-乳酸异键，碎片输入 `charge_required` |
| B1b 持久化 | `src/acp_services/store/molecule_identity.py` + migration **`029`**：`structure_identities(project_id, identity_key, scheme, canonical_smiles, charge, multiplicity, formula, inchikey, first_seen_at, updated_at)` PK(project_id, identity_key) | 23 条单测（含「老库无此表也能升级」与「重跑幂等」） |
| B1c 写入/回填 | `structure_source_index.molecule_identity_key`（幂等加列）、发现时按原始 xyz 计算身份、登记表写入并入同一事务、索引器有界幂等回填 | 117 条（`test_acp_structure_source_identity.py` + 结构源全量，本机以 POSIX 平台垫片执行；CI 为权威） |
| B1d 读面 | `chemistry_projection.py` 身份优先归组（identified/unidentified 结构性不可合并）+ `molecule_v1`/`mixed` 分层；路由只读连登记表、失败降级；前端 `mixed` 横幅带计数、`moleculeIdentityKey` 透传 | 26 条（含 10 条路由用例，本机以平台垫片执行）；前端 1152/0 |

**仍未做**：PES/IRC/Batch 边（§3.1 已侦察出它们大多没有结构级身份）、2D 绘制的原子映射、`molecule-viewer` 按 `moleculeIdentityKey` 解析（当前 viewer 会显示自身错误态，不会显示错分子）、真实 Controller/DSH 下的 live 验收。

## 9. 验收

| 层 | 用例 | 通过条件 |
|---|---|---|
| 单元 | 投影纯函数 | 同 `content_checksum` 收敛；缺哈希跳过并记账；边只来自封闭枚举；超限置 `truncated` |
| API | 项目隔离 | 未知/越权项目均 404 且 detail 一致；`limit` 越界被拒 |
| API | 无副作用 | `manager.store.counts()` 前后一致 |
| 前端 | 面板四态 | 未连接/空/就绪/错误可复现；点节点跳分子工作区 |
| 文案 | 身份诚实 | `identity_kind=structure_content_v1` 时**不得**出现“分子网络”字样 |
| 回归 | 双宿主 | `/next/` 与 DSH 面板同一份实现；前端套件 + 插件测试 + rph 全绿 |

**分项状态**：B1 身份代码与读投影见 §8，仍不关闭手工业务归并、构象聚类、PES/IRC/Batch 化学边及项目级 2D/语义编译；live 验收独立。

---

## 10. 实施证据（2026-11-03）

| 层 | 交付物 | 证据 |
|---|---|---|
| 后端纯投影 | `src/acp/api/chemistry_projection.py`（只 import `typing`） | 9 条纯测试 PASS |
| 后端路由 | `GET /api/v2/projects/{id}/chemistry-network`（`v2_routes.py:1984`），门禁与 404 对齐，`MAX_EDGE_LOOKUPS=500` | 路由级 7 条在本机 `skip`（Windows 无 `fcntl`），CI 首跑 |
| Schema | `V2ChemistryNetworkNode/Edge/Response`（`v2_schemas.py`），`identity_kind="structure_content_v1"` | `model_validate` + key-set 相等断言 |
| 前端 | `reaction-network` 走 `deps.networkSource`；四态 + 身份横幅；节点/边导航；`identity_kind` 决定文案 | 前端套件 1148/0，其中 `reaction_network` 10 条 |
| 布局 | `recipes.js` 研究区主槽 = 网络画布；`layout-preferences.js` 一次性迁移（标记 `acp-next-layout-network-migrated`）；`agent-workspace` 仍可固定 | `layout_preferences` 8 条 + `shell_bootstrap` 7 条 + `shell_a11y` 10 条 PASS |
| 插件资源 | `build.mjs` 重出 31 文件（含内联适配器 + `runtime-scope`），`--check` 通过 | digest `9f9cc6191437ba01`；插件测试 84/0 |
| 提交版清单 | `static-assets.manifest.json`（217 文件）+ `release-manifest.json.static_assets` 同步 | M5-01 一致性测试 PASS |

**仍未验收（写死为未通过）**：真实 Controller 下的 HTTP 路由（本机 Controller 未运行）、真实 DSH 面板内的渲染与点击导航、浏览器套件 `acp2_next_entry.browser.js`。B1 的后续工作区实施记录见 §8，本段仅记录 B0 时点，不撤销 §8 或提升 live 状态。

## 11. 后续化学语义扩展与兼容（2026-11-04，待实现）

1. B1 的同 molecule key 聚图是读投影，不等于 `apply_group_merge` 业务归并，也不意味着同构象/同驻点。任务名、分子式、InChIKey 不可替代身份。
2. 当前 identity 实现可能发出 `connectivity_only`、`stereo_ignored`、`multiplicity_not_used_by_bond_perception` 等警告；B1 不能据此证明完整 Chemical Species 身份正确。C0/C2 由 B 评审适用域，A 传递/展示警告；规则升级用新 scheme 与映射，不原地改写 mol_identity_v1 的键。
3. 新 Species/Conformer/StationaryPoint/TS/ElementaryStep/Claim 独立建模；ReactionStep 允许多参与者与计量，TS 不按普通键图合并。具体 IR/claim schema 依架构 §10.4 的 C0 交接冻结，不在现行 `relation` 枚举偷偷增值。
4. 现有 lineage 边仍表示任务谱系，不升级为化学反应边。PES 候选、Batch item、TS 振动、双向 IRC/端点及能量比较须由版本化证据来源关联，缺项为 unassessed/inconclusive，不能造边。
5. 新网络写入走 Services 的项目权限、CAS、幂等、事件与 Artifact；GET 保持 cache-only。旧结构哈希/混合身份仍可读；升级响应需显式版本/协商，旧客户端不接受未知 relation。
6. 科学验证器 B 产出证据判定，A 的服务适配验证引用/项目/attempt/revision 后更新 claim 投影。计算终态与证据结论独立；错端点保留原假设并追加证据，不删除。

退出门与任务进度仅在架构 §10.6—10.8 登记；本文的 B0/B1 历史计数不是 C2/C4 的 PASS。
