# 并行计划一：Frontend 收口与真实能力接入

日期：2026-10-18。负责人：前端路 A。共同基线、状态、原编号和写权见[并行索引](ACP_Architecture_Progress_and_Parallel_Index_20261018.md)。本清单可与 Services、CCCP 两路同时启动。

## 1. 目标、范围与已有成果

先在现有 S0 服务上完成前端任务面兼容重构，再逐片接真实的新服务。保留现有界面、URL、操作流程与科学结果，不安排整页重设计。

已实现并应复用：HTML/CSS/JS 拆分、Client transport/store/taskView/jobDetail/events/polling/jobCommands/wizardEndpoints，taskList/taskSession/jobDetail/candidateReview controllers，能量 renderer、framing 与 mol3d 创建 adapter。任务会话实际文件是 `task_session_controller.js`。这些成果需要当前基线复验，不再重复创建实现。

当前关键缺口：scene 模块未接默认页，原生 viewer/model 仍外泄；wizard 表单职责与通用节点/后端用例尚未完全归位；真实浏览器/服务、静态发布与回退证据未收口。

写入范围为 `frontend/**`、前端模块与页面测试和本清单；后台科学 fixture、API/config/打包文件由对应 owner 修改。仍有两名前端开发者时，沿用主方案 §40 的入口单写者规则。

## 2. 可立即执行的清单

### A00：重建当前前端基线与接管表

关联：P0、CQ-01，H/R0、FE/OC 既有索引；无跨路实现前置。

- [x] 从实际默认 HTML 建立脚本/样式装载、DOM hooks、全局函数、网络资源、缓存写者、SSE/timer、viewer 和调用方地图，补回当前 checkout 缺失的证据内容至可交付位置。（T2 证据 + 基线地图 §1）
- [x] 将 §43/“42.7”中的旧文件名、createInstance 与全部完成声明校正为当前代码事实；历史测试计数保留时点/范围。（T3 §5 校正 C1–C7 + 基线地图 §7 可应用补丁；交付≠应用，主方案未编辑）
- [x] 对 H、R1—R4 已实现项做基线复验，记录已登记兼容面及实际缺陷；固定 F0/F1＋S0 组合和完整静态资源回退集。（T1/T3/T4：三套复验 183/508/303 delta 0；F0 摘要 `fe9da69f…` 77 文件、51 默认页引用、回退集 `tmp/t4_resource_listing.txt`；R5 scene_instance 未装载=已知余项→T8）
- [x] 记录固定任务/轨迹样本上的请求数、并发合并、切换/隐藏后的资源占用；未知值写未测，不承诺拆文件加速。（T5：M1–M9 实测 + N1/N2 NOT_VERIFIED 显式行）

A00 状态（2026-10-18，T6 收口）：四项全部交付，证据 = `docs/reference/current/ACP_Frontend_Baseline_Map_20261018.md`（接管表权威副本）+ `.omo/evidence/acp-parallel-plan-1-frontend/task-{1..6}-…md`（日志层）；基线提交 `f410ac9d`。未迁移符号逐一有 owner（基线地图 §5，无「无 owner」条目）。

退出门：已有实现、余项、缺陷、未测项可区分；当前未迁移符号逐一有 owner。A00 的地图可以逐片冻结，不阻塞 A01 的首个场景审计。

### A01：完成 Visualization 实例边界与逐场景接入 ✅（T34 勾选，2026-10-19）

关联：P3-02/03/04、R5/R6、CQ-03；依赖 A00 对应场景地图，无新后端前置。

- [x] 审计并扩展现有 `scene_instance.js`，在 Visualization 内部创建/持有 viewer，收敛模型句柄、监听器、动画/resize/framing 及销毁；明确 setData/selection/style/export 等受限能力和错误结果。（T7 `7327259`；模块 v2 受限实例 API）
- [x] 处理当前 `opts.viewer` 外部注入及 `addModel` 返回原生 model 的边界缺口；不得只用函数白名单或隐藏一个字段宣称封装完成。（T7 `7327259`：TEST-ONLY `opts.viewer` seam + addModel 结果 token 白名单方法 pin `test_add_model_result_restricted_methods_pin`）
- [x] 先迁主结构与能量预览，再迁向导、NMR、振动、结构查看/编辑及其他地图内活跃场景。每个场景分别完成数据注入、回调、默认页装配、撤除旧句柄/动画 owner 和验证。（T8–T14：`9fe8f3d`/`d836600`/`83367e9`/`4e863ec`/`26ee295`/`5954b5a`/`6521c1a`；scene_instance HTML:1239 装载）
- [x] 使用稳定原子/帧/原生振动模式身份；保留 scheduleViewerFraming/frame3DViewer、多实例相机/选择、PNG 导出和缺 3Dmol 的页面降级。（T15 `10afe61` 矩阵 8 行；T31 S7 降级 / S8 多实例 / S9 PNG 浏览器复证）
- [x] 验证反复挂载、A→B→A、隐藏/显示、暂停/停止、销毁后迟到回调；一个 canvas 一个实例，不让不同场景共享一个原生 viewer。（T15 `10afe61` `scene_lifecycle_matrix.test.js` + red-ability；T30 Node 306 全绿含 scene_instance 27）

