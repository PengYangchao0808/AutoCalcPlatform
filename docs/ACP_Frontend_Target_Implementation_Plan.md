# ACP 前端目标完成实现计划

生成日期：2026-06-25

适用项目：`ACP_V1_20260519`

## 1. 文档目标

本文档综合当前 ACP 代码状态、WSL 安装环境、浏览器无法连接后端的诊断结果、前端形态参考、xTBridge 架构计划，以及 Phase 4 机理/过渡态工作流需求，给出一份完整的 ACP 前端目标实现计划。

本文档回答四个问题：

1. 为什么当前 ACP 后端网页无法打开？
2. 在 WSL 中安装 ACP 时，Windows 浏览器如何与 ACP 后端通信？
3. ACP 前端最终应完成到什么形态？
4. 前后端、调度器、工作流、测试应按什么顺序落地？

## 1.5 现实校正（2026-06-25 代码复核）

> ⚠️ 本节为对真实代码的复核结果。本文档 §2 的原始诊断基于更早的代码快照，部分结论已过时；执行前必须以本节为准。

经直接读码核实，下列原诊断项**已不成立**（代码已走到文档前面）：

| 原诊断（§2） | 真实代码状态（2026-06-25） | 证据 |
|---|---|---|
| `acp run serve` 是 placeholder，返回 "not yet implemented" | **已实现**：`_handle_serve` 真实调用 `uvicorn.run("acp.api.server:app", ...)`，dispatch map `"serve": _handle_serve` | `src/acp/cli.py:708-735, 819` |
| `python -m acp.cli run serve` 返回 not yet implemented | **错误**：该消息只由 `mechanism` 触发（`_handle_placeholder`），serve 已绕过 | `cli.py:699-702` |
| `src/acp/api/` 是空占位符 | **已存在** `server.py`(34行)/`routes.py`(86行)/`schemas.py`(63行)，`/api/status`、`/api/backends` 已可用 | `src/acp/api/` |
| `frontend/` 需新建 | **已存在** `ACP_Workbench.html`(164行)，dark 主题 Dashboard，已对接 `/api/status`、`/api/backends` | `frontend/ACP_Workbench.html` |
| WSL 中 fastapi/uvicorn 未安装 | **已安装** fastapi 0.138.0、uvicorn 0.49.0；`import acp` 成功 | 运行时验证 |
| xTB 误用 `--neb` 需纠正 | **不存在该 bug**：legacy `xtb.py` 无任何 neb/path 引用，是干净绿地 | `interfaces/xtb.py` grep 为空 |

**仍成立的真实缺口**（这才是剩余工作）：

1. `pyproject.toml` 未声明 `api` optional dependencies——fastapi/uvicorn 虽已装，但 `pip install -e .` 不会带它们（packaging gap，新环境复现必败）。
2. 无 `src/acp/scheduler/` 包——`/api/jobs`、SSE、SQLite 持久化全部缺失（最大缺口）。
3. `/api/status` 响应缺 `host/port/environment/queue` 字段；`/api/backends` 的 capabilities 是扁平 `list[str]`，非文档要求的 `{capability: status}` dict。
4. `mechanism` 仍是 placeholder；`workflows/mechanism.py` 不存在。
5. xTB `pathfinder`、Gaussian `transition_state_opt`/`irc` 均未实现（绿地）。
6. `tests/` 无任何 api/server/scheduler 测试。
7. 两份 AGENTS.md 此前称 api/ 为空占位符——已勘误。

**因此：M0、M1、M2 已基本完成**（见 §20 校正）。下一阶段真正起点是 M3 收尾 + M4 Scheduler。

**前端视觉决策**：保留现有 dark 主题（§7.2 的浅色令牌降级为参考，不再作为强制目标）。

## 2. 当前问题诊断

### 2.1 浏览器错误

Windows Chrome 显示：

```text
This site can't be reached
127.0.0.1 refused to connect
ERR_CONNECTION_REFUSED
```

这类错误表示浏览器成功访问了本机网络栈，但目标端口没有进程监听，或者监听服务拒绝连接。

### 2.2 已确认的本地状态

诊断结果：

1. WSL 发行版为 Ubuntu，WSL 版本为 2。
2. WSL Ubuntu 正在运行。
3. Windows 侧访问 `127.0.0.1:80` 和 `127.0.0.1:8765` 均没有 ACP 服务响应。
4. WSL 内直接 `import acp` 失败，说明 ACP 未在当前 WSL Python 环境中安装，或当前环境未加入 `PYTHONPATH`。
5. WSL 内 `fastapi`、`uvicorn`、`python-multipart` 未安装。
6. ~~即使设置 `PYTHONPATH=src` 后执行 `python -m acp.cli run serve`，当前结果仍是 "not yet implemented"~~ **【已过时，见 §1.5】**：`run serve` 现已真实调用 uvicorn；该 "not yet implemented" 提示当前仅 `mechanism` 子命令触发。若仍观察到 serve 报错，应优先排查 fastapi/uvicorn 是否在该环境实际可 import，以及 `pip install -e .` 是否补齐 `api` optional dep。

