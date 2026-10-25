# 验收 Harness 契约（Acceptance Harness Contract）

**Frozen:** 2026-10-20 (task 1) — **在代码冻结之前**交付给任务 44/45 实现。
**Candidate revision:** `d5d85e7c804878edd0cedb6bce1d32af2d92ad83`
**Implementers:** 任务 44 `scripts/acceptance/four_combinations.py`、任务 45 `scripts/acceptance/real_environment.py`（+ 各自 `tests/test_acceptance_*.py`）。
**Executors:** 任务 41（四组合）、任务 42（真实环境）只**运行**本契约定义的场景，不改 harness。

本文件是**契约**：场景清单、输出 schema、CLI flags、环境变量在冻结时定死。
实现若需要偏离，必须先改本文件（证据-only 提交）并在 `E/task-44`/`E/task-45` 记录差异，不得静默改变含义。

---

## 1. 场景清单（Scenario list）

### 1.1 任务 45 — `scripts/acceptance/real_environment.py`

| scenario id | 前置（缺失 → `NOT_VERIFIED`，绝不 PASS） | 步骤 | 必需断言 |
|---|---|---|---|
| `m1-singlepoint` | `ACP_M1_ORCA` 指向真实 ORCA 可执行；`ACP_RUN_ROOT` 指向原生文件系统临时目录 | 1) 起临时 `acp run serve`（临时端口）2) 经普通 **v1 API** 提交 singlepoint（不走 TestClient、不走 FakeBackend）3) 轮询到终态 4) 断言规范产品 `RESULT/result_manifest.json` 存在且为 v2 5) `GET /api/v1/jobs/{id}/structure-viewer` 帧列表非空 6) 可选 CLI 交叉核对（`acp run singlepoint` 同输入） | 退出码 0 且四条断言全为真；任一断言失败 → `FAIL`。CLI 仅交叉核对，CLI 失败单独记，不吞掉 API 结果 |
| `lsf-singlepoint` | `ACP_LSF_NODE` 已配置且 SSH 凭据可用；真实 `bsub/bjobs/bstop` 在该节点可达；隔离的远端目录 | 1) 提交 → 2) 观测（bjobs）→ 3) 停止确认（bstop + 确认已停）→ 4) 未知 bsub 对账 → 5) release/markers 校验 → 6) 断线恢复 → 7) 终态预取 → 8) 旧 attempt 拒绝 → 9) 结果拉取 | 每步有原始命令 + 退出码 + 回执；“生成的命令字符串”或 mock paramiko **不构成执行**；缺环境 → `NOT_VERIFIED` + 具体原因 |

辅助 scenario（同一脚本，独立 id，失败互不掩盖）：

| scenario id | 说明 |
|---|---|
| `m1-failure-cancel-restart` | 同 `m1-singlepoint` 提交后：失败路径、取消路径、重启恢复各一次；恢复只复用身份有效且完成事实完整的结果 |
| `browser-default-page` | 有断言的真实 Chromium：任务列表→详情→文件/日志→结果；rerun、A→B→A、候选保存、编辑重算、NMR、振动、多实例、导出、隐藏/销毁、断网恢复、历史结果。`browser_probe.py` 退出码 0 **永不构成 pass**（仅诊断） |

### 1.2 任务 44 — `scripts/acceptance/four_combinations.py`

四个组合格，每格独立启动、独立结果：

| cell | F（静态 bundle/前端） | S（服务） | 定义 |
|---|---|---|---|
| `F0/S0` | 冻结的真实**旧**静态文件集（worktree @ `d5d85e7c804878edd0cedb6bce1d32af2d92ad83`） | 旧服务实现 @ F0 | 本次整改前真实基线 |
| `F1/S0` | 修复候选静态文件集（worktree @ 代码候选 revision） | 旧服务实现 | 新前端 + 旧服务 |
| `F0/S1` | 冻结旧静态文件集 | 新服务实现 | 旧前端 + 新服务 |
| `F1/S1` | 修复候选静态文件集 | 新服务实现 | 修复候选 |

每格必须：

1. 用 `git worktree add` 准备 F0/F1 worktree（**不修改** `reports/architecture_acceptance_20261019/`）。
2. S0/S1 起在**不同端口**，各自**临时 `ACP_RUN_ROOT`**。
3. 覆盖：原端点 / 字段 / 错误 / 副作用 与 7 个 legacy 工作流
   （`Confsearch`、`PESsearch`、`BatchOptimize`、`XtbPathSearch`、`XtbConstrainedOpt`、`tsmode`、`nmr`）。
4. 记录每格的 bundle 摘要、revision、config 摘要。
5. **禁止**：用 S1 的旧路由冒充 S0；用 FastAPI TestClient 冒充 F0；
   要求 F0 原有已知缺陷凭空消失（旧基线缺陷保留，验收重点是兼容行为与新增回归）。

单元测试必须拒绝两类坏格：`S1-as-S0` 与 `TestClient-as-F0`（`tests/test_acceptance_four_combinations.py`）。

---

## 2. JSON 输出 schema（两者共用）