退出门：迁移范围内业务只持有受限实例；返回值/事件也不泄漏原生模型；默认页实际使用新实例且没有第二套动画或旧监听器。→ **交付面已满足（T27 复核 features 创建点=0；接管表 §5.1 终态）；整门随 A05/终验关闭。**

### A02：拆分向导四片并归位通用任务用例 ✅（T34 勾选，2026-10-19）

关联：R3/R4/R6、FE-08、CQ-03；依赖 A00 的对应契约，沿用 S0。

- [x] 在 `wizard-endpoints.js` 端点单源基础上，按上传/解析、预览/校验、草稿/表单、提交/编辑重算四片提取用例，先迁真实职责与状态 owner，再按完整依赖拆文件。（T16 边界冻结 + T17–T20：`345ab67`/`5408146`/`b5196a3`/`94ccfff`；四片 `features/wizard/*.js` HTML:1251–1254 装载）
- [x] 新建/编辑继续共用 defaults 与校验，保留 `batch_role_defaults.js`、旧草稿 reader、显式 0/false/null、FormData、无超时例外、409/422 和编辑 POST `/jobs` 拦截。（T21 `5dfa994` 跨片保留行为 pin 全 PASS；T32 A6/A8 真实 409/422 直测）
- [x] 将 `reaction-builder.js::loadNodes/loadBackends` 等通用职责通过 Client 端点 facade 和 Workbench 用例归位；保留有实际调用者的历史机理只读逻辑。（T22 `ebfc772`：`system-endpoints.js` + `system_resources_controller.js`；F-④-1 catalog cache 终 owner draft-form.js；机理只读/410 由 T25 清理后保持）
- [x] 逐项核对文件/日志、候选与结果加载在 taskSession 下的身份、revision、dirty/锁定与迟到响应处理；已有 controller 只补缺口，不重写第二份。（T23 `783435a`：`task_session_identity.test.js` 4 项 + files_session +2；T31 B1/B2 迟到竞态浏览器复证）

退出门：四片各有单一状态与调用入口；原向导/编辑/候选流程保持，候选保存不隐式创建任务。→ **已满足（T30 五组回归 + T31 S5/S6 编辑不新建/候选冲突 PASS）。**

### A03：完成既有视图与前端清理候选的逐项关闭 ✅（T34 勾选，2026-10-19）

关联：P3-01、OS-08、FE-04/05/09/10/11、CQ-03/07、P7 前端部分。

- [x] 复核能量图 22 个窄委托与其他在用二维视图；renderer 不联网、不读全局任务、不做全量科学分析，必要选择由 controller 注入。（T24 `3503d4e` 纯度复核修复 PASS；FE-08 inspector markup / NMR 2D N/A 登记为设计内例外）
- [x] 对 FE-04 报告链、FE-05 退役写交互/空钩子及 FE-11 样式候选核查调用闭包，先替代或确认无用再清理，保留 `/legacy/` 与历史报告读取。（T25 `b7f95de` 5 项 REMOVED + s2scan 死字段；T26 `6421ffe` CSS 归并 computed-style/截图等价；`apiV2Remote` 死入口未删→登记开放项）
- [x] FE-09 的创建入口归并与 A01 同一实现交付，不能另建第二 adapter。ECharts 仅在确有视图需求时做比较试点，不作为前端收口前置。（T27 验证 PASS：features 创建点=0，唯一 raw 驻点 mol3d_adapter.js，未建第二 adapter，未引入 ECharts）
- [x] FE-10 二面角既有差异保持黄金样例；统一需 C02/C07 确认科学约定并单列数值行为变更，不当作等价去重。（T28 验证 PASS：fe10_dihedral_golden 9 项保持，T30 复跑不变）
- [x] 拆分巨大页面测试时保留等效断言、fixture 和收集范围；异常区分缺库/销毁/网络失败，不静默显示成功或填零。（T29 `5518aa6`：`frontend_test_selection.txt` 权威清单 525 passed 等效断言）