### 2.3 根因判断

> 注：本节为原始诊断。截至 2026-06-25 复核（§1.5），`acp run serve`、`api/` 骨架、`frontend/`、fastapi/uvicorn 安装均已落地。当前若仍出现 `ERR_CONNECTION_REFUSED`，根因通常收窄为：**该环境未 `pip install -e ".[api]"`**（pyproject 未声明 api extra，见 §6）或 **未实际执行 `acp run serve`**。

原始诊断（针对更早快照，保留作历史记录）：

当前故障不是单纯的 WSL 网络转发问题，而是后端服务尚未实现和未启动：

```text
Windows 浏览器无法连接
        |
        +-- WSL 中没有 ACP FastAPI 服务监听端口
        +-- acp run serve 当前仍是 placeholder
        +-- API 依赖尚未安装
        +-- ACP 未在 WSL Python 环境中安装为包
```

~~因此当前修复重点不是改前端 HTML，而是先补齐后端服务入口。~~ **【更新】** 后端服务入口已存在；当前修复重点是补齐 `api` optional dep 声明并确保 `pip install -e ".[api]"` 执行，再推进 scheduler。

## 3. 总体目标

ACP 前端目标不是一个孤立静态网页，而是一个本地 Web 控制台：

```text
Windows Chrome
  http://127.0.0.1:8765
        |
        v
WSL2 Ubuntu FastAPI 服务
  acp run serve
        |
        v
ACP Scheduler
        |
        v
ACP Workflows
  conformer / nmr / mechanism / benchmark
        |
        v
External Backends
  Gaussian / xTB / CREST / ORCA / ISOSTAT / Shermo
```

目标形态：

1. 用户从 Windows 浏览器打开 ACP 前端。
2. ACP 后端运行在 WSL Ubuntu。
3. 前端由后端托管，避免 `file://`、CORS 和 WSL IP 变化问题。
4. 后端负责创建任务、排队、运行、取消、重试、持久化、日志和结果文件。
5. 前端只负责表单、队列、进度、日志、结果展示。

## 4. WSL 网页通信方案

### 4.1 推荐方案：Windows localhost 访问 WSL 服务

在 WSL 中启动服务：

```bash
acp run serve --host 127.0.0.1 --port 8765
```

Windows 浏览器访问：

```text
http://127.0.0.1:8765
```

这是首选方案。WSL2 默认支持 Windows 通过 localhost 访问 WSL 中监听的网络服务，但前提是 WSL 中确实有服务在监听目标端口。

### 4.2 开发阶段启动方式

在真正实现 `acp run serve` 前，开发阶段可使用：

```bash
cd /mnt/e/Calculations/Common_Script/Auto_Calc_Platform/ACP_V1_20260519
source .venv/bin/activate
PYTHONPATH=src uvicorn acp.api.server:app --host 127.0.0.1 --port 8765 --reload
```

### 4.3 兜底方案：使用 WSL IP

如果 Windows 访问 `127.0.0.1:8765` 失败，可在 PowerShell 中查看 WSL IP：

```powershell
wsl hostname -I
```

然后访问：

```text
http://<WSL_IP>:8765
```

此时建议 WSL 服务监听：

```bash
uvicorn acp.api.server:app --host 0.0.0.0 --port 8765
```

### 4.4 兜底方案：Windows 端口转发

如果 localhost 转发异常，可使用管理员 PowerShell：

```powershell
$wslIp = (wsl hostname -I).Trim().Split()[0]
netsh interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=8765 connectaddress=$wslIp connectport=8765
```

删除转发：

```powershell
netsh interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=8765
```

### 4.5 可选方案：WSL mirrored networking

Windows 11 22H2 及以上可在 `%UserProfile%\.wslconfig` 中启用：

```ini
[wsl2]
networkingMode=mirrored
localhostForwarding=true
```

修改后执行：

```powershell
wsl --shutdown
```

然后重启 Ubuntu。

该方案适合长期减少 WSL NAT 网络问题，但不是 ACP 前端的必需条件。

## 5. WSL 环境修复步骤

在 WSL Ubuntu 中执行：

