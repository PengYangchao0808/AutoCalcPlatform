# ACP V2 分布式体验等价性实施计划（Read-Root 收口 · RTT 批量化 · 投影存储）

**日期**：2026-11-02（实施记录追加于 2026-11-03）
**性质**：**部分实施**（见 §9：W-01a/W-02/W-03/W-04(flag-off)/W-05/W-06 已落地；**W-01b 仍被登记决策阻塞**）；文中"现状"经本机源码核对，不代表已交付
**定位**：**ACP V2 线的自然延伸** —— 只收敛现有读取 / 传输 / 接口路径，不引入 V3 的执行模型改造（Agentless、统一插件协议均不在本计划内）
**来源**：[ACP V3 后端关键架构方案](ACP_V3_CCCP_ACR_Backend_Architecture_20261102.md) §4 / §5（问题来源与边界参考，**非实施依赖**）

> **一句话**：分布式要像本地，靠的是「每一次读取都落到离消费者最近的投影上」，不是「把远端变快」。本计划先把 locality 从读取契约里赶出去，再谈投影存储。

---

## 0. 一页结论

- **不需要毫秒级传输**。当前控制面轮询地板是 5–15 s（`manager.py:534`、`:7094`），优化 SFTP 单次 RTT 不会改变任何可感知体验。
- **真正的敌人是 locality 泄漏**：同一个逻辑操作在两种后端产生**不同的可观测结果**（含错误分类），这比延迟严重得多。已核实 4 层泄漏，见 §2。
- **读取根的抽象已经存在**，并被文档声明为唯一权威（`read_root.py` 模块 docstring：「no second read root」「resolved without any network」），但旧的双份实现从未退役。**W-01 是收口，不是新建。**
- **A 档 6 项可以立即开工**，彼此独立、互不阻塞，且都不需要冻结新契约、不需要生产重启、不需要真实 QC 提交。
- **B 档 3 项必须先拍板**，否则 W-01 做完立刻撞车。

---

## 1. 目标与可验收定义

「和本地部署相同的 UI 体验，MCP 同理」翻译成五条**可断言**的等价条件。缺一条就不算达成：

| # | 等价条件 | 断言方式 |
|---|---|---|
| **E1** | **同结果**：同一逻辑操作返回同一 schema 的同一内容 | 双后端回放对拍 |
| **E2** | **同错误**：错误码集合相同；**不存在远端特有错误** | 错误码集合差集为空（环境类单列） |
| **E3** | **同时序**：不存在「只在某一种后端阻塞」的操作 | 交互路径无 > 500 ms 的无反馈阻塞 |
| **E4** | **同预算**：交互读取 P95 首字节在预算内，且由架构保证而非链路速度 | 热命中读本地投影 |
| **E5** | **同能力**：不存在「这个功能只有本地有」 | 能力矩阵双列一致 |

**关键区分**：`SOFTWARE_UNAVAILABLE`（本机没装 ORCA）是**环境事实**，两种后端都可能发生，**不算违反 E2**。`AttemptCacheIdentityUnverified`（只有远端缓存才需要证明）是**远端特有错误**，**违反 E2**。

---

## 2. 现状诊断（证据均为本机源码核对）

### 2.1 读根已统一，但未收口

| 事实 | 位置 |
|---|---|
| `ReadRoot` 契约 | `src/acp_services/data/read_root.py:87` |
| `ReadRootPort` 协议 | 同上 `:116` |
| `ReadRootResolver`（声明"唯一读取根"） | 同上 `:129` |
| 装配器 + 端口适配器 | `src/acp/services_data_bootstrap.py:2295`、`:2319` |
| **已示范正确用法** | `src/acp/api/v1_science.py:12` —— 注释写明「**never `_job_read_root`**, which fetches a remote catalog on a terminal job and would turn a GET into a write/network side effect」 |
| **旧双份实现仍在** | `src/acp/api/v1_routes.py:791`、`src/acp/api/v2_routes.py:401` |
| 旧实现里的同步网络副作用 | 两者都在终态时调用 `cache.fetch_catalog(...)` |

