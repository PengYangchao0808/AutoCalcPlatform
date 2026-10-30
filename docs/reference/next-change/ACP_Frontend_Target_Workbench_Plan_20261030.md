# ACP 前端目标形态方案（对齐样板图 v1）

日期：2026-10-30
性质：**方案与分期计划，不是实现记录**。本文按用户样板图（设计板 + 手绘稿）重定 Next 入口的目标形态；已实现部分标注事实，未实现部分标注依赖与门禁。
上游：`ACP_2.0_前端同步升级开发方案_20261029.md`（§6 分期）、UI-01 设计规格、[UI-03/04/05 收口记录](ACP_Frontend_UI03_UI04_UI05_Closure_20261030.md)
取代关系：本文重新定义 UI-05 的「组装深化」与 UI-06 的范围；UI-00 的 DSH 结论、UI-03 的提交契约继续有效。本文不改变任何已完成契约。

---

## 0. 结论先行

样板图要的不是「三个页签 + 若干面板」，而是**一个化学工作台内核**：

```
统一 Shell（项目上下文 / 模块注册 / 布局 / 权限事件）
   ├─ 研究：Agent（原生 DSH 网页版） + 项目级化学网络
   ├─ 分子：2D 绘制（预留）+ 3D + 属性（能量曲线先行，轨道后续）
   └─ 任务：配置 + 任务/进度 + 文件 + 主机（+ 权限受控终端）
全局：左侧「化学回路」项目分子导轨，三个工作区共享
```

与当前实现的根本差距有四类，必须分别处理，不能靠加面板糊过去：

| 差距 | 性质 | 处理 |
|---|---|---|
| 布局只有 `main/side/bottom` 三槽，没有左导轨与多列 | Shell 能力 | **Shell v2：具名槽位 + 每工作区模板**（P1） |
| 化学网络按「任务/结构来源」平铺，没有「按键连接关系归并的项目分子图」 | 数据契约 + 新服务 | **连通性归并服务 + `/api/v2/projects/{id}/chemical-network`**（P2，先 spike） |
| Agent 是只读投影，不是真实 DSH | 宿主集成 | **DSH 嵌入：A1 iframe 验证 → A2 托管启动/反代**（P0 决策，P2 落地） |
| 第三视图是「任务 + 证据」，没有终端/文件/主机 | 组件 + 安全边界 | 文件/任务/主机复用+抽取（P4），终端**分两级**（P4/P5） |

### 0.1 五个诉求对照

| 诉求 | 方案落点 | 关键事实/依赖 |
|---|---|---|
| ① Agent 原生支持 DSH 网页版 | §2.1 A1/A2 | DSH 无 XFO/CSP，可 iframe；鉴权是 per-process token + Strict cookie；无 postMessage，上下文走 MCP |
| ② 化学网络按「相同键连接关系」归并 | §2.2 | InChIKey 骨架为主键；新增迁移 024 + 索引器挂载；现名称分组不可用（`CCO`/`OCC` 会分家） |
| ③ 2D 绘制/识别先留位 | §2.3 | 经典页也没有 2D 画布；服务端无 2D 坐标；先占位 + JSME/Ketcher spike；图像识别不排期 |
| ④ 结构调整同步经典 + 能量曲线先行 | §2.4 | 复用 `structure_editor` 纯函数 + `geometry_store`；能量用已抽取的 `energy_chart_renderer` |
| ⑤ 第三视图重做 | §2.5 | Shell v2 五/六槽；文件层 controller-ready；进度双数据源；终端 L0 结构化动作、L1 PTY 单独立项 |

---

## 1. 目标布局（对齐样板图）

### 1.1 Shell v2：具名槽位

当前 `layout-manager.js` 硬编码 `["main","side","bottom"]`。改为**每工作区一份模板**：

```js
// recipes.js（目标形态）
research: {
  template: {
    columns: "56px minmax(0, 1.35fr) minmax(340px, 1fr)",
    rows: "minmax(0, 1fr)",
    slots: ["rail", "agent", "network"]
  },
  panels: [ ... ]
}
molecular: {
  template: {
    columns: "56px minmax(0, 1fr) minmax(0, 1fr) minmax(280px, 340px)",
    rows: "minmax(0, 1fr)",
    slots: ["rail", "draw2d", "view3d", "properties"]
  }
}
execution: {
  template: {
    columns: "56px 300px minmax(0, 1fr) minmax(0, 1fr) 280px 200px",
    rows: "minmax(0, 1fr)",
    slots: ["rail", "config", "terminal", "tasks", "files", "actions"]
  }
}
```