```bash
cd /mnt/e/Calculations/Common_Script/Auto_Calc_Platform/ACP_V1_20260519

python3 -m venv .venv
source .venv/bin/activate

python -m pip install -U pip
pip install -e .
pip install fastapi uvicorn python-multipart
```

验证：

```bash
python - <<'PY'
import acp
import fastapi
import uvicorn

print("ACP OK:", acp.__file__)
print("FastAPI OK")
print("Uvicorn OK")
PY
```

预期：

```text
ACP OK: ...
FastAPI OK
Uvicorn OK
```

## 6. 依赖与打包修复

### 6.1 pyproject.toml

新增 API 可选依赖：

```toml
[project.optional-dependencies]
api = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "python-multipart>=0.0.9",
]
```

后续安装方式：

```bash
pip install -e ".[api]"
```

### 6.2 开发依赖建议

保留现有 `dev` 依赖。若后续 API 测试需要，可加入：

```toml
dev = [
    "pytest>=7.4.0",
    "pytest-cov>=4.1.0",
    "ruff>=0.4.0",
    "mypy>=1.8.0",
    "pre-commit>=3.6.0",
    "httpx>=0.27",
]
```

`httpx` 用于 FastAPI TestClient 相关测试。

## 7. 前端目标形态

### 7.1 产品定位

ACP 前端应定位为本地计算任务工作台，而不是静态报告页。

用户不应关心：

1. 如何拼接 `acp run conformer` 命令。
2. Gaussian/xTB/CREST 的实际日志路径。
3. 哪个 stage 正在写哪个中间文件。
4. WSL IP 是否变化。

用户只应关心：

1. 我要算什么。
2. 输入结构是什么。
3. 用什么协议和后端。
4. 分配多少资源。
5. 当前跑到哪一步。
6. 成功结果在哪里。
7. 失败原因是什么。

### 7.2 视觉风格

> **决策（2026-06-25）**：保留现有 `frontend/ACP_Workbench.html` 的 **dark 主题**（GitHub-dark 风格，已对接 `/api/status`、`/api/backends`）。下方浅色令牌降级为**参考**，不再作为强制目标；如未来需要浅色可再切换。dark 主题令牌见 `ACP_Workbench.html` 的 `:root`（`--bg #0d1117 / --surface #161b22 / --accent #58a6ff` 等）。

原始浅色令牌建议（仅供参考）：

1. 单页纵向布局。
2. 卡片式模块。
3. 清晰状态标签。
4. 任务时间线。
5. 移动端可读，桌面端更高效。
6. 避免复杂后台系统式大表格第一屏。

建议视觉令牌：

```text
background: #f6f7f9
surface:    #ffffff
text:       #172033
muted:      #687385
primary:    #1677ff
success:    #07c160
warning:    #fa9d3b
danger:     #fa5151
border:     #dde3ea
radius:     8px
```

### 7.3 首页布局

```text
+------------------------------------------------------+
| ACP Workbench                         service: online |
+------------------------------------------------------+
| Backend Status                                        |
| Gaussian ok | xTB ok | CREST ok | ORCA missing        |
+------------------------------------------------------+
| New Calculation                                       |
| [Conformer] [NMR] [Mechanism/TS] [Benchmark]          |
+------------------------------------------------------+
| Active Queue                                          |
| running job card                                      |
| queued job card                                       |
+------------------------------------------------------+
| Recent Results                                        |
| completed job card                                    |
+------------------------------------------------------+
```

### 7.4 主视图

前端至少包含五个视图：

1. Dashboard
   - ACP 服务状态
   - WSL 状态提示
   - 后端可用性
   - 队列概览
   - 最近失败任务

2. New Job
   - workflow 选择
   - 构象搜索表单
   - NMR 表单
   - 机理/TS 表单
   - benchmark 表单
   - 提交前检查

3. Queue
   - queued
   - running
   - completed
   - failed
   - cancelled
   - retry / cancel / clone

4. Job Detail
   - stage 时间线
   - 实时日志
   - 输入参数
   - 关键输出
   - 错误摘要
   - 结果文件

5. Results
   - 完成任务索引
   - 按 workflow / molecule / date 搜索
   - 下载报告
   - 打开结果目录

## 8. 前端文件规划

第一版建议不引入构建工具：

```text
frontend/
├── ACP_Workbench.html
├── acp.css
└── acp.js
```

也可以在 MVP 阶段使用单文件：

```text
frontend/ACP_Workbench.html
```

后端托管方式：

```text
GET /                  -> frontend/ACP_Workbench.html
GET /assets/acp.css    -> frontend/acp.css
GET /assets/acp.js     -> frontend/acp.js
```

前端 API 调用必须使用相对路径：