**`_job_read_root` 调用点共 7 处**：`v1_routes.py:3489 / 3547 / 4094 / 6726 / 7222 / 7228`、`v2_routes.py:437`。

### 2.2 locality 泄漏清单

| 层 | 泄漏 | 后果 |
|---|---|---|
| 数据 | `ReadRoot.is_remote` 是公开字段（`:98`），往 6 处透传 | 抽象携带位置，而非封装它 |
| API | `science_projection.py:958` 用 `is_remote` 做**语义分支**：远端须 attempt sidecar 证明，否则抛 `AttemptCacheIdentityUnverified` / `AttemptHistoryUnavailable` | **同一个 job，本地能看、分布式报错** → 违反 E2 |
| 路由 | `v2_routes.py:382` 复制 v1 的 `_is_remote_job`/`_job_read_root`，注释原因为「v1_routes.py 是 user-dirty 文件」 | **该理由已失效**（见 §4 W-01 前提） |
| UI | `frontend/js/workbench/i18n.js:763` 有远端专属错误文案 `energy.irc_remote_error` | 用户看到的措辞就不同 → 违反 E2 |

### 2.3 RTT 未批量化清单

仓库里已有两次被同一问题教育过的证据（`sftp.py:364` 的 `remote_sha256_many` docstring：「one SSH command per file … ~1 s on a real cluster link … a 600-file release needed **~10 minutes of verification on every submission**」）。**同类病灶还有四处**：

| 病灶 | 位置 | RTT 数量 |
|---|---|---|
| 远端目录递归列举 | `fetcher.py:462` | 每目录 1 次 |
| `bjobs` 逐 job 查询 | `monitor.py:79-130` | 每 job 1 次 |
| 上传仍逐文件 `put` | `sftp.py:411` | 每文件多次 |
| MCP 工具逐 job/逐 artifact 调用 | `mcp.py:493` / `:496` | 每实体 1 次 |

量化：RTT 30 ms 链路，100 目录 + 50 文件 ≈ 数百 RTT ≈ 10 s 级；批量化后 ≈ 30 ms 级。跨区 150 ms 时差距为分钟级 vs 亚秒级。

---

## 3. 工作分解

### 3.1 A 档 · 可立即开工（6 项，互不阻塞）

| ID | 事项 | 主要位置 | 风险 | 依赖 |
|---|---|---|---|---|
| **W-01** | 读根收口（退役 `_job_read_root` 双份） | `v1_routes.py` / `v2_routes.py` → `build_read_root_resolver()` | **高** | 无 |
| **W-02** | `bjobs` 批量查询 | `monitor.py:79-130` | 中 | 无 |
| **W-03** | 递归列举批量化 | `fetcher.py:462` | 中 | 无 |
| **W-04** | 上传合并为单次流（仅 release 路径） | `sftp.py:411` | 中高 | W-03 |
| **W-05** | 消除远端专属 UI 文案 | `i18n.js:763` | 极低 | 无 |
| **W-06** | MCP 批量参数（additive） | `mcp.py:493` / `:496` | 中低 | 无 |

### 3.2 B 档 · 必须先决策（3 项）

| ID | 决策 | 备选 | 最晚截止 |
|---|---|---|---|
| **D-01** | `AttemptCacheIdentityUnverified` 处理策略 | (a) 本地也加 attempt sidecar / (b) 缺 sidecar 时有界 republish 再重读 / (c) 仅 UI 层呈现为"校验中" | W-01 合入前 |
| **D-02** | 投影存储落点 | (a) 扩 `RemoteStructureCache`（manager 单例，地基现成）/ (b) 新建 store | W-01 之后、任何 `artifact_locations` 设计之前 |
| **D-03** | `reductions` 是否进 P0 冻结 | 先做数据调研再定：统计现有 `.out` 大小分布与归约后大小 | P0 冻结前 |