退出门：已接管能力唯一实现；所有保留兼容面都有调用者和保留理由，清理不改变原外观/交互或科学语义。→ **已满足（接管表 §5.3 终态 + T26 截图等价 + T30/T31 回归）。**

## 3. 依契约并行、依真实能力启用的清单

### A04：接入只读快照、prepare、事件与科学产品 — **DEFERRED / 未接入（T34 记录，评审 P1-2；不关闭）**

> 状态（2026-10-19）：本节 6 条**全部未勾选、未实施**。本轮（A00–A03/A05）零新能力启用，S0 保持默认可运行路径。入场门不变：B02（DataResourcePort/prepare 契约 + fixture 冻结版）、B03/B04（task-view 快照与持久事件协议）、C02/C06（产品 schema + frames/VIEW_REGISTRY 投影 adapter 版本）。交接契约与门状态见基线地图 §6（GT-1…GT-5 全 DEFERRED）；启用时在彼处登记版本号。相关产品/UI 目标保持开放，不得以本轮前端收口当作本节完成。

关联：P1-03/04、P2-05、P5-01/03、D1/D2；协议开发可与 B02—B04/C02 同时开展。

- [ ] 使用 B 提供的版本化 schema 和正常/空/部分/失败/旧版本 fixture 开发 Client adapter，保留 S0 为默认可运行路径。
- [ ] job/name/catalog/组织 revision、attempt/generation 分开比较；共享取消只取消当前消费者；新增资源只由 ResourceStore 发布共享快照。
- [ ] 真实 prepare/availability 就绪后呈现未准备/准备中/失败/可用及操作回执，不把 202 当作科学完成；读取缺失不自行解析原始 QC 或启动收费计算。
- [ ] 真实事件协议就绪后接 cursor/去重/重连/快照重置；只停止被事件确实覆盖的轮询，保留终态最后刷新与结果可用性重试。
- [ ] 消费 C 科学产品经 B 的 frames/VIEW_REGISTRY adapter，保留原子/帧/模式 ID、单位/参考态和缺失原因；有界加载，不在 JS 重算权威抽样/判据。
- [ ] 错误 code/params 与本地化文案对接，保留兼容 message/status/body、本地 8s/适用远端 60s 和明确例外；命令超时保持结果未知，禁止盲目重发。

退出门：F1＋S0 保持，F0＋S1 由 B 证明兼容，F1＋S1 对实际交付接口联调；mock 通过只记模块完成。

### A05：当前前端联合验收与静态发布包 ✅ 交付物完成（T34 勾选，2026-10-19；**退出门保持 OPEN**）

关联：H5/R6、CQ-08、P7；依赖本轮实际选用的 A01—A04 切片。

- [x] 按当前源码运行 Node、相关前端/编辑/候选/结果 API 回归，分别记录基线、改动后结果与跳过原因。（T30：Node 306 / 前端选择 525 / 扩展 303 / 编辑候选 API 202 / `node --check` 80/80，全绿零跳过；基线对照 T3 183/508/303 delta 表）
- [x] 在隔离数据、真实 S0 默认入口执行列表→详情→文件/日志→结果，覆盖分页/筛选、A→B→A、rerun、编辑不新建、候选冲突、缺库、多实例、导出、隐藏/重挂载、中英和断网恢复。（T31：13 场景 + 2 async race 全 PASS + 独立复验一致；INJECTED 行 S2b/S7/S12/R1/R2 行内标注）
- [x] 对真实响应和故障注入分别标记；真实科学/LSF/SSH/SFTP 与生产另记 PASS/FAIL/NOT_VERIFIED，不以模拟成功替代。（T32：表 A REAL 6 行 + 表 B INJECTED 6 行 + 表 C 真实科学/LSF/SSH/SFTP/生产全 NOT_VERIFIED 注明未涉及）
- [x] 交付匹配 HTML/JS/CSS、装载顺序与摘要，验证冷/暖缓存、普通刷新和旧标签页；按现有静态挂载形成一致切入口与回退步骤。（T33 `e0acc29`：58/58 清单冷 200/暖 304/硬刷 200 字节一致；回退演练 A/B1/B2 三方一致；旧标签页浏览器组合交 B-T71）
- [x] 分列“H 拆分完成”“任务前端兼容重构完成”“新服务接入状态”“真实远端/生产状态”，删除被替代实现并关闭接管表。（T34 = 本勾选：五维分列见 `docs/reference/current/ACP_Frontend_Plan1_Acceptance_20261019.md` §1；被替代实现删除审计 §3.2 全 PASS（唯一遗留 `apiV2Remote` 死入口登记开放）；接管表终态 = 基线地图 §5）