- 研究区的「对话历史 / Agent 对话」两列由嵌入的 DSH 自带（§2.1），ACP 不重造聊天；走 C 回退时才用 `agent-workspace` 的只读卡片占位。
- 槽位由模板生成（`acp-slot-<ws>-<slot>`），不再写死在 `index.html`；旧三槽模板作为默认，现有三条 recipe 行为不变。
- `singleton` 面板跨工作区迁移（viewer 已有机制）推广到 `project-molecules` 导轨。
- 布局偏好升级为 v2（按槽位存 panelId + 尺寸），带 v1→v2 迁移；布局编辑器按模板动态列槽位。
- 面板 chrome、加载/空/错误/无权限四态、`data-acp-module-state` 契约保持不变。

**验收**：三条旧 recipe 的 DOM 快照、键盘 Alt+1/2/3、布局编辑器、偏好迁移全部有测试；新模板在 1440×900 与 1280×720 不出现横向滚动。

### 1.2 三个工作区

| 工作区 | 槽位 | 模块（新/旧） | 对应样板 |
|---|---|---|---|
| 研究 | rail / agent / network | `project-molecules`(新) · `agent-dsh`(新) · `chemical-network`(重写) | 一 |
| 分子 | rail / draw2d / view3d / properties | `project-molecules` · `structure-2d`(新, 先占位) · `molecule-viewer`(现有) · `molecule-properties`(新) | 二 |
| 任务 | rail / config / terminal / tasks / files / actions | `project-molecules` · `task-config`(现有) · `task-console`(新, 先命令面板) · `task-explorer`(扩进度) · `file-browser`(新) · `remote-actions`(新) | 三 |

> 手绘稿里的「标题/选项/工作区/配置」竖排是应用级导航；ACP 已有顶栏工作区切换，**不新增第二套导航**，只把「化学回路」做成全局导轨。

---

## 2. 专题方案

### 2.1 专题一：Agent 原生支持 DSH 网页版

**已核实事实（2026-10-30，安装版 DSH 0.2.1-rc.3）**
- DSH 可独立起 Web，不必依赖 Electron 窗口：`dsh web`（= `--profile web`），支持 `--host / --port 0 / --public-url / --no-open / --patch`；启动后 stdout 打印 `dsh web: <authenticatedUrl>`（含一次性 launch token）。
- 无凭据 `GET /` 返回 **401**；但 **没有 `X-Frame-Options`，也没有 CSP `frame-ancestors`**（307 个 first-party 包 grep 0 命中）→ iframe 不被响应头禁止。
- 鉴权：`?token=` 换 HttpOnly cookie `dsh-auth-*`，`SameSite=Strict`。`127.0.0.1:8765 → 127.0.0.1:58708` 同 site，iframe 会带 cookie；`localhost` 与 `127.0.0.1` 不同 site，会掉 cookie。
- **没有** postMessage / 嵌入 SDK / iframe 路由；DSH 的 ACP 面只有 **stdio**（`dsh --profile acp`），HTTP/WS ACP 为 ABSENT；ACP 侧 research REST 只有两个 GET，提交必须走 MCP 工具。
- 仓库已有现成装配：`rph/packages/dsh-bundle/profiles/web.cordis.yml` 用 `--patch` 注入 `dsh-mcp-client`（stdio 指向 `python -m acp mcp serve --transport stdio --controller …`）与 `rph-research` preset——**真 DSH 聊天 + ACP 八工具**，不需要另造协议。
- DSH 客户端插件面存在（`dsh.client` 扫描、`__DSH_BOOT__`、index tap、`dsh plugin`），但无现成插件、`rph` 无 dist/构建产物。

**四档方案**

| 方案 | 做法 | 成立条件 | 代价/风险 |
|---|---|---|---|
| **A1 直接 iframe**（最快验证） | Agent 槽位 iframe `http://127.0.0.1:58708/`（复用浏览器已有会话） | 用户已打开 DSH；固定端口；同 site cookie | 依赖外部进程；换端口/换用户不可移植；无上下文注入 |
| **A2 托管启动 + 同源反代**（推荐产品化） | ACP 后端 spawn `dsh web --profile web --patch rph/…/web.cordis.yml --port 0 --no-open`，解析 stdout URL；`/dsh/*` 反代并注入 launch token，前端只看到同源 `/dsh/` | 允许 ACP 拉起 DSH；`--public-url /dsh` 支持子路径；代理保活 | 进程生命周期/升级；token 仅服务端；Origin/Host/缓存策略 |
| B 客户端插件（长期） | ACP 面板做成 DSH client plugin（`./client` + `./typert`），DSH 当宿主 | 需要构建链、rph dist、插件发布 | 方向反转；跨源身份；工作量最大 |
| C 回退 | 启动器 + ACP SSE 事件镜像（现有只读投影增强） | 无 DSH 时 | 不是原生体验 |