> D-02 建议 **(a) 优先**：符合根 `AGENTS.md` ANTI #30「旧路径只做 forwarder，不做第二实现」。D-01 建议 **(b)+(c) 组合**：契约不放宽，体验上可恢复。

### 3.3 C 档 · 本轮明确不做

- `RemoteNativeExecutor`（等 `CompiledSciencePlan` / `NativeExecutionBundle` 冻结）
- `ArtifactReadPort` 新契约（等 D-01 / D-02）
- `artifact_locations` 新表（等 D-02）
- 彻底移除 `is_remote`：`v1_routes.py` 24 处、`manager.py` 21 处 `_is_remote_job(record)` 分支，其中多为**真实能力差异**，不得一刀切

---

## 4. 逐项详情

### W-01 · 读根收口

**现状**：见 §2.1。抽象、装配、正确用法示范全部就位，缺的是退役旧路径。

**改法（分三步，可分别合入）**：

1. **W-01a · 读根解析统一**：7 处 `_job_read_root` 调用改走 `build_read_root_resolver().resolve(job_id, attempt)`；删除 `v2_routes.py:382-420` 的复制实现。
2. **W-01b · 副作用搬移**：把 `fetch_catalog(...)` 从读路径移到唯一允许的三个入口——**显式 prepare 命令 / 终态后台流程 / 启动恢复**（这是 `read_root.py` docstring 已写死的规则）。
3. **W-01c · 能力差异登记**：保留真正需要远端行为的 `_is_remote_job` 分支（fetch / IRC 轨迹 / geometry），逐条登记到台账并写明「这是环境差异，不是体验差异」。

**前提（已核实成立）**：`v1_routes.py` **当前不 dirty**（`git status` 仅 4 个未跟踪文件，`git diff HEAD` 对两个路由文件为空）。`v2_routes.py:382` 注释里复制实现的唯一理由已失效。

**最大行为变化点**：把 `fetch_catalog` 移出读路径后，**终态远端 job 的首次 GET 可能拿不到 catalog**。必须先确认终态后台流程确实在跑；若不能保证，则返回 `catalog_ready=False` 的**降级投影**（明确"未就绪"），**绝不允许**在读路径里同步拉取。

**验收**：

- `pytest tests/test_services_read_root.py tests/test_acp2_v1_science_status.py tests/test_acp_api_v1.py -q` 全绿
- 新增负断言：7 个调用点在**冷缓存**下 GET 不产生任何 SSH/SFTP 调用（mock 计数），且 `store.counts()` 前后一致（照 `v1_science.py` 已有先例）

**回退**：按资源（catalog / manifest / frame_candidates / frequency_modes / geometry）各自留开关，与既有 cache-only 切换矩阵一致。

---

### W-02 · `bjobs` 批量查询

**现状**：`monitor.py:90` 每 job 一次 `bjobs <id>`。解析器**已能处理多行表格**（"find the line whose first column matches the job ID"），所以是改造不是重写。

**必须点明的正确性陷阱**：现有兜底

```python
# Fallback: scan for any recognised LSF stat code in the output.
for stat_code, mapped in _LSF_STATE_MAP.items():
    if stat_code in raw.upper():
        return mapped
```

批量模式下会把 A 任务的 `DONE` **误判给 B 任务**。

**改法**：

1. 一次 `bjobs` 取全表，**严格按行** `parts[0] == job_id` 匹配
2. **删除该兜底**，无匹配时返回 `STATUS_UNKNOWN`（宁可未知，不可猜错）
3. 分块，照 `sftp.py:362` 的 `_SHA256_BATCH = 96` 先例
4. 兼容性：OpenLAVA 2.0 不支持 `-o` / `-noheader`（源码注释已声明），只能走整表解析

**验收**：新增「3 个 job 混合 `DONE` / `RUNNING` / `NOT_FOUND`，一次查询后各自状态正确」；现有 `tests/test_remote_phase*.py` 全绿。

