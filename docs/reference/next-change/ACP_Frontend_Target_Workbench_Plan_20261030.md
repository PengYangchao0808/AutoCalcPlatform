# ACP 前端目标形态方案（对齐样板图 v1）

日期：2026-10-30（v2 修订：2026-10-31——D2 定为 Ketcher、D3 确认、D5 直接替换；细化 DSH 宿主拓扑与 DSH 终端/文件复用）
后续：本文的前端形态已并入 [ACP 3.0 前端兼容计划](ACP_3.0_Frontend_Plan_20261031.md)；顶层架构见 [ACP 3.0 架构](ACP_3.0_Architecture_20261031.md)。
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
| ① Agent 原生支持 DSH 网页版 | §2.1 A1/A2 | DSH 无 XFO/CSP，可 iframe；鉴权是 per-process token + Strict cookie；无 postMessage，上下文走 MCP；推荐 ACP 托管 `dsh web` + `/dsh` 反代 |
| ② 化学网络按「相同键连接关系」归并 | §2.2 | InChIKey 骨架为主键；新增迁移 024 + 索引器挂载；现名称分组不可用（`CCO`/`OCC` 会分家） |
| ③ 2D 绘制/识别（ChemDraw 级） | §2.3 | **Ketcher**（Apache-2.0）：standalone iframe、CDXML/CDX 读写、Indigo WASM 离线、`recognize()` OCR |
| ④ 结构调整同步经典 + 能量曲线先行 | §2.4 | 复用 `structure_editor` 纯函数 + `geometry_store`；能量用已抽取的 `energy_chart_renderer` |
| ⑤ 第三视图重做 | §2.5 | 六槽；终端/文件优先嵌 DSH 面板（`acp-embed` 插件），失败回退 ACP L0/L1；进度双数据源 |

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
| 分子 | rail / draw2d / view3d / properties | `project-molecules` · `structure-2d`（Ketcher iframe） · `molecule-viewer`(现有) · `molecule-properties`(新) | 二 |
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

**先把三件事分开（这是 D1 的关键）**
1. **DSH 进程（服务端）**：`dsh web` 是一个可独立运行的本机 HTTP 服务（`--host/--port/--public-url/--no-open/--patch`），不需要 Electron 窗口；启动时在 stdout 打印 `dsh web: <authenticatedUrl>`，URL 里带**按进程生成**的 launch token。
2. **浏览器里的壳（谁提供页面 origin）**：可以是 ACP（ACP 为壳、DSH 被 iframe），也可以是 DSH（DSH 为壳、ACP 以插件/面板进入）。
3. **权限边界**：launch token 能换 HttpOnly cookie 与 API operator，等同于**完整 Agent 控制权**；DSH 的文件/终端都绑定在一个 **workspace 目录**上。谁持有 token、workspace 指向哪个目录，决定了整个安全模型。

**三种宿主拓扑**

| 拓扑 | 形态 | 适合 | 代价 |
|---|---|---|---|
| **T1 ACP 为壳**（本文主线） | ACP `/next/` 三工作区；研究区 iframe 一个 DSH；分子区 Ketcher；任务区 ACP 面板 | 保留样板图的 ACP 工作台与化学工作流 | 终端/文件要么 ACP 自建，要么做 DSH 面板嵌入（见 §2.5） |
| **T2 DSH 为壳** | DSH web 作为主界面，ACP 以 **DSH client plugin** 提供任务/网络/结构面板；DSH 的聊天、终端、文件原生复用 | 最彻底的「原生 DSH」，终端/文件零重复 | 需要 React/TS 构建链、插件发布、ACP API 跨源或宿主代理；与 DSH 版本强耦合；ACP 现有壳/品牌被替换 |
| **T3 双入口并列** | ACP 与 DSH 各自独立，互相深链；ACP 只读镜像任务 | 过渡/降级 | 不是「原生」体验 |