**建议**：先 A1 用半天确认 iframe + cookie 真的可用（这是全部方案的前提），再 A2 做自包含产品化；B 留到 rph 有构建产物之后；C 永久保留为降级态。

**任务权威与闭环**：DSH 通过 MCP `mcp__acp__acp_*` 提交/查询；ACP 用既有 SSE 与任务列表观察同一 `job_id`；**前端不新增第二条提交通道**，也不把 DSH 会话当任务真值。

**安全边界**：launch token 同时能换浏览器 cookie 与 API operator（等同完整 Agent 控制权）——只能存在于 ACP 服务端与 `/dsh/*` 反代的内存中；反代仅回环、按用户隔离、不缓存、不改写 `Host/Origin` 之外的头；iframe 先不加 `sandbox`（会破坏应用），但需评估 `allow`/`referrerpolicy`；日志不得记录 token。

**验收**：A1——iframe 首屏可交互、`/api` 调用成功、刷新保持登录；A2——spawn→URL→反代→刷新→在 DSH 聊天里提交一次 `xtb_optimize`→ACP 任务列表 10 s 内出现同一任务且无重复；DSH 未安装/未启动→自动降级 C 并可一键重试；token 不出现在任何前端响应/日志。

### 2.2 专题二：项目级化学网络（按键连接关系归并）

**语义（需用户确认，见 §7-D3）**
- 节点 = **一种键连接关系**（constitutional graph）：忽略构象、忽略立体化学；**电荷/质子化/自由基状态参与身份**；互变异构体视为不同节点（是否归并留作开关，默认关）。
- 成员 = 该连接关系下的所有结构实例（不同任务/attempt/构象/能量点）。
- 边 = **有证据的路径关系**：PES 候选→Batch 项、IRC 两端、历史 mechanism 网络、任务 lineage；没有证据就不画边，只显示孤立节点。

**现有数据事实（已核对）**
- 项目结构全集在 `structure_source_index`（`src/acp/scheduler/structure_source_store.py:113-143`），主键 `source_uid = "ss_"+sha256(job_id+"\n"relpath)[:24]`；已有 `formula/atom_count/charge/multiplicity/role/candidate_id/content_checksum`，**没有 SMILES/InChI/连通性**（`structure_sources.py:1345` 硬编码 `smiles=None`）。
- `content_checksum` 是整段 XYZ 的字节哈希 → 每个构象都不同，**只能做几何指纹，不能做分子身份**。
- 现有 `molecule_group_key` 只做名称字符串归并（空白折叠、保留大小写，`acp_services/store/naming.py:64-74`）；SMILES 提交的任务 `molecule_name` 就是 SMILES 文本 → `CCO` 与 `OCC` 会被分成两个组。**名称分组不能替代本方案。**
- RDKit 是核心依赖（`pyproject.toml:26`），`rdDetermineBonds` 感知键已有先例（`cccp/analysis/structure/topology.py:130-207`、`v1_routes.py:1268-1279`）；`POST /api/v1/molecule/resolve` 已能返回 canonical SMILES + InChI + InChIKey。
- 跨任务的构象身份目前 **ABSENT**：`structure_viewer` 的 entry id 是几何级（`conf_*`/`pes_*`/`batch_*`/`irc_*`），`Provenance.molecule_hash` 字段存在但无写入方。

**身份算法（新科学基元，建议分层）**
- 新增 `src/cccp/analysis/structure/connectivity.py`（与 embedding 的真实驻点同包）：
  1. **首选** `InChIKey` 前 14 位（骨架 + 氢层）：同键连接关系的判定基准；立体化学不进 key。
  2. InChI 不可用 → `MolToSmiles(mol, isomericSmiles=False)`（去立体）作为 `ck1:smiles:` 命名空间。
  3. 键感知失败 / 电荷未知 / 多重度>1（`topology.py` 会拒绝）→ `ck1:degraded:<formula>#<heavy_atom_count>` **独立命名空间**，绝不与 1/2 合并。
- 键感知走 `rdDetermineBonds.DetermineBonds(mol, charge=行内电荷)`；结果带 `identity_source: molfile|smiles|perceived_xyz|degraded`、`status: ok|degraded|unresolved` 与 `warnings`。
- **科学诚实**：从 3D 坐标感知的键不是权威结构；UI 必须在节点/成员上标注「由几何感知」。立体异构默认并入同一连接节点（符合「相同键连接关系」），但节点保留 `stereo_variants` 计数并提供 `stereo=split` 开关（按完整 InChIKey 拆分）。
- key 版本号 `CONNECTIVITY_KEY_VERSION`（`ck1`）；版本变更即整表可重算。

