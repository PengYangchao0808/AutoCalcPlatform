# ACP 功能全览与真实计算任务测试手册

> **Functional Test Manual — Real Computation Task Submission (Manual & Automated)**
>
> 适用范围：ACP (Auto-Calc Platform) 全部**可提交**功能，用于**真实提交 QC 计算任务**的手工测试与自动化测试。
>
> 基准版本：`main` @ `b87a86a`（2026-09-30）。文档中所有 CLI 参数、状态码、产物路径均以 `src/acp/` 当前代码为准（当本手册与 `README.md` / `docs/ACP_Job_File_Layout_Spec.md` 冲突时，以本手册标注的代码行为为准，差异见 §8.3）。
>
> 维护：新增 active 工作流 / 状态 / 端点时必须同步更新本手册，否则视为回归。

---

## 0. 阅读指南

| 章节 | 内容 | 适用读者 |
|------|------|----------|
| §1 | 环境与前置条件（安装、QC 可执行文件、run_root、启动服务） | 首次搭建测试环境 |
| §2 | 功能总览矩阵（14 个 active 工作流 + 提交渠道 + 依赖） | 快速索引 |
| §3 | **手工测试用例（真实提交计算任务，逐工作流）** | 手工测试执行者 |
| §4 | 调度器 / API / Web 提交与任务生命周期 | 集成 / 端到端测试 |
| §5 | 结果产物与验证方法 | 判定测试通过与否 |
| §6 | 自动化测试（pytest 套件映射 + 命令） | 回归 / CI |
| §7 | 负向与错误用例 | 边界测试 |
| §8 | 附录（参数速查、端点速查、错误码、已知漂移/坑） | 参考 |

**测试用例编号规范**：`MT-<工作流缩写>-<序号>`（手工 Manual Test）；`AT-`（自动化 Automation Test）；`NT-`（负向 Negative Test）。

**通用判定原则（real run）**：

1. 进程退出码 = `0`（CLI）；调度器任务最终状态 = `completed`。
2. `RESULT/result_manifest.json` 存在且可被读取器解析（`load_result_manifest` 不返回 `None`）。
3. 每个 `products[].path`（相对 `RESULT/`）在磁盘上真实存在。
4. 关键产物（如 `confsearch_manifest.json` / `pes_profile.json` / `nmr_report.json`）schema 字段完整（见 §5.2）。
5. `metrics.json` 仅用于展示，**不得**作为通过/失败判据。

---

## 1. 测试环境与前置条件

### 1.1 Python 依赖安装

```bash
cd /ACPServer/AutoCalcPlatform

# 最小运行
pip install -e .

# 测试环境（推荐：覆盖 CI 所需的全部 extras）
pip install -e '.[dev,api,remote]'

# NMR Bruker 原始数据处理（--bruker 路径需要）
pip install -e '.[nmr]'

# 真实远程 LSF 测试需要 paramiko
pip install -e '.[remote]'
```

> **注意**：本机若已存在旧版 `acp 0.1.3` site-packages，直接 `pytest` 可能收集报错。测试时统一使用仓库源码：
> `PYTHONPATH=src python3.11 -m pytest ...`（见 §6.1）。

### 1.2 QC 可执行文件（真实计算任务的硬前置）

ACP 通过 `cccp.software.resolve_executable()` 解析可执行文件，优先级：
**配置 `executables.<tool>.path` → 环境变量 `CONFSEARCH_<TOOL>_PATH` → PATH + Python 环境 → 遗留回退**。

| 二进制 | 用途 | 配置键 | 环境变量 |
|--------|------|--------|----------|
| `orca` | SP / OPT / FREQ / TS / IRC / scan / GIAO-NMR | `executables.orca.path` | `CONFSEARCH_ORCA_PATH` |
| `crest` | 构象搜索 | `executables.crest.path` | `CONFSEARCH_CREST_PATH` |
| `xtb` | 半经验优化 / MD 采样 | `executables.xtb.path` | `CONFSEARCH_XTB_PATH` |
| `isostat` | 构象去重 / 聚类 | `executables.isostat.path` | `CONFSEARCH_ISOSTAT_PATH` |
| `censo` | 构象排序筛选 | `executables.censo.path` | `CONFSEARCH_CENSO_PATH` |
| `shermo` | 热力学修正（`opt_freq_sp_thermo`） | `executables.shermo.path` | `CONFSEARCH_SHERMO_PATH` |
| `molclus` | xTB-MD + Molclus 管线 | `executables.molclus.path` | `CONFSEARCH_MOLCLUS_PATH` |

**环境自检命令**：

```bash
for b in orca crest xtb censo isostat shermo molclus; do
  printf "%-10s: " "$b"; command -v "$b" || echo "NOT FOUND"
done

# 配置体检（含远程节点）
acp init            # 交互式：探测本地软件 + 写入 ~/.cccp.yaml + 配置远程节点
acp doctor          # 检查已配置远程节点可达性/Python/二进制；任一缺失 → exit 1
```

> **本机实测（2026-09-30）**：`orca=/usr/bin/orca`、`crest`、`xtb`、`censo` 存在；`isostat`、`shermo`、`molclus` **未安装**。
> 因此本机可完整跑通 **simple 组 / scan / irc（ORCA 段）/ tsmode（ORCA 段）/ nmr（ORCA GIAO 段）**；
> 而 **Confsearch 全协议**（依赖 `crest,xtb,isostat,censo,orca`）与 **BatchOptimize 的 `opt_freq_sp_thermo`**（依赖 `shermo`）需先补齐二进制。
> 测试前务必用上表命令确认，勿把「缺二进制」误判为产品缺陷。

### 1.3 配置来源与合并顺序

合并顺序（后者覆盖前者）：

1. Python 内置默认值 `_get_default_config()`（**唯一权威源**）
2. `~/.cccp.yaml`（用户级；回退读取 `~/.conformer_search.yaml`）
3. `./cccp.yaml`（项目级）
4. `--config <file>`
5. `CONFSEARCH_*` 环境变量
6. CLI 参数（`--nproc`、`--mem` 等）

> ⚠️ `config/defaults.yaml` 仅供参考，**不是**权威默认值。

```bash
# 导出某一工作流的有效配置，用于核对参数合并结果
acp run singlepoint --input water.xyz --save-config /tmp/effective.yaml
```

### 1.4 数据目录 run_root（两层目录约定）

**安装目录 ≠ 数据目录**。任务产物（`WORK/`、`RESULT/`、QC 中间文件）与 `acp_jobs.db` 全部落在 run_root，必须位于**原生文件系统**（禁止 9p/NFS/CIFS，QC 子进程 I/O 在 9p 上慢约 1000×）。

解析优先级：CLI `--run-root`（**仅 `acp run serve` 有此参数**）> `ACP_RUN_ROOT` 环境变量 > 平台默认：

- root → `/var/lib/acp/runs`
- 普通用户 → `$XDG_DATA_HOME/acp/runs` 或 `~/.local/share/acp/runs`

```bash
# 测试隔离：为每次测试指定独立 run_root（强烈推荐）
export ACP_RUN_ROOT=/tmp/acp_test_runs
acp run serve   # 启动时打印生效 run_root；慢文件系统/与安装目录重叠/空间不足时告警（仅告警不阻断）
```

### 1.5 启动 Web 服务（调度器 / API 测试的前置）

```bash
acp run serve --host 127.0.0.1 --port 8765 --run-root "$ACP_RUN_ROOT"
# 产物目录查看：GET /api/status；前端：http://127.0.0.1:8765
```

