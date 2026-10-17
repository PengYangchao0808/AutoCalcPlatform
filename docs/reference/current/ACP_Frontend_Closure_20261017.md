# ACP 前端任务面收口实施记录（§42 批次 R0—R6 首轮）

日期：2026-10-17。范围：`docs/ACP_Architecture_Adjustment_Plan_20261016.md` §42 定义的 R0—R6 批次在默认页面的实施、验证与清理；基于 `HEAD 779a29a` + 未提交拆分工作树（该未提交状态即基线，全程未回退）。三项最终状态分列见 §5。

## 1. 落地批次与模块

| 批次 | 交付物 | 状态 |
|---|---|---|
| R0 | `.omo/evidence/frontend-arch-20261016/r0-state-map-20261017.md`（请求/状态写者/timer/SSE/viewer/调用者/兼容行为全登记）+ 符号接管表 §42 批次行 | 完成，逐批回填 |
| R1 | `client/job-detail-query.js`（ACPClient.jobDetail：/detail→/jobs/{id} 404/405 结构化判定回退 + onLegacyPin 单写点 + store 去重）；`controllers/task_session_controller.js`（taskSession：单调会话令牌、逐 await 校验、20 步序列保真）；`selectJob` 委托、`fetchJobDetail` 网络路径单源化、`jobDetailCache` 仅作投影 | module-verified + page-wired |
| R1b | `controllers/job_detail_controller.js` 单源（recovery 矩阵/execution identity/stale/prune，live 语义胜出、prune 首匹配修复）；job-detail.js 五函数同名薄委托 | module-verified + page-wired |
| R2 | `client/polling-strategy.js`（四通道 4/15/60s、2.5/30s、5s、30s + 世代守卫 + 互斥 + window._* 兼容镜像）；`client/event-stream.js`（连接世代、init({baseUrl}) 注入、`?after_seq` 替代 `?history=200` 与 legacy 逐字一致、after A→B→A 旧重连不可复活）；compat/api.js startPolling 转发、openSSE 委托；删除 summaryPoll 残体与静默无轮询陷阱 | module-verified + page-wired |
| R3a | `client/job-commands.js`（10 命令：pause/unpause/continue/rerun(去重)/cancel/purge/batch/rename(expected_name_revision 透传, null 保留)/move/clone；超时不重试；409/422 status/body 透传；成功后才失效；init 唯一 owner=queue.js；api()/apiV2() 非 GET chokepoint 保持 task-view 失效）；queue.js/molecule-view.js 薄委托，页面 UX 流程原位 | module-verified + page-wired |
| R3b | `client/wizard-endpoints.js`（7 处裸 fetch 全迁：uploads/parse/structure-source 预览/Bruker/hessian-preview/config-preview/validate-method；逐站冻结 URL/body/无超时例外 timeoutMs:0/错误文本推导；不新增失效语义；HTML 标签+装载序 pin 已接） | module-verified + page-wired |
| R4a | `controllers/candidate_review_controller.js` 成为候选审查唯一 owner（toggle/lock/confirm/dirty/revision/snapshot 同步/upsert/remove/move/setFrameRole；getState 深拷贝）；energy.js ~20 站点迁移、s2scanState 九个审查字段删除（revision 一并归 controller，切任务不再泄漏 expected_revision）；`files-logs.js` 会话纪律（`ACPWorkbench.contracts.filesSessionUnchanged` 纯判定 + fetchAndExpand/refreshLoadedFolders/expandPath/IRC tail 迟到丢弃 + logPollToken 会话化） | module-verified + page-wired |
| R5（模块） | `visualization/scene_instance.js`（ACPVisualization.scene：23 键冻结白名单、原生句柄封闭、动画/隐藏暂停/销毁无旧回调、多实例独立）；mol3d_adapter 重组为单一受限实例 API（并发的 createInstance/getRawViewer 逃逸口已移除、viz_framing 键集 pin 还原） | module-verified（**未接入页面**） |
| R6（首轮） | openSSE 旧 EventSource 实现体删除（types 字面量保留并作为 opts.types 注入模块，pin 不变；"reconnecting" 日志行回归页面呈现）；废弃 job_session_controller 及其测试/标签/pin、compat/api.js 重复 jobCommands.init、summaryPoll 残体清除；全部 init 单 owner | 完成（见 §4 余项） |

