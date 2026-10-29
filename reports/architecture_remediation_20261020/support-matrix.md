# ACP 架构整改验收 — 支持矩阵（D07 分格证据）

**Candidate revision:** `d5d85e7c804878edd0cedb6bce1d32af2d92ad83`（基线冻结）+ T24–T32 交付
**Published:** 2026-10-20（task 31）
**矩阵性质:** 一行一个 active 工作流；每个**已执行**格子必须带命令/工件路径；没有执行证据的格子只能是 `OPEN` 或 `NOT_VERIFIED`，不得写 `PASS`。

> 本文件只发布**分格证据**。它不把 `ENABLED_WORKFLOWS`（科学闭包 8 项）写成默认启用计数；
> 默认冻结集合是独立的 `DEFAULT_ENVELOPE_WORKFLOWS`；2026-10-29 批次按证据优先加入 `xtb_optimize`（`singlepoint` 之后第二个），其余工作流未启用。
> 15/15 验收门保持 **OPEN**，直到所有格子都有候选 revision 上的执行证据。

## 行来源（row source）

- `src/acp_services/runtime/workflow_enablement.py::ACTIVE_WORKFLOWS`（15 项，canonical catalog order）
- `src/acp_services/runtime/execution_chain.py::FROZEN_WORKFLOW_CAPABILITY_TABLE`
  （`closure=integrated|legacy`、`remote_supported=True|False`）
- 默认冻结集合：`src/acp_services/runtime/execution_chain.py::DEFAULT_ENVELOPE_WORKFLOWS`
  （当前 `frozenset({"singlepoint"})`，与科学闭包集合**分开**）

## 词汇（vocabulary）

| token | 含义 |
|---|---|
| `PASS` | 该格在候选交付上实际执行，命令 + 原始回执/测试路径存在 |
| `OPEN` | 尚未交付/未执行该格证据；不得外推为已交付 |
| `NOT_VERIFIED` | 环境缺失（真实 QC/LSF/浏览器/冻结 bundle），带具体原因 |
| `FAIL` | 已执行且失败；不得改写为 `NOT_VERIFIED` |
| `N/A` | 该格对该工作流不适用（例如 local-only 工作流的远端格） |

## 矩阵（15 rows）