**"DSH 服务端"具体指什么**
- 指 **A2：由 ACP 托管一个 `dsh web` 进程**（推荐），不是把 DSH 塞进 ACP 的 Python 进程，也不是共享多用户 DSH 服务。
- 每次浏览器会话对应一个受管 DSH 实例（或一个已存在的本机实例），**不是**一个全公司共享的 DSH server：token 等于完整控制权、workspace 是本机路径、DSH 没有多租户审计边界。多用户共享 DSH 服务端明确**不做**。

**A2 的鉴权链路（为什么 token 不进前端）**
1. ACP 后端 spawn：`dsh web --host 127.0.0.1 --port 0 --no-open --public-url /dsh --patch rph/…/web.cordis.yml`；
2. 解析 stdout 的 `dsh web: …?token=…`，token 只存在 ACP 进程内存；
3. 浏览器 iframe **同源** `/dsh/` → ACP 反代首跳注入 `?token=` → DSH 303 设 HttpOnly cookie → 后续 `/dsh/api/*` 与 WebSocket upgrade 都带 cookie；
4. token 不出现在前端 HTML、URL、日志；cookie 属于 ACP origin（反代下），`SameSite=Strict` 不再是问题。

**四档执行方案**

| 方案 | 做法 | 成立条件 | 代价/风险 |
|---|---|---|---|
| **A1 直接 iframe**（半天验证） | Agent 槽位 iframe `http://127.0.0.1:58708/`（复用浏览器已有会话） | 用户已打开 DSH；固定端口；同 site cookie | 依赖外部进程；不可移植；无上下文注入 |
| **A2 托管 + 同源反代**（推荐） | 上述鉴权链路 | 允许 ACP 拉起 DSH；`--public-url` 子路径工作 | 进程生命周期/升级；代理需处理 WS/静态资源 |
| **B DSH 插件**（T2） | ACP 面板做成 DSH client plugin，DSH 当宿主 | 需要构建链 + 插件发布 | 方向反转；版本耦合；工作量最大 |
| **C 回退** | 启动器 + ACP SSE 事件镜像 | 无 DSH 时 | 不是原生体验 |

**建议**：A1 半天验证前提，A2 做自包含产品化；B 作为独立的「T2 评估」，不混进本轮；C 永久降级态。终端/文件是否复用 DSH，走 §2.5 的 D-embed spike 单独决定。

**任务权威与闭环**：DSH 通过 MCP `mcp__acp__acp_*` 提交/查询；ACP 用既有 SSE 与任务列表观察同一 `job_id`；**前端不新增第二条提交通道**，也不把 DSH 会话当任务真值。

**验收**：A1——iframe 首屏可交互、`/api` 调用成功、刷新保持登录；A2——spawn→URL→反代→刷新→在 DSH 聊天里提交一次 `xtb_optimize`→ACP 任务列表 10 s 内出现同一任务且无重复；DSH 未安装/未启动→自动降级 C 并可一键重试；token 不出现在任何前端响应/日志。

### 2.2 专题二：项目级化学网络（按键连接关系归并）

**语义（D3 已确认，见 §7）**
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

### 2.3 专题三：2D 结构绘制与识别（Ketcher，ChemDraw 级）

**决策（D2 已定）**：选 **Ketcher**（Apache-2.0），硬指标是兼容 ChemDraw 级图形的导入导出（CDXML/CDX）。

**已核实能力（Ketcher 官方 README，2026-10）**
- 提供 **ready-to-run standalone 应用**，可直接 iframe 或独立页面；暴露 `window.ketcher` API；iframe 初始化完成后向父窗口 postMessage `{eventType:"init"}`（不要靠 load 事件轮询）。
- 格式：MOL V2000/V3000、RXN、SMILES/SMARTS、InChI/InChIKey、CML、SDF/RDF、KET、**CDXML/CDX 读写**、PNG/SVG。
- standalone 用 **Indigo WASM**，可离线；`layout()` 自动布局；`recognize(image)` 提供 OCR 结构识别；`generateImage` 出图。
- 默认英文构建；多语言需构建时开 `KETCHER_MULTI_LANGUAGE_BUILD=true`（支持简体中文）。
- 其 3D 用 Miew——我们不用，3D 仍走 ACP 现有 viewer。