## 2. 验证

| 验证 | 结果 | 说明 |
|---|---|---|
| Node 模块行为 | **183 passed, 0 failed** | 75 基线 + 本轮新增 108（transport/store/renderer 等原 75 全保持） |
| 前端 pytest 四文件 | **508 passed, 0 failed** | integration/sync/structure_picker/candidate_workspace；两条定位 pin 迁移均以同文件等效或更强守卫替换（R1：api("/ 计数 56→54 + 客户端模块逐字 pin；R4a：payload 改 controller 快照 + 禁 s2scanState 引用） |
| 扩展回归 | **303 passed, 0 failed** | test_acp_energy_graph / test_acp_api_structure_viewer / test_acp_job_edit / test_scan_workflow |
| 语法 | PASS | 页面引用的全部非 vendor 脚本逐文件 node --check |
| 装载序 | PASS | 新增 (wizard-endpoints→wizard) 等 pin 全部加性更新，无删减弱化 |

复验入口：`node frontend/js/module_tests/run_all.js`；`PYTHONPATH=src python3 -m pytest tests/test_frontend_integration.py tests/test_frontend_sync.py tests/test_frontend_structure_picker.py tests/test_frontend_candidate_workspace.py -q`。

## 3. 边界与未验证项

- **真实浏览器/S0 场景：NOT_VERIFIED。** 本环境 Playwright 不可用；§41 的 8 场景未复跑，GAP-9 浏览器用例（tests/test_frontend_interaction.py）未执行——源级等价语义保留（会话令牌/世代守卫/prune 首匹配均有 Node 测试与 pin）。浏览器回归（列表→详情→文件/日志→结果、筛选/分页、A→B→A、编辑重算不新建、候选冲突、3D 缺库、多实例导出、中英、断网恢复）待具备浏览器环境后执行。
- **真实 ORCA/xTB/CREST、LSF/SSH/SFTP、取消/断线/恢复：NOT_VERIFIED**（本轮未涉及科学计算与远端实现，仅前端静态层）。
- **生产发布/静态缓存/旧标签页窗口：NOT_VERIFIED。**
- **R5 仅模块验证**：scene_instance 未接入任何页面；6 个业务 viewer 句柄（molecule-view/energy/wizard/reaction-builder/nmr/scan-s2）仍持原生 viewer——逐场景接入为后续片。
- **登记的兼容余面**（有调用者与保留理由）：reaction-builder 保留机理只读职责 + loadNodes/loadBackends 页面函数（slow-poll 注入回调消费）；energy 候选卡/检查器标记仍读全局任务态（renderer 注入投影已单源）；wizard 表单内部四片拆分（上传解析/预览校验/草稿表单/提交编辑重算）未做（端点层已迁）。
- **提交建议**：整个前端拆分（含本轮）仍为未提交工作树。建议按 §40.3 分片提交（纯搬运与行为激活分开），提交前以本记录 §2 复验。

## 4. R6 余项（下轮）

wizard 表单四片拆分；reaction-builder 机理外通用职责最终归位（loadNodes/loadBackends → Client sys 端点）；scene_instance 逐场景接入（主视图/能量、向导、NMR、振动）；真实浏览器+S0 联调与发布包核对。

## 5. 三项最终状态

1. **任务前端兼容重构（本轮范围）**：R0—R4 + R5 模块 + R6 首轮清理完成——网络/共享缓存归 Client（transport/store/taskView/jobDetail/jobCommands/wizardEndpoints/events/polling），用户会话/动作归 Workbench（taskSession/taskList/jobDetail/candidateReview controllers），图形资源归 Visualization（framing/mol3d/scene_instance，句柄封闭）。无未登记双写/双刷新（唯一 init owner、单取数路径、单实例 API 均已核）。
2. **真实远端/生产验收状态**：NOT_VERIFIED（见 §3）。
3. **后续服务能力接入状态**：prepare/availability、持久事件 cursor、科学产品升级、账户/配额、Services/CCCP 迁移按第 37—40/42.6 节另行推进，非本轮阻塞项。