**服务与契约**
- 新增 `acp_services/chemistry/`（服务层唯一实现根，禁 import `acp`）：聚合用例 + 缓存读写 + 端口（`ConnectivityKeyPort` 由 acp 侧注入 cccp 实现）。
- 缓存：迁移 **024** 新表 `structure_connectivity(source_uid PK, key_version, connectivity_key, canonical_smiles, inchikey, formula, heavy_atom_count, bond_count, status, input_checksum, computed_at)`，索引 `(key_version, connectivity_key)`；`input_checksum` 复用 `content_checksum`，索引重建只重算变化的几何。**请求时现算不可行**（500 结构 = 500 次读文件 + RDKit ≈ 1–3 s，远端行还要 SFTP）；计算挂在既有结构来源索引器的 `_index_job` 里，随 60 s 增量扫描走（每结构约 1–5 ms，500 结构一次性 0.5–2.5 s），面板查询变成毫秒级 SQL group-by。
- 新路由 `GET /api/v2/projects/{project_id}/chemical-network`：

```json
{
  "project_id": "proj_…", "generated_at": "…", "key_version": "ck1",
  "nodes": [{
    "connectivity_key": "ck1:inchikey:AAAA-BBBB-CCCC-DD",
    "canonical_smiles": "CCO", "inchikey": "AAAA-BBBB-CCCC-DD",
    "formula": "C2H6O", "charge": 0, "multiplicity": 1,
    "heavy_atom_count": 3, "bond_count": 8,
    "identity_grade": "exact|degraded",
    "stereo_variants": 2,
    "structure_count": 7, "task_count": 3,
    "representative_source_uid": "ss_…",
    "members": [{
      "source_uid": "ss_…", "job_id": "…", "relative_path": "RESULT/…",
      "source_kind": "final|saved_candidate", "role": "TS|INT|",
      "energy_hartree": null, "availability": "available|pending_fetch|unavailable",
      "identity_source": "perceived_xyz", "connectivity_status": "ok|degraded|unresolved"
    }]
  }],
  "edges": [{
    "edge_id": "…", "from_key": "ck1:…", "to_key": "ck1:…",
    "relation": "irc_forward_endpoint|irc_reverse_endpoint|pes_candidate|batch_item_source|task_lineage",
    "task_id": "…", "evidence_doc": "RESULT/result_manifest.json",
    "evidence": {"product_id": "irc_forward_endpoint", "direction": "forward"},
    "confidence": "explicit|inferred"
  }],
  "stats": {"structures_total": 42, "canonicalized": 40, "degraded": 1, "unresolved": 1, "edges": 5},
  "indexing": {"indexed_jobs": 12, "pending_jobs": 1, "last_indexed_at": "…"}
}
```

- **边的证据边界（不要越界）**：只有 IRC 端点是结构→结构的强证据（`irc_forward_endpoint`/`irc_reverse_endpoint` 产物 + `EndpointMatchResult`）；PES 候选与 Batch 项只有 `source_ref`/`candidate_id` 弱关联，标 `inferred`；任务 lineage 是任务级，**不得**冒充结构级边；历史 mechanism 网络按原有 per-study 图显示，不并入项目图。没有证据就是孤立节点。
- 远端行（`pending_fetch`/`pending_sync`）在几何进缓存前保持 `unresolved`，不参与归并。
- 与现有名称分组的关系：新图**先做加法**（rail 与网络用连接性分组），`group_by=molecule` 暂不动；等新图覆盖稳定后再决定是否替换，避免两套分组互相污染。

**前端**
- `chemical-network` 重写：BFS 深度布局（复用现有 SVG 经验）、节点大小=成员数、颜色=角色、边标签=能垒/关系；点击节点 → 右侧详情（成员表、来源、能量、打开 2D/3D、新建任务预填）；点击边 → 证据（job/artifact）。
- 左侧 `project-molecules` 导轨消费同一响应：按成员数/最近活跃排序，搜索，点击聚焦网络节点。
- 空态/降级态必须说明「项目无可识别结构 / N 个结构无法解析」，不得画假节点。

**验收**：同分异构体分组正确；构象归并；质子化分离；盐/多片段单节点；金属/异常价态落 `unresolved` 并可见；1000 结构响应 < 2 s（缓存后 < 300 ms）。

### 2.3 专题三：2D 结构绘制与识别（先留位）