**回退**：保留逐 job 查询配置开关，默认批量。

---

### W-03 · 递归列举批量化

**现状**：`fetcher.py:462` `_list_remote_dir_recursive` 逐目录 `sftp.listdir_attr`。调用方：`structure_sources.py:992 / 1067 / 1142`、`v1_routes.py:7885`。

**改法**：一条 `find <root> -mindepth 1 -printf '%y\t%s\t%T@\t%p\n'`，本地解析成 `RemoteFileInfo`。**失败必须返回 `None` 让调用方回落 SFTP 原路径**——照 `remote_sha256_many` 的既有先例，不要抛。

**要处理**：`-printf` 的 GNU 依赖、文件名含换行/制表符、权限错误（stderr 非空）、符号链接、`max_entries` 截断语义保持不变。

**验收**：新增三例——特殊字符文件名、`find` 不可用时回落、`max_entries` 截断；现有 structure source 测试全绿。

**回退**：返回 `None` 即回落，天然安全。

---

### W-04 · 上传合并

**现状**：`sftp.py:411` 只合并了 session，仍逐文件 `sftp.put`。

**改法**：**先只对 release 路径启用**（几百个小文件、目录结构最可控、收益最大），tar 流一次上传 + 远端 `tar -xf`。其余上传路径保持原样。

**要处理**：路径穿越（复用 `_norm_remote` + 白名单）、远端 `tar` 可用性、解包后权限与所有权。

**验收**：`tests/test_remote_code_release.py`、`tests/test_remote_code_release_binding.py`、`tests/test_remote_installed_sync.py` 全绿。

**回退**：配置开关，默认关，灰度到 release 路径。

---

### W-05 · 消除远端专属 UI 文案

**现状**：`i18n.js:763` `energy.irc_remote_error`：「暂时无法读取远程 IRC 轨迹…」。

**改法**：统一为不区分 locality 的文案，技术细节（连接/超时/缓存状态）放进 detail 区域。**不是简单删除**——保留可诊断性，只去掉"远端"这个暴露给用户的分类。

**验收**：前端 module tests（`run_all.js` 非空强制门）。

**回退**：纯文案，无回退风险。

---

### W-06 · MCP 批量参数

**现状**：`mcp.py:493 STATUS`、`:496 READ_ARTIFACT`，逐实体调用。

**改法（严格 additive）**：`acp_status` 接受可选 `job_ids: []`；`acp_read_artifact` 接受多个 ref。旧参数与返回形状不变。

**理由**：agent 的行为是 `读 → 判断 → 读` 循环，且有 timeout。N 次工具调用 = N 次 WAN RTT，会直接导致超时重试并污染 agent 决策路径——比 UI 卡顿严重。

**验收**：`pytest tests/test_acp2_mcp_sdk_contract.py -m contract`（**JUnit 计数防静默空过门**，新增 schema 不得让它空过）+ 新增批量用例。

**回退**：additive，无回退风险。

---

## 5. 排期与依赖

```
批次 1（可并行）    W-05 ─┐
                          ├─→ 独立合入
                   W-02 ─┘

批次 2（单独）      W-01a ─→ W-01b ─→ W-01c

批次 3（可并行）    W-06 ─┐
                          ├─→ 独立合入
                   W-03 ─┘

批次 4             W-04（依赖 W-03 的回落机制成型）

并行决策           D-01（卡 W-01 合入）
                   D-03（纯调研，随时可做）
                   D-02（W-01 之后）
```

**排序理由**：W-05/W-02 是低垂果实且收益/风险比最高；W-01 改动面最大必须独占一批；W-03/W-06 相互独立；W-04 需要 W-03 的回落模式先跑通。

---

## 6. 证据与三态规则

遵循根 `AGENTS.md`：**`NOT_VERIFIED` 不得改写为 `PASS`**。

