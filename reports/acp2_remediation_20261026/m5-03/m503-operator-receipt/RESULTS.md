# M5-03 独立操作者验收 — 结果汇总

对照 `RUNBOOK.md` 七步。逐步结论 + 发现清单。原始命令输出见 `logs/`。

## 逐步结论

| 步骤 | 结论 | 摘要 |
|---|---|---|
| 1 制品完整性 | **PASS** | 27/27 组件 sha256 与 `bom/bom.current.json` 全部匹配（0 MISSING / 0 DIGEST） |
| 2 四发行安装 + 互斥 | **FAIL（按 Runbook 原文）** + 适配验证通过 | 见发现 F1/F2/F3/F4 |
| 3 服务与协议 | **PASS（含适配）** | 服务起、能力 JSON 200、MCP 握手 `2025-06-18`、恰好 8 个 `acp_*` 工具 |
| 4 Workbench | **部分：加载/穿越通过，零控制台错误不成立** | 页面 200、`/js/../etc/passwd`→404；1 条稳定控制台错误（见 F6）；截图 `screenshots/workbench-load.png` |
| 5 RPH 离线安装 + doctor | **PASS** | `pnpm install --offline --frozen-lockfile` OK；`RPH INSTALL VERIFY PASS`（4 包 dist 就位）；doctor 9 项 = 5 pass + 4 skip（skip 均为 `--connect` 在线类，离线场景设计如此），总结 `ok` |
| 6 声明一致性 | **PASS（一项非独立核对）** | 见下 |
| 7 签收回执 | 本目录 | — |

## 发现清单（按 Runbook「原样记录、不尝试修复」）

- **F1（步骤2，阻塞离线故事）**: 制品库不含第三方依赖 wheelhouse。`pip install --no-index --find-links .../python/root acp` 报 `ERROR: No matching distribution found for numpy>=2.1.0`。Runbook「制品库（含全部安装包）」的离线安装承诺不成立；实现侧构建日志自身也是联网装 setuptools/wheel。
  - *适配验证*: 第三方依赖改由 PyPI 安装后，四个发行包仍以 `--no-index` 从制品库安装成功：root `acp 0.3.0`；roles `cccp 0.3.0 + acp-worker 0.3.0 + acp-web 0.3.0`。
- **F2（步骤2，互斥守卫缺失）**: 互斥负例**未被拒绝**。依赖补齐后，在已装 root `acp 0.3.0` 的 venv 中 `pip install --no-index --find-links .../roles acp-web` 输出 `Successfully installed acp-web-0.3.0 acp-worker-0.3.0 cccp-0.3.0`；混合环境中 `acp --help`、`acp doctor` 均正常运行。pip 层与运行时层均未观察到 Runbook 所述「typed guard 拒绝」。
- **F3（步骤2）**: root 发行 `acp --version` 不被支持：argparse 报 `the following arguments are required: command`（版本经 `python -c "import acp; acp.__version__"` 确认为 0.3.0）。
- **F4（步骤2）**: root venv（未装 `[remote]` extra）中 `acp doctor` 崩溃：`ModuleNotFoundError: No module named 'paramiko'`（`acp/scheduler/remote/sftp.py` 无条件 import）。
- **F5（步骤3，环境）**: 干净机 8765 被实现仓库旧 uvicorn 占用（见 ENVIRONMENT.md 偏差 1）；验收服务改 8766。另：`acp mcp serve --transport stdio` 不带 `--controller` 直接报错退出（Runbook 命令原文缺少该参数）。
- **F6（步骤4）**: 页面加载产生 1 条稳定控制台错误：`GET /api/v2/science/capabilities?project_id=demo-project` → 403 Forbidden（前端自身在加载时发出）。"零控制台错误"不成立。
- **F7（步骤6，交付缺口）**: 覆盖账本 PASS 行引用的 `reports/...` 回执未随制品库交付，独立操作者无法从库内核对 digest。

## 步骤 6 明细

- 抽样 3 行 PASS（`G09`、`G04`、`G26`，随机种子 503）：`receipt_sha256` 与实现仓库回执文件实测一致
  - `m4-01/summary.json` = `887b4102…7694`（G09、G26 声明一致）
  - `m4-02/summary.json` = `d6c5fa06…61d4`（G04 声明一致）
  - （核对路径为实现仓库副本，**非独立**，见 ENVIRONMENT.md 偏差 3 / F7）
- 如实 NOT_VERIFIED 确认：**H4**（DA 科学命题，OPEN）、**H3**（LSF DA，OPEN）、**M3-03**（生产模型凭据，tasks 行 notes 明示 NOT_VERIFIED）、**DEF-01**（worker-hosted 第三方 COMPUTE，DEFERRED）— 全部与声明一致，未被改写为 PASS
- `contracts/release/scope.json`（acp2-contracts.tar.gz 内）: `candidate_full_gate.allowed == false` ✓（操作者未签收前不放行）

## 操作者判定

步骤 1/3/5/6 通过；步骤 4 除「零控制台错误」外通过；**步骤 2 按原文不通过（F1 离线依赖缺口、F2 互斥守卫缺失）**。F2 属发行安全承诺（四发行互斥）未兑现，建议实现侧优先回应 F1/F2；F3/F4/F6 为次要缺陷；F5/F7 为环境与交付缺口。

— 昔涟（独立操作者），2026-10-28