- 新增 `structure-2d` 模块：工具栏（选择/键/环/文本/橡皮/撤销）+ 画布区 + 右侧属性；未配置引擎时画布显示**明确的占位说明**（不画假分子），并保留 `import/export` 契约位：`{molfile, smiles, width, height}`。
- 注意：经典页的 `frontend/js/structure_editor.js` 是 **3D 内坐标编辑**（键长/键角/二面角调整），不是 2D 键线式画布；2D 绘制能力在经典页同样 **ABSENT**，不能误当已有组件。
- 引擎选型（P0 spike，二选一 + 渲染备选）：

| 候选 | 许可 | 与 no-build 架构 | 能力 | 结论 |
|---|---|---|---|---|
| JSME | BSD-3 | 单文件 classic script，最契合 | 键编辑/环/文本，molfile/SMILES | **首选 spike** |
| Ketcher | Apache-2.0 | 需打包（React+WASM） | ChemDraw 级、模板/反应 | 若必须 ChemDraw 手感则评估引入构建步骤 |
| Kekule.js | MIT | classic script | 编辑+渲染 | 备选 |
| SmilesDrawer | MIT | classic script | 只读渲染 | 仅用于节点/列表预览 |

- 识别（`识别`）分两级：① 粘贴/导入 SMILES/MOL/XYZ（已具备，`/structures/parse`）；② 图像识别（OSRA/DECIMER 类）**NOT_VERIFIED**，不排期，只留接口位。
- 2D→3D：服务端已有 `POST /molecule/embed`；2D→2D 坐标存 molfile，不新增科学逻辑。

**验收**：占位态不误导；引擎接入后 SMILES/MOL 往返一致；`structureRef` 与查看器/网络互通；切换工作区不丢未保存编辑（草稿）。

### 2.4 专题四：结构调整工具同步经典 + 能量曲线先行

**已核实事实**
- 经典页已有**结构编辑内核**：`frontend/js/structure_editor.js`（`window.ACPStructureEditor`，76 KB）——纯函数 `editBondLength/editBondAngle/editDihedral`、`buildAdjacency`、`fragmentsAfterCut`、`checkCollisions`、`buildEditedXyz`、`applyMeasuredEdit`，以及事务/undo/redo/`saveEditedAsset`。它做的是**3D 内坐标编辑**（只平移/旋转片段，不改键拓扑），不是 2D 画布。
- 经典测量在 `features/molecule-view.js`：`measurementValue/formatMeasurementDisplay/doAngle/doDihedral/handleMeasureClick`，与页面 DOM 耦合。
- 能量曲线/反应网络是 `features/energy.js` 的**纯 SVG 字符串构建**：`buildEnergyProfileSvg`（:718）、`buildReactionNetworkSvg`（:575），但依赖页面全局 `t/escapeHtml/formatMechanismNumber`。
- 2D 绘制画布在经典页 **ABSENT**（`structure_editor` 不生成 2D 键线式）。

**方案**
- **共享编辑/测量控制器**（不做第二套实现）：新建 `frontend/js/workbench/controllers/structure_tools_controller.js`，沿用 `candidate_review_controller.js` 的「纯状态机：无 document/无 fetch/无 $3Dmol/无页面全局」范式，注入 `{store: ACPGeometryStore, editor: ACPStructureEditor, gating: editBlockReason, t}`。经典页与 Next 各写一个薄适配层：经典用现有 scene 绘制与 edit bar，Next 用 sanctioned scene API 绘制。
- **测量数学去重**：`molecule-view.js:23-57` 的 `measurementValue` 与 `geometry_store.js:326-361` 的 `computeMeasurementValue` 是同一套数学（后者已有 golden test）；共享控制器只认 `ACPGeometryStore`（Next 已加载），删除重复实现。
- **编辑内核**：`structure_editor.js` 的纯函数（`editBondLength/editBondAngle/editDihedral`、`buildAdjacency`、`fragmentsAfterCut`、`checkCollisions`、`buildEditedXyz`）已是无 DOM 的，抽到共享模块供两端加载；编辑范围仍限「键长/键角/二面角 + 片段刚性移动」，**不改键拓扑、不加删原子**（这是经典契约，2D 加键属于专题三的新引擎）。
- **能量曲线先行（用已有渲染器，不抽旧机制图）**：Next `molecule-properties` 直接复用 **`frontend/js/visualization/energy_chart_renderer.js`**（已按 `init(deps)` 注入、23 个导出、已有 `energy_renderer.test.js`），数据是 `GET /api/v1/jobs/{id}/energy-graph` 的 `EnergyGraphResponse`（series/nodes/annotations）。**不要**把 `buildEnergyProfileSvg` 当作 Next 面板的数据源：它消费 `report.mechanism_profile.routes[].points[].energy_kcal`，与后端 reaction-path 构建器（`routes[].methods[...]` + `energy_hartree`）形状不一致，且 mechanism 已退役、无写入方——只服务历史数据。
- **不要第三次实现网络 SVG**：Next 的 `reaction-network` 已有自己的渲染器；经典 `buildReactionNetworkSvg` 保持原状，二者只在数据契约上对齐。
- 轨道/波函数/振动/系综：tab 占位 + 数据缺口说明；轨道需要 cube/轨道数据端点（当前 **ABSENT**），不在本方案承诺。

