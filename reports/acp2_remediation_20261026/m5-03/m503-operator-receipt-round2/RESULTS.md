# M5-03 独立操作者验收 — 第二轮（复验）结果

对照第一轮发现 F1–F7 逐项复验。原始输出见 `logs/`，截图见 `screenshots/`。

## 环境

- **日期**: 2026-10-28（16:56–17:20 Asia/Shanghai）
- **操作者**: 昔涟（Cyrene, DSH agent）— 独立操作者（非实现者）
- **主机**: 同第一轮 ARM64 干净机 `10.15.92.10`（`ACPtest@`，无 sudo）；Python 3.11.18 / Node 22.19.0 / pnpm 11.28.5
- **制品库**: `m5-03/`（481MB，31 组件）整体 rsync 至 `~/acp2-m503-store/`（482MB / 1690 文件，替换式）
- **隔离边界**: 全部安装 `--no-index`（wheelhouse + 库内发行包）；服务/协议/MCP 仅用本轮新建 venv；回执核对**全部在库内完成**（本轮未访问实现仓库）

## 上轮发现复验矩阵

| 上轮发现 | 本轮结果 | 证据 |
|---|---|---|
| F1 离线依赖缺口 | ✅ **修复** | `python/wheelhouse/`（55 wheels，aarch64+x86_64 双平台）；root 与 roles 均 `--no-index` 全离线装通（root: acp-0.3.0 + 18 依赖；roles: acp-web/acp-worker/cccp-0.3.0 + 39 依赖） |
| F2 互斥守卫缺失 | ✅ **修复** | 混装后三入口全拒：CLI `acp --version` → **exit 78** `mutually_exclusive_distributions`；`create_app()` 抛 `acp.install_guard.MutuallyExclusiveDistributions`（拒绝启动）；`acp mcp serve --transport stdio` → **exit 78**（在缺少 --controller 之前先被互斥守卫拦截） |
| F3 `acp --version` 不支持 | ✅ **修复** | 输出 `acp 0.3.0`，exit 0 |
| F4 root 无 [remote] 时 doctor 崩溃 | ✅ **修复** | 输出一行 `remote: NOT_VERIFIED — paramiko 未安装（安装 acp[remote] 后可检查远程节点）`，正常结束，无 Traceback |
| F5 Runbook 环境项 | ✅ **已修正** | Runbook 明确 `--controller` 必填与 8765 冲突回退；本轮 8765 仍被旧 uvicorn 占用（环境未变），按手册回退 8766 |
| F6 Workbench 控制台错误 | ✅ **修复** | 未选项目：0 控制台错误、0 次 `/api/v2/science/capabilities` 自动请求；选择项目 `Uncategorized` 后 `?project_id=uncategorized` → **200**；`/js/../etc/passwd` → 404（截图 ×2） |
| F7 回执未随库交付 | ✅ **修复** | `evidence/receipts/` + `coverage-receipts-manifest.json`：**53/53** 回执 sha256 库内核对全过；抽 3 行 PASS（G09/G04/G26，种子 503）与回执文件一致 |

## 逐步结论

| 步骤 | 重跑 | 结论 |
|---|---|---|
| 1 完整性 | ✅ | **PASS** — 31/31 组件 sha256 全过（commonpath 跨机解析） |
| 2 安装+互斥 | ✅ | **PASS** — 全离线装通四发行；混装三入口 typed 拒绝（exit 78 / typed exception）；`--version`、doctor 行为符合预期 |
| 3 服务与协议 | ✅ | **PASS** — `/api/capabilities` 200（能力 JSON 完整）；MCP initialize 回 `2025-06-18`；tools/list 恰好 8 个 `acp_*` 工具 |
| 4 Workbench | ✅ | **PASS** — 零控制台错误；未选项目零自动请求；选项目后 200；穿越 404 |
| 5 RPH | ⏭️ 免重跑 | BOM 比对：RPH 6 组件与第一轮**字节一致**（sha256 相同），沿用第一轮 PASS 证据（verify PASS + doctor 9 项 ok） |
| 6 声明一致性 | ✅ | **PASS** — 53/53 库内回执核对；H4/H3（h_rows）、M3-03（tasks）、DEF-01（deferred_rows）均如实 `NOT_VERIFIED`；`scope.json` `candidate_full_gate.allowed == false` |

## 残留观察（非阻塞，如实记录）

1. 8765 仍被实现仓库旧 uvicorn（2026-10-17 起）占用——环境问题非制品问题，Runbook 已给回退方案；建议实现侧择机清理干净机。
2. `create_app` 入口以未捕获异常拒绝（Python traceback、exit 1）而非 exit 78——Runbook 该入口只要求「TypedError」，异常类型 `acp.install_guard.MutuallyExclusiveDistributions` 满足；CLI/MCP 两入口均 78。行为与 Runbook 预期一致，仅备注。

## 操作者判定

第一轮 7 项发现在本轮**全部修复或修正**；重跑步骤 1/2/3/4/6 全部通过，步骤 5 依 BOM 字节一致免重跑。M5-03 独立操作者验收第二轮：**PASS**。

— 昔涟（独立操作者），2026-10-28
