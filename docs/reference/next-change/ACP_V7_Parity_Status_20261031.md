# ACP 3.0 V7 Parity Status (2026-10-31)

> **2026-11-04 双人开发同步**：本表仍是原 revision 的 parity 证据快照。本次[架构 §10](ACP_3.0_Architecture_20261031.md)新增的 Chemical IR/两级编译/严格物种归集/证据闭环不在旧 PASS 范围，须独立按 C0—C5 验收；本次不重计旧测试或提升 parity/live 状态。

> Read-only status for the §2.4 / §7 parity gate. **Not a claim that the classic page may be retired.**
> Authority: `docs/reference/next-change/ACP_3.0_Frontend_Plan_20261031.md` §2.4 / §7.
> Evidence: module registration + four-state code + Node vm tests in `frontend/js/module_tests/`; full suite **1103 passed / 0 failed** at the time of this snapshot.

## 1. 24-face matrix

| # | 功能面 | 新 UI 证据 | 状态 |
|---|---|---|---|
| P01 | 任务创建向导（15 工作流/方法/电荷自旋/资源/校验/预检/提交） | `task-wizard` A-01…A-08；`acp2_task_wizard.test.js` 66 | ✅ 代码/契约 |
| P02 | 草稿（自动保存/恢复/409/提交后删除） | `task-wizard` A-09；同 66 项 | ✅ 代码/契约 |
| P03 | 修改参数后重算（preview/coverage/attempt+1） | `job-edit` A-10…A-12；29 项 | ✅ 代码/契约 |
| P04 | 结构来源（多来源/多帧/远程） | `task-config` A-13…A-15；23 项 | ✅ 代码/契约 |
| P05 | TS Mode 编辑器 | `tsmode-editor` A-16；21 项 | ✅ 代码/契约 |
| P06 | 队列运维（暂停/继续/重跑/清除/克隆/移动/重命名/标签） | `task-explorer` A-17…A-23；58 项 | ✅ 代码/契约 |
| P07 | 项目与任务组织（CRUD/批量清除/回收站） | `task-explorer` A-22…A-24；58 项 | ✅ 代码/契约（回收站仅 archived；purge 不可恢复） |
| P08 | 任务详情（阶段/日志/文件/工件） | `exec-detail` A-25…A-28；27 项 | ✅ 代码/契约 |
| P09 | 文件与日志浏览 | `exec-detail` A-26…A-28；27 项 | ✅ 代码/契约 |
| P10 | 系统与资源（节点/软件/后端） | `node-status` A-30/A-31；15 项 | ✅ 代码/契约 |
| P11 | 结构查看器（结构/能量/路径、叠加、IRC、样式、相机、窄屏） | `molecule-viewer` A-32…A-37；51 项 | ✅ 代码/契约 |
| P12 | 能量/轨迹与候选评审 | `energy-profile` A-38…A-41；24 项 | ✅ 代码/契约 |
| P13 | 振动检查器（频率/箭头/动画） | `molecule-viewer` A-42…A-44；51 项 | ✅ 代码/契约 |
| P14 | 结构编辑（测量/内坐标/事务/资产） | `molecule-viewer` A-45…A-47；51 项 | ✅ 代码/契约 |
| P15 | 候选详情 | `energy-profile` A-39/A-41；24 项 | ✅ 代码/契约 |
| P16 | S2 键长扫描 | `s2-scan` A-48；20 项 | ✅ 代码/契约 |
| P17 | 机理构建（反应坐标/路径/PES 衔接） | `mechanism-plan` A-49（29 项，**plan 确认按 ADR-009 退役**）、`path-view` A-50（25）、`pes-handoff` A-51（37） | ⚠️ **ADR-retired（仅确认动作）**：读/展示/路径选择/PES handoff 可用；后端 plan 端点 410，ADR-009 明确不伪造 |
| P18 | NMR 报告（DP4/DP5） | `nmr-report` A-52/A-53；30 项 | ✅ 代码/契约 |
| P19 | 手工研究提问 | `research-record` A-54；31 项 | ✅ 代码/契约（提交需 shell 注入 `researchBinding`） |
| P20 | 科学证据 claim states | `research-record` A-55；31 项 | ✅ 代码/契约 |
| P21 | 平台证据投影 | `research-record` A-56；31 项 | ✅ 代码/契约 |
| P22 | 科研审批（预算/plan_digest） | `approval-card` A-57/A-58；16 项 | ✅ 代码/契约 |
| P23 | 能力表单（schema 驱动） | `capability-form` A-59/A-60；20 项 | ✅ 代码/契约 |
| P24 | 语言/主题/布局/窄屏/键盘可达 | `i18n` A-61（1175 keys）、`theme-narrow` A-62（6）、`shell-a11y` A-63（10） | ✅ 代码/契约（浏览器未跑） |