| 范围 | 证据等级 |
|---|---|
| 本机 mock SSH 闭环（全部 A 档） | 可记 `PASS`，须注明"本机 / mock 链路" |
| 真机集群链路（W-02 / W-03 / W-04 的真实收益） | 有可用集群前记 **`NOT_VERIFIED`** |
| 「体验等价性」（E1—E5） | 必须有**双后端回放对拍**证据才可记 `PASS` |

**本计划全部工作项不触发生产 service 重启，不提交真实 QC / 模型 / LSF 作业。**

---

## 7. 明确不做

- 不新建第二套 read root、第二套任务库、第二套插件生命周期
- 不为了"少几行代码"删除兼容面或资产（根 `AGENTS.md` ANTI #31）
- 不在 W-01 里顺手改任何 409/200 语义
- 不把「相同状态码」当作兼容通过
- 不用"链路更快了"替代体验等价性证据

---

## 8. 文档归置

- 本文件按约定放在 `reference/next-change/`，命名 `ACP_<主题>_<YYYYMMDD>.md`
- 来源方案已按本计划移入 `reference/next-change/ACP_V3_CCCP_ACR_Backend_Architecture_20261102.md`（原在 `docs/` 根，不符合 `docs/README.md` §2「根下只保留 3 个跨批次文件」），并已同步 `docs/README.md`、`reference/next-change/README.md` 与本文件链接
- 新增/移动后必须跑 `python scripts/check_docs_links.py`（守护 `tests/test_docs_links.py`），并同步 `docs/README.md` 与 `reference/next-change/README.md`

---

## 9. 实施记录（2026-11-03）

> **性质**：部分实施。A 档 6 项中 W-01a / W-02 / W-03 / W-04（flag-off）/ W-05 / W-06 已落地并有本机 mock/单测证据；**W-01b 未实施**（带门，见 §9.1）。E1—E5 体验等价性需要双后端回放对拍，本机不可得，统一记 `NOT_VERIFIED`；真机 LSF 收益同样 `NOT_VERIFIED`。

### 9.1 W-01 读根收口：拆分后只交付 W-01a，W-01b 带门

审阅结论（两位独立只读审阅者一致）：`_job_read_root` 的保留不是"陈旧注释"，而是已登记决策——根 `AGENTS.md` ANTI #27、`tests/baseline/services-evidence/migration_ledger.md` `[RETAINED]`、`cache_only_switch_matrix.md` §6 "窗口内不得删除"、`docs/reference/current/ACP_Services_Closure_20261019.md` L2-03。W-01b 直接摘除终态 `fetch_catalog` 会与这些门冲突，且 F0/F1×S0/S1 矩阵与"旧 GET 零消费者"证据本机不可得。

| 切片 | 状态 | 内容 | 证据 |
|---|---|---|---|
| **W-01a** | **PASS（本机）** | 删除 `v2_routes` 的本地 `_job_read_root` 镜像，改为 `from acp.api.v1_routes import _job_read_root`（同一函数对象）；**行为不变**（远端终态仍走 `RemoteStructureCache` + 登记的 `fetch_catalog`） | `tests/test_acp_v2_read_root_parity.py`；`tests/test_acp2_runtime_lsf_scenario.py`；`tests/test_services_cache_only_switch.py` |
| **W-01b** | **BLOCKED / NOT_VERIFIED** | 摘除终态同步 fetch、删除 `_job_read_root`、GET 冷缓存零 SSH | 前置：① F0/F1×S0/S1 矩阵齐备 + 旧 GET 零消费者证据；② 改写 ANTI #27 为 `build_read_root_resolver`；③ 同步 migration_ledger / cache_only_switch_matrix / closure L2-03；④ 同步 current DevDocs（Energy Viewer:215、PES Manual Review:139、Structure Viewer:247）；⑤ 为 `get_job_edit_draft` 的 `fetch_reusable_geometries` 与 `GET /structure-viewer` 的 `refresh_remote_irc` 明确归属；⑥ 负断言须排除上述两处显式入口 |
| **W-01c** | **PASS（本机台账）** | 能力差异台账见 §9.2 | 本文件 §9.2 |