```javascript
fetch("/api/status")
fetch("/api/backends")
fetch("/api/jobs")
new EventSource(`/api/jobs/${jobId}/events`)
```

不要写死：

```javascript
fetch("http://127.0.0.1:8765/api/status")
```

这样可以避免 WSL IP、端口、CORS 变化。

## 9. 后端 API 目标

### 9.1 服务状态

```text
GET /api/status
```

响应：

```json
{
  "service": "ACP Workbench",
  "status": "ok",
  "version": "1.0.0",
  "host": "127.0.0.1",
  "port": 8765,
  "environment": {
    "platform": "wsl",
    "python": "3.x",
    "cwd": "/mnt/e/..."
  },
  "queue": {
    "queued": 0,
    "running": 0,
    "completed": 0,
    "failed": 0
  }
}
```

### 9.2 后端能力

```text
GET /api/backends
```

响应：

```json
{
  "gaussian": {
    "available": true,
    "path": "g16",
    "capabilities": {
      "geometry_optimization": "available",
      "single_point": "available",
      "frequency": "available",
      "nmr": "available",
      "ts_optimization": "planned",
      "irc": "planned"
    }
  },
  "xtb": {
    "available": true,
    "path": "xtb",
    "capabilities": {
      "geometry_optimization": "available",
      "single_point": "available",
      "pathfinder": "planned"
    }
  }
}
```

### 9.3 工作流发现

```text
GET /api/workflows
GET /api/protocols
```

返回可用工作流、协议、默认参数和说明。

### 9.4 任务管理

```text
POST /api/jobs
GET  /api/jobs
GET  /api/jobs/{job_id}
POST /api/jobs/{job_id}/cancel
POST /api/jobs/{job_id}/retry
POST /api/jobs/{job_id}/clone
GET  /api/jobs/{job_id}/events
GET  /api/jobs/{job_id}/logs
GET  /api/jobs/{job_id}/files
GET  /api/jobs/{job_id}/report
```

### 9.5 SSE 事件流

实时日志和进度采用 Server-Sent Events：

```text
GET /api/jobs/{job_id}/events
```

事件示例：

```text
event: stage
data: {"stage":"crest_search","status":"running"}

event: log
data: {"stream":"stdout","line":"Running CREST..."}

event: result
data: {"file":"results/final_ensemble.xyz"}
```

## 10. 后端模块规划

新增：

```text
src/acp/api/
├── __init__.py
├── server.py
├── routes.py
├── schemas.py
└── static.py

src/acp/scheduler/
├── __init__.py
├── jobs.py
├── manager.py
├── runner.py
├── store.py
├── events.py
├── logs.py
└── files.py
```

### 10.1 acp.api.server

职责：

1. 创建 FastAPI app。
2. 注册 API routes。
3. 托管前端静态文件。
4. 初始化全局 JobManager。

### 10.2 acp.api.routes

职责：

1. `/api/status`
2. `/api/backends`
3. `/api/workflows`
4. `/api/jobs`
5. `/api/jobs/{job_id}/events`
6. `/api/jobs/{job_id}/files`

### 10.3 acp.api.schemas

职责：

1. 定义请求/响应模型。
2. 校验 JobSpec。
3. 隔离 API JSON 与内部 dataclass。

### 10.4 acp.scheduler.manager

职责：

1. 创建任务。
2. 管理队列。
3. 并发控制。
4. 取消任务。
5. 重试任务。
6. 记录事件。
7. 调用 runner。

### 10.5 acp.scheduler.runner

职责：

1. 将 JobSpec 转成具体 workflow 调用。
2. 调用 `run_conformer_search`。
3. 调用 `run_nmr_calculation`。
4. 后续调用 `run_mechanism_search`。
5. 捕获异常并转成 Job 失败状态。

### 10.6 acp.scheduler.store

职责：

1. SQLite 保存任务索引。
2. 每任务保存 `job.json`。
3. 每任务保存 `state.json`。
4. 每任务保存 `events.jsonl`。

## 11. 调度器目标

### 11.1 JobStatus

```python
class JobStatus(str, Enum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
```

### 11.2 JobSpec

```python
@dataclass(frozen=True)
class JobSpec:
    workflow: str
    name: str
    input: dict[str, object]
    method: dict[str, object]
    resources: dict[str, object]
    output_dir: Path | None = None
    config_path: Path | None = None
    tags: list[str] = field(default_factory=list)
```

### 11.3 JobRecord

```python
@dataclass
class JobRecord:
    id: str
    spec: JobSpec
    status: JobStatus
    work_dir: Path
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    current_stage: str | None = None
    progress: float | None = None
    error: str | None = None
    pid: int | None = None
```