**集成架构（不破坏 ACP 的 no-build 原则）**
- 把**固定版本**的 Ketcher 预构建产物 vendored 到 `frontend/vendor/ketcher/<version>/`，FastAPI 以 `/ketcher/` 静态托管；ACP 的 CI 不构建它，只校验版本/哈希与许可证 NOTICE；登记进静态资源 manifest（C35）。
- `structure-2d` 模块 = iframe `/ketcher/` + 同源 `contentWindow.ketcher`；ACP 外层工具栏负责「从当前结构打开 / 发送到 3D / 保存为候选 / 新建任务预填」，绘制工具用 Ketcher 自带（原子/键/模板/反应/清理/CIP/立体化学）。
- 事件：`init` 后取句柄；`ketcher.editor.subscribe('change')` 做脏标记；导出用 `getMolfile('v3000')/getSmiles()/getCDXml()/getCDX()/getKet()`。

**2D ↔ 3D 与识别**
- 2D→3D：`getMolfile()/getSmiles()` → `POST /api/v1/molecule/embed`（已有）。
- 3D→2D：服务端**没有 2D 坐标**；二选一——新增 `POST /api/v1/molecule/depict2d`（RDKit `Compute2DCoords`，B 车道薄路由），或在 Ketcher 内 `setMolecule(molfile)` 后 `layout()`。P3 前定。
- 识别：SMILES/MOL/XYZ 导入已有；**图像识别优先用 Ketcher `recognize(image)`**，P0 验证 standalone 是否包含该能力——包含则纳入 P5，不包含则标 NOT_VERIFIED，不另造 OSRA/DECIMER 管线。
- CDXML/CDX 是 ChemDraw 兼容硬指标：导入后 `getSmiles/getMolfile` 必须语义一致，纳入验收测试。

**风险**：Ketcher 是 React 应用，必须接受「vendored 预构建产物」这一例外（不进入 ACP 源码依赖图）；WASM 体积与首屏延迟需实测；CDXML 的 s-group/反应语义要逐项验证；中文 UI 需多语言构建。

**验收**：CDXML/CDX 往返；MOL V3000/SMILES 往返；`layout()` 可用；2D↔3D 交接；脏标记/保存；离线可用；许可证与哈希记录在案。

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
- **DSH 自带终端/文件（已核实包，2026-10）**：终端——`dsh-api-terminal-controller`（session-owned 交互终端、shell 发现、screen recovery、typed Remote control）、`dsh-terminal`/`dsh-terminal-bash`（PTY seam/backend）、`dsh-tool-terminal`、`dsh-client-ui-sidebar-terminal`（右栏 shell tabs）；文件——`dsh-api-workspace-files`（`workspaceFiles` Remote：bounded reads、目录列表、实时元数据）、`dsh-client-ui-sidebar-files`、`dsh-client-ui-sidebar-documentpreview`、`dsh-workspace`（workspace registry + session attachment）；布局——`dsh-client-ui-layout`（三栏 AppFrame + `ctx.layout`）；插件——`dsh-client-modules`/`dsh-client-ui-plugin-manager`/`dsh plugin`。**但它们是 DSH 的客户端服务 + Remote 命名空间，不是公开 HTTP API；终端/文件 tab 由 `ctx.sidebarRight.openTab(...)` 程序化打开，没有 URL 路由可深链。**
- **ACP 侧终端完全 ABSENT**：FastAPI 无任何 `@router.websocket`；仓库无 `pty/termios/winpty/conpty/xterm`；无 exec/shell/command 路由；进程启动只在 scheduler 的 `subprocess.Popen`。权限面已有 `EntryClass`（7 值，未知类 fail-closed）、`EntryAuthorizer`、`AccessAction`、`AdminAuditLog`；`RestrictedBroker` 明确拒绝命令字符串。