文件：每场景一个 `<out>/<scenario_id>.json`；汇总 `<out>/summary.json`。

```json
{
  "schema": "acp.acceptance.harness.v1",
  "harness": "four_combinations.py | real_environment.py",
  "harness_version": "1",
  "scenario": "m1-singlepoint | lsf-singlepoint | m1-failure-cancel-restart | browser-default-page | F0/S0 | F1/S0 | F0/S1 | F1/S1",
  "started_on": "2026-10-20T17:00:00+08:00",
  "finished_on": "2026-10-20T17:05:00+08:00",
  "candidate_revision": "d5d85e7c804878edd0cedb6bce1d32af2d92ad83",
  "f0_revision": "d5d85e7c804878edd0cedb6bce1d32af2d92ad83",
  "f1_revision": "<code candidate sha>",
  "environment": {
    "machine": "<uname -a>",
    "python": "3.11.13",
    "node": "v20.19.0",
    "run_root": "/tmp/...",
    "env_present": {"ACP_M1_ORCA": true, "ACP_LSF_NODE": false},
    "env_missing_reason": null
  },
  "result": "PASS | FAIL | NOT_VERIFIED",
  "scope": "IN_SCOPE | DEFERRED | NOT_APPLICABLE",
  "skip_reason": null,
  "not_verified_reason": null,
  "steps": [
    {
      "id": "submit",
      "command": "python3.11 -m acp.cli run ...",
      "exit_code": 0,
      "seconds": 12.34,
      "stdout_log": "evidence/real-env/m1-singlepoint/submit.log",
      "assertions": [{"name": "terminal_state", "expected": "COMPLETED", "actual": "COMPLETED", "ok": true}],
      "ok": true
    }
  ],
  "artifacts": [
    {"path": "RESULT/result_manifest.json", "sha256": "<...>", "bytes": 1234}
  ],
  "metrics": {
    "requests": 0, "p50_ms": 0.0, "p95_ms": 0.0,
    "remote_io": {"read_bytes": 0, "write_bytes": 0, "files": 0, "seconds": 0.0},
    "db_writes": {"total_changes_delta": 0, "statements": 0},
    "block_size_bytes": 32768,
    "cache": {"hits": 0, "misses": 0, "hit_ratio": 0.0},
    "browser": {"js_heap_before_bytes": 0, "js_heap_after_teardown_bytes": 0, "fps": 0.0}
  },
  "cell": null
}
```

规则：

- `result` 只能是 `PASS | FAIL | NOT_VERIFIED`（证据词汇）；`scope` 只能是 `IN_SCOPE | DEFERRED | NOT_APPLICABLE`。
- **已执行失败必须 `FAIL`**，不得写 `NOT_VERIFIED`；`DEFERRED` 不是 `PASS`；每个 skip 单独说明。
- `steps[*].command` 必须是**真实执行**的命令行原文 + `exit_code`；字符串生成、mock、TestClient 结果必须在
  `cell`/`steps` 中显式标记，且**不得**让 `result` 变为 `PASS`。
- `metrics` 缺测时填 `null` 并在 `not_verified_reason` 说明；**禁止**用 0 冒充实测 0。
- 所有相对 `path` 相对该 scenario 输出目录；`artifacts[].sha256` 由 harness 计算。
- `summary.json` = `{"schema":"acp.acceptance.summary.v1","scenarios":[<每个 scenario 的裁剪>],"all_pass":false}`，
  裁剪字段至少含 `scenario/result/skip_reason`。

---

## 3. CLI flags（冻结）

### 3.1 `scripts/acceptance/four_combinations.py`（任务 44）

| flag | 必需 | 说明 |
|---|---|---|
| `--help` | – | 退出码 0，打印用法与场景清单 |
| `--check-only <fixture.json>` | – | 只校验给定 fixture 的 schema/格合法性，**不起服务** |
| `--cells F0/S0,F1/S0,F0/S1,F1/S1` | 否（默认四格） | 选择要执行的格 |
| `--out <dir>` | 是（执行时） | 输出目录（`<out>/<cell>/...json`） |
| `--f0-revision <sha>` | 否（默认 `d5d85e7c804878edd0cedb6bce1d32af2d92ad83`） | F0 worktree revision |
| `--f1-revision <sha>` | 否（默认 `HEAD`） | F1 worktree revision |
| `--dry-run` | – | 打印将执行的命令，不执行，退出码 0 |

### 3.2 `scripts/acceptance/real_environment.py`（任务 45）

| flag | 必需 | 说明 |
|---|---|---|
| `--help` | – | 退出码 0 |
| `--scenario <id>` | 是（执行时） | `m1-singlepoint` \| `lsf-singlepoint` \| `m1-failure-cancel-restart` \| `browser-default-page` |
| `--out <dir>` | 是（执行时） | 输出目录 |
| `--dry-run` | – | 打印 M1/LSF 的**确切命令**而不执行，退出码 0 |
| `--timeout <sec>` | 否 | 单场景总超时（默认 1800） |
| `--keep-temp` | 否 | 保留临时 `ACP_RUN_ROOT`/worktree 以便取证 |