**验收**：同一 job 在经典页与 Next 的能量曲线数值/标注一致（同一 fixture）；同一组测量/编辑操作在两端产生逐字段相同的结果（键长/角度容差内）；抽取后经典页回归全绿；Next 不再保留重复的测量数学。

### 2.5 专题五：第三视图重做（任务与文件工作台）

目标：从「任务 + 证据」变成**任务配置 / 任务与进度 / 文件 / 主机与远程 / 终端**五件事一屏。

**已核实事实**
- **文件浏览器**：经典 `features/files-logs.js`（639 行）的数据层已接近 controller-ready——`buildTree`/`sortedEntries` 是纯函数，folder cache、`filesSessionUnchanged` 会话守卫、3/6 tick 刷新策略都不依赖 DOM；渲染层每次全量重建、**无虚拟化**，Next 没有对应模块。端点：`GET /jobs/{id}/files?path=`、`/remote-files?path=`、`/logs?lines=`、`/irc/log-tail`、`/remote-files/{path}/preview?mode=`。
- **进度图**：经典有三套渲染（`job-detail.js:330-474` 时间线、`queue.js:1828-1858` 进度条、`job-detail.js:808-829` 步骤条），数据来自**两处**：`V1JobRecordModel` 的 `stage_index/stage_total/stage_order/stage_progress/stage_detail/live_status`（源头 `state.json`）＋ `GET /jobs/{id}/detail` 的 `detail.stages`（标签/状态，源头 `stage_tasks`）。Next 目前一个都没画；v2 `TaskRowProjection` 已带 `stage_index/stage_total/stage_detail/progress_state/live_status`，但**缺 `stage_order`/`stage_progress`**；`job-detail-query.js` 已在 Next 加载。队列百分比只认 `job.progress`，不是 `stage_progress`。
- **终端**：**完全 ABSENT**。FastAPI 无任何 `@router.websocket`；仓库无 `pty/termios/winpty/conpty/xterm`；无 exec/shell/command 路由；进程启动只在 scheduler 的 `subprocess.Popen`。权限面已有 `EntryClass`（7 值，未知类 fail-closed）、`EntryAuthorizer`、`AccessAction`、`AdminAuditLog`；`RestrictedBroker` 明确拒绝命令字符串（只接受结构化参数，禁 `sudo/shell/command/args/script/exec`）。

| 槽位 | 模块 | 内容 | 现状 |
|---|---|---|---|
| rail | `project-molecules` | 项目分子导轨 | 新 |
| config | `task-config`（现有） | 任务类型/方法/基组/资源/高级参数 | **已有**（4 工作流端到端） |
| terminal | `task-console` | 先「结构化命令面板」，后 PTY | 新 |
| tasks | `task-explorer`（扩） | 任务列表 + 进度图 + 当前任务状态 + 节点 | 已有列表，需加进度 |
| files | `file-browser` | input/output/scripts/data/results 树、预览、下载 | 经典数据层可抽，渲染新写 |
| actions | `remote-actions` | 本地/远程切换、节点选择、SSH 文件、远程调度入口 | 新（复用 `/nodes`、`/remote-files`） |

**文件浏览器**：抽 `frontend/js/workbench/controllers/file_browser_controller.js`（树构建、folder cache、会话守卫、刷新策略，沿用 `filesSessionUnchanged`），Next 渲染层新写（树 + 预览 + 下载 + 远程标记）；虚拟化先不做，超过 500 行再评估。

**进度图**：不做第三套投影——用 `job-detail-query.js` 取 `detail.stages`，`stage_order/stage_progress` 从 `GET /api/v1/jobs/{id}` 取，或给 v2 `TaskRowProjection` 补这两个字段（二选一，需在实现前定）；百分比用 `job.progress`。