**Current parity: 24/24 by code/contract + ADR = 23 implemented + 1 ADR-retired (P17 plan confirmation). No face is marked retired without ADR-009.**

## 2. V7 gaps that still block the parity gate

1. **A-49 backend (ADR-009)**: `POST /api/v1/mechanism-studies/{id}/mechanism/plan` returns 410 and has no implementation. ADR-009 retires the **plan-confirm action** only; P17 remains read-only. Restoring the endpoint requires revoking ADR-009 and a real plan_confirmed acceptance run.
2. **Default assembly (superseded 2026-11)**: the former V7 generic tool drawer and its 12-module catalog were removed from the UI. Current ownership: E hosts `config-workspace` (task wizard plus dedicated `tsmode` / `scan` editors and selected-task `job-edit`); F hosts applicable `energy-profile` / `path-view` / `pes-handoff` / `nmr-report` / `research-record` tabs and a history-only mechanism tab; A owns `capability-form`; the topbar owns `approval-card` and `node-status`. Module ids, files, `ACPNextModules` registrations and focused module tests are retained. `acp2_tool_drawer*.test.js` now guard removal of the drawer rather than its presence. This repository evidence does not claim a live-browser verification of the new ownership routing.
3. **Browser suite**: `acp2_next_entry.browser.js` / `acp2_layout_editor.browser.js` were **not executed** (Playwright is not installed on this host). A **real headless Edge + CDP** run on 2026-10-31 covers:
   - 1280×900 static boot of `/next/` (shell/tabs/panels render);
   - skip-link focus (`#acp-ws-research`), live region (`研究`), shell-tab ArrowRight selection/workspace switch;
   - layout dialog `role=dialog`/`aria-modal=true`, focus inside, Escape closes (`open=false`, `display=none`);
   - historical 2026-10-31 run: old `#acp-tools-btn` opened a 12-tab drawer; this describes a retired build, not current UI;
   - **390×844 mobile emulation**: `innerWidth=390`, `scrollWidth=390`, `data-acp-ready=true`, single-column panels, full-width drawer, no horizontal overflow;
   - 0 uncaught exceptions; console errors are only expected 404s.
   Evidence: `reports/acp2_implementation/next_boot_20261031/`. The full Playwright interaction suite (forms, real drawers with backend data, authz) remains NOT_VERIFIED.
4. **Python next-entry test**: `tests/test_acp_api_next_entry.py` not run (Windows host lacks `fcntl` / fastapi runtime); static HTML pins were checked only.
5. **i18n**: A-61 now covers **16 modules / 1783 keys per language** (1717 module-owned keys verified in zh+en); the former five-module zh-fallback gap is closed. Browser EN copy was not visually walked.
6. **Research binding**: `research-record` needs `researchBinding` in the context or a per-module deps channel; `layout-manager.hostApi()` currently has neither, so proposal/record submit is refused unless the advanced JSON binding is used.
7. **Real science**: no live five-view energy comparison, no real asset save/delete/submit, no real PES/Batch/QC run.

## 3. What this document does and does not mean

- It records the **current machine-checkable evidence** per face and prevents a face from being called complete when only a placeholder exists.
- It does **not** authorize classic-page retirement, close G05/G10/G13/G15/G18/H3/H4/H7, or turn `NOT_VERIFIED` browser/real-service items into PASS.
- Next gate work: real browser backend/Playwright + real science closure. ADR-009 records the A-49 plan-confirm retirement; the frontend/assembly parity is 24/24 (23 implemented + 1 ADR).