| 槽位 | 模块 | 内容 | 现状 |
|---|---|---|---|
| rail | `project-molecules` | 项目分子导轨 | 新 |
| config | `task-config`（现有） | 任务类型/方法/基组/资源/高级参数 | **已有**（4 工作流端到端） |
| terminal | `task-console` | DSH 终端面板嵌入（优先）或 ACP L0 结构化动作 | DSH 有 PTY，但非公开 API；先 spike |
| tasks | `task-explorer`（扩） | 任务列表 + 进度图 + 当前任务状态 + 节点 | 已有列表，需加进度 |
| files | `file-browser` | DSH files 面板嵌入（优先）或经典数据层抽取 + 新渲染 | DSH 有 workspaceFiles；先 spike |
| actions | `remote-actions` | 本地/远程切换、节点选择、SSH 文件、远程调度入口 | 新（复用 `/nodes`、`/remote-files`） |

**文件浏览器**：D-embed 通过则 files 槽直接嵌 DSH files 面板；同时保留 ACP `file_browser_controller.js`（经典数据层抽取）作为无 DSH 环境与 job-dir 只读兜底。ACP 渲染层（树 + 预览 + 下载 + 远程标记）按需新写；虚拟化先不做，超过 500 行再评估。

**进度图**：不做第三套投影——用 `job-detail-query.js` 取 `detail.stages`，`stage_order/stage_progress` 从 `GET /api/v1/jobs/{id}` 取，或给 v2 `TaskRowProjection` 补这两个字段（二选一，需在实现前定）；百分比用 `job.progress`。

**终端/文件复用（D4）：先 spike，后二选一**

| 路线 | 做法 | 通过条件 | 回退 |
|---|---|---|---|
| **D-embed（优先）** | 写一个最小 DSH client plugin `acp-embed`：`?acp-embed=terminal|files` 时只渲染对应面板（隐藏 DSH 其它 chrome），并绑定指定 workspace；ACP 同源 iframe `/dsh/?acp-embed=terminal` | plugin slots 能单独渲染 tab 内容；能隐藏 AppFrame；能程序化绑定 workspace；cookie/token 复用 | 走 ACP-native |
| **ACP-native（回退）** | L0 结构化动作（P4）＋ L1 PTY（P5，WS + 逐消息授权 + entry-authz + 审计）；文件用经典数据层抽取 | 安全评审通过 | 只保留 L0 |

- **Workspace 映射（两种路线都需要）**：每个 ACP 项目对应一个 DSH workspace，root 指向项目 `run_root`（或当前 job 目录），让 DSH 的终端/文件天然看到 ACP 产物；agent 与终端共享同一目录。P0 验证 DSH 是否能被程序化创建/绑定 workspace；不能则在 DSH 内手动选择。
- **边界**：无论 D-embed 还是 ACP-native，**任务控制（pause/cancel/logs/审批）仍归 ACP**，终端只做 inspect/命令；DSH 终端继承 DSH 的 sandbox 模型，ACP 不绕过；ACP 自己的 L0/L1 走 `EntryClass` fail-closed + `AdminAuditLog`。
- **验收**：D-embed——iframe 只显示目标面板、workspace 正确、刷新保持、跨 workspace 访问被拒；ACP-native——L0 只影响任务目录且有审计，L1 逐消息授权、默认关闭。

**验收**：无权限身份看不到可执行终端（denied 态）；L0 动作只影响任务目录且有审计；文件预览/下载走既有 entry-access 门禁；远程文件读取走 `_job_read_root`/缓存契约；进度图在 running/terminal 两种任务上都与经典页显示一致。

---

## 3. 架构落点（不新增平行实现）