### 11.4 并发策略

MVP 默认：

```yaml
max_running_jobs: 1
max_gaussian_jobs: 1
max_xtb_jobs: 2
```

原因：

1. Gaussian 任务重，默认不并发。
2. xTB 可允许轻量并发。
3. WSL 与 Windows 文件系统交互下，过高并发容易导致 I/O 和 scratch 问题。

## 12. 状态与文件持久化

建议运行根目录：

```text
ACP_runs/
```

每个任务目录：

```text
ACP_runs/
└── 20260625_001_ethanol_conformer/
    ├── job.json
    ├── state.json
    ├── events.jsonl
    ├── stdout.log
    ├── stderr.log
    ├── inputs/
    ├── work/
    ├── results/
    └── report/
```

SQLite 表：

```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    workflow TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    work_dir TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    current_stage TEXT,
    progress REAL,
    error TEXT,
    pid INTEGER
);
```

## 13. CLI 修复目标

> **状态（2026-06-25）**：`acp run serve` **已实现**（`_handle_serve` 调用 `uvicorn.run("acp.api.server:app", ...)`），支持 `--host/--port/--reload`。原描述"当前是 placeholder"已过时。cli.py 头部注释仍误标 "API server (placeholder)"，需顺手修正。

目标命令：

```bash
acp run serve --host 127.0.0.1 --port 8765 --run-root ./ACP_runs
```

参数：

```text
--host
--port
--run-root
--no-browser
--reload
--log-level
```

行为：

1. 加载配置。
2. 创建 run root。
3. 启动 uvicorn。
4. 默认打开浏览器到 `http://127.0.0.1:8765`。
5. 在 WSL 中可通过 `explorer.exe` 或输出 URL 由用户手动打开。

WSL 中建议默认不强行自动打开浏览器，先打印：

```text
ACP Workbench is running:
  http://127.0.0.1:8765

If Windows cannot connect, try:
  wsl hostname -I
```

## 14. 工作流接入计划

### 14.1 Conformer

第一批接入，因为已有 `run_conformer_search`。

前端字段：

1. input type
2. input value/file
3. protocol
4. charge
5. multiplicity
6. nproc
7. mem
8. output directory

输出：

1. final ensemble
2. energy table
3. workflow state
4. logs

### 14.2 NMR

第二批接入，因为已有 NMR workflow。

前端字段：

1. conformer source
2. backend
3. temperature
4. energy window
5. max conformers
6. reference overrides

输出：

1. JSON report
2. XLSX report
3. selected conformers
4. parsed shielding data

### 14.3 Mechanism / TS

第三批接入，风险最高。

前端字段：

1. reactant file
2. product file
3. charge
4. multiplicity
5. TS guess strategy
6. TS backend
7. IRC backend
8. nproc
9. mem

TS guess 策略：

```text
xtb_pathfinder
gaussian_scan
linear_interpolate
```

重要约束：

1. xTB 应使用官方 `--path` path finder。
2. 不应使用未确认的 `xtb --neb` 命令。
3. Gaussian TS/IRC 应先补后端能力，再接入前端。

## 15. xTB / Gaussian 机理升级目标

### 15.1 xTB pathfinder

命令：

```bash
xtb reactant.xyz --path product.xyz --input path.inp
```

新增：

```text
XTBInterface.pathfinder()
XTBBackend.pathfinder()
```

返回：

1. TS guess coordinates
2. TS guess file
3. path files
4. path energies
5. stdout/stderr log

### 15.2 Gaussian TS

新增：

```text
GaussianInterface.transition_state_opt()
GaussianBackend.transition_state_opt()
```

route preset：

```text
Opt=(TS,CalcFC,NoEigenTest,MaxCycles=100)
```

### 15.3 Gaussian frequency validation

要求：

1. 提取所有频率。
2. 识别负频率。
3. 默认要求 exactly one imaginary frequency。
4. 将验证结果写入 metadata 和 report。

### 15.4 Gaussian IRC

新增：

```text
GaussianInterface.irc()
GaussianBackend.irc()
```

route preset：

```text
IRC=(Forward,CalcFC,MaxPoints=50)
IRC=(Reverse,CalcFC,MaxPoints=50)
```

解析要求：

1. 不使用 `lines.index(line)` 解析重复 orientation block。
2. 使用 indexed iteration。
3. 输出 `irc_profile.json`。

## 16. 前端任务表单设计

### 16.1 Conformer 表单

```text
Workflow: Conformer Search

Input:
  [SMILES/File]

Protocol:
  ext
  censo-zero
  censo-lite
  censo-full
  allopt
  reference-sp

Resources:
  nproc
  memory

Actions:
  Validate
  Submit Job
```