**终端分级（安全门禁）**
- **L0 结构化命令面板**（P4）：不是自由 shell，而是服务端定义的动作（`list_files`、`tail_log`、`job_status`、`remote_queue` 等），POST `{action, params}`；参数白名单、任务目录 jail、`EntryClass` 新增一类并 fail-closed、`AdminAuditLog` 同形审计；默认只读。这与 `RestrictedBroker` 的「拒命令字符串」原则一致。
- **L1 PTY**（P5，安全评审立项）：FastAPI WebSocket 路由（逐消息授权，不是只授权连接）+ POSIX `pty` / Windows ConPTY（新依赖）+ cwd jail + 命令前缀白名单 + 空闲超时 + 全量审计 + 按项目授权 + 默认关闭。**不提供无限制 shell**。
- 样板图「终端功能需要权限控制」按此落地；无权限时面板显示 denied 态与申请路径。

**验收**：无权限身份看不到可执行终端（denied 态）；L0 动作只影响任务目录且有审计；文件预览/下载走既有 entry-access 门禁；远程文件读取走 `_job_read_root`/缓存契约；进度图在 running/terminal 两种任务上都与经典页显示一致。

---

## 3. 架构落点（不新增平行实现）

| 能力 | 层 | 落点 | 规则 |
|---|---|---|---|
| 连接性 key / 键感知 | 科学基元 | `src/cccp/analysis/structure/connectivity.py`（与 `embedding.py` 同真实驻点） | 新科学能力只落 cccp；pure + RDKit lazy import；复用 `topology.py` 的 `DetermineBonds` 语义 |
| 项目图聚合/缓存 | 平台服务 | `src/acp_services/chemistry/` + 迁移 **024** `structure_connectivity` | 禁 import acp；端口注入；缓存随结构来源索引器增量填充 |
| 图 API | 接口 | `src/acp/api/v2_chemical_network.py`（挂 `/api/v2`，参照 `server.py:437`） | 薄路由 + 项目/权限校验 + `stats/indexing` 如实返回 |
| 终端 L0/L1 | 服务 + 接口 | `acp_services/runtime/terminal.py` + `api/terminal_routes.py` | 权限 scope + 审计 + cwd jail |
| DSH 宿主/反代 | 服务 + 接口 | `acp_services/runtime/dsh_host.py`（spawn/探活/回收）+ `src/acp/api/dsh_proxy.py`（`/dsh/*` 同源反代，注入 launch token） | 仅回环、按用户隔离、不缓存、token 不落前端/日志 |
| 共享结构工具/能量渲染 | 前端共享 | `frontend/js/workbench/controllers/{structure_tools_controller,file_browser_controller}.js`；能量复用 `js/visualization/energy_chart_renderer.js` | 经典页与 Next 同源，双入口回归；不新增第三套渲染 |
| 新模块 | 前端 | `frontend/js/modules/{project-molecules,chemical-network,structure-2d,molecule-properties,task-console,file-browser,remote-actions,agent-dsh}/` | 经典 script、注册表、四态、i18n 双语 |

数据库迁移走 `acp_services/store/migrations.py`；缓存表不写 ACP 业务真值。所有新端点需 `tests/` 契约测试与静态资源 manifest 登记（C35 门禁）。

**车道分工（沿用 A/B/C 契约）**：A 车道负责 `connectivity.py` 的科学契约、key 版本与正负 fixture；B 车道负责图 API、DSH host/反代、终端 entry-authz 与审计；C 车道负责 Shell v2、模块与共享控制器；跨边界需求按 `acp2-contracts/experience/requests/` 提交（含版本、正负例、错误码、兼容性与证据）。

---

## 4. 分期路线（建议）

| 期 | 内容 | 产出/验收 | 估时（单人） |
|---|---|---|---|
| **P0 spike** | ① DSH：A1 直接 iframe 半天验证 + A2 托管启动/`/dsh` 反代（title/`/api`/刷新/一次 MCP 提交）；② 2D 引擎 JSME/Ketcher；③ 真实项目连通性 key（100 结构：成功率/降级率/单结构耗时/InChIKey 覆盖） | 三份 spike 结论（GO/NO-GO + 数据），冻结布局、选型与 key 版本 | 5–7 d |
| **P1 Shell v2** | 具名槽位/模板/导轨迁移/偏好 v2/布局编辑器 | 旧 recipe 快照不变；新模板可用；测试全绿 | 5–7 d |
| **P2 研究** | 迁移 024 + 索引器挂载 + `chemical-network` 路由 + 网络 v2 + 导轨 + Agent（A 或 C） | 分组正确性（同分异构/构象/质子化/盐/degraded）+ 真实项目浏览器验收 | 9–13 d |
| **P3 分子** | `structure-2d` 占位 + `structure_tools_controller`（复用 geometry_store/editor，删重复测量数学）+ `molecule-properties`（`energy_chart_renderer` + `/energy-graph`） | 经典/Next 数值一致；测量/编辑逐字段一致 | 6–9 d |
| **P4 任务** | 第三视图六槽 + 进度（v2 投影补字段或读 v1 record）+ `file_browser_controller` + 主机/远程 + L0 结构化命令面板 | 一屏完成「看任务/看文件/选主机/受控动作」 | 9–13 d |
| **P5 门禁项** | 2D 引擎正式接入、L1 PTY（WS/PTY/entry-authz/审计）、轨道/波函数、图像识别 | 各自安全/科学评审后单独立项 | 不定 |