| 能力 | 层 | 落点 | 规则 |
|---|---|---|---|
| 连接性 key / 键感知 | 科学基元 | `src/cccp/analysis/structure/connectivity.py`（与 `embedding.py` 同真实驻点） | 新科学能力只落 cccp；pure + RDKit lazy import；复用 `topology.py` 的 `DetermineBonds` 语义 |
| 项目图聚合/缓存 | 平台服务 | `src/acp_services/chemistry/` + 迁移 **024** `structure_connectivity` | 禁 import acp；端口注入；缓存随结构来源索引器增量填充 |
| 图 API | 接口 | `src/acp/api/v2_chemical_network.py`（挂 `/api/v2`，参照 `server.py:437`） | 薄路由 + 项目/权限校验 + `stats/indexing` 如实返回 |
| 终端/文件（DSH 复用） | DSH 插件 + 接口 | `rph/packages/dsh-embed-plugin/`（`acp-embed` 面板模式）+ ACP `/dsh/*` 反代 | 只读/绑定 workspace；版本探测；失败回退 ACP-native |
| 终端 L0/L1（回退） | 服务 + 接口 | `acp_services/runtime/terminal.py` + `api/terminal_routes.py` | 权限 scope + 审计 + cwd jail |
| DSH 宿主/反代 | 服务 + 接口 | `acp_services/runtime/dsh_host.py`（spawn/探活/回收）+ `src/acp/api/dsh_proxy.py`（`/dsh/*` 同源反代，注入 launch token） | 仅回环、按用户隔离、不缓存、token 不落前端/日志 |
| 共享结构工具/能量渲染 | 前端共享 | `frontend/js/workbench/controllers/{structure_tools_controller,file_browser_controller}.js`；能量复用 `js/visualization/energy_chart_renderer.js` | 经典页与 Next 同源，双入口回归；不新增第三套渲染 |
| 新模块 | 前端 | `frontend/js/modules/{project-molecules,chemical-network,structure-2d,molecule-properties,task-console,file-browser,remote-actions,agent-dsh}/` | 经典 script、注册表、四态、i18n 双语 |

数据库迁移走 `acp_services/store/migrations.py`；缓存表不写 ACP 业务真值。所有新端点需 `tests/` 契约测试与静态资源 manifest 登记（C35 门禁）。

**车道分工（沿用 A/B/C 契约）**：A 车道负责 `connectivity.py` 的科学契约、key 版本与正负 fixture；B 车道负责图 API、DSH host/反代、终端 entry-authz 与审计；C 车道负责 Shell v2、模块与共享控制器；跨边界需求按 `acp2-contracts/experience/requests/` 提交（含版本、正负例、错误码、兼容性与证据）。

---

## 4. 分期路线（建议）

| 期 | 内容 | 产出/验收 | 估时（单人） |
|---|---|---|---|
| **P0 spike** | ① DSH：A1 直接 iframe 验证 + A2 托管/反代；② DSH `acp-embed` 面板插件（terminal/files 单独渲染 + workspace 绑定）；③ Ketcher：CDXML/CDX 往返、WASM 体积、OCR 是否可用；④ 真实项目连通性 key（100 结构） | 四份 spike 结论（GO/NO-GO + 数据），冻结宿主拓扑、终端/文件路线与 key 版本 | 8–11 d |
| **P1 Shell v2** | 具名槽位/模板/导轨迁移/偏好 v2/布局编辑器（旧三工作区**替换**，保留一个版本的回退开关） | 旧 recipe 快照不变；新模板可用；测试全绿 | 5–7 d |
| **P2 研究** | 迁移 024 + 索引器挂载 + `chemical-network` 路由 + 网络 v2 + 导轨 + Agent（A2） | 分组正确性（同分异构/构象/质子化/盐/degraded）+ 真实项目浏览器验收 | 9–13 d |
| **P3 分子** | `structure-2d`（Ketcher iframe）+ `structure_tools_controller` + `molecule-properties`（energy_chart_renderer） | CDXML/MOL/SMILES 往返；经典/Next 数值与测量一致 | 7–10 d |
| **P4 任务** | 第三视图六槽 + 进度 + DSH terminal/files 面板（D-embed 通过时）或 ACP `file_browser_controller` + L0；主机/远程 | 一屏完成「看任务/看文件/选主机/受控动作」 | 9–13 d |
| **P5 门禁项** | L1 PTY（仅 D-embed 失败时）、Ketcher OCR 接入、轨道/波函数、CDXML 深度语义 | 各自安全/科学评审后单独立项 | 不定 |