### 16.2 NMR 表单

```text
Workflow: NMR

Input:
  existing conformer job
  ensemble xyz

Options:
  backend
  temperature
  energy window
  max conformers
  references

Actions:
  Validate
  Submit Job
```

### 16.3 Mechanism 表单

```text
Workflow: Mechanism / TS

Reactant:
  file path

Product:
  file path

TS Guess:
  xTB pathfinder
  Gaussian scan
  linear interpolate

DFT:
  backend
  method
  basis

Validation:
  frequency
  IRC
  endpoint optimization

Actions:
  Validate
  Submit Job
```

## 17. 错误处理目标

前端应将错误分为四类：

1. Environment Error
   - ACP 未安装
   - FastAPI 未安装
   - WSL 服务未启动

2. Backend Error
   - Gaussian 不存在
   - xTB 不存在
   - ORCA 不存在
   - CREST 不存在

3. Input Error
   - 文件不存在
   - reactant/product 原子数不一致
   - charge/multiplicity 不合法

4. Calculation Error
   - Gaussian failed
   - TS 没有单虚频
   - IRC 未收敛
   - xTB pathfinder 未产生 TS guess

错误展示：

```text
失败阶段
核心错误信息
建议处理动作
相关日志链接
重试按钮
克隆配置按钮
```

## 18. 安全边界

MVP 只做本地服务：

1. 默认监听 `127.0.0.1`。
2. 不提供任意 shell 命令执行接口。
3. 文件读取限制在：
   - 项目目录
   - run root
   - 用户提交的输入路径
4. 前端不直接访问文件系统。
5. 后端负责路径 normalize 和校验。
6. 不开放公网访问。

如果需要局域网访问，必须显式配置 `--host 0.0.0.0`，并增加安全提示。

## 19. 测试计划

### 19.1 环境测试

1. WSL 中 `import acp` 成功。
2. WSL 中 `import fastapi` 成功。
3. `acp run serve` 可以启动。
4. Windows 浏览器打开 `http://127.0.0.1:8765`。
5. `curl http://127.0.0.1:8765/api/status` 返回 JSON。

### 19.2 API 单元测试

1. `/api/status`
2. `/api/backends`
3. `/api/workflows`
4. `/api/jobs`
5. `/api/jobs/{id}`
6. `/api/jobs/{id}/events`

### 19.3 Scheduler 测试

1. 创建任务。
2. 任务入队。
3. 任务运行。
4. 任务完成。
5. 任务失败。
6. 任务取消。
7. 任务重试。
8. 服务重启后任务仍可读取。

### 19.4 前端测试

1. 首页加载。
2. 后端状态渲染。
3. backend 卡片渲染。
4. 新建任务表单校验。
5. 队列更新。
6. 日志流更新。
7. 结果文件列表显示。

### 19.5 计算工作流测试

默认使用 fake binaries 或 mock backend：

1. fake xTB 输出 path result。
2. fake Gaussian 输出 TS log。
3. fake Gaussian 输出 frequency log。
4. fake Gaussian 输出 IRC log。

真实外部程序测试用 marker：

```python
@pytest.mark.requires_xtb
@pytest.mark.requires_gaussian
@pytest.mark.requires_crest
@pytest.mark.requires_orca
```

## 20. 里程碑计划

### M0：环境与诊断修复 — ✅ 基本完成

> **复核结论**：ACP 可 import、fastapi 0.138.0/uvicorn 0.49.0 已装、`import acp` 通过。**唯一残留**：`pyproject.toml` 未声明 `api` optional dep（见 §6.1），导致 `pip install -e .` 不带 web 依赖。完成 §6.1 即关闭 M0。

目标：

1. WSL 中创建 `.venv`。
2. 安装 ACP。
3. 安装 API 依赖。
4. 明确 Windows 到 WSL 的访问方式。

验收：

```bash
python -c "import acp, fastapi, uvicorn"
```

通过。

预计：0.5 天。

### M1：后端服务骨架 — ✅ 基本完成

> **复核结论**：`src/acp/api/server.py`(FastAPI app + 静态托管)、`routes.py`(`/api/status`、`/api/backends`)、`schemas.py` 均已存在；`_handle_serve` 真实启动 uvicorn。**残留**：(1) `/api/status` 响应缺 `host/port/environment/queue` 字段；(2) `/api/backends` capabilities 为扁平 list，需升级为 `{capability: status}` dict；(3) cli.py:8 注释 "serve (placeholder)" 需改正；(4) 无 api 测试。这些归入 M3 收尾。

目标：