**W-01b 若要继续，建议的切换顺序**：先补降级投影（`catalog_ready=False` 的 pending 状态）并保留回退开关 → 再改 ANTI #27/台账 → 最后摘除副作用。不得反过来"先删后补"。

### 9.2 locality 能力差异台账（W-01c）

分类口径：**环境能力差异**（远端才有的事实，例如远端文件预览/实时轨迹）不等于体验差异；**体验差异**（同一逻辑操作在两种后端结果/错误/文案不同）才是 E1—E5 的目标；**显式 prepare/后台预取**是允许的副作用入口。

| # | 入口 / 分支 | 位置（以当前代码为准） | 分类 | 处理 | 备注 |
|---|---|---|---|---|---|
| 1 | `_job_read_root` 的终态 `fetch_catalog` | `src/acp/api/v1_routes.py` | 已登记过渡副作用（体验差异） | **保留**；W-01b 才切换 | ANTI #27 / L2-03 / switch matrix §6 |
| 2 | `get_job_edit_draft` → `fetch_reusable_geometries` | `src/acp/api/v1_routes.py`（edit-draft） | 显式 prepare（geometry） | 保留 | 几何不在终态 catalog 预取范围；删除会丢 `last_structure` |
| 3 | `GET /jobs/{id}/structure-viewer?fetch=1` | `src/acp/api/v1_routes.py` | 显式用户 prepare（catalog）；默认（无 `fetch`）不取 | 保留 409 `pending_fetch` 语义 | 不得把 `?fetch=1` 当普通读取路径 |
| 4 | IRC 远端 live 轨迹刷新 | `src/acp/api/v1_routes.py`（energy graph IRC 分支） | 环境能力差异（remote-only live） | 保留并登记 | 失败 reason 会以 `remote_fetch_*` 出现，W-05 只中立化 UI 文案 |
| 5 | 远端文件预览 / 下载 / ZIP archive | `src/acp/api/v1_routes.py`（remote-files / archive） | 环境能力差异（remote-only 源） | 保留 | 下载语义就是访问远端 |
| 6 | 终态后台预取 `_queue_catalog_prefetch` | `src/acp/scheduler/manager.py`（终态 side effects 内） | 后台预取（允许入口） | 保留 | prepare operation 持久去重 |
| 7 | 启动恢复 `_queue_startup_catalog_prefetch` | `src/acp/scheduler/manager.py` | 启动恢复（允许入口） | 保留 | 升级/重启后补未决操作 |
| 8 | 结构源索引器 `fetch_catalog` | `src/acp/scheduler/structure_source_indexer.py` | 后台索引；只读 GET 不触发 | 保留 | 索引器自有节奏 |
| 9 | rerun 保留旧输出 `fetch_catalog` | `src/acp/scheduler/manager.py` | 显式 rerun 写操作 | 保留 | 破坏性操作前的显式读取 |
| 10 | `attempt_view` 的 remote identity 分支 | `src/acp/api/science_projection.py` | 身份/证据规则（D11），**不是 locality 泄漏** | 保留严格拒绝 | E2 定义改为"无**未解释**的远端特有错误"；`AttemptHistoryUnavailable` 本地/远端同形 |
| 11 | `energy.irc_remote_error` → `energy.irc_unavailable` | `frontend/js/workbench/{i18n.js,features/energy.js}` | 体验差异（用户可见文案） | W-05 已中立化 | 诊断 reason 保留在 detail/title |
| 12 | `ReadRoot.is_remote` 字段透传 | `src/acp_services/data/read_root.py` | 契约字段（C 档不在本轮移除） | 保留 | 彻底移除 `is_remote` 属 C 档，不在本轮 |

**三类允许的 fetch 入口**（`read_root.py` docstring 规则，本台账与之对齐）：

