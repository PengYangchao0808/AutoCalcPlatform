# ACP 前端 Plan_1 验收记录与接管表关闭（2026-10-19）

- 生成：并行计划一路 A（Frontend 收口）todo T34（`.omo/plans/acp-parallel-plan-1-frontend.md`，Closes GAP-6/GAP-7）。
- 执行日期：**2026-10-19**（计划帧记日 2026-10-18）；branch `feat/frontend-plan1-closure`，冻结基线 `f410ac9d`，发布修订 `899c7c0f`，本文件提交 = commit Y（见 §2 末行）。
- 上游输入：T6 基线地图（`docs/reference/current/ACP_Frontend_Baseline_Map_20261018.md`，接管表权威副本）、T29–T33 证据、Plan_1（`docs/reference/next-change/ACP_Parallel_Plan_1_Frontend_20261018.md`）、评审（`docs/reference/next-change/ACP_Parallel_Plans_Review_20261018.md` A5/A7/X3、P1-2/P1-4）。
- 三态纪律：证据词汇只用 PASS / FAIL / NOT_VERIFIED；范围状态词汇 IN_SCOPE / DEFERRED / NOT_APPLICABLE；延期≠通过；已执行失败只能是 FAIL，缺依赖未执行才是 NOT_VERIFIED，两者不得互替（评审 A5）。
- **总声明（§9 详述）：前端收口完成 ≠ 整端完成。** 本文件只关闭前端任务面（A00–A03/A05）的交付与接管表；A04/A06 未接入、A05 退出门保持 OPEN、真实科学/远程/生产全部 NOT_VERIFIED、终验波（F1–F4）待执行。

---

## 1. 五维分列（评审 A5）

五维 = **实现完成 / 模块通过 / 浏览器通过 / 服务接入 / 真实科学·远程·生产状态**。每行给依据（证据文件 + 提交）。证据根目录：`.omo/evidence/acp-parallel-plan-1-frontend/`。