1. 新增 `src/acp/api/server.py`。
2. 实现 FastAPI app。
3. 实现 `/api/status`。
4. 实现静态前端托管。
5. 修复 `acp run serve`。

验收：

```bash
acp run serve --host 127.0.0.1 --port 8765
curl http://127.0.0.1:8765/api/status
```

返回 JSON。

预计：1 天。

### M2：前端 MVP — 🟡 约 80% 完成

> **复核结论**：`frontend/ACP_Workbench.html`(164行) 已是 dark 主题 Dashboard，含 Backends 表、Job Queue 表、Log 面板，已轮询 `/api/status`、`/api/backends`。视觉风格已决定保留 dark（§7.2）。**残留**：(1) Job Queue 表无数据源（后端无 `/api/jobs`，待 M4）；(2) "Submit Job" 按钮未接通；(3) Workflow select 仅 conformer/nmr。这些随 M4/M5/M6 自然闭合，无需单独重做 M2。

目标：

1. `ACP_Workbench.html`。
2. Dashboard。
3. backend status。
4. New Job 静态表单。
5. Queue 静态布局。

验收：

Windows 浏览器打开：

```text
http://127.0.0.1:8765
```

能看到 ACP Workbench。

预计：1-2 天。

### M3：后端能力发现

目标：

1. 实现 `/api/backends`。
2. 对接 `acp.backends.capabilities`。
3. 检测 Gaussian/xTB/CREST/ORCA 可用性。
4. 前端显示状态。

验收：

前端能显示：

```text
Gaussian available/missing
xTB available/missing
CREST available/missing
ORCA available/missing
```

预计：1 天。

### M4：Scheduler Core

目标：

1. 新增 `acp.scheduler`。
2. JobSpec / JobRecord / JobStatus。
3. SQLite store。
4. JobManager。
5. fake runner。
6. `/api/jobs`。

验收：

前端可以创建 fake job，队列状态从 queued 到 completed。

预计：2 天。

### M5：实时日志与事件

目标：

1. `events.jsonl`。
2. SSE endpoint。
3. 前端日志面板。
4. stage timeline。

验收：

前端无需刷新即可看到 stage 和 log 更新。

预计：1-2 天。

### M6：接入 Conformer

目标：

1. scheduler runner 调用 `run_conformer_search`。
2. 前端提交 conformer job。
3. 展示 final ensemble。
4. 展示日志和结果文件。

验收：

SMILES 输入可以通过前端跑完构象搜索。

预计：1-2 天。

### M7：接入 NMR

目标：

1. 前端 NMR 表单。
2. scheduler runner 调用 `run_nmr_calculation`。
3. 展示 JSON/XLSX report。

验收：

从构象结果发起 NMR job 并生成报告。

预计：1-2 天。

### M8：接入 xTB pathfinder

目标：

1. `XTBInterface.pathfinder`。
2. `XTBBackend.pathfinder`。
3. capability matrix 增加 `pathfinder`。
4. fake xTB 测试。

验收：

命令使用：

```bash
xtb reactant.xyz --path product.xyz --input path.inp
```

不是 `--neb`。

预计：1-2 天。

### M9：接入 Gaussian TS/IRC

目标：

1. `GaussianInterface.transition_state_opt`。
2. `GaussianBackend.transition_state_opt`。
3. `GaussianInterface.irc`。
4. `GaussianBackend.irc`。
5. imaginary frequency metadata。
6. IRC parser。

验收：

mock Gaussian log 可完成 TS + freq + IRC 解析。

预计：2-3 天。

### M10：Mechanism 前端与工作流

目标：

1. `src/acp/workflows/mechanism.py`。
2. mechanism 表单。
3. reactant/product 输入。
4. TS guess。
5. TS opt。
6. frequency verify。
7. IRC。
8. reaction profile。

验收：

mock mechanism job 端到端跑完，前端显示 profile 和报告。

预计：3-4 天。

### M11：结果中心

目标：

1. completed job 搜索。
2. result file manifest。
3. report 下载。
4. ZIP 导出。

验收：

前端能按任务查看所有结果文件和报告。

预计：1-2 天。

## 21. 总体时间估算

> **更新（2026-06-25）**：M0-M2 已基本完成（原估 3.5-5 天的量已落地）。净剩余工作从 M3 收尾起算。

```text
M0-M3: 3.5-5 天   → 实际净剩约 1-1.5 天（仅 M3 收尾 + pyproject api dep）
M4-M5: 3-4 天
M6-M7: 2-4 天
M8-M10: 6-9 天
M11: 1-2 天

净剩余总计：约 12-19 天（原 15.5-24 天扣除已完成的 M0-M2）
```