1. **显式 prepare 命令**：`POST /api/v1/jobs/{id}/prepare`、`GET /structure-viewer?fetch=1`、edit-draft geometry；
2. **终态后台流程**：`_queue_catalog_prefetch`（`manager.py` 终态 side effects）；
3. **启动恢复**：`_queue_startup_catalog_prefetch`。

### 9.3 决策记录（B 档）

- **D-01（AttemptCacheIdentityUnverified 处理策略）**：**修订为 (a) + 有界 (c)**。(b) "读路径有界 republish" **不采用**：`read_root.py` 只允许三类入口创建 prepare，`src/acp/api/AGENTS.md` 也禁止 GET 入队/触发网络。当前实现保留严格投影拒绝（D11），UI 侧把可恢复的"校验中"与技术 reason 分开呈现；E2/E5 记 `NOT_VERIFIED`，blocker 是双后端回放对拍缺失。D-01 **不是** W-01a 的前置（W-01a 不碰 attempt view）。
- **D-02（投影存储落点）**：本轮不做；`artifact_locations` / `ArtifactReadPort` 仍属 C 档。
- **D-03（`reductions` 是否进 P0 冻结）**：纯调研，本轮不做。
- **W-04 开关**：`ACP_REMOTE_TAR_UPLOAD` **默认关**；release 路径真机冒烟通过前不得默认开启。

### 9.4 实施清单与证据（细节）

> 逐项测试命令与结果由本批 PR 的执行回执汇总；只记本机/ mock 链路可复跑的结果。

| ID | 落地内容 | 证据（命令） | 结果 |
|---|---|---|---|
| W-01a | 单一 `_job_read_root` 实现（行为不变） | `pytest tests/test_acp_v2_read_root_parity.py tests/test_acp2_runtime_lsf_scenario.py tests/test_services_cache_only_switch.py tests/test_acp_remote_catalog_prefetch.py tests/test_acp_api_structure_viewer.py -q`；另跑 `test_acp_api_v1.py`/`test_acp_api_v2.py` 全文件 | **168 passed**（5 文件命令，独立验收复跑）；含 v1/v2 全文件时 **194 passed** |
| W-02 | `bjobs` 批量原语 + 每节点一次热轮询 + 单 job 保留单 token 兜底 | `pytest tests/test_remote_lsf_batch.py tests/test_remote_phase{1,2,3,4,5,6}.py -q`；另跑 poll/cancel/storage 相邻回归 | **264 passed**；3 个 phase1_integration 真机用例 skip → `NOT_VERIFIED`（无集群凭据）；152 + 38 + 46 passed |
| W-03 | `find` 单次递归列举（NUL 分隔）+ 失败回落 SFTP | `pytest tests/test_remote_fetcher_find_batch.py tests/test_remote_phase4.py tests/test_acp_structure_sources.py tests/test_acp_api_structure_sources.py -q` | **118 passed**（含新 20 例）；`max_entries`/特殊字符/回落均覆盖 |
| W-04 | release-only tar 流上传（flag-off，失败回落） | `pytest tests/test_remote_release_tar_upload.py tests/test_remote_code_release*.py tests/test_remote_installed_sync.py -q` | **76 + 106 passed**（两组验收）；默认关闭，真机 tar 收益 `NOT_VERIFIED` |
| W-05 | IRC 文案 locality-neutral（zh+en）+ reason 进诊断 | `node frontend/js/module_tests/run_all.js`；`pytest tests/test_frontend_sync_energy.py -q` | **574 passed / 0 failed**（含新 3 例）；parity **24 passed** |
| W-06 | MCP `acp_status.job_ids` / `acp_read_artifact.artifact_refs` + Controller `GET /jobs?ids=` 单次批量 | `pytest tests/test_acp2_mcp_batch_tools.py tests/test_services_mcp.py tests/test_acp2_mcp_list_jobs_tool.py tests/test_acp_v1_batch_jobs.py tests/test_acp2_runtime_mcp_backends.py -q`；契约门 `pytest tests/test_acp2_mcp_sdk_contract.py -m contract` | **100 passed**（同组含 parity 3 例时 103 passed）；契约门 **8 passed（PASS，非 skip）**，`ACP2_TOOLS=/ACPServer/acp2-tools` |