| 面域（todo 群） | 实现完成 | 模块通过 | 浏览器通过 | 服务接入（S0） | 真实科学·远程·生产 |
|---|---|---|---|---|---|
| **A00 基线与接管表**（T1–T6） | ✅ `db0fcc8`（基线地图 tracked）；T1–T5 记录型（零提交） | ✅ T3 三套复验 Node 183 / 前端 pytest 508 / 扩展 303，delta 0 | ✅ T5 Playwright chromium 请求面测量 M1–M9（真实 S0 服务，128 请求全捕获）；N1/N2 显式 NOT_VERIFIED | ✅ T4 TestClient 51/51 资源 200 字节一致 + 304 语义 + 回退集清单 | **NOT_VERIFIED**（T5 N1/N2；T32 表 C） |
| **A01 viewer 实例边界**（T7–T15） | ✅ `24ffca5`(T7) `9a33fa6`(T8) `7cb26a1`(T9) `d6cb7cc`(T10) `931343f`(T11) `5184f14`(T12) `77daa87`(T13) `05b8e18`(T14) `4d7d371`(T15) | ✅ T30 Node 306/306（scene_instance 27、scene_lifecycle_matrix 8 等 30 子套件）；T15 red-ability 已证 | ✅ T31 S8 多实例 / S7 缺库降级 / S9 PNG+XYZ 导出 / S10 隐藏-重挂载 全 PASS（真实浏览器子矩阵） | ✅ HTML:1239 装载 `scene_instance.js`（T33 清单 #40）；T31 S1 真实服务结构面板渲染 | **NOT_VERIFIED**（T32 表 C） |
| **A02 向导四片与通用用例**（T16–T23） | ✅ T16 边界冻结（零提交，审计型）+ `e564c30`(T17) `ec3779b`(T18) `7ce58a6`(T19) `a35ccfd`(T20) `ea5dd26`(T21) `3f2b031`(T22) `c3375ba`(T23) | ✅ T30 Node 306/306（draft_form 7 / preview_validate 9 / wizard_upload_parse 8 / submit_edit 9 / system_endpoints 6 / system_resources_controller 10 / task_session_identity 4） | ✅ T31 S5 编辑不新建 / S6 候选冲突 409 / S2 分页筛选（真实+注入）/ B1 迟到筛选 / B2 迟到选择 全 PASS | ✅ HTML:1231/1236 + 4 个 wizard/*.js 片装载（T33 清单 #52–55）；T31 S5 编辑工作区在真实服务可用 | **NOT_VERIFIED**（T32 表 C） |
| **A03 视图纯度与清理**（T24–T29） | ✅ `ee0d849`(T24) `6376137`(T25) `233b096`(T26)；T27/T28 验证型（零提交，结论 PASS）；`899c7c0`(T29) | ✅ T30 Node 306/306 + pytest 525（T29 拆分后等效断言）；fe10 黄金样例 9 项不变（T28 核验） | ✅ T26 computed-style + 截图等价（task-26 before/after 1280 对照）；T31 全矩阵在其上通过 | ✅ T33 资源集 58/58 冷 200 / 暖 304 / 硬刷 200 字节一致 | **NOT_VERIFIED**（T32 表 C） |
| **A05 联合验收与发布包**（T30–T34） | ✅ T30/T31/T32 零提交（执行/记录型）；`3cfd439`(T33 发布包 tracked)；T34 = 本文件（commit Y） | ✅ T30 五组命令全绿：Node 306 / 前端选择 525 / 扩展 303 / 编辑候选 API 202 / `node --check` 80/80 | ✅ **T31 真实浏览器子矩阵 13+2 全 PASS**（12 核心场景 + 2 async race，独立复验一致）；**A05 退出门仍 OPEN**（§8-OPEN-1） | ✅ T32 REAL 行 A1/A6/A7/A8/A9（真实 409/422 直测）；T33 冷/暖/硬刷 58/58 | **NOT_VERIFIED**（T32 表 C 全部 5 行：真实科学/LSF/SSH/SFTP/生产，注明未涉及） |

分列说明（不混用）：
- 「模块通过」= Node module_tests + 前端 pytest 在合并后代码上的机器验证（T30，2026-10-18T19:37–19:41Z，HEAD `899c7c0`）。
- 「浏览器通过」= 真实 headless Chromium 对隔离真实 S0 服务的执行（T31，含独立复验）；INJECTED 行（S2b/S7/S12/R1/R2）在 T31 §2 行内标注，不计入真实侧。
- 「服务接入」= 页面装载面（T33 清单）+ 真实服务行为探针（T32 表 A）。
- 「真实科学·远程·生产」= T32 表 C 权威口径，本轮全部 NOT_VERIFIED；任何一行不得用 S0/模拟证据升级。

---

## 2. 交付物与提交清单（T1–T34 逐条）

提交哈希已于 2026-10-19 经 `git log --oneline -1 <hash>` 逐个核实（证据 §命令记录）；分支序列 `f410ac9d..3cfd439` 共 21 个前端 lane 提交（1 docs T6 + 19 代码/测试 + 1 docs T33），与下表一致。

| todo | 交付物 | 提交 | 备注 |
|---|---|---|---|
| T1 | F0/S0 冻结 + dirty 快照 | —（零提交） | F0 聚合 `fe9da69f…`（77 文件） |
| T2 | 装载/DOM/全局/网络/缓存/SSE/viewer 全地图 | —（零提交） | §①–§⑨；F-①/④/⑥ 发现登记 |
| T3 | §43/"42.7" 校正复验 + 三套计数 | —（零提交） | 183/508/303 delta 0；C1–C7 |
| T4 | 资源回退集 + TestClient 校验 | —（零提交） | 51/51；回退步骤 |
| T5 | 请求/并发/占用测量 | —（零提交） | M1–M9；N1/N2 NOT_VERIFIED |
| T6 | 基线地图与接管表（tracked） | `db0fcc8` | `docs(frontend): baseline map and takeover table` |
| T7 | scene_instance v2（受限实例 API） | `24ffca5` | `refactor(visualization): bounded scene instance…` |
| T8 | 主结构迁移 + HTML:1239 装载 | `9a33fa6` | R5 页面接入收口 |
| T9 | 能量预览迁移 | `7cb26a1` | dispose 幂等/PNG 保持 |
| T10 | 向导预览迁移 | `d6cb7cc` | 唯一声明保持 reaction-builder |
| T11 | 反应预览迁移 | `931343f` | hover 回调 + 失效守卫 |
| T12 | NMR viewers 迁移 | `5184f14` | per-candidate dispose |
| T13 | 振动播放迁移 | `77daa87` | 单 interval 断言 |
| T14 | 结构查看/编辑迁移 | `05b8e18` | overlay 经 token |
| T15 | 生命周期矩阵 + 泄漏 pin | `4d7d371` | 8 行矩阵；3 个 pytest pin；发现 (a)/(b) 登记 |
| T16 | wizard 边界冻结与逐符号映射 | —（零提交，审计型） | 串行链 T17→T20 依据 |
| T17 | 上传/解析片 | `e564c30` | FormData/timeoutMs:0 保持 |
| T18 | 预览/校验片 | `ec3779b` | task_input_workspace 消费 pin |
| T19 | 草稿/表单片 | `7ce58a6` | catalog cache 单 owner 落位 |
| T20 | 提交/编辑重算片 | `a35ccfd` | 编辑 POST `/jobs` 拦截保持 |
| T21 | 跨片保留行为 pin | `ea5dd26` | defaults/0-false-null/409-422 全 PASS |
| T22 | loadNodes/loadBackends facade | `3f2b031` | F-④-1 owner 撕裂裁定收口 |
| T23 | taskSession 身份缺口补齐 | `c3375ba` | files/candidates/results 身份 |
| T24 | renderer 纯度复核修复 | `ee0d849` | 22 窄委托不联网/不读全局任务 |
| T25 | FE-04/FE-05 调用闭包清理 | `6376137` | s2scan 死字段等 5 项删除（apiV2Remote 未删，见 §8-OPEN-3） |
| T26 | FE-11 CSS 安全归并 | `233b096` | computed style + 截图等价 |
| T27 | FE-09 创建入口唯一性验证 | —（零提交，验证型） | features 创建点 = 0；结论 PASS |
| T28 | FE-10 黄金样例保持核验 | —（零提交，验证型） | 双实现差异保持；结论 PASS |
| T29 | 巨型页面测试拆分 | `899c7c0` | 525 passed 等效断言；`frontend_test_selection.txt` |
| T30 | 全量回归五组命令 | —（零提交，执行型） | 306/525/303/202/80 全绿 |
| T31 | 真实浏览器 + S0 联合验收 | —（零提交，执行型） | 13+2 全 PASS + 独立复验 |
| T32 | 故障注入/真实响应分列 | —（零提交，记录型） | 表 A/B/C；表 C 全 NOT_VERIFIED |
| T33 | 静态发布包与回退步骤 | `3cfd439` | `docs(frontend): release resource set…` |
| T34 | 本验收记录 + 接管表关闭 + Plan_1 勾选 | **commit Y（本提交）** | `docs(frontend): plan1 acceptance record and takeover closure` |

核实记录：任务清单给出的哈希全部命中且主题匹配（T7 `24ffca5`…T33 `3cfd439`）；补核实 T6 `db0fcc8`、T19 `7ce58a6`、T21 `ea5dd26`、T29 `899c7c0` 与 T30 §1 的切片归属一致；T16/T27/T28/T30/T31/T32 = 计划内 `Commit: N`，无漂移。

---

## 3. 接管表关闭（tracked）与「被替代实现已删除」审计

### 3.1 接管表终态

接管表权威副本 = 基线地图 §5（本次同步更新为终态，随 commit Y 提交）。终态摘要：

- **§5.1 viewer/实例边界（T7–T15）**：全部行 = **已迁移·单实现**（`scene_instance.js` 已装载 HTML:1239；六场景经受限实例；生命周期矩阵 pin 生效）。`s2scanState.viewer/resultViewer` 死字段行 = **已清理（T25 `6376137`，REMOVED-1；pin 改 post-cleanup 极性）**。`$3Dmol` 注释级行与 FE-09 行 = **T27 已复核（唯一 raw 驻点 `mol3d_adapter.js:34`；唯一实例工厂 `scene_instance.js`；features 创建点 0）**。
- **§5.2 向导四片（T16–T23）**：wizard.js 整体行与四片行 = **已迁移·单实现**（四片 `frontend/js/workbench/features/wizard/{upload-parse,preview-validate,draft-form,submit-edit}.js` + 保留壳 wizard.js，HTML:1251–1254 装载）；`loadNodes/loadBackends/backendsCache/softwareDiscoveryCache` 行 = **已迁移·单实现（T22 `3f2b031`：`js/client/system-endpoints.js` facade + `system_resources_controller.js`；reaction-builder 仅存薄委托调用；`/nodes` 503 错误面由 T32-B1/T30 守护）**；`workflowCatalogCache/methodCatalogCache`（F-④-1）= **已裁定终 owner：`wizard/draft-form.js:59-60` 唯一声明+唯一写者（live global view 供 job_editor/reaction-builder/bootstrap 只读）**；taskSession 行 = **T23 缺口已补（`c3375ba`）**。
- **§5.3 视图与清理（T24–T28）**：能量 22 委托 = **已复核·纯度 PASS（T24 `ee0d849`）**；FE-04 残留闭包 = **已关闭（T25）**；FE-05 退役写交互/空钩子 = **已关闭（T25，5 项 REMOVED）**；FE-11 CSS = **已归并（T26 `233b096`，6→更少文件 computed-style 等价）**；FE-10 = **黄金样例保持（T28 核验 PASS；统一需 C02/C07，不当等价去重）**；`apiV2Remote`（F-④-2 死入口）= **未删除，登记开放项（§8-OPEN-3；0 产品调用者，非被替代实现而是候选清理遗留）**。
- **§5.4 已迁移保持面**：无变化；bootstrap 225-226 调用者已随 T22 迁移。
- 「无 owner」符号计数：**0**（终态复核；`apiV2Remote` 有 owner = 后续清理切片，登记于开放项）。

### 3.2 「被替代实现已删除」审计（2026-10-19 现盘 grep，HEAD 写作时点）

| # | 审计项 | 命令口径 | 结果 |
|---|---|---|---|
| A-1 | features/页根重复 viewer 创建 | `grep -rn 'ACPVisualization\.mol3d\.create(\|\$3Dmol\.createViewer(' frontend/js/workbench/features/ frontend/js/*.js`（排 vendor） | **0 命中 = PASS**（与 T27/T15 pin 一致） |
| A-2 | s2scan 死 viewer 字段 | `grep -rn 's2scanState\.viewer\|resultViewer' frontend/ tests/` | **0 命中 = PASS**（T25 已删；tests 反极性护栏断言亦无剩余命中，T25 §3 复核） |
| A-3 | 旧 S2 模态（markup/CSS/JS 闭包） | `grep -rn 's2scan-modal\|S2scanOpen\|s2scanStep\|s2scanSubmit' frontend/` | **0 命中 = PASS**（首片此前已清，T25 复核零残留） |
| A-4 | 旧 owner `job_session_controller.js` | `ls frontend/js/workbench/controllers/` | **不存在 = PASS**（仅 task_session_controller.js，C1 校正事实） |
| A-5 | `loadNodes/loadBackends` 旧 owner | grep reaction-builder.js | **仅薄委托（:1345-1346/:1813-1814 转发 systemResources controller）= PASS**（T22 facade，非重复 owner） |
| A-6 | catalog cache owner 撕裂（F-④-1） | grep 声明/写者 | **单一 owner `draft-form.js:59-60` + `Object.defineProperty` live view = PASS**（reaction-builder :968 经全局 getter 只读） |
| A-7 | `apiV2Remote` 死入口（F-④-2） | grep 全 frontend/ | **仍存在（compat/api.js:80，0 产品调用者，仅 module_tests 注释提及）→ 未删除，登记 §8-OPEN-3** |

审计结论：被替代实现（重复创建点/旧 owner 函数/旧 S2 模态/死 viewer 字段）**已全部删除或收敛为单一实现**；唯一遗留 = `apiV2Remote` 死入口（本就是"候选清理"而非被替代实现，T25 范围未含，如实登记不关闭）。

---

## 4. Plan_1 勾选与 A04/A06 状态

Plan_1（`docs/reference/next-change/ACP_Parallel_Plan_1_Frontend_20261018.md`）本次更新：

- **A00×4 / A01×5 / A02×4 / A03×5 / A05×5 = 23 条全部勾选**，每条带证据指针（提交/证据文件），逐条对应见 §7 终版映射。
- **A04（6 条）/ A06（4 条）= 保持未勾选，标注 `DEFERRED / 未接入`**（评审 P1-2）：本轮未启用任何新能力；相关产品/UI 目标**不关闭**；入场门不变（B02–B04 prepare/快照/事件契约、C02/C06 产品 schema、B01/B08–B11 账户/配额/包）。交接契约与门状态见基线地图 §6（GT-1…GT-5 全 DEFERRED）。
- A05 退出门（Plan_1 `:90`「默认页真实通过、资源无混载/缺失、旧 owner 撤除，前端回退不修改 DB/attempt/工件、不重复发命令」）：三项事实均已具备证据（T31 默认页真实通过、T33 资源零缺失零混载、§3.2 旧 owner 撤除、T33 §3 回退不触碰 DB/attempt/工件/命令），但按计划指令**保持 OPEN**（§8-OPEN-1；F4 与终验未执行，不条件关闭）。

---

## 5. §43/"42.7" 校正补丁：已交付事实

- 补丁全文：基线地图 **§7**（T6 随 `db0fcc8` 入库，tracked）。对象 = 主方案 `docs/ACP_Architecture_Adjustment_Plan_20261016.md` §43/§42.7 的 C1（task_session_controller 文件名）、C2（无 createInstance）、C3（scene_instance 未装载——**该条前提已被 T8 关闭**，补丁文本为 2026-10-18 时点事实，主方案维护者应用时须知）、C4–C6（计数时点/范围）。
- 状态 = **DELIVERED（已交付，交付≠应用）**：主方案文档本轮**未编辑**（约束 + 该文件属架构规划工作）；应用由主方案维护者执行，应用后版本号登记回基线地图 §6。
- 证据链：T3 §5（C1–C7 逐条 grep 复核）→ 基线地图 §7（diff 格式）→ 本节（交付事实记录）。

---

## 6. 四组合验收责任（评审 X3/P1-4/P1-2）

| 组合 | 含义 | 执行责任 |
|---|---|---|
| **F0+S0** | 冻结前端基线 + 现有 S0 服务 | **A**（本路已完成：T3/T4/T30/T31 S0 证据即此组合） |
| **F1-base+S0** | 本轮收口前端（未接新服务）+ S0 | **A**（已完成：T30 五组 + T31 浏览器矩阵 + T33 发布包均在此组合执行；HEAD `899c7c0`） |
| **F0+S1** | 旧前端 + 新服务（旧客户端兼容） | **B**（在其选定集成提交上执行黑盒测试，产出 = B-T75；A 提供测试与 S0 证据基线） |
| **F1-integrated+S1** | 集成前端 + 新服务 | **B 执行（B-T75）**；**C** 在同一集成候选判科学等价；**T69 消费** B-T75 结果收口 |

绑定规则（X3 原文口径）：
- 每份组合证据绑定**实际提交 + 契约版本 + 配置 + fixture 摘要**；不得拿 S0 的旧浏览器记录证明 S1。
- A 维护前端测试并提供 S0 证据；B 对选定集成提交执行黑盒测试（B 执行 A 提供的黑盒用例，不取得 A 源码写权）；C 判科学等价；启用新科学路径时另记录该组合的 C/release 版本。
- 当前状态：S1/集成环境未就绪，F0+S1 与 F1-integrated+S1 = **DEFERRED**（GT-4）。

---

## 7. 覆盖映射终版（Plan_1 checkbox × 23）

| Plan_1 条目 | 交付 todo | 状态 | 证据 |
|---|---|---|---|
| A00-1 地图建立 | T2 | ✅ 已交付 | task-2 证据 + 基线地图 §1 |
| A00-2 §43/42.7 校正 | T3、T6 | ✅ 已交付 | task-3 §5 + 基线地图 §7（DELIVERED） |
| A00-3 H/R1–R4 复验 + F0/S0 固定 | T1、T3、T4 | ✅ 已交付 | 基线地图 §2/§3 |
| A00-4 请求/并发/占用测量 | T5 | ✅ 已交付 | 基线地图 §4（N1/N2 显式） |
| A01-1 scene_instance 审计扩展 | T7 | ✅ 已交付 | `24ffca5` + task-7 |
| A01-2 opts.viewer/addModel 边界 | T7 | ✅ 已交付 | `24ffca5`（TEST-ONLY seam + token 白名单 pin） |
| A01-3 逐场景迁移 | T8–T14 | ✅ 已交付 | `9a33fa6`/`7cb26a1`/`d6cb7cc`/`931343f`/`5184f14`/`77daa87`/`05b8e18` |
| A01-4 身份/framing/多实例/PNG/降级 | T8–T15 | ✅ 已交付 | T15 矩阵 8 行 + pin；T31 S7/S8/S9 |
| A01-5 生命周期验证 | T15 | ✅ 已交付 | `4d7d371`；red-ability 已证 |
| A02-1 四片提取 | T16–T20 | ✅ 已交付 | T16 冻结 + `e564c30`/`ec3779b`/`7ce58a6`/`a35ccfd` |
| A02-2 defaults/校验/保留行为 | T19、T21 | ✅ 已交付 | `7ce58a6` + `ea5dd26`（跨片清单全 PASS） |
| A02-3 loadNodes/loadBackends 归位 | T22 | ✅ 已交付 | `3f2b031`；F-④-1 终 owner draft-form.js |
| A02-4 taskSession 核对 | T23 | ✅ 已交付 | `c3375ba`（身份/revision/dirty/迟到） |
| A03-1 能量 22 委托复核 | T24 | ✅ 已交付 | `ee0d849`；renderer 纯度 PASS |
| A03-2 FE-04/05/11 闭包 | T25、T26 | ✅ 已交付 | `6376137`（5 REMOVED）/ `233b096` |
| A03-3 FE-09 | T27 | ✅ 已验证 | task-27（零提交，验证型 PASS） |
| A03-4 FE-10 | T28 | ✅ 已验证 | task-28（黄金样例保持） |
| A03-5 测试拆分 | T29 | ✅ 已交付 | `899c7c0`；525 passed |
| A05-1 Node/pytest 回归 | T30 | ✅ 已交付 | task-30（306/525/303/202/80） |
| A05-2 走查矩阵 | T31 | ✅ 已交付 | task-31（13+2 全 PASS + 复验） |
| A05-3 故障/真实标记 | T32 | ✅ 已交付 | task-32（表 A/B/C） |
| A05-4 发布包 | T33 | ✅ 已交付 | `3cfd439` + 发布包文档 |
| A05-5 状态分列/收尾关闭 | T34 | ✅ 本文件 | commit Y |

计数核对：4+5+4+5+5 = **23**，无缺漏；A04（6）+ A06（4）不在计数内（DEFERRED / 未接入）。

---

## 8. 开放项与观测（非失败，各有 owner/next step）

| # | 项 | 内容 | Owner / 下一步 |
|---|---|---|---|
| OPEN-1 | **A05 退出门保持 OPEN** | 计划指令：不条件关闭。浏览器子矩阵已 PASS（T31），但 A05 整门含 F4/终验与跨车道真实轴；已执行失败只能是 FAIL、缺依赖未执行才是 NOT_VERIFIED（评审 A5） | 终验波 F1–F4 + Orchestrator；门关闭需终验 APPROVE |
| OPEN-2 | T31 S4 观测：rerun API `attempt` 投影 ≠ DB | API `/api/v1/jobs/{id}` 投影 attempt=1，DB `jobs.attempt`=5→6；场景验收不受影响（原地重跑/同 work_dir/attempts 归档全满足）；产品观测非场景 FAIL | 产品 owner（scheduler/API 投影）；复跑 `select attempt from jobs where id='20261019_035529_001_t31_real_singlepoint'` |
| OPEN-3 | `apiV2Remote` 死入口未删（F-④-2） | compat/api.js:80，0 产品调用者；T25 范围（FE-04/05）未含此项 | 后续清理切片（grep 面：frontend/**+HTML+module_tests+tests+/legacy/，删除前零调用者复核） |
| OPEN-4 | T15 发现 (a) 振动箭头鸡生蛋 | `arrowState.enabled` 仅绘制成功后置 true → 冷启动静态箭头画不出（既有，自 `5a430fc`）；`vibration_playback.test.js::RECORDED FINDING` 锁定 | 可视化收口复核 owner（T27 已核登记，修复需产品 JS 变更 + 断言反转） |
| OPEN-5 | T15 发现 (b) nmr textContent 摘除 canvas | `nmr.js:837` 成功路径 `container.textContent=""` 删除 3Dmol canvas 子节点（既有，自 T40）；`nmr_viewer.test.js::RECORDED FINDING` 锁定 | 同上 |
| OPEN-6 | T24 登记项 | NMR 2D 谱图 = N/A→REGISTERED（现无 2D 谱图图表，未来新增须守纯度规则）；FE-08 inspector/candidate markup 留页（设计内例外，读 lastSelectedJob/s2scanState.candidates） | NMR 前端 lane / FE-08 后续切片 |
| OPEN-7 | T2 发现处置汇总 | F-①-1 scene 未装载→**已关闭（T7/T8）**；F-①-2 memory_input IIFE 扫描口径→非缺陷，保持；F-④-1 catalog owner 撕裂→**已关闭（T22）**；F-④-2→OPEN-3；F-⑥-1 s2scan 死字段→**已关闭（T25）**；F-T4-1 HTML 无 Cache-Control→LOW 登记（T33 F-T33-1 延续） | F-T4-1：服务端响应头改进（B 路域） |
| OPEN-8 | 跨车道延期（T32 表 C） | 真实科学执行 / LSF / SSH / SFTP / 生产 8765 = NOT_VERIFIED（未涉及） | 各归属 lane（C 科学 / B 远端 / 部署运维）；不得用本轮 S0 证据升级 |
| OPEN-9 | 四组合中 S1 两组合 | F0+S1、F1-integrated+S1 = DEFERRED（S1/集成环境未就绪） | B-T75 执行 / T69 收口 / C 判等价（§6） |
| OPEN-10 | T33 交接 | 旧标签页+资源缓存+新服务组合验证交 B-T71；生产发布与 systemd 重启不在本计划 | B-T71 / 部署运维 |

---

## 9. 完成边界声明

1. **前端收口完成 ≠ 整端完成。** 本记录关闭的是：前端任务面兼容重构（A00–A03）+ 联合验收与发布准备（A05 交付物）。Services/科学内核/独立发行/多用户/生产发布验收不在本声明内（Plan_1 §4 原文口径）。
2. **A04/A06 未接入**：本轮零新能力启用；相关产品/UI 目标保持开放（评审 P1-2）；入场门（B02–B04/C02/C06/B01/B08–B11）不变。
3. **终验波待执行**：F1（计划合规）/F2（代码质量）/F3（真实手动 QA）/F4（理想态保真）全部待跑；F4 不得把 A05 未验证标为整路无条件完成（计划补充判据）。
4. 真实科学/远程/生产五维全部 NOT_VERIFIED（§1 末列）；任何后续文档不得引用本记录将其升级为 PASS。