退出码约定：`0` = 场景 `PASS`（或 `--help`/`--dry-run` 正常）；
`1` = `FAIL`；`2` = `NOT_VERIFIED`（环境缺失）；`3` = 用法错误。
**退出码 2 绝不代表通过。**

---

## 4. 环境变量（冻结）

| 变量 | 用途 | 缺失时 |
|---|---|---|
| `ACP_M1_ORCA` | `m1-singlepoint` 使用的真实 ORCA 可执行绝对路径 | `NOT_VERIFIED` + `not_verified_reason="ACP_M1_ORCA unset"` |
| `ACP_LSF_NODE` | `lsf-singlepoint` 的目标节点 id（`acp init` 已配置的 node 名） | `NOT_VERIFIED` + `ACP_LSF_NODE unset` |
| `ACP_RUN_ROOT` | 运行数据根；harness **必须**指向临时目录（原生文件系统，禁 `mnt/*`、禁慢 FS） | 未设置则 harness 自建 `mktemp -d` 并在输出中回写实际值 |
| `ACP_ACCEPTANCE_OUT` | 与 `--out` 等价的默认输出目录 | 无 `--out` 且无此变量 → 用法错误（退出码 3） |
| `ACP_ACCEPTANCE_TIMEOUT` | 默认场景超时（秒），被 `--timeout` 覆盖 | 默认 1800 |
| `ACP_F0_REVISION` / `ACP_F1_REVISION` | 与 `--f0/--f1-revision` 等价 | 默认见 §3.1 |
| `ACP_ACCEPTANCE_S0_PORT` / `ACP_ACCEPTANCE_S1_PORT` | S0/S1 服务端口（必须不同） | 默认 `18765` / `18766` |
| `ACP_ACCEPTANCE_SEED` | perf 采样种子/样本标识，F0 与 F1 必须同值才可比较 | 缺失 → `metrics` 置 `null` 并说明 |
| `ACP_ACCEPTANCE_HEADLESS` | 浏览器场景是否无头（默认 `1`） | 默认 `1` |

**禁止**把任何凭据写进输出 JSON；凭据只经环境/ssh-agent 读取。

---

## 5. 性能/资源方法与预算（在任务 1 冻结，晚于此的任何性能声明必须引用本节）

定义见 `evidence/baseline.json::performance_budget`（同源、机器可读）。摘要：

| 维度 | 方法 | 预算/门槛 |
|---|---|---|
| 机器/样本 | 与本基线同一台主机（`uname -a` 见 `evidence/collection.log`）、python3.11.13、同一 `ACP_RUN_ROOT`（原生 FS）、同一冻结样本（`ACP_ACCEPTANCE_SEED` 同值）；F0 与 F1 顺序交替执行，各 3 轮 | 机器/样本不同 → 数字不可比，不得声称收益 |
| 请求次数 | 每系列 200 请求（50 list + 50 detail + 50 structure-viewer frames + 50 status poll），3 轮；**每轮前 20 个请求为 warmup 丢弃** | 必须报 `requests` 实数；不足 → `null` + 原因 |
| P50/P95 | 客户端 `time.perf_counter()` 包裹单请求；warmup 剔除后最近秩法 `ceil(p*n)`（n=540） | local list/detail P50 ≤ 150 ms、P95 ≤ 400 ms；frame list P95 ≤ 800 ms；F1 P95 不得比同种子 F0 恶化 >10% |
| 远端 I/O | 在 SFTP 传输边界按次累加 read/write 字节与文件数（计数器挂在传输层，不是 mock 执行）+ 传输耗时 | singlepoint attempt 拉取 ≤20 文件、≤5 MB、≤120 s |
| DB 写入 | harness 进程内对 `acp_jobs.db` 连接取 `total_changes` 差值 + `set_trace_callback` 语句直方图 | 单次 submit→terminal ≤120 条写语句；**worker 进程 jobs-DB 写入必须为 0**（硬不变量） |
| 块大小 | 文件拷贝固定 32 KiB/块；SFTP chunk 大小按实况记录；任何 F0/F1 比较必须同块大小 | 块大小不同 → 比较无效 |
| 缓存命中 | 在 read/cache 查找入口计数：命中=查找时资源已在本地，未命中=触发抓取；报 hits/misses/hit_ratio 与第二轮网络字节 | 同会话第二轮读同一资源集：hit_ratio=1.0 且网络字节 0 |
| 浏览器内存/帧率 | Playwright Chromium + 仓库 `3Dmol-2.5.5.min.js`：CDP `Performance.getMetrics` 取 `JSHeapUsedSize`（t=0 / t=10s / teardown 后 10s）；rAF 回调计数 ÷10 s 得 fps；10 次开关循环查单调增长 | teardown 后堆增长 ≤ 基线 20%；播放期 fps ≥ 30；10 轮开关后无单调增长 |

**纪律**：无前后实测不得宣称收益比例；`compileall` 只证明语法可编译，
**不证明 import/依赖可用**（import 冒烟必须单独跑 `python3.11 -c "import acp, cccp, acp_services"`）。