**E1—E5 状态**：全部 `NOT_VERIFIED`。理由：无双后端回放对拍 harness；真机集群链路本机不可得；W-01b 未切换前 locality 泄漏仍存在（台账 §9.2）。

### 9.5 对原文的事实更正（P2）

- `services_data_bootstrap.py` 的锚点是 `_ReadRootAdapter:1788`、`build_read_only_reader:2263`、`build_read_root_resolver:2315`；原文 `:2295/:2319` 是函数体内的行，不能作锚点。
- W-03 的真实热路径是 `_walk_remote_dir` ← `walk_remote_files`（structure_sources / archive）；`_list_remote_dir_recursive` 只被无生产调用方的 `list_files_recursive` 使用。本轮两者都改，但验收以 `_walk_remote_dir` 为准。
- W-04 的真实上传循环在 `release.py::_upload_missing`（按 64 文件分块调用 `upload_files`），不是只改 `sftp.py:411`；W-04 与 W-03 没有技术依赖，只是回落模式相似。
- `mcp.py:493/:496` 是 `McpTool` 名称常量，不是调用点；W-06 的实际改动面是 handler、schema、`ControllerClient` 与 `tests/test_services_mcp.py` 的精确断言。
- `_STATUS_SCHEMA` 是 `additionalProperties: True`（不是 False）；`_READ_SCHEMA` 是 `additionalProperties: False` 且 `required: ["artifact_ref"]`。W-06 的批量参数因此是 **wire/schema 变更**（不是纯 additive）：用 `anyOf` 表达二选一，旧单参调用仍然合法。
- E2 原文"不存在远端特有错误"过于绝对：D11 的身份拒绝是证据规则，不是 locality 泄漏。修订为"不存在**未解释**的远端特有错误"；`SOFTWARE_UNAVAILABLE` 与身份拒绝分别按环境事实/证据状态登记。
- `§3.1` 的"A 档 6 项互不阻塞"更正为：W-01 被登记决策阻塞；W-04 仅灰度 release 路径；W-02/W-03/W-05/W-06 可并行。

### 9.6 已知限制（后续，不阻塞本批）

- **batch status 的 item 是 list 行投影**：单 job `GET /jobs/{id}` 会做 live-status/artifact descriptors 等增强，batch 用列表行，增强字段较少。Agent 需要增强字段时应读单 job；批量只用于低成本状态发现。
- **全 error 的 batch 仍 `status="ok"`**：错误在 item 级（`items[].status=="error"`），上层必须逐项检查，不能把顶层 `status=ok` 当整体成功。
- **batch read 无总体积预算**：最多 20 × `max_bytes` 的嵌套 body；后续可加总上限。
- **batch 空 `bjobs` 输出语义**：单 job 空输出→`NOT_FOUND`（旧契约），batch 空输出→`UNKNOWN`（未知优于猜错）；真机若对已消失 job 返回空，轮询会延后一轮，靠 `.exit_code`/`state.json` 兜底。
- **W-04 远端 staging 既有 symlink**：成员名校验防穿越，但 `tar -xf` 可能跟随 staging 内既有 symlink；该路径默认关且 staging 由服务独占，风险低。
- **W-03 `find` 全树**：即使 `max_entries` 很小也会执行完整 `find`；只是性能差异，截断结果正确。
- **`GET /jobs?ids=` 的精确回退**：列表页窗口之外（>1000 行）的 id 由 `store.get` 逐项补取（≤100），保持 batch 不丢 id；这是本批新增的兜底，已在 `tests/test_acp_v1_batch_jobs.py` 覆盖。