`serve` 关键参数：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--host` | `127.0.0.1` | LAN 访问用 `0.0.0.0` |
| `--port` | `8765` | |
| `--run-root` | None | 覆盖 run_root（见 §1.4） |
| `--poll-interval` | `30` | 轮询间隔秒 |
| `--execution-mode` | None | 服务器默认执行目标 `local`/`remote` |
| `--max-running` | `1` | **已废弃**（no-op），用 `--poll-interval` 调度 |
| `--reload` | False | 开发热重载 |
| `--no-browser` | False | 不自动打开浏览器 |

systemd 部署：`sudo systemctl restart acp`（**代码改动后必须重启**，服务未启用 `--reload`）。

### 1.6 测试分层（关键认知）

| 层级 | 覆盖内容 | 是否需要真实 QC 二进制 |
|------|----------|------------------------|
| 单元 / 契约测试 | 数据模型、解析器、清单读写、状态机 | 否（mock） |
| 工作流管线测试 | 14 个 active 工作流的**编排逻辑** | 否（`FakeBackend` / `subprocess` patch） |
| 真实二进制冒烟 | 二进制存在 + `--version` | 是（仅 5 个 marker 门控用例，见 §6.4） |
| **真实计算任务端到端** | 真实 CREST/xTB/CENSO/ORCA 全链路 | **是 — 目前仅手工覆盖，自动化为缺口** |

> **结论**：现有自动化测试**不覆盖任何真实 QC 全链路**。本手册 §3 的手工用例是真实提交计算任务的**唯一系统化方案**；§6.5 给出补齐自动化的建议。

---

## 2. 功能总览矩阵

### 2.1 Active 工作流（14 个，可提交）

| # | workflow id | CLI 子命令 | 类别 | 依赖二进制 | 调度器支持 | 远程支持 |
|---|-------------|-----------|------|-----------|-----------|---------|
| 1 | `singlepoint` | `acp run singlepoint` | simple | orca | ✅ | ✅ |
| 2 | `optimize` | `acp run optimize` | simple | orca | ✅ | ✅ |
| 3 | `frequency` | `acp run frequency` | simple | orca | ✅ | ✅ |
| 4 | `scan` | `acp run scan` | simple | orca | ✅ | ✅ |
| 5 | `irc` | `acp run irc` | simple | orca | ✅ | ✅ |
| 6 | `tsmode` | `acp run tsmode` | simple | orca（+ 频率源） | ✅ | ✅ |
| 7 | `casscf` | `acp run casscf` | simple | orca | ✅ | ❌ **本地专用**（不在远端允许集） |
| 8 | `xtb_optimize` | `acp run xtb_optimize` | simple | xtb | ✅ | ✅ |
| 9 | `nmr` | `acp run nmr` | preset | crest, censo, orca | ✅ | ✅ |
| 10 | `Confsearch` | `acp run Confsearch` | preset | crest, xtb, isostat, censo, orca | ✅ | ✅ |
| 11 | `PESsearch` | `acp run PESsearch` | preset | orca, xtb | ✅ | ✅ |
| 12 | `BatchOptimize` | `acp run BatchOptimize` | preset | orca, shermo（shermo 仅 `opt_freq_sp_thermo` 使用） | ✅ | ✅ |
| 13 | `XtbPathSearch` | `acp run XtbPathSearch --path-config <json>` | preset（pes2ts 冻结 payload） | xtb | ✅ | ❌ **本地专用**（不在 `_ALLOWED_REMOTE_WORKFLOWS`） |
| 14 | `OrcaGradient` | `acp run OrcaGradient --gradient-config <json>` | preset（pes2ts 冻结 payload） | orca | ✅ | ❌ **本地专用**（不在 `_ALLOWED_REMOTE_WORKFLOWS`） |

> **CLI 拼写注意**：xTB 优化的真实子命令是 **`xtb_optimize`（下划线）**，`xtb-optimize` 写法不可用。
> **XtbPathSearch / OrcaGradient**（2026-10 起 active）：消费冻结 `pes2ts_xtb_path_request_v1` / `pes2ts_orca_gradient_request_v1` payload（PES2TS → ACP 执行统一）；调度器可提交（stage_tasks 已登记），远程不可提交。执行核心在 `cccp/calculation/tasks/{xtb_path_search,orca_gradient}.py`。

### 2.2 CLI 顶级语法

```
acp {run, doctor, init} ...
acp run {Confsearch|PESsearch|BatchOptimize|XtbPathSearch|OrcaGradient|irc|scan|tsmode|nmr|
         singlepoint|optimize|frequency|xtb_optimize|casscf|serve|fake}
```

- 无全局 `--output` / `--run-root`：`--output` 由各工作流自带，`--run-root` 仅 `serve` 有。
- **字符串 `choices=` 全部大小写不敏感**（解析器构建后统一注入 `make_case_insensitive_type`），例如 `--protocol XTB-CREST` → 规范化为 `xtb-crest`，`--opt-convergence VERYTIGHT` → `verytight`。自由文本（method/basis/路径/溶剂名）不做折叠。

### 2.3 已退役工作流（14 个，不可提交，仅历史作业只读展示）

`optfreq`, `optfreqsp`, `conformer`, `benchmark`, `ensemble`, `energy`, `xtbmd_censo_energy`, `mechanism`, `mech-conf`, `mech-step`, `mech-confirm`, `mech-chain`, `Lowconfirm`, `Highconfirm`。

- **API 提交** → HTTP **400** `Unsupported workflow '<id>'. Supported: [...]`。
- **CLI**（12 个有解析器，`_CLI_REMOVED_WORKFLOWS`）→ **exit 2**，双语退役提示（见 §7 NT-01）；`conformer`/`benchmark` 无 CLI 入口。
- 退役映射：`ensemble`→`Confsearch --protocol censo-crest --refinement-policy screen`；`energy`→`Confsearch --protocol censo-crest --refinement-policy rank1|cumulative-99`；`xtbmd_censo_energy`→`Confsearch --protocol xtbmd-censo`；`mechanism/mech-*`→`PESsearch`+`BatchOptimize`+`irc`；`Lowconfirm`→`BatchOptimize --profile opt_freq`+`irc`；`Highconfirm`→`BatchOptimize --profile opt_freq_sp_thermo`+`irc`；`optfreq`/`optfreqsp`→ simple/BatchOptimize。

### 2.4 Confsearch 协议 × 精修策略矩阵

- 协议 `--protocol`：`xtb-crest` | `xtb-md` | `censo-crest`（默认） | `xtbmd-censo`
- 档位 `--profile`：`light` | `default`（默认） | `high`
- 精修策略 `--refinement-policy`：`screen`（默认） | `rank1` | `cumulative-99` | `all`
- 后端 `--backend`：仅 `native`（`rph-parity` 已退役，传其它值报错）
- CENSO 协议专属：`--preset censo-light|censo-default|censo-zero`
- 纯 xTB 协议（`xtb-crest`/`xtb-md`）配非 `screen` 策略 → 仅告警，按 `screen` 处理。

---

## 3. 手工测试用例（真实提交计算任务）

> 通用约定：下文所有 `RESULT/...` / `WORK/...` 路径**相对于本次运行的输出目录**。
> CLI 非调度器运行通常产出在 `<output>/<molecule_safe_name>/`；调度器任务为**扁平布局**（产物直接位于任务根目录）。
> 以命令实际打印的输出目录 / `GET /api/v1/jobs/{id}/detail` 的 `work_dir` 为准。

### 3.0 准备测试用输入文件

```bash
mkdir -p /tmp/acp_mt && cd /tmp/acp_mt

# 水分子（singlepoint/optimize/frequency/xtb_optimize/scan 通用）
cat > water.xyz <<'EOF'
3
water
O    0.000000    0.000000    0.117300
H    0.000000    0.757200   -0.469200
H    0.000000   -0.757200   -0.469200
EOF

# 批量优化输入（TAG 注释驱动角色识别）
cat > structures.xyz <<'EOF'
3
TAG: INT | candidate_id=int_01
O    0.000000    0.000000    0.117300
H    0.000000    0.757200   -0.469200
H    0.000000   -0.757200   -0.469200
3
TAG: TS | candidate_id=ts_01
O    0.000000    0.000000    0.117300
H    0.000000    0.957200   -0.369200
H    0.000000   -0.657200   -0.569200
EOF

# NMR 实验谱（格式见 §3.12）
cat > exp_spectrum.txt <<'EOF'
C: 167.33(C1), 59.58(C2)
H: 4.81(H4), 7.18(H5), 3.09(H6)
EQ: C10,C12
OMIT: H19
EOF
```

> `TAG` 别名：TS ⇐ `ts/tst/ts_seed/transition_state/...`；INT ⇐ `int/int_seed/minimum/intermediate/...`（大小写不敏感，折叠为 `TS`/`INT`）。

---

### 3.1 MT-SP — Single Point Energy

- **目的**：验证 ORCA 单点能真实计算与结果清单写入。
- **前置**：`orca` 可用。
- **步骤**：

```bash
acp run singlepoint --input water.xyz --method r2SCAN-3c --output /tmp/acp_mt/sp --nproc 4
# 或 SMILES：acp run singlepoint --input "O" --method wB97X-D4 --basis def2-TZVPPD
```

- **预期产物**（`<out>/RESULT/`）：
  - `result_manifest.json`（products：`structure` / `energy_report`）
  - `WORK/05_SP/...`（ORCA 原始输出）
  - `WORK/00_RUNTIME/checkpoint.json`
- **判定**：exit 0；`energy_report` 产品存在；ORCA 输出含正常 termination。
- **自动化**：`tests/test_acp_workflows_simple.py`、`tests/test_orca_*.py`（mock + CLI `--help` 冒烟，**不跑真实 ORCA**）。

### 3.2 MT-OPT — Geometry Optimization

```bash
acp run optimize --input water.xyz --method r2SCAN-3c \
    --opt-convergence Tight --geom-maxiter 100 --calc-hess auto \
    --output /tmp/acp_mt/opt --nproc 4