| # | workflow | current path | default_envelope | scientific closure | default submit freeze | local dispatch+receipt | remote dispatch+receipt | publication recovery | real science |
|---|---|---|---|---|---|---|---|---|---|
| 1 | singlepoint | envelope | yes | OPEN — no candidate-revision C01 rerun; `tests/baseline/services-evidence/workflow_enablement_matrix.md` | PASS — `tests/test_services_chain_switch.py::test_t29_ordinary_singlepoint_submit_uses_the_frozen_chain` | PASS — `tests/test_services_chain_switch.py::test_t27_opt_in_singlepoint_runs_worker_advances_once_and_restarts_noop` | NOT_VERIFIED — controlled fake transport only; `.omo/evidence/acp-architecture-acceptance-remediation/task-30-acp-architecture-acceptance-remediation.md` | PASS — `tests/test_services_chain_switch.py::test_t28_publish_failure_keeps_scientific_record_then_retries` | NOT_VERIFIED — no real QC; `reports/architecture_remediation_20261020/evidence/qc-discovery.log` |
| 2 | optimize | legacy | no | OPEN — no candidate-revision C01 rerun; `tests/baseline/services-evidence/workflow_enablement_matrix.md` | OPEN — not default; `src/acp_services/runtime/execution_chain.py` | OPEN — builder registered, no controlled local receipt run; `.omo/evidence/acp-architecture-acceptance-remediation/task-29-acp-architecture-acceptance-remediation.md` | OPEN — no controlled remote evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-30-acp-architecture-acceptance-remediation.md` | OPEN — no per-workflow publication evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-28-acp-architecture-acceptance-remediation.md` | NOT_VERIFIED — no real QC; `reports/architecture_remediation_20261020/evidence/qc-discovery.log` |
| 3 | frequency | legacy | no | OPEN — no candidate-revision C01 rerun; `tests/baseline/services-evidence/workflow_enablement_matrix.md` | OPEN — not default; `src/acp_services/runtime/execution_chain.py` | OPEN — builder registered, no controlled local receipt run; `.omo/evidence/acp-architecture-acceptance-remediation/task-29-acp-architecture-acceptance-remediation.md` | OPEN — no controlled remote evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-30-acp-architecture-acceptance-remediation.md` | OPEN — no per-workflow publication evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-28-acp-architecture-acceptance-remediation.md` | NOT_VERIFIED — no real QC; `reports/architecture_remediation_20261020/evidence/qc-discovery.log` |
| 4 | scan | legacy | no | OPEN — no candidate-revision C01 rerun; `tests/baseline/services-evidence/workflow_enablement_matrix.md` | OPEN — not default; `src/acp_services/runtime/execution_chain.py` | OPEN — builder registered, no controlled local receipt run; `.omo/evidence/acp-architecture-acceptance-remediation/task-29-acp-architecture-acceptance-remediation.md` | OPEN — no controlled remote evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-30-acp-architecture-acceptance-remediation.md` | OPEN — no per-workflow publication evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-28-acp-architecture-acceptance-remediation.md` | NOT_VERIFIED — no real QC; `reports/architecture_remediation_20261020/evidence/qc-discovery.log` |
| 5 | irc | legacy | no | OPEN — no candidate-revision C01 rerun; `tests/baseline/services-evidence/workflow_enablement_matrix.md` | OPEN — not default; `src/acp_services/runtime/execution_chain.py` | OPEN — builder registered, no controlled local receipt run; `.omo/evidence/acp-architecture-acceptance-remediation/task-29-acp-architecture-acceptance-remediation.md` | OPEN — no controlled remote evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-30-acp-architecture-acceptance-remediation.md` | OPEN — no per-workflow publication evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-28-acp-architecture-acceptance-remediation.md` | NOT_VERIFIED — no real QC; `reports/architecture_remediation_20261020/evidence/qc-discovery.log` |
| 6 | tsmode | legacy | no | OPEN — C01 closure pending; `tests/baseline/services-evidence/workflow_enablement_matrix.md` | OPEN — not default; `src/acp_services/runtime/execution_chain.py` | OPEN — no controlled envelope receipt run; `.omo/evidence/acp-architecture-acceptance-remediation/task-31-acp-architecture-acceptance-remediation.md` | OPEN — no controlled remote evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-30-acp-architecture-acceptance-remediation.md` | OPEN — no publication evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-28-acp-architecture-acceptance-remediation.md` | NOT_VERIFIED — no real QC; `reports/architecture_remediation_20261020/evidence/qc-discovery.log` |
| 7 | casscf | legacy | no | OPEN — no candidate-revision C01 rerun; `tests/baseline/services-evidence/workflow_enablement_matrix.md` | OPEN — not default; `src/acp_services/runtime/execution_chain.py` | OPEN — builder registered, no controlled local receipt run; `.omo/evidence/acp-architecture-acceptance-remediation/task-29-acp-architecture-acceptance-remediation.md` | N/A | OPEN — no per-workflow publication evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-28-acp-architecture-acceptance-remediation.md` | NOT_VERIFIED — no real QC; `reports/architecture_remediation_20261020/evidence/qc-discovery.log` |
| 8 | xtb_optimize | envelope | yes | OPEN — scientific closure not extended by envelope activation; `tests/baseline/services-evidence/workflow_enablement_matrix.md` | PASS — real pre-flip rehearsal (frozen attempt via registered builder, `entry=envelope`); `reports/acp2_dev_batch_20261029/phaseC2/c2-facts.json` | PASS — real xTB worker run (`execution_receipt.json` state=completed, worker entry event); `reports/acp2_dev_batch_20261029/phaseC2/c2-facts.json` | NOT_VERIFIED — no controlled remote evidence this batch; `.omo/evidence/acp-architecture-acceptance-remediation/task-30-acp-architecture-acceptance-remediation.md` | PASS — publication products (`RESULT/energy/optimize_energy.json`) + `publication_state`; `reports/acp2_dev_batch_20261029/phaseC2/c2-facts.json` | PASS — real xTB `normal termination of xtb`; `reports/acp2_dev_batch_20261029/phaseC2/c2-facts.json` |
| 9 | nmr | legacy | no | OPEN — C01 closure pending; `tests/baseline/services-evidence/workflow_enablement_matrix.md` | OPEN — not default; `src/acp_services/runtime/execution_chain.py` | OPEN — no controlled envelope receipt run; `.omo/evidence/acp-architecture-acceptance-remediation/task-31-acp-architecture-acceptance-remediation.md` | OPEN — no controlled remote evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-30-acp-architecture-acceptance-remediation.md` | OPEN — no publication evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-28-acp-architecture-acceptance-remediation.md` | NOT_VERIFIED — no real QC; `reports/architecture_remediation_20261020/evidence/qc-discovery.log` |
| 10 | Confsearch | legacy | no | OPEN — C01 closure pending; `tests/baseline/services-evidence/workflow_enablement_matrix.md` | OPEN — not default; `src/acp_services/runtime/execution_chain.py` | OPEN — no controlled envelope receipt run; `.omo/evidence/acp-architecture-acceptance-remediation/task-31-acp-architecture-acceptance-remediation.md` | OPEN — no controlled remote evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-30-acp-architecture-acceptance-remediation.md` | OPEN — no publication evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-28-acp-architecture-acceptance-remediation.md` | NOT_VERIFIED — no real QC; `reports/architecture_remediation_20261020/evidence/qc-discovery.log` |
| 11 | PESsearch | legacy | no | OPEN — C01 closure pending; `tests/baseline/services-evidence/workflow_enablement_matrix.md` | OPEN — not default; `src/acp_services/runtime/execution_chain.py` | OPEN — no controlled envelope receipt run; `.omo/evidence/acp-architecture-acceptance-remediation/task-31-acp-architecture-acceptance-remediation.md` | OPEN — no controlled remote evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-30-acp-architecture-acceptance-remediation.md` | OPEN — no publication evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-28-acp-architecture-acceptance-remediation.md` | NOT_VERIFIED — no real QC; `reports/architecture_remediation_20261020/evidence/qc-discovery.log` |
| 12 | BatchOptimize | legacy | no | OPEN — C01 closure pending; `tests/baseline/services-evidence/workflow_enablement_matrix.md` | OPEN — not default; `src/acp_services/runtime/execution_chain.py` | OPEN — no controlled envelope receipt run; `.omo/evidence/acp-architecture-acceptance-remediation/task-31-acp-architecture-acceptance-remediation.md` | OPEN — no controlled remote evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-30-acp-architecture-acceptance-remediation.md` | OPEN — no publication evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-28-acp-architecture-acceptance-remediation.md` | NOT_VERIFIED — no real QC; `reports/architecture_remediation_20261020/evidence/qc-discovery.log` |
| 13 | XtbPathSearch | legacy | no | OPEN — C01 closure pending; `tests/baseline/services-evidence/workflow_enablement_matrix.md` | OPEN — not default; `src/acp_services/runtime/execution_chain.py` | OPEN — no controlled envelope receipt run; `.omo/evidence/acp-architecture-acceptance-remediation/task-31-acp-architecture-acceptance-remediation.md` | N/A | OPEN — no publication evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-28-acp-architecture-acceptance-remediation.md` | NOT_VERIFIED — no real QC; `reports/architecture_remediation_20261020/evidence/qc-discovery.log` |
| 14 | OrcaGradient | legacy | no | OPEN — no candidate-revision C01 rerun; `tests/baseline/services-evidence/workflow_enablement_matrix.md` | OPEN — not default; `src/acp_services/runtime/execution_chain.py` | OPEN — builder registered, no controlled local receipt run; `.omo/evidence/acp-architecture-acceptance-remediation/task-29-acp-architecture-acceptance-remediation.md` | N/A | OPEN — no per-workflow publication evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-28-acp-architecture-acceptance-remediation.md` | NOT_VERIFIED — no real QC; `reports/architecture_remediation_20261020/evidence/qc-discovery.log` |
| 15 | XtbConstrainedOpt | legacy | no | OPEN — C01 closure pending; `tests/baseline/services-evidence/workflow_enablement_matrix.md` | OPEN — not default; `src/acp_services/runtime/execution_chain.py` | OPEN — no controlled envelope receipt run; `.omo/evidence/acp-architecture-acceptance-remediation/task-31-acp-architecture-acceptance-remediation.md` | N/A | OPEN — no publication evidence; `.omo/evidence/acp-architecture-acceptance-remediation/task-28-acp-architecture-acceptance-remediation.md` | NOT_VERIFIED — no real QC; `reports/architecture_remediation_20261020/evidence/qc-discovery.log` |

## 范围声明（scoped claims）

- **默认冻结范围**: `DEFAULT_ENVELOPE_WORKFLOWS == {"singlepoint"}`。只有 singlepoint 的普通提交在本
  revision 上走 freeze → worker → receipt → CAS → publish；其余 14 项保持各自 current path。
- **科学闭包**: `ENABLED_WORKFLOWS` 仍是 C01 的 8 项（integrated），它与默认冻结集合不是同一集合；
  本矩阵不把它写成“默认启用计数”，也不据此关闭 15/15 门。
- **legacy 保留**: 7 项 legacy 是兼容决定，不是缺陷，也不是完成证据；它们继续走注册路径。
- **15/15 gate**: **OPEN**。只有每格都有候选 revision 上的实际执行证据、且默认集合按工作流逐项
  证据扩张后，门才可关闭。
- **真实环境**: real science 与 real remote 未执行（`qc-discovery.log` 显示本机缺 QC/LSF 客户端）；
  对应格子为 `NOT_VERIFIED`，不是 PASS。

## 证据位置

- 默认冻结/本地链路: `tests/test_services_chain_switch.py`（T27/T28/T29 段）与
  `.omo/evidence/acp-architecture-acceptance-remediation/task-27..29-*.md`
- 远端受控传输: `.omo/evidence/acp-architecture-acceptance-remediation/task-30-*.md`
- 冻结/发布/恢复基础: `.omo/evidence/acp-architecture-acceptance-remediation/task-24..28-*.md`
- 同 attempt/归档: `.omo/evidence/acp-architecture-acceptance-remediation/task-32-*.md`
- 环境缺失: `reports/architecture_remediation_20261020/evidence/qc-discovery.log`

## 谁可以改写格子

| 改写 | 需要 |
|---|---|
| `OPEN` → `PASS` | 该格实际命令 + 退出码 + 原始回执落在 `reports/architecture_remediation_20261020/evidence/`；manifest 由 T41/42/43 更新 |
| `OPEN` → `NOT_VERIFIED` | 逐格写明缺失环境（二进制/凭据/冻结 bundle），不接受笼统 skip |
| `NOT_VERIFIED` → `PASS` | 环境补齐后实际执行 |
| 任意 → `FAIL` | 已执行且失败；`FAIL` 不得改回 `NOT_VERIFIED` |

相关门：`G3 默认链路`（default submit freeze / dispatch+receipt / publication recovery）、
`G5 功能验收`（real science + 浏览器）、`G4 独立交付`（release/markers），
见 `docs/reference/next-change/ACP_Architecture_Acceptance_Remediation_Plan_20261020.md` §7.2。