总计约 **7–9 周**（不含 P5）。P5 中任何一项不得阻塞 P1–P4。

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
4. **终端/文件复用未验证**：DSH 的 terminal/files 是客户端插件 + Remote 服务，**没有 URL 路由可深链**；`acp-embed` 插件能否只渲染面板、隐藏 AppFrame、程序化绑定 workspace，都是 spike 要回答的问题；不通过就必须 ACP-native，PTY 安全成本回到 ACP。
5. **Ketcher 集成**：必须接受「vendored 预构建产物」这一例外；WASM 体积、CDXML 的 s-group/反应语义、standalone OCR 是否可用都需实测；服务端没有 2D 坐标，需 `depict2d` 或 Ketcher `layout()`。
6. **能量图两套形状**：经典 `buildEnergyProfileSvg` 吃 `mechanism_profile.routes[].points[].energy_kcal`（历史 mechanism 数据），Next 面板必须走 `EnergyGraphResponse`（`/jobs/{id}/energy-graph`）；不要为了复用把两套拼在一起。
7. **进度字段缺口**：v2 `TaskRowProjection` 没有 `stage_order/stage_progress`；要么扩投影，要么 Next 读 v1 job record。禁止在前端再算一套。
8. **DSH 版本漂移**：反代与 `acp-embed` 插件都依赖 DSH 的资源路径/`--public-url`/slot 名；需版本探测与降级。
9. **workspace 映射与越权**：DSH workspace 指向 ACP 项目目录后，必须验证「不能从 ACP 页面访问其他 workspace」；DSH 的 sandbox 与 ACP 的 entry-authz 不能互相绕过。
10. **轨道/波函数**：数据端点 ABSENT，本文不承诺时间。

---

## 7. 决策状态

**已定（2026-10-31 用户确认）**
- **D2 = Ketcher**：ChemDraw 级绘制，CDXML/CDX 兼容；按 §2.3 的 vendored standalone 方案接入。
- **D3 = 网络语义按 §2.2**：InChIKey 骨架主键、立体默认并节点可切分、电荷/质子化分开、互变异构分开、盐整体一节点、几何感知标注来源、先与名称分组并存。
- **D5 = 直接替换旧三工作区**：新形态默认入口；保留一个版本周期的回退开关（回退只切入口，不回滚数据）。

**待确认（需要你点头即可开工）**
- **D1 = 宿主拓扑 T1 + A2**：ACP 为壳；由 ACP 服务端托管 `dsh web`（即你说的「DSH 服务端」），`/dsh/*` 同源反代并注入 per-process token。需要你确认：**允许 ACP 在内存中持有 DSH launch token**（仅回环、不落日志、不进前端）；在 DSH 无 postMessage 的前提下，项目/任务上下文只经 MCP 工具与 URL 传递。
- **D4 = 终端/文件先做 D-embed spike**：2–3 天验证 `acp-embed` 插件能否把 DSH 终端/文件面板单独嵌入 ACP 槽位，并让 DSH workspace root 指向 ACP 项目目录；通过则复用 DSH，失败则回退 ACP-native（L0→L1）。需要你确认批准这个 spike 与 workspace 映射方案。
- **D1b（可选）**：是否保留 T2「DSH 为壳 + ACP 插件」作为后续独立评估？不阻塞本轮；若选它，ACP 的壳/品牌会被 DSH 替换。

> 2026-10-31 后续：顶层拓扑（每用户 Linux 主机 + 每用户 DSH + 虚拟文件系统）已单独定稿于 [多用户拓扑方案](ACP_MultiUser_VirtualHost_DSH_Topology_20261031.md)；T1/T2 取舍以该文档为准（用户级 T2）。