```

- **预期产物**：`RESULT/result_manifest.json`；`WORK/03_OPT/<step>/optimization_trajectory.json`（cycles/scf_energies/gradients_rms/阈值判定）。
- **判定**：exit 0；优化轨迹 `converged=true`（或达到 MaxIter 但无错误）。
- **ORCA 坑**：`%geom` 关键字必须是 `Trust`（**不是** `TrustRadius`，后者会导致输入解析失败）。`--calc-hess 0` 被显式拒绝（用 `--no-calc-hess`）。
- **自动化**：`tests/test_acp_workflows_simple.py`、`tests/test_orca_opt_controls.py`。

### 3.3 MT-FREQ — Frequency

```bash
acp run frequency --input water.xyz --method r2SCAN-3c --output /tmp/acp_mt/freq --nproc 4
```

- **预期产物**：`RESULT/result_manifest.json`（`frequency_modes` 产品）；`WORK/04_FREQ/<step>/normal_modes.json`；`RESULT/frequencies/normal_modes.json`。
- **判定**：exit 0；`normal_modes_v1` 含 `frequencies` / `modes[].frequency_cm1`；水分子应 0 个虚频。TS 场景恰好 1 个负频（`imaginary:true`）。
- **自动化**：`tests/test_acp_frequency_modes.py`、`tests/test_acp_structure_viewer.py`（normal_modes_v1）。

### 3.4 MT-SCAN — Relaxed Coordinate Scan

```bash
# 注意：--coordinate 的原子索引是 0-based
acp run scan --input "CCO" --coordinate 0,2,1.0,3.0 --scan-points 21 \
    --method r2SCAN-3c --output /tmp/acp_mt/scan --nproc 4
# 多坐标耦合（可重复 --coordinate）
acp run scan --input "CCO" --coordinate 0,2,1.0,3.0 --coordinate 2,3,1.0,2.0
```

- **预期产物**：`RESULT/trajectories/scan_trajectory.json`（`frames[{index, path, energy_hartree, ...}]`）；`RESULT/structures/scan_frame_NNN.xyz`；`RESULT/result_manifest.json`（`scan_trajectory` + `scan_frame_*`）。
- **判定**：exit 0；帧数与 `--scan-points` 一致；能量曲线可被 `energy-graph?view=scan` 渲染。
- **自动化**：`tests/test_scan_workflow.py`、`tests/test_acp_pes_dft_scan.py`（mock）。

### 3.5 MT-XTB — xTB Optimization

```bash
acp run xtb_optimize --input water.xyz --gfn 2 --opt-level tight \
    --solvent water --solvent-model gbsa --output /tmp/acp_mt/xtb
```

- **预期产物**：`RESULT/result_manifest.json`（structure/energy）；`WORK/03_OPT/...`。
- **判定**：exit 0；xTB 正常收敛。
- **自动化**：`tests/test_acp_workflows_simple.py`（`run_xtb_optimize` GFN 映射 + 失败路径）。

### 3.6 MT-CAS — CASSCF / NEVPT2（本地专用，进阶）

```bash
acp run casscf --input water.xyz --active-electrons 2 --active-orbitals 2 \
    --nroots 1 --dynamic-correlation none --output /tmp/acp_mt/casscf --nproc 4
# 可选：--nroots >1（SA-CASSCF）--dynamic-correlation sc_nevpt2|fic_nevpt2
```

- **预期产物**：`WORK/08_CASSCF/...`；`RESULT/result_manifest.json`（`multireference_report` / `active_space` 等产品）。
- **判定**：exit 0。
- **远程限制**：`casscf` **不在** `_ALLOWED_REMOTE_WORKFLOWS` 内；调度器远端提交会 `ValueError`。仅测本地。
- **自动化**：catalog/CLI 层覆盖；真实 CASSCF 端到端为缺口。

### 3.7 MT-CS — Confsearch（统一构象搜索，4 协议）

- **前置**：`crest` + `xtb` + `isostat` + `censo` + `orca` 全部可用（本机缺 `isostat`，需先补齐）。

```bash
# 协议 1：xtb-crest（CREST 搜索 + DFT 精修）
acp run Confsearch --input "CCO" --protocol xtb-crest --profile light \
    --refinement-policy screen --output /tmp/acp_mt/cs_xtbcrest --nproc 4

# 协议 2：xtb-md（xTB-MD 采样）
acp run Confsearch --input "CCO" --protocol xtb-md --profile light \
    --refinement-policy screen --output /tmp/acp_mt/cs_xtbmd

# 协议 3：censo-crest（CREST + CENSO 排序）
acp run Confsearch --input "CCO" --protocol censo-crest --profile light \
    --preset censo-light --refinement-policy rank1 --output /tmp/acp_mt/cs_censo

# 协议 4：xtbmd-censo（xTB-MD + ISOSTAT + CENSO）
acp run Confsearch --input "CCO" --protocol xtbmd-censo --profile light \
    --refinement-policy cumulative-99 --output /tmp/acp_mt/cs_xtbmd_censo
```

- **预期产物**（`RESULT/confsearch/`）：
  - `confsearch_manifest.json`（**S1 交接产物**，schema `confsearch_v1`）
  - `quality_gates.json`、`ensemble.xyz`、`ensemble.csv`、`energies.json`、`boltzmann.json`
  - `conformers/conf_NNNN.xyz`、`final_report.json`、`final_conformers.xyz`
  - `sampling_history.json`（仅 `xtb-md` / `xtbmd-censo`）
  - `RESULT/result_manifest.json`（`confsearch_final_report` / `confsearch_final_conformers`）
  - 原始输出：`WORK/02_SEARCH/{xTB,CREST,ISOSTAT,CENSO}/...`、`WORK/03_OPT/...`
- **判定**：exit 0；`confsearch_manifest.json.conformers[]` 非空且含 `relative_energy_kcal` 与 `boltzmann_weight`；`quality_gates` 通过；几何引用路径可解析。
- **profile 覆盖值核对**（`confsearch/profiles.py`）：如 `xtb-crest/light` → `ewin=6.0`；`xtbmd-censo/high` → `md_time_ps=200, md_seeds=3, max_frames=800, preset=censo-default`。
- **自动化**：`tests/test_acp_confsearch*.py`、`tests/test_acp_workflows_energy.py`、`tests/test_xtbmd_md.py`、`tests/test_acp_censo_p5_acceptance.py`（**全部 mock**）。

### 3.8 MT-PES — PESsearch（势能面搜索）

- **前置**：一个已完成的 Confsearch 任务（含 `RESULT/confsearch/confsearch_manifest.json`），或直接提供 XYZ。

```bash
# 形式 A：从 Confsearch 任务（调度器场景推荐）
acp run PESsearch --from-job <Confsearch_job_id> --output /tmp/acp_mt/pes
# 形式 B：从 artifact 相对路径
acp run PESsearch --from-artifact RESULT/confsearch/confsearch_manifest.json \
    --output /tmp/acp_mt/pes
# 形式 C：键长扫描（bond_length_scan，默认模式）
acp run PESsearch --mode bond_length_scan --scan-atoms "0,1" \
    --scan-start 1.0 --scan-end 3.0 --scan-points 21 \
    --scan-kind distance --selection-kind bond_stretch \
    --sp-method B97-3c --output /tmp/acp_mt/pes_scan