如果只做“能打开网页 + 能看状态 + fake job 队列”，约 4-6 天。

如果做到“完整 conformer/NMR/mechanism 前端调度”，约 3-5 周更稳妥。

## 22. 最小可交付版本

最小可交付版本不包含真实计算调度，只打通通信链路：

1. WSL 中 `acp run serve` 可以启动。
2. Windows 浏览器打开 ACP Workbench。
3. `/api/status` 正常。
4. `/api/backends` 正常。
5. 前端显示环境和后端状态。

完成后，当前截图中的 `ERR_CONNECTION_REFUSED` 应消失。

## 23. 第一阶段建议立即实施内容

> **状态（2026-06-25）**：下方第 2、3、4 项（server.py / 前端 / cli serve）**已完成**。第 1 项（pyproject api dep）仍待做。第 5、6 项（api/cli 测试）仍待做。本节保留下方清单作为"第一阶段收尾"的 checklist——即 §26 步骤 0 + 7.5。

第一阶段 PR/变更范围：

1. `pyproject.toml`（**待做**）
   - 增加 `api` optional dependencies。

2. `src/acp/api/server.py`（**已完成**）
   - FastAPI app。
   - `/api/status`。
   - 静态前端托管。

3. `frontend/ACP_Workbench.html`（**已完成**）
   - 最小 Dashboard。
   - 调用 `/api/status`。

4. `src/acp/cli.py`（**已完成**）
   - 为 `run serve` 增加真实参数。
   - 启动 uvicorn。

5. `tests/test_acp_api.py`（**待做**）
   - 测试 `/api/status`。

6. `tests/test_acp_cli.py`（**待做**）
   - 更新 `run serve` placeholder 测试。

## 24. 完成标准

最终 ACP 前端目标完成时，应满足：

1. Windows 浏览器访问 `http://127.0.0.1:8765` 可打开 ACP Workbench。
2. WSL 中运行的 ACP 后端可以稳定响应 API。
3. 前端不依赖 `file://`。
4. 前端不硬编码 WSL IP。
5. 后端能够发现 Gaussian/xTB/CREST/ORCA 状态。
6. 用户可以从前端创建计算任务。
7. 任务进入统一队列。
8. 任务状态可持久化。
9. 日志可实时查看。
10. 失败任务有错误摘要。
11. 成功任务有结果文件入口。
12. Conformer/NMR/Mechanism 三类核心工作流均可通过前端发起。
13. xTB pathfinder 使用官方 `--path`。
14. Gaussian TS/IRC 通过后端能力系统接入。
15. 所有不依赖商业软件的测试可在普通环境通过。

## 25. 与现有 xTBridge 计划的关系

已有文档：

```text
docs/xTBridge_Implementation_Plan.md
```

该文档偏系统架构和后端调度细节。

本文档：

```text
docs/ACP_Frontend_Target_Implementation_Plan.md
```

偏前端目标完成、WSL 通信修复、端到端交付路线。

二者关系：

1. 本文档作为前端目标完成总计划。
2. xTBridge 文档作为调度器和后端 API 的细化实施参考。
3. 两份文档的关键一致点是：
   - FastAPI 本地服务
   - 前端由后端托管
   - Scheduler 统一调度
   - WSL 通过 localhost 或 WSL IP 通信
   - xTB 使用 `--path` 而不是 `--neb`

## 26. 推荐执行顺序

> **更新（2026-06-25）**：步骤 1-7 已基本完成（serve/api 骨架/前端/status/backends 均已落地）。实际从步骤 0（补 pyproject api dep）+ 步骤 7.5（status/backends schema 对齐）起算，然后进入步骤 8。

```text
0. pyproject api optional dep 声明（M0 残留，必做）
1. ~~WSL 环境修复~~ ✅
2. ~~api optional dependencies~~ → 步骤 0
3. ~~FastAPI server skeleton~~ ✅
4. ~~acp run serve~~ ✅
5. ~~ACP_Workbench.html~~ ✅
6. ~~/api/status~~ ✅（schema 需对齐文档，归入 7.5）
7. ~~/api/backends~~ ✅（capabilities 需升级为 dict，归入 7.5）
7.5. status/backends schema 对齐 + cli 注释修正 + api 测试（M3 收尾）
8. Scheduler fake job（M4，真正的新起点）
9. SSE logs（M5）
10. Conformer integration（M6）
11. NMR integration（M7）
12. xTB pathfinder（M8）
13. Gaussian TS/IRC（M9）
14. Mechanism workflow（M10）
15. Result center（M11）
```

这样可以最快解除“后端无法打开”的阻塞，同时避免一开始就把高风险的机理化学流程和前端基础设施绑在一起。