总计约 **6–8 周**（不含 P5）。P5 中任何一项不得阻塞 P1–P4。

---

## 5. 与现有计划的冲突处理

- UI-05 的「Execution 三面板」将被 P4 的六槽取代；现有 `exec-detail` 证据面板保留为 `tasks` 槽的详情抽屉，不删除。
- UI-04 的 `reaction-network` 只读历史数据面板在 P2 被 `chemical-network` 取代；`reaction-network` 的 SVG 布局与空态文案可复用。
- UI-06 原「可选能力」中的终端/多 viewer/轨迹，按本文升为 P4/P5 的受控项。
- UI-03 的提交/预检/草稿契约不变；第三视图的 `config` 槽直接复用。

---

## 6. 风险与未验证项

1. **DSH 鉴权与嵌入**：iframe 不被响应头禁止已核实（0 个 XFO/CSP 命中），但 cookie 是 `SameSite=Strict` 且 `127.0.0.1`≠`localhost`；launch token 只在 stdout、按进程生成；没有 postMessage 协议 → **上下文注入没有官方通道**，只能靠 MCP 工具 + URL/手工。A2 还要处理 DSH 进程生命周期、端口分配、`--public-url /dsh` 的静态资源与 WebSocket 路径重写。
2. **键感知的科学风险**：`rdDetermineBonds` 对金属/异常价态会失败或误判；必须落 `unresolved`/`warnings`，不得静默归并；InChIKey 骨架会把对映体并入同节点（默认符合「键连接关系」，但需 `stereo=split` 开关）。
3. **性能**：项目级归并是 O(结构数) 的 RDKit 计算；P0 用 100 结构定 key/耗时，P2 前必须补 1k 结构压测，否则缓存策略需提前。
4. **终端安全**：即使 L0 也需要命令注入/路径穿越/审计的专项测试；L1 需安全评审，当前无任何 PTY 后端。
5. **2D 引擎**：Ketcher 引入构建步骤与本仓库「无构建」原则冲突；若选它需单独立项并隔离产物。**服务端没有任何 2D 坐标生成**（无 `Compute2DCoords`/`Depict`/`MolDraw2D`；唯一 2D 投影是采样 MDS，不相关），2D 引擎必须自带 2D 布局或新增端点，这是选型硬指标。
6. **能量图两套形状**：经典 `buildEnergyProfileSvg` 吃 `mechanism_profile.routes[].points[].energy_kcal`（历史 mechanism 数据），Next 面板必须走 `EnergyGraphResponse`（`/jobs/{id}/energy-graph`）；不要为了复用把两套拼在一起。
7. **进度字段缺口**：v2 `TaskRowProjection` 没有 `stage_order/stage_progress`；要么扩投影，要么 Next 读 v1 job record。禁止在前端再算一套。
8. **DSH 版本漂移**：反代依赖 DSH 资源路径与 `--public-url` 行为；需版本探测与降级。
9. **轨道/波函数与图像识别**：数据端点与识别引擎均 ABSENT，本文不承诺时间。

---

## 7. 需要拍板的决策

- **D1（DSH）**：接受「A1 半天验证 → A2 托管启动 + `/dsh` 反代」吗？允许 ACP 服务端 spawn DSH 并在内存中持有 per-process launch token（仅回环、不落日志、不进前端）吗？在 DSH 没有 postMessage 的前提下，接受「项目/任务上下文只经 MCP 工具与 URL 传递」吗？
- **D2（2D 引擎）**：优先「无构建、JSME 手感」还是「ChemDraw 级、引入 Ketcher 构建」？
- **D3（网络语义）**：确认「主键用 InChIKey 前 14 位（连接性+氢层，立体不进 key）；默认按连接性归并，可切 `stereo=split`；电荷/质子化分开；互变异构默认分开；盐按整体一个节点；几何感知键标注来源；与现有名称分组先并存」？
- **D4（终端）**：P4 只做 L0「结构化命令面板」（服务端动作，不是 shell）是否可接受？L1 PTY 是否单独立项并指定安全负责人？
- **D5（范围）**：旧三工作区是**替换**还是**并存开关**？建议并存一个版本周期，默认进新形态。