```

- **输入校验（runtime，rc=2）**：必须提供 `--input` / `--from-artifact` / `--from-job` / `--scan-config` / `--xyz-text` 之一，否则报错退出。
- **预期产物**（`RESULT/pes_search/`）：
  - `pes_profile.json`（schema `pes_profile_v2`：能量曲线 + `ts_candidates` / `int_candidates` + frames + quality）
  - `pes_recommendations.json`（`pes_recommendations_v1`，**仅供审计**）
  - `RESULT/trajectories/pes_scan_trajectory.json`（运行中实时快照）
  - `RESULT/result_manifest.json`
  - 中间产物：`WORK/07_PATH/pes_scan_001/...`
- **人工确认链路**：能量查看器人工选点 → `POST /api/v1/jobs/{id}/pes/review` → 写 `pes_review.json` + 物化 `RESULT/structures/*.xyz` + 更新 manifest（见 §4.6 / §5.4）。
- **判定**：exit 0；`pes_profile.json` 含 `frames` 与候选；`/api/v1/jobs/{id}/s2/profile` 可读（远程任务见 §4.7）。
- **自动化**：`tests/test_pes_search.py`、`tests/test_acp_pes_dft_scan.py`、`tests/test_acp_api_pes_review.py`（mock）。
  **唯一真实 ORCA 用例**：`tests/test_pes_orca_simulscan_integration.py`（`--run-slow`）。

### 3.9 MT-BATCH — BatchOptimize（批量优化确认，4 profile）

- **前置**：`orca`；`opt_freq_sp_thermo` 另需 `shermo`（本机缺）。

```bash
# 从 PES 任务继承人工确认候选
acp run BatchOptimize --from-job <PES_job_id> --profile opt_freq_sp \
    --select pes_ts_frame_027 --output /tmp/acp_mt/batch --nproc 4

# 从 items 文件（TAG 驱动 INT/TS 角色）
acp run BatchOptimize --items-file structures.xyz --profile opt_freq_sp_thermo \
    --output /tmp/acp_mt/batch_thermo --temperature 298.15 --pressure 1.0
```

- **profile → 步骤**：`opt_only` = OPT；`opt_freq` = OPT+FREQ（**CLI 默认**）；`opt_freq_sp` = +SP；`opt_freq_sp_thermo` = +THERMO。
- **预期产物**：
  - `RESULT/structures/<item_id>__TAG_<TS|INT>__optimized.xyz`
  - `RESULT/frequencies/<item_id>__normal_modes.json`
  - `RESULT/state_comparison.json`（多态运行）、`RESULT/batch_provenance.json`
  - `RESULT/result_manifest.json`（per-item structure + frequency_modes + energy_report）
  - `WORK/00_RUNTIME/checkpoint.json`（`items_state` 断点续算）
- **判定**：exit 0；每 item 有优化结构 +（按 profile）频率/SP/热力学；TS item 频率恰 1 虚频。
- **注意**：BatchOptimize **不支持断点续算**（`continue` 返回 409，改用 `rerun`）。
- **自动化**：`tests/test_batch_optimize.py`（86 用例）、`tests/test_batch_optimize_advanced_config.py`、`tests/test_acp_api_batch_preview.py`。

### 3.10 MT-IRC — IRC（端点验证）

- **前置**：一个已验证的 TS 结构 + 溯源（provenance）。
- **标准驱动路径（推荐）**：由 BatchOptimize 的 TS 产物经调度器发起
  `POST /api/v1/jobs/{id}/artifacts/{artifact_id}/run-irc`。
- **手工 CLI 路径**（需 `--ts-provenance-json`，schema `irc_ts_source_v1`，含 `geometry_sha256` 等）：

```bash
acp run irc --input ts.xyz --ts-provenance-json prov.json \
    --direction both --maxpoints 100 --step 0.1 \
    --output /tmp/acp_mt/irc --nproc 4
```

- **校验逻辑（rc=2）**：provenance 必须是 `schema == "irc_ts_source_v1"` 的 JSON；输入 XYZ 的 sha256 必须等于 `geometry_sha256`；`method/basis/charge/multiplicity` 若提供必须与溯源一致。
- **预期产物**（`RESULT/irc/`）：`irc_report.json`、`irc_forward.xyz`、`irc_reverse.xyz`、`irc_<dir>_path.xyz`、`irc_<dir>_point_NNNN.xyz`；`RESULT/trajectories/irc_trajectory.json`（`irc_trajectory_v1`）；`RESULT/result_manifest.json`（`irc_report` + `irc_trajectory`）。
- **判定**：exit 0；两个端点连通性指纹 + 原子 RMSD 校验通过。
- **自动化**：`tests/test_irc.py`、`tests/test_acp_irc_trajectory.py`、`tests/test_acp_irc_projection.py`（mock/文件 fixture）。

### 3.11 MT-TS — TS Mode 定向 OptTS

- **前置**：一份**频率计算输出**（`.out` + `.hess`）作为源。
- **默认门禁（重要）**：`TS_MODE_MAPPING_VERIFIED_VERSIONS` 当前为**空集** → 默认拒绝启动，报 `mode_mapping_unsupported`。真实测试必须显式加 `--allow-unverified-mapping`（或请求 `require_verified_mapping=false`）。

```bash
# bundle.json 最小形状（引用频率源文件）
cat > bundle.json <<'EOF'
{"files": {"output": "freq.out", "hessian": "freq.hess"},
 "charge": 0, "multiplicity": 1,
 "level": {"method": "r2SCAN-3c", "basis": ""},
 "origin": {"kind": "files"}}
EOF

acp run tsmode --source-bundle bundle.json --source-mode-index 7 \
    --allow-unverified-mapping --retry-limit 2 \
    --output /tmp/acp_mt/tsmode --nproc 4
```

- **预期产物**：
  - `INPUT/tsmode/{source.xyz,source.hess,source_modes.json,source_bundle.json}`（哈希快照）
  - `WORK/tsmode/optimize/attempt_001/`、`WORK/tsmode/frequency/`、`WORK/tsmode/tsmode_checkpoint.json`
  - `RESULT/tsmode/{target_resolution.json,tsmode_report.json,optimized.xyz,normal_modes.json}`
  - `RESULT/result_manifest.json`（`tsmode_target_resolution` / `tsmode_optimized` / `tsmode_normal_modes` / `tsmode_report`）
- **判定**：exit 0；`tsmode_report.json.mapping.status == "resolved"`（或明确说明）；最终频率恰 1 虚频。
- **错误码**（HTTP 422/409，plan §7.3）：`hessian_missing`、`frequency_source_incomplete`、`source_geometry_mismatch`、`target_mode_invalid`、`mode_mapping_ambiguous`、`mode_mapping_unsupported`、`source_revision_conflict`(409)、`source_fetch_pending`(409)。
- **自动化**：`tests/test_acp_tsmode_engine.py`、`..._mapping.py`、`..._source.py`、`..._orca_inputs.py`、`tests/test_acp_api_tsmode.py`。
  **缺口**：`workflows/tsmode.py::run_tsmode` 适配器与 `acp run tsmode` CLI **无直接测试**。

### 3.12 MT-NMR — NMR + DP4/DP5

- **前置**：`crest, censo, orca`（catalog `requires_binaries`；调度器节点匹配可能另计 xtb）；`--bruker` 另需 `nmrglue`。
- **约束**：CLI 解析器不强制，但运行时**必须恰好提供** `--spectrum` 或 `--bruker` 之一（都缺或都给 → rc=1）。`--bruker-ref` 形如 `NUC=PPM`。

```bash
# 多候选 + 实验谱（DP4/DP5 立体归属）
acp run nmr --input "CCO" --input "CCO" --spectrum exp_spectrum.txt \
    --nuclei "1H,13C" --solvent chloroform --preset censo-light \
    --nmr-method mPW1PW91 --nmr-basis "6-311G(d)" \
    --output /tmp/acp_mt/nmr --nproc 4

# Bruker 原始数据
acp run nmr --input "CCO" --bruker ./nmr_data --bruker-ref H=7.26 \
    --output /tmp/acp_mt/nmr_bruker

# 单输入枚举所有非对映异构体
acp run nmr --input "CC(O)C" --spectrum exp_spectrum.txt --enumerate \
    --stereocenters "C2" --output /tmp/acp_mt/nmr_enum
```

- **谱文件格式**：`C: 167.33(C1), 59.58(C2)` / `H: ...` / `EQ: C10,C12` / `OMIT: H19`（见用例内 epilog）。
- **预期产物**（`RESULT/reports/`）：`nmr_report.json`、`nmr_assignment.xlsx`（缺 openpyxl 时跳过）、`nmr_summary.json`、`plots/*.png`（缺 matplotlib 时跳过）；`RESULT/result_manifest.json`（`nmr_report` / `nmr_xlsx` / `plot_N`）。
- **判定**：exit 0；报告含 DP4/DP5 概率与归属；结论符合预期候选。
- **注意**：`--nmr-method` / `--nmr-basis` 必须与 error model 匹配（默认 `goodman-legacy` 要求 `mPW1PW91` / `6-311G(d)`）。README 里的 `--backend` / `--reference` 已过时，勿用。
- **自动化**：`tests/test_acp_workflows_nmr.py`、`tests/test_acp_nmr_*.py`（mock；`test_acp_nmr_spectra.py` 使用真实 nmrglue + 合成数据）。

---

### 3.12a MT-P2T — XtbPathSearch / OrcaGradient（pes2ts 冻结 payload，本地专用）

- **前置**：`XtbPathSearch` 需 xtb；`OrcaGradient` 需 orca。两者均消费 PES2TS 侧冻结的 request JSON（`--path-config` / `--gradient-config`），**本地专用**（不在 `_ALLOWED_REMOTE_WORKFLOWS`，远程提交被 script_gen 拒绝）。
- **payload 形状（以代码为准）**：`XtbPathSearch` = `pes2ts_xtb_path_request_v1`（`source_type:"xyz_text_pair"` + `start_xyz`/`end_xyz` + `gfn_level` 等）；`OrcaGradient` = `pes2ts_orca_gradient_request_v1`（`xyz` + `method` + theory 摘要）。

```bash
acp run XtbPathSearch --path-config pes2ts_xtb_path_request.json \
    --output /tmp/acp_mt/xtbpath --nproc 4
acp run OrcaGradient --gradient-config pes2ts_orca_gradient_request.json \
    --output /tmp/acp_mt/grad --nproc 4
```

- **预期产物**：`XtbPathSearch` → `RESULT/pes_search/xtbpath.xyz` + `RESULT/pes_search/path_frames/path_frame_*.xyz` + manifest 注册（`pes_search` 视图）；`OrcaGradient` → 梯度/能量载荷 + manifest 注册。
- **判定**：exit 0；帧 XYZ 原子数一致；manifest 产品路径与实际文件相符。
- **负向**：缺 `--path-config`/`--gradient-config` 或 payload 缺必填键（如 `xyz`/`method`）→ 结构化错误退出。
- **自动化**：`tests/test_acp_workflows_xtb_path.py`、`tests/test_acp_workflows_orca_gradient.py`、`tests/test_cccp_task_xtb_path_search.py`、`tests/test_cccp_task_orca_gradient.py`（mock xtb/orca；执行核心在 `cccp/calculation/tasks/`）。

### 3.13 组合工作流端到端（真实链路）

推荐的真实全链路顺序测试（每条命令完成后再执行下一条，用 `--from-job` 串联）：

```
singlepoint / xtb_optimize（环境冒烟）
   └─▶ Confsearch（构象搜索，得 confsearch_manifest.json）
          └─▶ PESsearch --from-job <cs_id>（势能面 + TS/INT 候选）
                 └─▶ 人工确认（能量查看器 POST /pes/review）
                        ├─▶ BatchOptimize --from-job <pes_id> --profile opt_freq（低精度确认）
                        │        └─▶ irc（端点验证）
                        └─▶ BatchOptimize --profile opt_freq_sp_thermo（高精度 + 热力学 + IRC）
```

判定：每一步 `completed` 且产物清单完整；下游任务能正确解析上游 manifest。

---

## 4. 任务提交与生命周期（调度器 / API / Web）

> Base URL 默认 `http://127.0.0.1:8765`。以下示例使用 `curl`；Web 端为 `frontend/ACP_Workbench_v2.html`（新建任务向导）。

### 4.1 提交流程

**主提交端点**：`POST /api/v1/jobs` → `201`。

标准提交封装字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `workflow` | str（必填） | 必须在 active 集合内，否则 400 |
| `input` | dict | 各工作流形状见下 |
| `method` | dict | 方法/级别；可用 `POST /api/v1/validate-method` 单独校验 |
| `resources` | dict | `nproc`、`mem`（如 `"32GB"`） |
| `name` / `output_dir` / `config_path` | str | |
| `project_id` | str\|null | 默认项目 |
| `execution_mode` | `"local"\|"remote"\|null` | null 跟随服务默认 |
| `target_node` | str\|null | 指定节点；`"local"` = 本地 |
| `molecule_name` / `task_name` / `remark` | str | 任务目录命名 `<molecule>_<task>_<remark>` |
| `tags` / `node_tags` | | 节点匹配标签 |

**响应**：`{job_id, status:"queued", workflow, project_id}`，SSH/上传/bsub 在后台线程执行。

**各工作流 `input` 形状（摘要）**：

| workflow | 必需 input 键 |
|----------|---------------|
| simple / scan / nmr / Confsearch | `source_type: "smiles"\|"xyz_text"\|"structure_asset"` + `source` + `charge` + `multiplicity` |
| nmr（多候选） | `candidates:[{source_type,source,charge,...}]` + `experiment:{mode:"assigned"\|"bruker", content \| spectrum_asset_id, references}` |
| PESsearch | `source_job_id` + `from_artifact`（或 `relative_path`）；bond scan 用 `source` + `coordinate` |
| BatchOptimize | `items:[{source_id\|xyz, tag, candidate_id}]` 或 `from_artifact` / `items_file` |
| irc | `source_job_id` + `source_product_id`（或 `source_id`）+ `directions` |
| tsmode | `source_job_id` + `source_mode_index` + `entry_id` + 可选 `allow_unverified_mapping` |

**curl 示例（simple 工作流）**：

```bash
curl -s -X POST http://127.0.0.1:8765/api/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{
        "workflow":"singlepoint",
        "input":{"source_type":"smiles","source":"CCO","charge":0,"multiplicity":1},
        "method":{"method":"r2SCAN-3c","basis":""},
        "resources":{"nproc":4,"mem":"8GB"},
        "molecule_name":"ethanol","task_name":"sp_smoke","execution_mode":"local"
      }'
```

**上传分子**：`POST /api/v1/uploads`（multipart `file`，query `parse=true|false`）；结构化资产：`POST /api/v1/structure-assets`。

### 4.2 任务状态机

状态集合：`queued`、`starting`、`pending`、`running`、`paused`、`cancelling`、`waiting_review`、`cancelled`、`completed`、`failed`。

- **终态**：`completed` / `failed` / `cancelled`。
- **active（占队列、禁删除）**：`queued` / `starting` / `pending` / `running` / `paused` / `cancelling` / `waiting_review`。
- 特殊退出码 `77` → `waiting_review`。
- `RUNNING ⇄ PAUSED`：本地 `killpg(SIGSTOP/SIGCONT)`；远程 LSF `bstop/bresume`。**暂停不释放内存/磁盘**。

### 4.3 生命周期操作测试矩阵

| 操作 | 端点 | 允许状态 | 关键语义 / 预期 |
|------|------|----------|-----------------|
| 暂停 | `POST /api/v1/jobs/{id}/pause` | 仅 `running` | 不满足 → 409 |
| 恢复 | `POST /api/v1/jobs/{id}/unpause` | 仅 `paused` | 不满足 → 409 |
| 取消 | `POST /api/v1/jobs/{id}/cancel` | 任意非终态 | 终态 no-op；PAUSED 先 SIGCONT 再终止 |
| 断点续算 | `POST /api/v1/jobs/{id}/continue` | 仅 `failed`/`cancelled` | 依 `WORK/00_RUNTIME/checkpoint.json`；**BatchOptimize 不支持**（409）；无有效检查点 → 409 |
| 原地重跑 | `POST /api/v1/jobs/{id}/rerun` | 仅终态 | **同一 job_id/work_dir**；清理 WORK/RESULT；项目锁定 |
| 删除 | `DELETE /api/v1/jobs/{id}?delete_data=false` | 非 active | active → 409 |
| 批量清除 | `POST /api/v1/jobs/purge` | 任意 | `{job_ids?, status?, project_id?, older_than_days?, force_cancel?}`；无选择器 → 422 |
| 移动 | `POST /api/v1/jobs/{id}/move` | 非 active | 目标目录已存在 → 400 |
| 克隆 | `POST /api/v1/jobs/{id}/clone` | 任意 | 新任务，同 spec |
| 详情 | `GET /api/v1/jobs/{id}/detail` | — | 阶段 + 产物 + 错误 + `recovery` 矩阵 |
| 事件流 | `GET /api/v1/jobs/{id}/events` | — | SSE |

> **测试提示**：`detail.recovery.can_continue` 只对 `mechanism`/`xtbmd_censo_energy` 置真，但 `/continue` 实际还接受带通用 checkpoint 的工作流。自动化应以 `/continue` 的 409/成功为准，不要只信该标志位。

### 4.4 修改参数后重算（edit-recalculate）

| 步骤 | 端点 | 说明 |
|------|------|------|
| 草稿 | `GET /api/v1/jobs/{id}/edit-draft` | 返回 `editable_spec` + `input_refs` + `capabilities` + `source_revision` |
| 预览 | `POST /api/v1/jobs/{id}/edit-recalculate/preview` | 校验 + diff + `blocking_reasons` + `preview_fingerprint`（**不写盘**） |
| 提交 | `POST /api/v1/jobs/{id}/edit-recalculate` | `mode: in_place\|new_job`；**`request_id` 必填** |

- **in_place**：锁定工作流/项目/物理目录；`_reset_work_dir_in_place(strict=True)` 清理失败 → 阻断排队（409）。
- **new_job**：单次 `submit()`，返回新 `job_id`，带 group 血缘。
- **并发/幂等**：`request_id` 命中已完成操作 → 重放旧结果（`replayed:true`，跳过 revision 校验）；同一 `request_id` 用于另一任务 → 409；`payload_hash` 不同 → 409。
- **409 冲突**：`source_revision_conflict`、`preview_fingerprint_conflict`。
- **422**：非法 `mode`、缺 `request_id`、退役/不可编辑工作流、in_place 改工作流。

### 4.5 远程 LSF 执行

**配置**（`cluster:` YAML 段，由 `acp init` 生成）：

- 集群级：`execution_mode`、`poll_interval`、`retention_days`、`auto_sync`、`require_all_binaries`、`pre_cmds[]`、`queue`、`walltime`、`extra_flags`、`nodes[]`。
- 节点级（必填）：`name`、`host`、`username`、`remote_work_dir`（禁空格）、`remote_code_dir`；可选：`port`、`password`/`key_file`、`python_executable`、`bin_symlinks`、`max_concurrent_jobs`、`enabled`、`capabilities{software[],tags[]}`。

**提交前置**：节点可达 + LSF（`bsub/bjobs/bkill/bstop/bresume`）+ `requirements-node.txt`（numpy/rdkit/pyyaml）+ 节点 bootstrap：

```bash
GET  /api/v1/nodes                 # 列出节点
GET  /api/v1/nodes/{name}/status   # 状态（30s TTL 缓存）
POST /api/v1/nodes/{name}/bootstrap
POST /api/v1/nodes/{name}/ping
POST /api/v1/nodes/matching        # 能力预览（缺 node_tags 键 → 400）
```

**提交行为**：`_prepare_and_submit` 上传 `input.xyz` / `batch_items.json` / `scan_config.json` + **`job.json`+`task.json` markers**（缺失 → 提交失败并清理远程目录）→ 代码增量同步 → 生成/上传 `submit.lsf` → `bsub`。轮询 `bjobs`：`PEND→pending`、`RUN→running`、`PSUSP/SSUSP/USUSP→paused`。

**远程支持范围**：除 `casscf` 外全部 active 工作流。远程任务读取须经 `_job_read_root`（本地工作目录 / 远端缓存根），写回经 `RemoteStructureCache.push_paths`。

### 4.6 PES 人工确认（能量查看器）

```
GET  /api/v1/jobs/{id}/pes/review            # 读取审核记录
POST /api/v1/jobs/{id}/pes/review            # 写入选点（物化 RESULT/structures/*.xyz + 更新 manifest）
POST /api/v1/jobs/{id}/pes/review/restore    # 恢复历史版本
```
远程任务：写操作先强制刷新远端 review/manifest，物化后按顺序 push structures → manifest → backup → `pes_review.json`（失败 502 + 尽力缓存重同步，可重试幂等）。

### 4.7 远程任务的读取回源（易错点）

`GET .../energy-graph`、`/s2/profile`、`/s2/candidates`、`/s2/frame`、`GET /pes/review` 必须经 `_job_read_root` 解析（远程任务先 `fetch_catalog` 再读缓存根），**不得**直读 `record.work_dir`。测试远程 PES/能量投影时若报 `No PES profile ... expected RESULT/pes_search/pes_profile.json`，优先怀疑该回源路径。

---

## 5. 结果产物与验证

### 5.1 标准任务目录布局（Zone A/B/C）

```
<task_dir>/
├── job.json                     # 作业快照（含 job_id）
├── task.json                    # job_id + task_dir_name + workflow + layout_version:2
├── input.xyz                    # 物化输入
├── input_source.json            # SMILES 输入溯源
├── state.json                   # WorkflowState 检查点（续算关键）
├── metrics.json                 # 展示用指标（不得作为判定依据）
├── .exit_code                   # 退出码标记（远程轮询用）
├── submit.lsf                   # 仅远程
├── batch_items.json             # batch_structures 提交（任务根）
├── INPUT/                       # tsmode 源快照区
├── WORK/
│   ├── 00_RUNTIME/              # stdout.log / stderr.log / events.jsonl / checkpoint.json
│   ├── 01_PREPARE/ … 08_ANALYSIS/   # 阶段目录
│   └── 08_CASSCF/
└── RESULT/
    ├── result_manifest.json     # 统一 v2 结果清单
    └── <category>/              # structures/ energies/ frequencies/ trajectories/ ensembles/ mechanism/ reports/
```

- 阶段目录映射（`calculations/executor.py::_STEP_DIRS`）：OPTIMIZE→`03_OPT`、FREQUENCY→`04_FREQ`、SINGLEPOINT→`05_SP`、THERMOCHEMISTRY→`06_THERMO`、SCAN→`07_PATH`、CASSCF→`08_CASSCF`。
- 调度器任务检测：`job.json` **且** `task.json` 存在于输出根（`_helpers.is_scheduler_task_dir`）→ 扁平写入；否则 CLI 多分子写 `{output}/{safe_name}/`。
- **路径铁律**：磁盘路径中**不得**出现 `job_id`（`<molecule>_<task>_<remark>` + `__NN` 去重）。

### 5.2 各工作流产物清单（代码为准）

| 工作流 | 关键产物（相对路径） |
|--------|----------------------|
| Confsearch | `RESULT/confsearch/confsearch_manifest.json`、`quality_gates.json`、`ensemble.xyz/.csv`、`energies.json`、`boltzmann.json`、`conformers/conf_NNNN.xyz`、`final_report.json`、`final_conformers.xyz`、`sampling_history.json`(xtb-md/xtbmd-censo) |
| PESsearch | `RESULT/pes_search/pes_profile.json`、`pes_recommendations.json`、`pes_review.json`(审核后)、`pes_review_backup_NNN.json`、`RESULT/structures/<candidate_id>.xyz`、`RESULT/trajectories/pes_scan_trajectory.json` |
| BatchOptimize | `RESULT/structures/<item_id>__TAG_<TS\|INT>__optimized.xyz`、`RESULT/frequencies/<item_id>__normal_modes.json`、`RESULT/state_comparison.json`、`RESULT/batch_provenance.json` |
| irc | `RESULT/irc/irc_report.json`、`irc_forward.xyz`、`irc_reverse.xyz`、`irc_<dir>_path.xyz`、`irc_<dir>_point_NNNN.xyz`、`RESULT/trajectories/irc_trajectory.json` |
| scan | `RESULT/trajectories/scan_trajectory.json`、`RESULT/structures/scan_frame_NNN.xyz` |
| tsmode | `INPUT/tsmode/*`、`WORK/tsmode/tsmode_checkpoint.json`、`RESULT/tsmode/{target_resolution.json,tsmode_report.json,optimized.xyz,normal_modes.json}` |
| nmr | `RESULT/reports/nmr_report.json`、`nmr_assignment.xlsx`、`nmr_summary.json`、`plots/*.png` |
| simple（sp/opt/freq/scan） | `RESULT/result_manifest.json`（structure/energy_report/frequency_modes）；`WORK/<step>/optimization_trajectory.json`；`WORK/<step>/normal_modes.json` |

> **文档漂移提醒**（以代码为准）：scan 帧是 `RESULT/structures/scan_frame_NNN.xyz`（**非** `frame_*.xyz`）；BatchOptimize 断点态在 `WORK/00_RUNTIME/checkpoint.json::items_state`（**任务根 `batch_items.json` 只是提交输入**）；`result_summary.json` 目前仅由 energy/ensemble/nmr 写出。

### 5.3 程序化验证 `result_manifest.json`

```python
from acp.results.manifest import load_result_manifest, find_products

m = load_result_manifest(task_dir)          # 缺失/损坏返回 None（读取器不抛异常）
assert m is not None and m["version"] == 2
for kind in ("structure", "energy_report", "frequency_modes"):
    for p in find_products(m, kind):
        assert (task_dir / "RESULT" / p["path"]).exists()
```

- product：`{id, label, path, kind, [metadata]}`，`path` 相对 `RESULT/`。
- `kind` 取值含 `structure | frequency_modes | energy_report | ensemble | trajectory | report | file | pes_profile | irc_endpoint | thermo_report | multireference_report | wavefunction | spin_diagnostics | active_space | state_comparison`（未知回退 `file`）。
- 可复用结构来源 = product kind ∈ `{structure, xyz}`（供下游 BatchOptimize / 结构来源面板消费）。

### 5.4 查看器 API（人工检查）

| 端点 | 用途 |
|------|------|
| `GET /api/v1/jobs/{id}/structure-viewer[?item_id=]` | 结构目录（groups/entries/revision/availability） |
| `GET .../structure-viewer/entries/{entry_id}/geometry` | 单条几何 XYZ（远程 409 `pending_fetch`，用 `?fetch=1` 恢复） |
| `GET .../structure-viewer/entries/{entry_id}/vibrations` | 振动数据（**永不 500**，不可用返回 `available:false`） |
| `GET .../structure-viewer/overlay?entry_a=&entry_b=` | 叠合 RMSD |
| `GET /api/v1/jobs/{id}/energy-graph[?view=&item_id=]` | 统一能量/轨迹投影 |
| `GET /api/v1/jobs/{id}/irc/frames/{direction}/{frame_index}/geometry` | IRC 逐帧 |
| `GET /api/v1/jobs/{id}/sampling/frame/{frame_index}` | MD 采样帧 |
| `GET /api/v1/jobs/{id}/s2/profile` · `/s2/candidates` · `/s2/frame/{i}` | PES 投影（远程回源） |
| `GET /api/v1/jobs/{id}/files[?view=raw\|summary]` | 文件树 / 下载 |

### 5.5 检查点与续算产物

| 文件 | 归属 | 用途 |
|------|------|------|
| `WORK/00_RUNTIME/checkpoint.json` | CalculationPlanExecutor / BatchOptimizeEngine / irc | `{plan_fingerprint, step_states, items_state, attempts}`；指纹不匹配 `CheckpointMismatchError` |
| `WORK/tsmode/tsmode_checkpoint.json` | tsmode | source/hash/target/level 指纹 |
| `state.json`（任务根） | WorkflowState | 阶段完成/失败追踪 |
| `.stage_*`（工作目录） | StageTaskObserver | 阶段状态镜像到 DB |

> 续算语义：`continue_job`（FAILED/CANCELLED→QUEUED，`attempts+1`）依赖检查点；`rerun_job` 原地重跑清理 WORK；in-place edit 严格重置 WORK。

---

## 6. 自动化测试

### 6.1 运行方式（重要前置）

本机 `acp 0.1.3` site-packages 过期，统一用源码：

```bash
cd /ACPServer/AutoCalcPlatform
PYTHONPATH=src python3.11 -m pytest <target> -q
# 或先 pip install -e '.[dev,api,remote]' 再 python3.11 -m pytest
```

### 6.2 pytest 机制

- **markers**：`slow`、`integration`、`requires_gaussian`（声明未用）、`requires_orca/crest/xtb/isostat/shermo`。
- **`--run-slow` / `--run-integration`**：任一传入才运行标记为 `slow`/`integration` 的用例；默认自动跳过。
- **真实二进制检测**：conftest 导入时 `shutil.which()` 确定 `HAS_ORCA` 等，生成 `requires_*` skipif。
- **`fake_backend` fixture**：monkeypatch `acp.backends.get_backend`，记录调用、支持预置结果/异常。
- **环境清理（autouse）**：删除所有 `CONFSEARCH_*` 与 `ACP_RUN_ROOT`，设置 `ACP_DISABLE_MPI_SNIFF=1`。

### 6.3 功能 → 测试文件 → 命令

| 功能 | 测试文件（节选） | 真实/mock | 命令 |
|------|------------------|-----------|------|
| Confsearch（4 协议） | `test_acp_confsearch*.py`、`test_acp_workflows_energy.py`、`test_acp_workflows_xtbmd_censo_energy.py`、`test_xtbmd_md.py`、`test_acp_censo_p5_acceptance.py`、`test_acp_backend_censo.py` | mock | `PYTHONPATH=src python3.11 -m pytest tests/test_acp_confsearch*.py tests/test_acp_workflows_energy.py tests/test_acp_workflows_xtbmd_censo_energy.py tests/test_xtbmd_md.py tests/test_acp_censo_p5_acceptance.py -q` |
| PESsearch | `test_pes_search.py`、`test_acp_pes_*.py`、`test_cli_pessearch_finalize.py`、`test_acp_api_pes_review.py` | mock（1 个真实 ORCA 见下） | `PYTHONPATH=src python3.11 -m pytest tests/test_pes_search.py tests/test_pes_*.py tests/test_acp_pes_*.py tests/test_cli_pessearch_finalize.py -q` |
| BatchOptimize | `test_batch_optimize*.py`、`test_acp_batch_materializer.py`、`test_acp_api_batch_preview.py`、`test_*keyword_case.py`、`test_keyword_parser_parity.py` | mock | `PYTHONPATH=src python3.11 -m pytest tests/test_batch_optimize*.py tests/test_acp_batch_materializer.py tests/test_acp_api_batch_preview.py -q` |
| irc | `test_irc.py`、`test_acp_irc_*.py`、`test_irc_ts_source.py` | mock/fixture | `PYTHONPATH=src python3.11 -m pytest tests/test_irc*.py tests/test_acp_irc_*.py -q` |
| scan | `test_scan_workflow.py`、`test_acp_pes_dft_scan.py` | mock | `PYTHONPATH=src python3.11 -m pytest tests/test_scan_workflow.py -q` |
| tsmode | `test_acp_tsmode_*.py`、`test_acp_api_tsmode.py`、`test_acp_structure_viewer_tsmode.py` | mock/fixture | `PYTHONPATH=src python3.11 -m pytest tests/test_acp_tsmode_*.py tests/test_acp_api_tsmode.py -q` |
| nmr | `test_acp_workflows_nmr.py`、`test_acp_nmr_*.py` | mock（`test_acp_nmr_spectra.py` 用真实 nmrglue） | `PYTHONPATH=src python3.11 -m pytest tests/test_acp_workflows_nmr.py tests/test_acp_nmr_*.py -q` |
| simple / casscf / xtb_optimize | `test_acp_workflows_simple.py`、`test_acp_cli.py`、`test_orca_*.py` | mock + CLI 子进程冒烟 | `PYTHONPATH=src python3.11 -m pytest tests/test_acp_workflows_simple.py tests/test_acp_cli.py tests/test_orca_*.py -q` |
| 调度器 / 生命周期 | `test_acp_job_operations.py`、`test_acp_job_edit.py`、`test_acp_scheduler.py`、`test_checkpoint_continue.py`、`test_local_cleanup.py` | mock/tmp | `PYTHONPATH=src python3.11 -m pytest tests/test_acp_job*.py tests/test_acp_scheduler*.py tests/test_checkpoint_continue.py -q` |
| API | `test_acp_api.py`、`test_acp_api_v1.py`、`test_acp_api_v2*.py`、`test_acp_api_structure_viewer.py` | TestClient + fakes | `PYTHONPATH=src python3.11 -m pytest tests/test_acp_api*.py -q` |
| 远程 LSF | `test_remote_phase1..6.py`（FakeSSH/FakeSFTP） | mock 网络 | `PYTHONPATH=src python3.11 -m pytest tests/test_remote_phase*.py -q` |
| 端到端（in-process） | `test_e2e_refactor.py` | mock backend + 真实编排 | `PYTHONPATH=src python3.11 -m pytest tests/test_e2e_refactor.py -v` |
| 前端契约 | `test_frontend_sync.py` | 源码扫描 | `PYTHONPATH=src python3.11 -m pytest tests/test_frontend_sync.py -q` |

### 6.4 真实二进制自动化现状（缺口）

标记门控的真实二进制/网络用例**仅 5 + 3 个**：

- `test_orca_binary_smoke_check`、`test_crest_binary_smoke_check`、`test_xtb_binary_smoke_check`（`slow`+`integration`+`requires_*`）
- `test_external_backend_binary_smoke_check`（`requires_isostat`+`requires_shermo`）
- `test_pes_orca_simulscan_integration.py::test_orca_synchronous_scan_tracks_both_targets`（`slow`+`requires_orca`，真实 ORCA 扫描）
- `tests/test_remote_phase1_integration.py`（**真实 SSH**，需 `ACP_REMOTE_PASSWORD_COMPUTE_01`，非 marker 门控）

```bash
# 真实 ORCA 扫描
PYTHONPATH=src python3.11 -m pytest tests/test_pes_orca_simulscan_integration.py --run-slow -v
# 真实 SSH（需节点密码环境变量）
PYTHONPATH=src ACP_REMOTE_PASSWORD_COMPUTE_01='<pw>' python3 tests/test_remote_phase1_integration.py
```

> **所有 14 个 active 工作流的真实 QC 全链路均无自动化覆盖**（Confsearch×4、PESsearch、BatchOptimize、irc、scan、tsmode OptTS、NMR GIAO、XtbPathSearch/OrcaGradient 均为 mock）。这是本手册 §3 手工用例存在的原因。

### 6.5 建议补充的真实端到端自动化（后续工作）

1. **Smoke 脚本**：`scripts/` 下新增 `smoke_real.sh`，对每个 active 工作流跑最小真实任务（水/乙醇），断言 `result_manifest.json` + 关键产物存在；用小分子 + 低精度控制耗时。
2. **pytest 真实工作流套件**：新增 `tests/test_real_<workflow>.py`，统一 `@pytest.mark.slow` + `requires_*`，用 `--run-slow` 门控。
3. **补齐 `acp run tsmode` CLI 适配器测试**（当前无覆盖）。
4. **远程节点往返**：把 `test_remote_phase1_integration.py` 推广到 6 个阶段 + 真实 bsub。
5. **CI**：`pytest tests/ -m "not slow"`（py3.10/3.11/3.12）+ 浏览器 job（见 `.github/workflows/ci.yml`）；真实二进制套件建议独立 nightly 任务。

---

## 7. 负向与错误用例

| ID | 场景 | 输入 | 预期 |
|----|------|------|------|
| NT-01 | 退役工作流（CLI） | `acp run ensemble --input O` | **exit 2** + 双语退役提示 + 映射建议（12 个已移除名同样拒绝，连 `--help` 也被 pre-parse 拒绝） |
| NT-02 | 退役工作流（API） | `POST /api/v1/jobs {"workflow":"energy",...}` | **HTTP 400** `Unsupported workflow 'energy'. Supported: [...]` |
| NT-03 | 非法协议 | `--protocol rph-parity` | ValueError：`RPH 已退役（2026-08 重构）：请使用 NATIVE provider`（CLI 层为 invalid choice） |
| NT-04 | 非法枚举值 | `--profile ultralight` | `invalid choice`（exit 2） |
| NT-05 | 大小写容错（正向） | `--protocol XTB-CREST --profile LIGHT --refinement-policy CUMULATIVE-99` | 规范化为小写并正常执行 |
| NT-06 | 互斥组缺失 | `acp run Confsearch --input "CCO"` 缺协议？ | `--input` XOR `--batch-file` 必需；缺一 → exit 2 |
| NT-07 | BatchOptimize 源缺失 | 无 `--from-job/--from-artifact/--items-file` | 必需组 → exit 2 |
| NT-08 | PESsearch 无输入 | 无任何输入形式 | runtime **exit 2**（"PESsearch requires ..."） |
| NT-09 | IRC 溯源不匹配 | XYZ 哈希 ≠ provenance `geometry_sha256` | **exit 2** |
| NT-10 | tsmode 默认门禁 | 不加 `--allow-unverified-mapping` | 422 `mode_mapping_unsupported` |
| NT-11 | NMR 谱缺失/冲突 | 既无 `--spectrum` 也无 `--bruker`（或都给） | **exit 1** |
| NT-12 | `--calc-hess 0` | `acp run optimize --calc-hess 0` | ArgumentTypeError，提示改用 `--no-calc-hess` |
| NT-13 | 暂停非运行任务 | `POST /pause` 于 `queued` | 409 |
| NT-14 | 续算不支持的工作流 | `POST /continue` 于 BatchOptimize | 409（"BatchOptimize 不支持断点续算，请使用重算 (rerun)"） |
| NT-15 | 删除 active 任务 | `DELETE /jobs/{id}`（running） | 409（"is active; cancel it before deletion"） |
| NT-16 | 批量清除无选择器 | `POST /jobs/purge {}` | 422（"Refusing to purge the whole queue"） |
| NT-17 | 编辑冲突 | `POST /edit-recalculate` 带过期 `expected_source_revision` | 409 `source_revision_conflict` |
| NT-18 | 编辑预览指纹不符 | 提交 `preview_fingerprint` 与预览不一致 | 409 `preview_fingerprint_conflict` |
| NT-19 | 执行目标冲突 | `execution_mode` 与 `target_node` 矛盾 | 400（D12 `execution_mode_conflict`） |
| NT-20 | 远程提交 casscf | 对 `casscf` 指定 remote | `ValueError`（不在远端允许集） |

---

## 8. 附录

### 8.1 各工作流关键参数速查

**Confsearch**：`--input|-i`(与 `--batch-file` 互斥必填) `--output`(默认 `./confsearch_out`) `--protocol` `--profile` `--refinement-policy` `--backend native` `--preset` `--levels`(JSON/文件) `--solvent` `--ewin` `--temperature` `--md-temp` `--md-time` `--md-seeds` `--max-frames` `--charge` `--multiplicity` `--nproc` `--mem` `--config` `--save-config` `--log-level`。

**PESsearch**：`--mode bond_length_scan|path` `--from` `--from-job` `--from-artifact` `--strategy guided-scan|reverse-peb|direct-ts` `--plan` `--product-manifest` `--reactant-conf` `--product-conf` `--ts-guess` `--source-type` `--xyz-text` `--asset-path` `--from-frame` `--scan-config` `--scan-kind distance|angle|dihedral` `--selection-kind` `--scan-bond-type` `--scan-atoms` `--scan-start` `--scan-end` `--scan-points` `--scan-method` `--scan-basis` `--scan-dispersion` `--scan-solvent-model` `--scan-solvent` `--scan-grid` `--scan-scf-convergence` `--scan-scf-max-iter` `--scan-ri-approximation` `--sp-method` `--sp-basis` `--no-sp` `--input` `--coordinate` `--points` `--reaction` `--output`(默认 `./pes_search_out`)。

**BatchOptimize**：源组 `--from-job|--from-artifact|--items-file`（互斥必填）；`--profile`(默认 `opt_freq`) `--layout-mode` `--select` `--method/--basis` `--sp-method/--sp-basis` `--temperature`(298.15) `--pressure`(1.0) `--scale-factor`(0.9905) `--opt-convergence`(loose/normal/tight/verytight) `--opt-initial-hessian`(auto/model/calculate) `--opt-recalc-hess` `--opt-trust-radius` `--opt-max-iter` `--opt-rescue-policy`(off/adaptive) `--scf-*`；每角色 `--{minimum,transition-state}-*` 覆盖；`--batch-roles-json`。

**irc**：`--input|-i`(必填) `--ts-provenance|--ts-provenance-json`(互斥必填) `--input-role transition_state` `--direction both|forward|reverse` `--maxpoints/--max-points`(100) `--step`(0.1) `--method` `--basis` `--charge` `--multiplicity` `--output`(默认 `./irc_output`)。

**tsmode**：`--source-bundle`(必填) `--source-mode-index`(必填) `--max-steps` `--recalc-hess` `--trust-radius` `--retry-limit`(2) `--no-final-frequency` `--allow-unverified-mapping` `--output`(默认 `./tsmode_output`)。

**nmr**：`--input`(可重复，必填) `--spectrum|--bruker`(运行时二选一) `--bruker-ref NUC=PPM` `--nuclei`(默认 `1H,13C`) `--nmr-method`(mPW1PW91) `--nmr-basis`(6-311G(d)) `--solvent` `--ewin`(6.0) `--boltzmann-temp`(298.15) `--tms-1h` `--tms-13c` `--error-model`(goodman-legacy) `--preset censo-light|censo-default|censo-zero` `--enumerate` `--stereocenters` `--charge` `--multiplicity` `--output`(默认 `./nmr_output`)。

**simple 公共**：`--input|-i`(必填) `--output`(默认 `./out`) `--charge` `--multiplicity` `--name` `--method`(r2SCAN-3c) `--basis` `--dispersion` `--solvent-model smd|cpcm|none` `--solvent` `--route-extras` `--spin-preset` `--spin-config` `--nproc` `--mem` `--config` `--log-level`；sp/opt/freq/scan 另有 `--aux-j-basis` `--aux-c-basis` `--ri-approximation none|RI|RIJCOSX|RIJK`。
`optimize` 追加：`--geom-maxiter` `--opt-convergence Loose|Normal|Tight|VeryTight`(默认 Tight) `--calc-hess [N|auto]|--no-calc-hess`。
`scan` 追加：`--coordinate A,B,START,END`（**0-based**，可重复，必填）`--scan-points`(21)。
`xtb_optimize` 追加：`--gfn 0|1|2`(2) `--opt-level crude..extreme`(normal) `--max-steps` `--solvent-model gbsa|alpb|none`。
`casscf` 追加：`--active-electrons/--nel`(必填) `--active-orbitals/--norb`(必填) `--nroots`(1) `--dynamic-correlation none|sc_nevpt2|fic_nevpt2` `--orbital-source` `--frozen-core/--no-frozen-core`。

### 8.2 关键 API 端点速查

| 类别 | 端点 |
|------|------|
| 提交 | `POST /api/v1/jobs`、`POST /api/v2/tasks/batch`、`POST /api/v1/jobs/{id}/clone`、`POST /api/v1/jobs/{id}/artifacts/{aid}/run-irc` |
| 上传 | `POST /api/v1/uploads`、`POST /api/v1/molecule/resolve`、`POST /api/v1/molecule/embed`、`POST /api/v1/structures/parse`、`POST /api/v1/structure-assets` |
| 生命周期 | `POST /api/v1/jobs/{id}/pause\|unpause\|cancel\|continue\|rerun\|move`、`DELETE /api/v1/jobs/{id}`、`POST /api/v1/jobs/purge` |
| 编辑重算 | `GET /edit-draft`、`POST /edit-recalculate/preview`、`POST /edit-recalculate` |
| 查询 | `GET /api/v1/jobs`、`GET /api/v1/jobs/{id}`、`GET /api/v1/jobs/{id}/detail`、`GET .../events`(SSE)、`GET .../files`、`GET .../logs` |
| 结果查看 | `GET .../structure-viewer[...]`、`GET .../energy-graph`、`GET .../s2/{profile,candidates,frame}`、`GET .../irc/...`、`GET .../sampling/...`、`GET|POST .../pes/review` |
| 节点 | `GET /api/v1/nodes`、`GET /nodes/{name}/status`、`POST /nodes/{name}/{ping,bootstrap}`、`POST /nodes/matching` |
| 方法校验 | `POST /api/v1/validate-method` |
| v2 组织 | `PATCH /api/v2/tasks/{id}`（custom_name）、`POST /api/v2/tasks/batch-ops`、`GET /api/v2/structure-sources` |

### 8.3 已知文档漂移 / 坑（测试时以代码为准）

1. ~~`README.md` 的 `xtb-optimize`~~ 已修复（2026-10-05 文档同步后 README 使用 `xtb_optimize`）；`--backend`/`--reference`（nmr）已过时，勿用。
2. ~~`tests/AGENTS.md` 计数过时~~ 已修复（2026-10-05 同步为 247 文件/≈5.7k 用例）。
3. `docs/ACP_Job_File_Layout_Spec.md` 列出的 `RESULT/batch_items.json` 无写入者；batch 提交输入在任务根 `batch_items.json`，断点态在 `checkpoint.json::items_state`。
4. scan 帧名 `scan_frame_NNN.xyz`（非 `frame_*.xyz`）。
5. `_SCHEDULER_MARKERS` 含 `INPUT`（spec 列表遗漏）。
6. IRC 产物比 spec 更丰富（`*_path.xyz` / `*_point_NNNN.xyz` / `irc_trajectory.json`）。
7. `detail.recovery.can_continue` 覆盖窄于实际 `/continue`（见 §4.3 提示）。
8. `casscf` 可编辑但**不支持远程**。
9. 启动服务后**代码改动必须 `systemctl restart acp`**（无 `--reload`）。
10. 时间/`job_id` 严禁进入磁盘路径（§5.1 铁律）。

---

*本手册基于 2026-09-30 代码普查生成。若某条断言与实现不符，请在修订时将「代码行为」写为本手册的权威口径，并同步修正上表漂移项。*
