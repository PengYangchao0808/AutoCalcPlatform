# Execution-chain switch registry (B06-3 / todo 32)

**Created:** 2026-10-19，基线 HEAD `260d032`（branch `feat/frontend-plan1-closure`）。
**Owner of this file:** B 路（todo 32）。**Authority:** `src/acp_services/runtime/execution_chain.py`
`FROZEN_WORKFLOW_CAPABILITY_TABLE` 是唯一能力表；本文件是它的可读快照 + 启用证据，不是第二权威。

## 单一调度器与按 attempt 冻结

- 入口选择只经 `execution_chain.select_execution_entry`；本地 `acp/scheduler/runner.py:_build_execution_cmd`
  与远端 `acp/scheduler/remote/runner.py:_resolve_remote_choice` 都调用它。
- 选择写入 attempt 级记录 `WORK/00_RUNTIME/execution_protocol.json`（`AttemptExecutionChoice`）：
  同一 attempt 恢复时不重选（复用记录）；新 attempt 重选。
- envelope 入口仅在一 workflow closure 为 `integrated` **且**该 attempt 存在冻结 envelope/plan 对时启用；
  否则记录 legacy 入口。整个约束保证「同一工作流闭包不半切」且同一 attempt 恰一个执行入口。

## 15 active 工作流切换矩阵（closure 来自 C01 HN-01 `ffcf1b8`）

| 工作流 | closure | 远端支持 | 入口（envelope 就绪时） | 未就绪/未闭包 |
|---|---|---|---|---|
| singlepoint | integrated | 是 | envelope | legacy |
| optimize | integrated | 是 | envelope | legacy |
| frequency | integrated | 是 | envelope | legacy |
| scan | integrated | 是 | envelope | legacy |
| irc | integrated | 是 | envelope | legacy |
| casscf | integrated | 否（本地专用） | envelope（仅本地） | legacy |
| xtb_optimize | integrated | 是 | envelope | legacy |
| OrcaGradient | integrated | 否（本地专用） | envelope（仅本地） | legacy |
| Confsearch | legacy | 是 | — | legacy |
| PESsearch | legacy | 是 | — | legacy |
| BatchOptimize | legacy | 是 | — | legacy |
| XtbPathSearch | legacy | 否 | — | legacy |
| XtbConstrainedOpt | legacy | 否 | — | legacy |
| tsmode | legacy | 是 | — | legacy |
| nmr | legacy | 是 | — | legacy |

- **启用范围（ENABLED_WORKFLOWS）**：`singlepoint / optimize / frequency / scan / irc / casscf /
  xtb_optimize / OrcaGradient`（C01 HN-01 已集成，见 `handoff_registry.md`）。
- **legacy 范围**：其余 7 项保持旧路径，**不声称独立**（其闭包由 C01 T20–T30 承担）。
- **未就绪语义**：即便 closure=integrated，若该 attempt 无冻结 envelope/plan，则记录 legacy；
  per-workflow 真实启用（控制器冻结计划）归 todo 34。

## 命令入口

- 本地 envelope 入口：`python -m acp_services.worker_bootstrap --envelope … --plan … --work-dir …`
  （`execution_chain.worker_argv` 单点构造）。
- 远端 envelope 入口：`build_remote_worker_command` 委托同一 `worker_argv`；LSF 脚本仍
  `export PYTHONPATH=<release>/src` + `cd <remote_job_dir>`（保留同步代码库语义）。
- legacy 入口：`python -m acp.cli run <workflow> …`（未闭包/未就绪工作流，保留）。
- Services 侧禁 `acp.cli` 字符串/subprocess（AST+字符串门 `tests/test_services_chain_switch.py`）。

## 验收

- `tests/test_services_chain_switch.py`（22 tests）：能力表、并行选择、按 attempt 冻结、
  唯一调度、本地/远端入口切换、worker 入口独立性、AST+字符串门、旧回执拒绝。