退出门：默认页真实通过、资源无混载/缺失、旧 owner 撤除，前端回退不修改 DB/attempt/工件、不重复发命令。→ **三项事实证据已备（T31/T33/接管表终态），但按计划指令与评审 A5，本门保持 OPEN 不条件关闭**（终验波 F1–F4 待执行；跨车道真实故障轴 NOT_VERIFIED）。

### A06：M2/M3/M4 的产品能力界面 — **DEFERRED / 未接入（T34 记录，评审 P1-2；不关闭）**

> 状态（2026-10-19）：本节 4 条**全部未勾选、未实施**。等待 B01/B08/B09/B10/B11（账户/项目/Key/配额/账本）与 M2—M4 里程碑范围；入场门见基线地图 §6 GT-5。本轮未启用任何相关 UI，旧功能保持可用；相关产品/UI 目标保持开放。

关联：§32/33、MU-09、PR-09、D4—D6；按实际里程碑启用。

- [ ] B08 项目包/溯源/MCP 就绪后接导入导出、证据/修订与诊断；原科学事实不被人工建议覆盖，项目导入不自动执行。
- [ ] B01/B09/B10 就绪后接登录/项目/Key、配额/周期核时和管理员界面，普通角色、等级与管理权限分开；菜单隐藏不当作授权。
- [ ] B11 通过账本/准入门后接预算/预留/用量/账单；余额不足不锁住已授权结果，价格或用量未确认时明确状态。
- [ ] GPU、高级科学视图、对象存储/Hosted 仅展示实际支持能力；新增界面另列最小增量，不提前重设计整页或显示成功占位。

退出门：每个新界面有真实接口、授权/错误与回退验证，未交付能力保持未启用。

## 4. 顺序、交接与完成定义

立即推进 A00，按场景并行准备 A01/A02/A03；共享 HTML/bootstrap/旧脚本仍单写者逐片接入。A04 在契约冻结后开发，真实启用等待 B/C；A05 验收已选择范围，A06 随 M2—M4 扩展，不阻塞纯 S0 收口。

向 B 交接 Client 所需端点、错误、availability、分页/数据预算、事件语义与浏览器证据；向 C 反馈无法忠实显示的科学字段，不自行发明其含义。每片包含公开接口、旧 owner 撤除清单、装载顺序、结果/限制及回退。

本清单全部关闭表示前端自身目标及实际启用增量完成；不替代 Services/科学、独立发行、多用户或生产发布验收。建议复验入口为 `node frontend/js/module_tests/run_all.js`、现有 frontend/integration 和浏览器系列，按切片补适用编辑/结果 API 回归；本次文档拆分未运行这些检查。

## 5. T34 收口记录（2026-10-19）

- A00–A03/A05 全部 23 条 checkbox 已勾选（A00×4 于 T6、其余于 T34），逐条证据指针见各行括注与 `docs/reference/current/ACP_Frontend_Plan1_Acceptance_20261019.md` §7 覆盖映射终版。
- A04（6 条）/A06（4 条）= DEFERRED / 未接入：不关闭、不勾选；入场门 B02–B04/C02/C06/B01/B08–B11 不变（基线地图 §6）。
- A05 退出门保持 OPEN（评审 A5：已执行失败只能是 FAIL、缺依赖未执行才是 NOT_VERIFIED，不得互替；F4 不得把 A05 未验证标为整路无条件完成）。
- **前端收口完成 ≠ 整端完成**：真实科学/LSF/SSH/SFTP/生产五维全部 NOT_VERIFIED（T32 表 C）；四组合验收责任（F0+S0 / F1-base+S0 / F0+S1 / F1-integrated+S1）与证据绑定规则见验收记录 §6；终验波 F1–F4 待执行。
