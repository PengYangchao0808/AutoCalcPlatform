# ACP 架构整改验收 — 支持矩阵（种子）

**Candidate revision:** `878e3557b3b593a0c42adf842e80fe1408db395e`
**Seeded:** 2026-10-20 (task 1, baseline freeze)
**Status of every cell at baseline:** `OPEN` or `NOT_VERIFIED` — **no cell may read `PASS` at baseline.**

## 行来源（row source）

一个活动工作流一行。活动集合取自代码，不臆造：

- `src/acp_services/runtime/workflow_enablement.py::ACTIVE_WORKFLOWS`（15 项，canonical catalog order）
- 每行的 closure / remote 事实取自 `src/acp_services/runtime/execution_chain.py::FROZEN_WORKFLOW_CAPABILITY_TABLE`
  （`closure=integrated|legacy`、`remote_supported=True|False`）

这些只是**代码事实**（capability table 的自述），不是验收证据；它们决定格子预期，不改变格子状态。

## 词汇（vocabulary）

| token | 含义 |
|---|---|
| `OPEN` | 整改尚未交付该格的证据；默认链/科学/恢复证据缺失 |
| `NOT_VERIFIED` | 本机/本环境无法执行（缺真实 QC/LSF/浏览器/冻结 bundle），带 skip 原因 |
| `PASS` | 仅在该格实际命令 + 原始回执落盘后由后续任务写入（**基线禁止**） |
| `FAIL` | 已执行且失败，不得改写为 `NOT_VERIFIED` |

## 列定义（column definitions）

| 列 | 关闭条件（证据必须实际存在） |
|---|---|
| scientific closure | 该工作流的 per-workflow 科学闭包（C01/HN-01 记录）在**候选 revision** 上被实际执行验证，科学容差与黄金值不变 |
| default submit freeze | 普通提交产生冻结 envelope/plan/binding（D07），断言真实模块路径/摘要，非缓存 hash |
| local dispatch+receipt | 本地派发 → 单次执行 → receipt 身份（attempt/release/digest）校验 → CAS 终态，含失败/重投递反例 |
| remote dispatch+receipt | 真实 SSH/SFTP/LSF：bsub 提交、bjobs 观测、停止确认、断线恢复、终态预取、旧 attempt 拒绝 |
| publication recovery | 发布只重试发布；进程在 terminal CAS 后、publisher 前被杀 → 重启续发布且无第二次 QC 运行 |
| real science | 真实 QC 程序从结构 → 提交 → 规范产品 → 可视化 → 证据报告；FakeBackend 不算 |

## 矩阵（15 行）

| # | workflow | closure | remote_supported | scientific closure | default submit freeze | local dispatch+receipt | remote dispatch+receipt | publication recovery | real science |
|---|---|---|---|---|---|---|---|---|---|
| 1 | singlepoint | integrated | yes | OPEN | OPEN | OPEN | OPEN | OPEN | NOT_VERIFIED |
| 2 | optimize | integrated | yes | OPEN | OPEN | OPEN | OPEN | OPEN | NOT_VERIFIED |
| 3 | frequency | integrated | yes | OPEN | OPEN | OPEN | OPEN | OPEN | NOT_VERIFIED |
| 4 | scan | integrated | yes | OPEN | OPEN | OPEN | OPEN | OPEN | NOT_VERIFIED |
| 5 | irc | integrated | yes | OPEN | OPEN | OPEN | OPEN | OPEN | NOT_VERIFIED |
| 6 | casscf | integrated | no | OPEN | OPEN | OPEN | NOT_APPLICABLE | OPEN | NOT_VERIFIED |
| 7 | xtb_optimize | integrated | yes | OPEN | OPEN | OPEN | OPEN | OPEN | NOT_VERIFIED |
| 8 | OrcaGradient | integrated | no | OPEN | OPEN | OPEN | NOT_APPLICABLE | OPEN | NOT_VERIFIED |
| 9 | tsmode | legacy | yes | OPEN | OPEN | OPEN | OPEN | OPEN | NOT_VERIFIED |
| 10 | nmr | legacy | yes | OPEN | OPEN | OPEN | OPEN | OPEN | NOT_VERIFIED |
| 11 | Confsearch | legacy | yes | OPEN | OPEN | OPEN | OPEN | OPEN | NOT_VERIFIED |
| 12 | PESsearch | legacy | yes | OPEN | OPEN | OPEN | OPEN | OPEN | NOT_VERIFIED |
| 13 | BatchOptimize | legacy | yes | OPEN | OPEN | OPEN | OPEN | OPEN | NOT_VERIFIED |
| 14 | XtbPathSearch | legacy | no | OPEN | OPEN | OPEN | NOT_APPLICABLE | OPEN | NOT_VERIFIED |
| 15 | XtbConstrainedOpt | legacy | no | OPEN | OPEN | OPEN | NOT_APPLICABLE | OPEN | NOT_VERIFIED |

### 格子状态说明（为什么不是 PASS）

- **全部 6 列 × 15 行 = 90 格，基线无任何 `PASS`。**
- `NOT_VERIFIED` 只用于**环境缺失**的真实科学/真实远端：本机 `command -v` 显示 `crest/xtb/censo/isostat/shermo/bsub/bjobs/bstop` **ABSENT**（见 `evidence/qc-discovery.log`），因此 `real science` 与需要 LSF 客户端的格子在本机不可执行。`acp doctor` 通过 SSH 探测到远端 node-157 上的软件版本（`evidence/qc-doctor.log`）是**远端节点事实**，不是本机可用性，也不是任何一格的 PASS。
- `remote dispatch+receipt` 对 `remote_supported=no` 的 4 行（casscf / OrcaGradient / XtbPathSearch / XtbConstrainedOpt）记 `NOT_APPLICABLE`（scope token），不记 PASS/FAIL。
- `closure` 列的 `integrated`/`legacy` 是 capability table 自述，**不是** scientific closure 列的证据；后者保持 `OPEN` 直到候选 revision 上的实跑证据落盘。

## 谁可以改写格子

| 改写为 | 需要 |
|---|---|
| `OPEN` → `PASS` | 该格的原始命令 + 退出码 + 日志落在 `reports/architecture_remediation_20261020/evidence/`，并由任务 41/42/43 更新 manifest |
| `OPEN` → `NOT_VERIFIED` | 具体缺失的环境前置（二进制/凭据/冻结 bundle），逐格单独说明，不接受笼统 skip |
| `NOT_VERIFIED` → `PASS` | 环境补齐后**实际执行**；不得因环境出现就直接改写 |
| 任意 → `FAIL` | 已执行且失败；`FAIL` 不得再改写为 `NOT_VERIFIED` |

相关门：`G3 默认链路`（default submit freeze / dispatch+receipt / publication recovery）、
`G5 功能验收`（real science + 浏览器）、`G4 独立交付`（release/markers），
见 `docs/reference/next-change/ACP_Architecture_Acceptance_Remediation_Plan_20261020.md` §7.2。
