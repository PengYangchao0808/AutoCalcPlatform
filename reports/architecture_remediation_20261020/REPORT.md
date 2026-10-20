# ACP 架构验收整改 — 修复候选报告（骨架）

**Status: DRAFT / NOT_ACCEPTED**

**Candidate revision:** `878e3557b3b593a0c42adf842e80fe1408db395e`（`git rev-parse HEAD`，task 1 冻结时实测）

> 本报告是 **DRAFT / NOT_ACCEPTED**：任何格子都还没有通过验收。
> 本文件存在的意义是给任务 2—45 一个固定的证据落点与状态行；在任务 43 收口前，
> 状态行 **必须** 保持 `DRAFT / NOT_ACCEPTED`，不得因局部绿灯改写为 `PASS`/`ACCEPTED`。
> 原 20261019 验收报告对该 revision 的 **FAIL** 结论保持原样，不被本目录覆盖（只读副本见
> `evidence/baseline-20261019/`）。

---

## 1. 候选与基线

| 项 | 值 | 证据 |
|---|---|---|
| Candidate revision | `878e3557b3b593a0c42adf842e80fe1408db395e` | `evidence/collection.log` |
| 工作区脏状态 | 16 条（2 modified + 13 untracked 文档 + 1 untracked 报告目录） | `git status --porcelain=v1` in `evidence/collection.log` |
| `git diff` 摘要 | `ef511ec9491adb3bd5b2d16148acfc805bd6516e110804a98d008fd4ad2c66e7` | 同上 |
| 逐文件补丁摘要 | `docs/ACP_Architecture_Adjustment_Plan_20261016.md`、`tests/baseline/refactor-evidence/e2e-task-root.txt` 各一 | `evidence/baseline.json::dirty.patch_sha256_by_file` |
| `e2e-task-root.txt` | **仅事实记录**（path/status/sha256/HEAD blob sha1）；内容未复制、未打包、未提交 | `evidence/baseline.json::dirty.e2e_task_root_fact` |
| 环境 | Python 3.11.13 · Node v20.19.0 · pip 22.3.1 · extras `api/browser/dev/nmr/remote` | `evidence/versions.log` |
| 配置源 | built-in → `~/.cccp.yaml` → `./cccp.yaml` → `--config` → `CONFSEARCH_*` → CLI；解析未写入任何文件 | `evidence/config-sources.log` |
| QC 发现 | 本机 `discover_all`：仅 `orca → /usr/bin/orca`（`file(1)` = Python script，非经验证的 QC ORCA）；`crest/xtb/censo/isostat/shermo` **ABSENT**；`acp doctor` 经 SSH 探到远端 node-157 版本 | `evidence/qc-discovery.log`、`evidence/qc-doctor.log` |
| 解析器 | `acp.intake.parsers` 11 个公开函数 + 解析栈版本（numpy/rdkit/PyYAML/scipy/nmrglue） | `evidence/parsers.log` |
| Fixture 摘要 | `tests/baseline/` 7 个目录树摘要 + `SHA256SUMS.txt` 自身摘要 | `evidence/fixtures.log`、`evidence/baseline.json::fixtures` |
| 性能/资源方法与预算 | 已在本 todo 冻结（同机同样本、200 请求×3 轮、P50/P95 最近秩、远端 I/O、DB 写入、32 KiB 块、缓存命中、浏览器堆/fps） | `evidence/baseline.json::performance_budget`、`harness-contract.md` §5 |

**缺失环境一律记 `NOT_VERIFIED` 并写明原因，不记 `PASS`。** 本机 `bsub/bjobs/bstop` 缺失、
本地 QC 二进制多数缺失 —— 真实科学与真实 LSF 格在本机 **不可执行**。

## 2. 支持矩阵（种子）

见 `support-matrix.md`：15 个 active workflow × 6 列 = 90 格，
基线状态只有 `OPEN` / `NOT_VERIFIED` / `NOT_APPLICABLE`，**没有任何 `PASS`**。
行来自 `workflow_enablement.py::ACTIVE_WORKFLOWS`，closure/remote 来自
`execution_chain.py::FROZEN_WORKFLOW_CAPABILITY_TABLE` —— 代码事实，不是证据。

## 3. 验收 Harness 契约

见 `harness-contract.md`（**任务 44/45 在代码冻结前实现**）：

- 任务 45：`m1-singlepoint`、`lsf-singlepoint`（+ `m1-failure-cancel-restart`、`browser-default-page`）；
  `--help` / `--dry-run` / 缺环境 `NOT_VERIFIED`；退出码 `0=PASS, 1=FAIL, 2=NOT_VERIFIED, 3=usage`。
- 任务 44：四格 `F0/S0`、`F1/S0`、`F0/S1`、`F1/S1`；`--check-only`；拒绝 `S1-as-S0` 与 `TestClient-as-F0`。
- 统一 JSON schema `acp.acceptance.harness.v1`；环境变量 `ACP_M1_ORCA`、`ACP_LSF_NODE`、
  `ACP_RUN_ROOT`、`ACP_ACCEPTANCE_*`。
- 性能/资源方法与预算在同文件 §5 冻结（本 todo 完成），晚于此的性能声明必须引用它。

## 4. 证据与词汇

- 本目录 `evidence/` 保存 task 1 采集的**原始日志**；`evidence_manifest.json` 给出每个文件的 sha256（确定性、已排序）。
- 证据词汇：`PASS / FAIL / NOT_VERIFIED` + scope `IN_SCOPE / DEFERRED / NOT_APPLICABLE`。
  **已执行失败不得改写为 `NOT_VERIFIED`；`DEFERRED` 不是 `PASS`；每个 skip 单独说明。**
- `python3.11 -m compileall` 只证明语法可编译，**不证明 import 可用**；import 冒烟单独执行
  （`python3.11 -c "import acp, cccp, acp_services"`），见 `evidence/verification.log`。
- 历史探针 `reports/architecture_acceptance_20261019/reproduce_defects.py` 是**审计件**：
  `--collect-only` 实收 12 项（exit 0），原模块无 `__main__`/argparse 入口；它不是门。
  原 20261019 目录**未被本任务修改**（只读副本在 `evidence/baseline-20261019/`）。

## 5. 关闭门（引用，不在此收口）

`G1 正确性` / `G2 正式回归` / `G3 默认链路` / `G4 独立交付` / `G5 功能验收` / `G6 发布运行`
见 `docs/reference/next-change/ACP_Architecture_Acceptance_Remediation_Plan_20261020.md` §7.2。
“本轮缺陷已修复”至少需要 G1/G2；本报告当前 **0/6 门**，故状态 `DRAFT / NOT_ACCEPTED`。

## 6. 状态历史

| 日期 | 状态 | 说明 |
|---|---|---|
| 2026-10-20 | `DRAFT / NOT_ACCEPTED` | task 1 基线冻结 + 证据骨架 + harness 契约；候选 `878e3557b3b593a0c42adf842e80fe1408db395e` |

（后续状态由任务 39—43 追加；**不得**在证据未落盘时改写本表。）
