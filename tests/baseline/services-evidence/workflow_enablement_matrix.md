# Workflow enablement matrix (B06-5 / todo 34)

**Created:** 2026-10-19，基线 HEAD `feat/frontend-plan1-closure`（todo 32 之后）。
**Owner of this file:** B 路（todo 34）。**Authority:** `src/acp_services/runtime/workflow_enablement.py`
+ `src/acp_services/runtime/execution_chain.py::FROZEN_WORKFLOW_CAPABILITY_TABLE`
（唯一能力表，todo 32）。本文件是**可读快照 + 启用证据**，不是第二权威；能力表/策略以代码为准。

## 语义

- 每个 active 工作流的执行入口由 C01（HN-01）逐工作流闭包状态决定：closure `integrated`
  → envelope 入口；closure `pending`（C01 未交付，表内 `legacy`）→ 保持旧兼容路径。
- **未满足切片保持旧路径，且不声称独立**：仅「执行入口」为旧，状态机/身份/回执同一套
  （`execution_chain` 单一调度器 + 按 attempt 冻结）。legacy 行**永不**标 `enabled`。
- 强制 envelope 对未就绪工作流 = 显式 typed 拒绝
  （`workflow_enablement.require_envelope_entry` → `WorkflowNotEnabledError`），不做静默
  legacy 回退，避免调用方把回退误记为独立。

## 启用矩阵 — 15 active（`test_architecture_invariants.CURRENT_ACTIVE_IDS`）

> closure 状态口径：`integrated` = C01 HN-01 已交付**且** B 侧 todo 32 已应用（`handoff_registry.md`
> HN-01 行 `integrated`，`ffcf1b8` → 应用 `f7a3321`）；`pending` = 闭包未交付，走 legacy。
> 入口列 = envelope 就绪时的策略入口（实际 attempt 未冻结 envelope/plan 时运行期仍走 legacy，
> 见 `execution_chain_switch.md`「未就绪语义」）。

| # | 工作流 | C01 closure | 本地执行入口 | 远端执行入口 | 本地专用 | 已启用 | 证据 |
|---|---|---|---|---|---|---|---|
| 1 | singlepoint | integrated | envelope | envelope | 否 | ✅ | HN-01 §12.1 |
| 2 | optimize | integrated | envelope | envelope | 否 | ✅ | HN-01 §12.2 |
| 3 | frequency | integrated | envelope | envelope | 否 | ✅ | HN-01 §12.3 |
| 4 | scan | integrated | envelope | envelope | 否 | ✅ | HN-01 §12.4 |
| 5 | irc | integrated | envelope | envelope | 否 | ✅ | HN-01 §12.5 |
| 6 | tsmode | pending | legacy | legacy | 否 | ❌ legacy | HN-01 §13.4 |
| 7 | casscf | integrated | envelope | legacy | 否 | ✅（本地） | HN-01 §12.6 |
| 8 | xtb_optimize | integrated | envelope | envelope | 否 | ✅ | HN-01 §12.7 |
| 9 | nmr | pending | legacy | legacy | 否 | ❌ legacy | HN-01 §13.5 |
| 10 | Confsearch | pending | legacy | legacy | 否 | ❌ legacy | HN-01 §13.1 |
| 11 | PESsearch | pending | legacy | legacy | 否 | ❌ legacy | HN-01 §13.2 |
| 12 | BatchOptimize | pending | legacy | legacy | 否 | ❌ legacy | HN-01 §13.6 |
| 13 | XtbPathSearch | pending | legacy | legacy | 是 | ❌ legacy | HN-01 §13.3 |
| 14 | OrcaGradient | integrated | envelope | legacy | 是 | ✅（本地） | HN-01 §12.8 |
| 15 | XtbConstrainedOpt | pending | legacy | legacy | 是 | ❌ legacy | HN-01 §13.7 |

### 本地专用限制（frozen `pes2ts_*_request_v1` payload，不在远程允许集）

三项 active 工作流同时是本地专用：`XtbPathSearch` / `XtbConstrainedOpt`（closure pending，legacy）
与 `OrcaGradient`（closure integrated，本地 envelope / 远端 legacy）。

- **OrcaGradient 双属集显式对齐**：它既是 15 active 之一、又是 3 本地专用之一，且属于 C01
  已集成的 8 项——因此**计在 enabled 8 内**，绝不重复计入 legacy 7。其远端入口因本地专用仍为
  legacy（`require_envelope_entry("OrcaGradient", remote=True)` 显式拒绝，reason
  `local-only-workflow-on-remote`）。
- `XtbPathSearch` / `XtbConstrainedOpt` 的科学发布半段仍属 C01（HN-01 §13），未闭包前保持 legacy。

## T34a — 选定工作流启用证据（首批范围）

**范围**：C01 已集成的 8 项 `singlepoint / optimize / frequency / scan / irc / casscf /
xtb_optimize / OrcaGradient`（最小验证面：singlepoint / optimize / frequency）。

| 检查 | 结果 |
|---|---|
| `select_entry(<integrated>) == envelope`（本地） | PASS |
| `require_envelope_entry(<integrated>) == envelope`（本地） | PASS |
| 本地 runner：closure integrated + 冻结 envelope/plan → `python -m acp_services.worker_bootstrap` 入口（无旧 CLI 字符串） | PASS |
| 本地 runner：closure pending（如 Confsearch）即便有冻结 envelope/plan → 仍 legacy 入口 | PASS |

**定向测试**：`tests/test_services_workflow_enablement.py`（T34a 段）PASS。

## T34b — 15 项最终覆盖（全量门）

> **IS-6 现行口径（todo 82 复核，2026-10-19）：`8/15 envelope + 7 legacy, T34b gate OPEN`。**
> 这只表示“首片 8 项经 envelope、7 项保持旧路径”，**不表示 15/15 已启用**。7 项 legacy 的闭包
> 由 C01 T20—T30 承担；本文件与闭包记录均不得写成“全量覆盖已完成”。

**检查**：`workflow_enablement.coverage_report()` —— 机器可校验的矩阵完整性/诚实性门。

| 指标 | 值 |
|---|---|
| active 覆盖 | 15/15 |
| 矩阵完整性（integrity） | PASS（全部 active 在场；入口与 closure 状态一致；无 legacy 行标 enabled） |
| enabled | **8/15** |
| legacy（保持旧路径、不声称独立） | **7/15** |
| **full-coverage gate** | **OPEN** |

- `coverage_report().summary` 原文：`enabled: 8/15; legacy: 7/15; full-coverage gate: OPEN`。
- **7 项 legacy（Confsearch / PESsearch / BatchOptimize / XtbPathSearch / XtbConstrainedOpt /
  tsmode / nmr）不计完成**，其闭包由 C01 T20–T30 承担（HN-01 §13）。
- **全量覆盖门保持 OPEN**，交完整发行（todo 41b / 76）关闭；本任务不以首片代表全覆盖。

## 验收命令

- `python3.11 -m pytest tests/test_services_workflow_enablement.py -q`（T34a + T34b + 拒绝负例）。
- 回归：`tests/test_services_chain_switch.py`、`tests/test_remote_phase1.py`、
  `tests/test_remote_phase2.py`、`tests/test_execution_integrity_e2e.py`、
  `tests/test_acp_services_isolation.py`、`tests/test_architecture_invariants.py`。
