# ACP Services Standalone Recipe — 可复现安装配方与 capability 报告（B41a+B41b，todo 41）

> **Status**: B41a（配方设计与首批复现）与 B41b（真实安装与完整 CLI 复现，todo 74 之后）均已执行。B41b 在本机干净 venv + 隔离 HOME/CWD/run_root 完成（见 §2.1）；**不是异机复现**。
> **Reproduced at**: B41a — 2026-10-19，repo `main` @ `8a66765`；B41b — 2026-10-19，repo `main` @ `2ecc517`（含 todo 74 CLI task 面），host python3.11.13（用户 redacted-user）。两批记录分列保留（§2 / §2.1），回填不覆盖原始记录。
> **依据**: 主方案 §35-M1（退出门）/§36.2 step7（PASS/FAIL/NOT_VERIFIED 分列）/§36.5 IN-03；并行计划二 B07-7；`docs/reference/current/ACP_Function_Test_Manual.md` §1/§3.0；`scripts/install_systemd.sh`；`scripts/start_acp.sh`。
> 两批任务均未重启 `acp.service`、未修改真实 run_root（`/var/lib/acp/runs`）、未修改真实 `~/.cccp.yaml`；干净环境复现全部落在 `/tmp/acp_recipe_*`（B41a）与 `/tmp/b41b_*`（B41b）。

---

## 1. 范围与依据

本配方回答一个问题：**在干净环境中，从零到"第一个本地科研闭环"需要哪些步骤、每一步的 expected/actual 是什么、哪些能力已被验证、哪些没有**。

**四态定义（每格只陈述一个状态，不合并）**：

| 状态 | 含义 | 不代表 |
|---|---|---|
| installed | 构件（Python 包 / 二进制 / unit 文件）在机器上存在且可被定位 | 不代表配置正确，不代表可用 |
| configured | 配置源中存在该项配置且被当前配置合并顺序读取到 | 不代表外部依赖真实可达 |
| enabled | 部署配置将该项开启（tier/组件选择、systemd enabled、cluster.enabled） | 不代表端到端行为正确 |
| verified | 有本文件记录的、可复跑的命令 + 实际观测证据 | 不代表覆盖全部工作流/场景 |

**环境基线（本次复现，如实记录）**：

- 干净 venv：`/tmp/acp_recipe_venv`（`python3.11 -m venv`，pip 26.2.1），`pip install -e '.[api,remote]'` 安装 acp-0.2.0 + 65 依赖（INSTALL_EXIT=0，544M）。
- 本机已有 editable 安装 acp 0.2.0（`~/.local`，console script `~/.local/bin/acp`，默认 PATH 不含 `~/.local/bin`）。
- **PATH 陷阱（务必记录）**：`command -v orca` 命中的是 GNOME Orca 40.3（`/usr/bin/orca`，Python 脚本，Igalia），**不是** Schatzburg 组 QC ORCA。QC ORCA 位于 `/home/redacted-user/orca611/orca`，只能经 `executables.orca.path`（配置）或 `CONFSEARCH_ORCA_PATH`（环境变量）解析。
- QC 二进制均不在 PATH，均位于 `~/.cccp.yaml` / 项目 `./cccp.yaml` 指定路径（7/7 存在且可执行）。
- **CWD 配置泄漏陷阱**：配置合并会读取 CWD 下 `./cccp.yaml`（项目级）。本次 host 仓库根存在未入库的 `cccp.yaml`（含 node-157 与凭据引用），从仓库根目录运行 `acp doctor` 会连真实远程节点；干净环境复现必须在**不含 `cccp.yaml` 的工作目录**中执行，或显式隔离 `HOME`。
- 主机 systemd：`acp.service` enabled + active（unit 生成于 2026-09-06），`/api/status` 200，run_root `/var/lib/acp/runs`；**该运行中进程自报 version 0.1.3（进程启动时加载的代码），本任务按约束未重启服务，故 HTTP 层验证不构成"当前源码已被主机服务加载"的证据**；当前源码行为以干净 venv 实例（自报 0.2.0）为准。
- **B41b 环境基线（2026-10-19）**：新干净 venv `/tmp/b41b-venv`（python3.11 -m venv，pip 升级至 26.2.1 后 `pip install -e 'repo-root[api,remote]'`，INSTALL_EXIT=0，acp-0.2.0 editable + 42 依赖，venv 544M）；隔离 `HOME=/tmp/b41b_home`（init 写入）、工作目录 `/tmp/b41b_work`（无 `cccp.yaml`）、`ACP_RUN_ROOT=/tmp/b41b_runs`。交互向导另用 `HOME=/tmp/b41b_home_s3a`、`ACP_RUN_ROOT=/tmp/b41b_runs_s3a`。B41b 另发现两条运行期要求（详见 §2.1）：**ORCA 需要配置 `executables.orca.ld_library_path` 且 OpenMPI `bin` 目录在 PATH 上（`mpirun` 缺失时 ORCA 启动即败）**；`run_root` 有实例锁（serve 持锁期间 `acp task` 拒绝第二控制器实例，防误杀运行中任务——正面行为）。

---

## 2. 干净环境复现步骤（install → init → doctor → 首个本地科研闭环）

通用约定：以下命令均在**不含 `cccp.yaml` 的工作目录**执行；`HOME=/tmp/acp_recipe_home` 隔离用户配置；`ACP_RUN_ROOT=/tmp/acp_recipe_runs` 隔离数据目录。判定原则遵循功能测试手册 §0（exit 0 + `RESULT/result_manifest.json` 可被 `load_result_manifest` 解析；`metrics.json` 不作判据）。

| 步骤 | 命令 | Expected | Actual | 结果 |
|---|---|---|---|---|
| S1 干净 venv 安装 | `python3.11 -m venv /tmp/acp_recipe_venv && /tmp/acp_recipe_venv/bin/python -m pip install -e '.[api,remote]'` | exit 0；venv 内出现 `acp` console script | acp-0.2.0 + 65 依赖（含 fastapi/uvicorn/paramiko/rdkit/numpy）安装成功，INSTALL_EXIT=0，venv 544M | PASS |
| S2 CLI 入口 | `acp --help` | 打印 usage（run / doctor / init），exit 0 | usage 打印 `positional arguments: {run,doctor,init}`，exit 0 | PASS |
| S3 init 非交互（回答文件） | `HOME=/tmp/acp_recipe_home acp init --config /tmp/acp_recipe_home/.cccp.yaml --answers answers.yaml`（answers：version 1 / usage local / tier standalone / components science-core+local-identity+local-storage / resources.software=7 二进制路径 / cluster.type local / params.run_root=/tmp/acp_recipe_runs） | 渲染版本化计划 → 应用到目标配置 → 输出 PASS/FAIL/NOT_VERIFIED 分列的 doctor 能力报告；不触碰真实 `~/.cccp.yaml` | 计划 v1 应用成功（写入 0644，含回退说明与配置差异清单）；内嵌 doctor 报告 PASS(12)/FAIL(0)/NOT_VERIFIED(0)；exit 0 | PASS |
| S3a init 交互式（TTY 向导） | `acp init` | 交互完成最小配置 | B41a 原记录（保留）："本任务未执行：交互路径会写真实 `~/.cccp.yaml`（任务约束禁止触碰真实配置），且交互验收不属于 B41a 范围 → NOT_VERIFIED"。**B41b 回填**：tmux TTY 驱动完整交互（隔离 `HOME=/tmp/b41b_home_s3a`）——usage=本机部署 → 档位=Standalone → 资源=本地（自动检测表 + isostat/molclus 手动路径回填）→ 组件=默认组合 → run_root 默认 → secrets 跳过 → 计划预览 → 应用；计划应用成功（deployment 段 + 手动 executables 路径写入 `.cccp.yaml`，含 `.bak` 回退），内嵌 doctor 报告 PASS(12)/FAIL(0)/NOT_VERIFIED(0)，`q` 干净退出（"再见"）。**注记**：自动检测把 GNOME Orca 40.3（`/usr/bin/orca`）列为 orca 且 doctor 报 PASS——即 S4c 缺陷在交互路径同样存在；自动检测项（orca/xtb/crest/censo/shermo）不写入配置（保持 PATH 解析），仅手动输入的 isostat/molclus 落盘 | PASS — **B41b 回填**：交互向导端到端跑通；orca 假成功缺陷见 S4c（同一缺陷，不重复计 FAIL）。原始 B41a NOT_VERIFIED 记录保留在 Actual 列 |
| S4 doctor（Standalone 本地配置，无远程节点） | `cd /tmp/acp_recipe_work && HOME=/tmp/acp_recipe_home acp doctor` | 输出能力报告；本地-only 档位不因缺远程节点整体判失败（待确认语义） | 输出 `No remote nodes configured (cluster.nodes is empty).`，exit 1。**B41b 复核（2026-10-19，/tmp/b41b-venv）**：同样输出且 exit 1——缺陷未修复 | FAIL — 原因：`acp doctor` 当前自检范围是远程节点（cli 描述 "Deployment self-check for remote compute nodes"），零节点即 exit 1；本地 Standalone 的 doctor 门应改用 S3 内嵌能力报告或扩展 doctor 本地检查——语义裁定不在本任务；**B41b 复核仍未修复** |
| S4b doctor（已配置远程节点） | `cd <含 cccp.yaml 的项目目录> && acp doctor`（或真实 `HOME`） | 各节点可达、Python 与二进制解析成功 → exit 0 | node-157（192.168.1.100）SSH 认证成功，python 3.13.9；censo 3.0.8 / crest 3.0.2 / xtb 6.7.1 / mpi 4.1.8 / orca / shermo / isostat / molclus 全部解析；`Doctor finished: all checks passed`，exit 0 | PASS |
| S4c 负例探针（错误配置的 typed 呈现） | `acp init --answers answers_bad.yaml`（隔离 HOME，answers 中 `executables.orca.path=/nonexistent/orca`，crest/censo/shermo/isostat/molclus 故意不配置） | 缺失/错误的软件配置必须 typed unavailable（FAIL 或 NOT_VERIFIED），不得假成功 | 未配置的 5 项正确记 `NOT_VERIFIED（未配置；配置完成但科学能力尚未验证）`；但 orca 配置路径不存在时被回落到 PATH，doctor 报 `software:orca — /usr/bin/orca` **PASS**——该二进制实为 GNOME Orca 40.3（`file /usr/bin/orca`：Python script，Igalia），非 QC ORCA；BAD_INIT exit=0。**B41b 复核（2026-10-19）**：同样回落 PATH 报 `software:orca — /usr/bin/orca` PASS、6 项 NOT_VERIFIED、BAD_INIT exit=0——缺陷未修复；交互向导（S3a）的软件检测表同样显示 orca→/usr/bin/orca 40.3 path | FAIL — 原因：`resolve_executable` 在配置路径无效时静默回落 PATH，无关二进制被报为 PASS（假成功）；负例门未通过，须在 doctor/解析层修复；**B41b 复核仍未修复** |
| S5 首个本地科研闭环 | `HOME=/tmp/acp_recipe_home ACP_RUN_ROOT=/tmp/acp_recipe_runs acp run xtb_optimize --input water.xyz --output out`（water.xyz 取自功能测试手册 §3.0） | exit 0；state.json status=completed；`RESULT/result_manifest.json` 存在且 `load_result_manifest` 非 None | exit 0；Energy -5.070544374391 Hartree；WORK/03_OPT/ 留有真实 xtb 产物（xtb.log、xtbopt.xyz、.xtboptok、charges、wbo）；state=completed；manifest 解析成功（4 products，structure converged） | PASS |
| S6 Web 服务冒烟（干净 venv，备用端口） | `HOME=… ACP_RUN_ROOT=/tmp/acp_recipe_runs acp run serve --host 127.0.0.1 --port 8899 --no-browser` | `/api/status` 200 | 200（version 0.2.0，run_root=/tmp/acp_recipe_runs 隔离生效）；`/` 200（Workbench 静态页）；`/api/workflows` 200 → 15 个工作流；测毕即杀进程，未与 8765 主机服务冲突 | PASS |
| S7a 主机服务状态观测 | `systemctl is-enabled acp; systemctl is-active acp; curl -s http://127.0.0.1:8765/api/status` | enabled + active + HTTP 200 | enabled / active / 200（run_root=/var/lib/acp/runs，进程自报 version 0.1.3，uptime≈23.7h——见 §1 基线说明） | PASS |
| S7b 从脚本重建 systemd unit | `sudo scripts/install_systemd.sh --user redacted-user --enable` | unit 由脚本生成、daemon-reload、服务启动 | 未执行：任务约束禁止重启服务；现役 unit 生成于 2026-09-06，与当前 `install_systemd.sh` 的逐字一致性未核验。**B41b 复核**：约束仍禁止重启/重建，未执行 | NOT_VERIFIED — 原因：两批任务均禁止重启/重建服务；须在无此约束的发布验收（todo 76）复现 |
| S8 完整 CLI 复现（B41b） | install → init → doctor → 15 工作流 CLI 全链路 + 进程重启后控制/恢复（todo 74 范围） | todo 74 交付后执行并回填证据 | **B41b 已执行**（见 §2.1）：install/init/doctor/闭环/控制恢复腿全部在 `/tmp/b41b-venv` 复现；15 工作流仅真实验收 4 项（xtb_optimize / frequency / Confsearch / singlepoint-ORCA），其余 11 项未跑 | NOT_VERIFIED — 原因：完整 CLI（含进程重启控制/恢复）PASS，但"15 工作流逐项真实验收"仅 4/15，未满足，仍属退出门二（§5）；不以本行代表完整发行 |

**步骤小结（B41a 原记录，保留）**：S1/S2/S3/S4b/S5/S6/S7a 全部 PASS（真实观测，命令可复跑）；S4 与 S4c 为 FAIL（doctor 本地-only 语义、错误配置回落 PATH 假成功——均如实记录不粉饰）；S3a/S7b/S8 为 NOT_VERIFIED（各附原因与复现条件）。**"向导跑完"不作为任何步骤的验收依据**（§8）。

**步骤小结（B41b 回填后，2026-10-19）**：S3a 由 NOT_VERIFIED 翻 PASS（TTY 向导端到端，缺陷注记随 S4c）；S4/S4c 复核仍 FAIL（缺陷未修复）；S7b 仍 NOT_VERIFIED（约束禁止重启）；S8 部分完成（完整 CLI + 进程重启控制/恢复 PASS，15 工作流逐项验收未做）。新增 §2.1 记录 B41b 的完整 CLI 复现与 todo 74 控制/恢复腿，并登记 3 项新缺陷（D1 孤儿收尾、D2 recover 配置丢失、D3 起步期 SIGKILL 孤儿）。

---

## 2.1 B41b 完整 CLI 复现记录（2026-10-19，`/tmp/b41b-venv`，todo 74 之后）

环境：`HOME=/tmp/b41b_home`（S3 init 写入）、CWD `/tmp/b41b_work`（无 `cccp.yaml`）、`ACP_RUN_ROOT=/tmp/b41b_runs`。ORCA 腿额外要求：PATH 前置 `/home/redacted-user/openmpi418/bin`（`mpirun`），配置含 `executables.orca.ld_library_path`（见 §1 B41b 基线）。以下每条命令均为独立进程（"fresh process" = 重启的控制器，验证持久连接语义）。

| 腿 | 命令 | Expected | Actual | 结果 |
|---|---|---|---|---|
| B1 CLI 入口（含 task 面） | `/tmp/b41b-venv/bin/acp --help` | usage 打印，exit 0 | `positional arguments: {run,doctor,init,task}`，exit 0；`acp task` 子命令 11 个动词（ingest/plan/execute/query/pause/unpause/continue/cancel/rerun/recover/export） | PASS |
| B2 init 非交互回答文件 | `HOME=/tmp/b41b_home acp init --config … --answers answers.yaml`（7 二进制路径 + cluster local + run_root=/tmp/b41b_runs） | 计划 v1 应用 + 内嵌 doctor 报告 | 计划应用成功（0644，配置差异清单完整）；内嵌 doctor PASS(12)/FAIL(0)/NOT_VERIFIED(0)；exit 0 | PASS |
| B3 init 交互 TTY 向导 | tmux 驱动 `acp init`（隔离 `HOME=/tmp/b41b_home_s3a`） | 交互完成最小配置并应用 | 全流程跑通（usage/档位/资源/组件/run_root/secrets/预览/应用），内嵌 doctor PASS(12)/FAIL(0)/NOT_VERIFIED(0)，`q` 干净退出；`.cccp.yaml`+`.bak` 落盘；注记：自动检测的 orca 停在 GNOME `/usr/bin/orca` 40.3（S4c 同缺陷） | PASS（缺陷注记见 S4c） |
| B4 doctor（本地-only） | `HOME=… ACP_RUN_ROOT=/tmp/b41b_runs acp doctor` | 能力报告；本地档位不整体判失败 | `No remote nodes configured (cluster.nodes is empty).`，exit 1 | FAIL（未修复，同 S4） |
| B5 doctor 负例探针 | `acp init --answers answers_bad.yaml`（orca=/nonexistent/orca，其余不配） | 错误配置 typed unavailable | orca 回落 PATH 报 `/usr/bin/orca` PASS；6 项 NOT_VERIFIED；BAD_INIT exit=0 | FAIL（未修复，同 S4c） |
| B6 首个本地闭环（xtb） | `acp run xtb_optimize --input water.xyz --output out` | exit 0；state completed；manifest 可解析 | exit 0；Energy -5.070544374391 Hartree；state=completed；`load_result_manifest(<task_dir>)` OK（4 products：structure/file×2/energy_report）；WORK/03_OPT 真实 xtb 产物 | PASS |
| B7 ORCA 真实科学（singlepoint） | `PATH=…/openmpi418/bin:… acp run singlepoint --input water.xyz --method B3LYP --basis def2-SVP` | exit 0 + ORCA 真实产物 | 首跑 exit 1：`sh: mpirun：未找到命令`（ORCA Startup error termination）——记录为环境要求；PATH 加 OpenMPI 后 exit 0，Energy -76.321049541296 Hartree（~3 s） | PASS（附环境要求；mpirun 缺失首跑 FAIL 如实记录） |
| B8 Web 冒烟（备用端口） | `acp run serve --host 127.0.0.1 --port 8899 --no-browser` + curl | /api/status 200 等 | 200（version 0.2.0，run_root=/tmp/b41b_runs 隔离）、/ 200、/api/workflows 200（15 项）；测毕杀进程 | PASS |
| B9 主机服务观测（只读） | `systemctl is-enabled/active acp; curl :8765/api/status` | 不重启、只观测 | enabled / active / 200（version 0.1.3 自报，run_root=/var/lib/acp/runs，uptime≈29.6h） | PASS（只读观测） |
| B10 任务闭环（todo 74 面，fresh process 逐腿） | `acp task ingest/plan/execute --follow/query`（workflow xtb_optimize，tmp run_root） | 各腿 JSON 状态正确 | ingest ok（H2O）→ plan ok（optimize）→ execute --follow **completed**（job `20261019_165540_001_b41bx`）→ 新进程 query **completed** | PASS |
| B11 rerun 腿 | `acp task rerun --job <completed>` | 新进程 → queued，attempt+1 | `{"status":"queued","attempt":2}`；worker 真实重算完成（state.json=completed，Energy -5.070544374391）——但 DB 行被后续控制器收尾为 **failed**（缺陷 D1） | PASS（rerun 语义）+ **FAIL（跨进程收尾，缺陷 D1）** |
| B12 continue 腿 | `acp task continue --job <failed>` | 新进程 → queued | `{"status":"queued","attempt":3}` → running（新进程 query 确认）；worker 真实重算完成（state=completed；日志 `recovery.step_not_adopted: adoption_disabled — recomputing`，符合"身份无效保守重算"不变量）——DB 行同样被收尾为 failed（缺陷 D1） | PASS（continue 语义）+ FAIL（D1） |
| B13 export 腿 | `acp task export --job <completed>` | 读生产 v2 manifest | `{"status":"completed","products":4,"task_dir":"…"}`，exit 0 | PASS |
| B14 recover 腿 | `acp task recover --task-dir t_rec --workflow optimize --method r2SCAN-3c`（及 singlepoint/B3LYP 复测） | 复用有效结果或保守重算 → completed | 两次均 **failed**：生成的 ORCA 输入为 `! default def2-TZVPP Opt/SP`（方法串未翻译，"default" 占位），且 orca 解析回落 GNOME `/usr/bin/orca`（"未能启动屏幕阅读器"）→ recover 执行上下文未获得用户配置的 executables/方法元数据（缺陷 **D2**） | **FAIL**（缺陷 D2；adopt 语义在套件内有覆盖，CLI 路径不可用） |
| B15 pause 腿（跨进程，存活 worker） | `acp task execute --workflow Confsearch --input CCO`（不 follow）→ 新进程 `acp task pause` | 新进程 SIGSTOP 存活 worker → paused | `{"status":"paused"}`；run.lock task_pid=1283878 `/proc` 状态 **T** | PASS |
| B16 unpause 腿（跨进程） | 新进程 `acp task unpause` | running 且 worker 离开 T | `{"status":"running"}`；worker 离开 T 并继续运行至完成（Confsearch/censo-crest+screen，2 构象，G1 PASS——真实科学） | PASS |
| B17 cancel 腿（running，跨进程） | 新进程 `acp task cancel`（worker 已完成后的 DB 行） | cancelling → cancelled | `{"status":"cancelling"}`；后续活控制器把 cancelling 收尾为 **failed**（应为 cancelled，缺陷 D1 同族） | PASS（cancel 意图持久化）+ **FAIL（跨进程终局，D1 族）** |
| B18 SIGKILL 控制器腿 | `acp task execute --follow` 后台 → t=8s `kill -9` 控制器 → 新进程 query | 存活任务可查 | 控制器死亡；新进程 query `{"status":"running","pid":1293694}`——持久连接成立 | PASS |
| B19 SIGKILL 起步期腿 | 同上但在提交窗口内（run.lock 未落）SIGKILL → 新进程 cancel | 可取消 | cancel 返回 `failed`：`Submission error: task … still has live process(es) [1295600]; refusing to start a second execution`；孤儿 worker 短暂存活后自然消亡（已人工确认 /proc 清空）——起步期孤儿不能被收养（缺陷 **D3**） | **FAIL**（缺陷 D3） |
| B20 真实工作流抽查（Confsearch 科学） | scheduler 提交的 Confsearch（默认 censo-crest + screen） | 真实 CREST+CENSO 执行 | 3 次真实完成（b41bconf 跨暂停恢复后完成 / b41bsig / b41bsig2）：`Confsearch completed (censo-crest + screen)`，2 构象，G1 gate PASS，manifest 落盘 | PASS（crest+censo 科学级验证，覆盖 §3 该行 NOT_VERIFIED） |
| B21 frequency 腿（scheduler + ORCA） | `acp task execute --workflow frequency --input water.xyz --follow` | completed | 2 次 completed（b41bfreq / b41bfreq2）；ORCA 频率计算真实执行 | PASS |
| B22 run_root 实例锁 | serve 持锁时 `acp task execute` | 拒绝第二实例 | `RuntimeError: run_root '…' is already owned by another ACP server process … refusing to start a second instance — it would kill the owner's running jobs`；杀掉临时 serve 后恢复；主机 8765 服务未受影响 | PASS（正面行为，防误杀） |

**B41b 缺陷登记（本批新发现，均如实记录，修复不在本任务范围）**：

- **D1（孤儿收尾缺陷，两次复现）**：`acp task execute`（无 `--follow`）/`rerun`/`continue` 启动的 worker 在**启动控制器退出后**完成时，产物与 `state.json` 均为 completed，但**下一个**控制器的 poll 循环把该 RUNNING 行收尾为 `failed`（exit_code=1，`workflow exited with code 1`）。根因：`runner.poll()` 的 untracked-RUNNING 分支在 `_foreign_task_alive=False`（worker 已自然退出）时直接 `(True, record.exit_code or 1)`，未检查 `state.json`/RESULT 的完成证据；cancelling 行跨进程同样被收尾为 failed（应为 cancelled）。影响：CLI 语义要求的"rerun/continue 后无需守候"不成立——必须有控制器存活到 worker 结束（`--follow` 或 serve），否则完成事实被误记为失败。
- **D2（recover 腿配置丢失）**：`acp task recover` 构建的执行上下文未携带用户配置的 `executables`（orca 回落 PATH → GNOME 假成功）与方法元数据（`--method r2SCAN-3c`/`B3LYP` 均生成 `! default def2-TZVPP …` 占位输入）。两个 compounding 问题叠加（D2a 方法翻译缺失、D2b 可执行解析回落），导致 CLI recover 腿必败。
- **D3（起步期 SIGKILL 孤儿）**：控制器在提交窗口内被 SIGKILL（run.lock 未落、job 行未 RUNNING）时，后续控制器拒绝收养存活 worker 并把 job 置 failed（"refusing to start a second execution"），孤儿 worker 继续消耗资源至自然结束（本例 Confsearch 数十秒后自然消亡）；长任务场景下为资源泄漏。

**B41b 步骤小结**：install/init（非交互+交互）/doctor 复核/xtb 闭环/ORCA singlepoint/Web 冒烟/主机观测/todo-74 全部 11 个 task 动词中 10 个 PASS（recover FAIL）、pause/unpause/SIGKILL-query 三个跨进程腿 PASS；S4/S4c 两缺陷复核仍未修复；D1/D2/D3 三个新缺陷如实登记。**15 工作流逐项真实验收仍缺 11 项**（Confsearch/frequency/singlepoint/xtb_optimize 已真实验收），不以本批代表完整发行（§5）。

---

## 2.2 Todo 79 缺陷修复后值（2026-10-19 追加；原始 S4/S4c 记录保留）

> 本段是**追加记录**，不覆盖 §2 / §2.1 的原始 FAIL 记录（审计痕迹保留）。修复实现与逐项实测
> 证据见 `tests/baseline/services-evidence/b41b_defect_fixes.md`（todo 79）。配方未在本任务重跑
> 这些腿；下列"修复后值"引用 todo 79 的隔离 `/tmp` run_root 实测，不把代码修复自动写成配方门已关闭。

| 缺陷 / 腿 | 修复前（§2.1 原始记录） | 修复后实测值（todo 79） | 配方门状态 |
|---|---|---|---|
| D2 recover 配置丢失 | `acp task recover` 生成 `! default def2-TZVPP` 占位输入，orca 回落 GNOME `/usr/bin/orca` → failed | recover 读取持久 `job.json`/`task.json` 的 workflow/method/resources 与合并配置；实测 `! r2SCAN-3c Opt`，ORCA 6.1.1 真实输出，`{"completed": true, "status": "completed"}`，exit 0 | 修复后值 PASS（todo 79 证据）；§4/§5 门仍待 F3/F4 复核，不在本文档内改写 |
| D1 跨进程 rerun/continue 收尾 | 无活控制器时 completed worker 被下一控制器收尾为 failed | `disk_completion_exit_code()` 先读 `.exit_code`/`state.json`（attempt-fenced）；实测下一控制器 poll `POLLED completed 0 1.0` | 修复后值 PASS（todo 79 证据）；原始 B11/B12 FAIL 记录保留 |
| D1 跨进程 cancel 终局 | CANCELLING 行被收尾为 failed | 已持久化 CANCELLING 且进程消失 = 确认停止 → CANCELLED；实测 `POLLED cancelled 130 3` | 修复后值 PASS（todo 79 证据）；原始 B17 FAIL 记录保留 |
| D3 起步期 SIGKILL 孤儿 | 拒绝收养存活 worker，置 failed 并放任资源 | `JobManager` 以 attempt-tagged `run.lock` + `/proc` 身份收养起步窗口 worker；跨进程 cancel 确认停止；`/proc` 扫描无残留，无二次派发 | 修复后值 PASS（todo 79 证据）；原始 B19 FAIL 记录保留 |
| S4c 错误配置假成功 | 显式无效 orca 路径回落 PATH，GNOME Orca 被报 PASS、BAD_INIT exit=0 | `acp doctor` 用 `resolve_executable_with_source()`：配置 pin 未生效即 FAIL（拒绝 PATH 回落）；PATH 命中过 QC 二进制 sanity 校验；实测显式 `/nonexistent/orca` → FAIL、exit 1，无 `/usr/bin/orca` PASS 行 | 修复后值 PASS（todo 79 证据）；§2 S4c 原始 FAIL 行与 §4 门状态保留 |
| S4 本地-only doctor | 零远程节点 exit 1 | local/standalone 且零节点时输出本地 capability 报告（10 PASS / 0 FAIL），exit 0；远程 doctor 输出未变 | 修复后值 PASS（todo 79 证据）；§2 S4 原始 FAIL 行保留 |

**边界声明**：以上仅记录 todo 79 已交付的修复后值；`src/cccp/software.py::resolve_executable`
的 PATH 回落根因仍登记为 C 路交接（见 `handoff_registry.md`），不在本配方范围内修改。
`S4/S4c` 的原 FAIL 行**不删除、不改写**；退出门一/二是否关闭由 F3/F4 与发布终验判定。

---

## 3. Capability 报告（installed / configured / enabled / verified 四态分列）

数据来源：本任务在 §2 各步的直接观测 + 配置文件读取（只读）。每格只陈述本状态；跨状态信息放"证据"列。

| 组件 | installed | configured | enabled | verified | 证据 |
|---|---|---|---|---|---|
| ACP CLI（science-core） | YES — venv `/tmp/acp_recipe_venv/bin/acp`（acp-0.2.0 editable）；host `~/.local` acp 0.2.0；B41b：`/tmp/b41b-venv/bin/acp`（同版本，另一干净 venv 二次复现） | YES — 本次 init 写入 `/tmp/acp_recipe_home/.cccp.yaml`；host `~/.cccp.yaml`（2026-09-06）；B41b：`/tmp/b41b_home/.cccp.yaml` + 交互向导 `/tmp/b41b_home_s3a/.cccp.yaml` | YES — tier standalone 必选组件 science-core 已启用（init 计划 deployment.components） | PASS — S2 help、S3 init、S5 闭环均 exit 0；B41b：B1/B2/B6/B7/B10–B21 复现 | §2 S1–S5；§2.1 B1–B21 |
| 本地 QC 软件：xtb | YES — `/home/redacted-user/xtb-dist/bin/xtb` 可执行（6.7.1） | YES — executables.xtb.path 已配置 | YES — science-core 组件选中 | PASS — S5 真实闭环执行（xtb.log / xtbopt.xyz 产物）；B41b：B6 + task 面 xtb_optimize 多次真实完成（含 rerun/continue 重算） | §2 S5；§2.1 B6/B10–B12 |
| 本地 QC 软件：crest / censo | YES — crest 3.0.2 / censo 3.0.8 可执行 | YES — executables.{crest,censo}.path 已配置 | YES — science-core 组件选中 | **PASS（B41b 升级）** — 3 次真实 Confsearch（censo-crest + screen 协议）完成：`Confsearch completed`、2 构象、G1 gate PASS、manifest 落盘（含跨暂停恢复后完成 1 次） | §2.1 B15–B17/B20 |
| 本地 QC 软件：shermo / isostat / molclus | YES — 3/3 配置路径可执行；交互向导手动路径回填 isostat/molclus | YES — executables.{shermo,isostat,molclus}.path 已配置 | YES — science-core 组件选中 | NOT_VERIFIED（原因：仅 `--version`/路径解析级证据；B41b 未跑依赖 shermo/isostat/molclus 的科学任务——xtbmd-censo 协议 / molclus 专用腿未执行） | `--version` exit 码 + §2.1 B3 |
| 本地 QC 软件：orca(6.1.1) | YES — `/home/redacted-user/orca611/orca` 可执行 | YES — executables.orca.path（含 ld_library_path）已配置 | YES — science-core 组件选中 | **PASS（B41b 升级，附环境要求）** — 真实 ORCA 6.1.1 科学任务 3 次完成：singlepoint B3LYP/def2-SVP（Energy -76.321049541296 Hartree）+ frequency ×2（scheduler 面）；**环境要求**：`mpirun` 须在 PATH（OpenMPI bin），缺失时 ORCA Startup error termination（首跑 FAIL 如实记录）；PATH 上的 orca 仍是 GNOME 40.3（S4c 缺陷未修复，不得以 doctor 的 orca PASS 为证据——本行证据是真实 SCF 产物） | §2.1 B7/B21 + sp.out 产物 |
| Web 服务（FastAPI + Workbench 静态） | YES — uvicorn 0.54.0 + fastapi 0.146.0（venv）；acp.api + frontend 静态文件在源码树 | YES — unit Environment=ACP_RUN_ROOT/ACP_HOST/ACP_PORT；serve 参数默认 127.0.0.1:8765 | YES — 主机 acp.service enabled；本次临时实例（8899）已启动并回收 | PASS — S6：/api/status 200（0.2.0）、/ 200、/api/workflows 200（15 项）；S7a：主机 8765 /api/status 200；B41b：B8/B9 二次复现（200/200/200，15 workflows；主机观测 200） | §2 S6/S7a；§2.1 B8/B9 |
| 本地任务管理 CLI（acp task，todo 74 面） | YES — `acp task` 11 动词随 acp-0.2.0 editable 安装（B41b 二次确认） | YES — 消费同一 run_root store（ACP_RUN_ROOT/--run-root）；`JobManager(reconnect_local_workers=True)` 持久控制器语义 | YES — B41b 在隔离 run_root 实际启用全部动词 | PASS — ingest/plan/execute/query/pause/unpause/continue/cancel/rerun/export 10 动词真实观测 PASS；**recover FAIL（缺陷 D2）**；跨进程收尾存在缺陷 D1/D3（见 §2.1） | §2.1 B10–B19 |
| systemd 服务 acp.service | YES — `/etc/systemd/system/acp.service` 存在（2026-09-06 生成） | YES — unit 指向 `scripts/start_acp.sh`、WorkingDirectory=项目根、StateDirectory=acp | YES — is-enabled=enabled | PASS — is-active=active + HTTP 200（S7a，仅主机现状观测） | §2 S7a；unit 由 `install_systemd.sh` 重建一致性 NOT_VERIFIED（原因：S7b 禁止重启服务） |
| 远程 LSF 执行（scheduler.remote + node-157） | YES — paramiko 5.1.0（remote extra）；`src/acp/scheduler/remote` 在源码树 | YES — cluster.nodes[0]=node-157（项目 `cccp.yaml`，含 python_executable/remote_code_dir/remote_work_dir） | YES — cluster.enabled=true、execution_mode=remote、auto_sync=true | NOT_VERIFIED（原因：doctor 可达性/二进制解析 PASS（S4b），但本任务未提交任何 bsub 作业、未做结果回取；远程执行端到端属真实 CLI/远程验收范围） | §2 S4b |
| MCP 薄入口（acp_services.entrypoints.mcp） | YES — `src/acp_services/entrypoints/mcp.py` 随 editable 安装存在 | NO — 本任务未观察到 MCP 协议配置或会话配置 | NO — 未开启（无客户端连接） | NOT_VERIFIED（原因：本任务未做 MCP 客户端联调；B08-2（todo 43）验收另行覆盖，不在本报告范围内） | 文件存在性检查 |
| Lab Server 档位（tier lab） | NO — 本任务范围未见多用户/配额/DB 交付构件 | NO — 未配置 | NO — 未启用（档位停在 standalone） | NOT_VERIFIED（原因：tier lab 未启用且无端到端证据；不得以 Standalone 证据代替） | init 计划 tier=local-single |
| Hosted 档位 | NO — 未见任何 Hosted 部署构件 | NO — 未配置 | NO — 未启用（默认排除组件 external-iam/object-store/payment，见 options.DEFAULT_EXCLUDED_COMPONENTS） | NOT_VERIFIED（原因：无任何 Hosted 端到端证据；按主方案 §36.2/IN-06，能力门全部通过前不得展示为可生产启用） | §7 |
| 数据库 / 对象存储 / 计费（条件组件） | NO — 未安装（PostgreSQL/对象存储不在最小科学安装内） | NO — 未配置 | NO — 默认不启用（payment/object-store/external-iam 恒不默认启用） | NOT_VERIFIED（原因：条件组件，未进入首批范围；属 M3/M4 门，不阻塞首批独立交付） | options.py 默认排除表 |

**阅读规则**：任何一格 YES 不得跨列引用（installed YES 不代表 verified）；任何 NOT_VERIFIED 均附原因；Hosted/Lab 行在没有证据时四态全部为 NO/NOT_VERIFIED，**不得在任何界面、矩阵或文案中展示为可生产启用**（文案与 UI 支持矩阵部分由并行计划 A 承接，本报告只交数据与门）。

---

## 4. 退出门一（首批范围退出门 / First-scope exit gate）

**定义**：第一主场景（本地 Standalone：结构 → 计算 → 结果清单 → 证据报告）闭环可用，且每个步骤有 expected/actual 证据。**不包含** 15 工作流全覆盖、完整发行验收（那是 §5）。

| 门项 | 当前状态 | 依据 |
|---|---|---|
| 干净 venv 安装（S1） | 已满足 | §2 S1 PASS；§2.1 B41b 二次复现 PASS |
| CLI 入口（S2） | 已满足 | §2 S2 PASS；§2.1 B1 PASS（含 task 面） |
| init 最小配置（非交互回答文件，S3） | 已满足 | §2 S3 PASS（12 PASS / 0 FAIL / 0 NOT_VERIFIED）；§2.1 B2 PASS |
| init 交互向导（S3a） | 已满足（B41b） | §2.1 B3 PASS — TTY 向导端到端跑通（orca 自动检测缺陷归 S4c 计） |
| doctor 门（本地-only 语义，S4） | 未满足 | FAIL（原因：零远程节点 exit 1；B41b 复核未修复，见 §2/§2.1 B4） |
| doctor 门（含节点场景，S4b） | 已满足 | §2 S4b PASS |
| 负例探针（错误配置 typed 呈现，S4c） | 未满足 | FAIL（原因：无效配置路径回落 PATH 假成功；B41b 复核未修复，见 §2/§2.1 B5） |
| 首个本地科研闭环（S5） | 已满足 | §2 S5 PASS（真实 xtb 产物 + manifest 解析）；§2.1 B6 PASS |
| Web 服务可服务（S6/S7a） | 已满足 | §2 S6/S7a PASS；§2.1 B8/B9 PASS |
| systemd unit 可重建（S7b） | 未满足 | NOT_VERIFIED（原因：两批任务均禁止重启服务；留待 todo 76） |
| 真实安装复现（B41b） | 已满足（本机干净 venv） | §2.1 — install→init（双路径）→doctor→闭环→控制/恢复全链路已复现；**非异机复现**，且新缺陷 D1/D2/D3 与 S4/S4c 未修复项不因此关闭 |
| 完整 CLI 控制/恢复（todo 74 面） | 部分满足 | §2.1 — 10/11 task 动词 PASS；recover FAIL（D2）；跨进程收尾 D1/D3 |

**退出门一状态：OPEN（部分达成）。** 已满足项不因未满足项回退；S3a/B41b 两项已由 B41b 清零；S4/S4c/S7b/recover(D2) 清零前不得宣称"首批范围退出门已过"。**"向导结束"不计入任何门项**（§8）。

---

## 5. 退出门二（完整发行退出门 / Full-release exit gate）

**定义**：完整发行 = 15 个 active 工作流（Confsearch / PESsearch / BatchOptimize / XtbPathSearch / OrcaGradient / XtbConstrainedOpt / irc / scan / tsmode / casscf / nmr / singlepoint / optimize / frequency / xtb_optimize）逐项真实验收 + 完整发行验收（真实 CLI 全流程 todo 74、四组合联合验收 todo 75、发布终验 todo 76、发行清单 todo 40）。

| 门项 | 当前状态 | 依据 |
|---|---|---|
| 15 工作流 CLI 注册可枚举 | 部分 | /api/workflows 200 → 15 项（S6/S7a/B8）——仅"可枚举"，非"已验收" |
| 15 工作流逐项真实验收 | 未满足 | 已真实验收 4 项：xtb_optimize（S5/B6）、singlepoint-ORCA（B7）、frequency（B21）、Confsearch/censo-crest（B20）；其余 11 项未跑 |
| 完整 CLI 复现（todo 74） | 部分满足 | §2.1 — install→init→doctor→闭环→10/11 task 动词 + pause/unpause/SIGKILL-query 跨进程腿 PASS；recover(D2)、跨进程收尾(D1/D3) FAIL；15 工作流全链路未做 |
| 联合验收与发布终验（todo 75/76） | 未满足 | 未开始（W14 收口任务） |
| 发行清单与文件归属（todo 40 B40b） | 未满足 | 未完成（B40b 依赖声明范围内迁移） |

**退出门二状态：OPEN（保持开放）。** 按并行计划共同约束 §8：**未进入范围的能力保持开放，不阻塞已满足的独立交付，也不计作完成**。任何"首批范围"材料（含本文档）**不得代表完整发行**。

---

## 6. B41a / B41b 顺序拆分

- **B41a（已完成，2026-10-19 @ `8a66765`）**：配方设计、四态 capability 报告、两级退出门定义、首批复现记录（§2，含 NOT_VERIFIED 标注）。**不依赖完整发行**（评审 P1-3 顺序修正：B41a 配方设计可先做）。
- **B41b（已完成本机复现，2026-10-19 @ `2ecc517`，todo 74 之后）**：真实安装与完整 CLI 复现——**在本机干净 venv + 隔离 HOME/CWD/run_root**（`/tmp/b41b-*`）按配方执行全部步骤，含 S3a 交互向导、S4/S4c doctor 语义复核、todo 74 全部 `acp task` 控制/恢复腿。记录见 §2.1。**范围限制（如实声明）**：① 非异机/异账户复现（同一台 host，仅 venv/HOME/run_root 隔离）；② S7b systemd unit 重建仍受"禁止重启服务"约束未执行；③ 15 工作流仅真实验收 4 项（其余 11 项未跑）；④ 发现 3 个新缺陷（D1 孤儿收尾 / D2 recover 配置丢失 / D3 起步期 SIGKILL 孤儿），已登记 §2.1，修复不在本任务范围。
- 回填规则（已执行）：§2 各行以"**B41b 回填/复核"标注附加在原记录之后，原始 NOT_VERIFIED/FAIL 记录保留（审计痕迹）；B41b 全量新记录集中在 §2.1，不覆盖 §2。
- 依赖关系：B41a blocked by todos 35—38（已完成）；B41b blocked by todos 74、40、32—34（按 T34a 声明范围）；本任务 blocks todo 69（分层验收）。
- **T74 不等待本任务**：B41a 的配方设计与首批复现可先做，不反向阻塞 todo 74；B41b 的真实安装与完整 CLI 复现必须在 **todo 74 交付后**执行（本文件 §2.1 即按该顺序执行）。B41b 不阻塞任何其他任务，也不因 D1/D2/D3 关闭任何门。

---

## 7. Hosted 与 Lab Server 未验证声明

1. **Hosted：未安装、未配置、未启用、未验证。** 无任何 Hosted 端到端证据；按主方案 §36.2（部署档位"能力未验证时不可启用"）与 §36.5 IN-06（Hosted 能力门与支持清单全部通过后才开放配方），Hosted 在任何界面/矩阵/文案中**不得展示为可生产启用**，也不得作为"升级路径"默认项。
2. **Lab Server：未安装、未配置、未启用、未验证。** Standalone 证据不得代替 Lab Server 证据（多用户/隔离/配额属 M3）。
3. 组件默认排除（payment / object-store / external-iam）不因配方存在而改变；生成配置不等于 Hosted 上线认证（§36.4）。
4. 文案与前端 UI 支持矩阵的呈现由并行计划 A（前端路）承接；本报告只提供上述四态数据与门定义。

---

## 8. 禁止性声明（Must NOT）

1. **不把"向导结束"当验收**：init 返回 0 只证明配置写入与内嵌自检通过，不证明科学任务、隔离、备份或运维验收；验收一律以 §2 的 expected/actual 证据为准。
2. **不宣称 Hosted 可用**（见 §7）。
3. **不以首批范围代表完整发行**（见 §4/§5 两级门）。
4. 不伪造 PASS：缺依赖/未执行的项一律 NOT_VERIFIED + 原因 + 复现命令；观察到的失败（S4、S4c）记 FAIL，不粉饰；发现"假成功"（无效配置回落 PATH 报 PASS）必须作为缺陷记录而非验收证据。
5. 不修改真实 `~/.cccp.yaml`、不重启 `acp.service`、不触碰真实 run_root 以换取本报告的通过记录。

---

## 9. 复现命令速查

```bash
# 0) 隔离环境（重要：工作目录不得含 cccp.yaml，否则项目级配置会泄漏进合并结果）
mkdir -p /tmp/acp_recipe_home /tmp/acp_recipe_runs /tmp/acp_recipe_work
cd /tmp/acp_recipe_work

# 1) install
python3.11 -m venv /tmp/acp_recipe_venv
/tmp/acp_recipe_venv/bin/python -m pip install -U pip setuptools wheel
/tmp/acp_recipe_venv/bin/python -m pip install -e 'repo-root[api,remote]'

# 2) init（非交互回答文件；answers.yaml 内容见 §2 S3 行）
export HOME=/tmp/acp_recipe_home
/tmp/acp_recipe_venv/bin/acp init --config /tmp/acp_recipe_home/.cccp.yaml --answers answers.yaml

# 3) doctor
# 3a) 本地-only（当前语义：零节点 → exit 1，见 S4 FAIL）
ACP_RUN_ROOT=/tmp/acp_recipe_runs /tmp/acp_recipe_venv/bin/acp doctor; echo "exit=$?"
# 3b) 含节点：在配置了 cluster.nodes 的目录/账号下执行，期望 "all checks passed"
# 3c) 负例探针（typed unavailable 门）：answers 中把 orca 指向不存在路径、其余软件不配置
#     期望：未配置项 NOT_VERIFIED + 错误配置项 FAIL；实测（2026-10-19）：错误配置回落 PATH
#     把 GNOME Orca 报为 PASS → 该负例门当前 FAIL（见 §2 S4c），修复前勿引用 doctor 的 orca PASS

# 4) 首个本地科研闭环
cat > water.xyz <<'EOF'
3
water
O    0.000000    0.000000    0.117300
H    0.000000    0.757200   -0.469200
H    0.000000   -0.757200   -0.469200
EOF
ACP_RUN_ROOT=/tmp/acp_recipe_runs /tmp/acp_recipe_venv/bin/acp run xtb_optimize \
  --input water.xyz --output /tmp/acp_recipe_work/out
# 判据：exit 0；out/water/RESULT/result_manifest.json 可被 load_result_manifest 解析；state.json status=completed

# 5)（可选）Web 冒烟（备用端口，勿占用 8765）
ACP_RUN_ROOT=/tmp/acp_recipe_runs /tmp/acp_recipe_venv/bin/acp run serve \
  --host 127.0.0.1 --port 8899 --no-browser &
curl -s http://127.0.0.1:8899/api/status   # 期望 200 + version 0.2.0 + run_root=/tmp/acp_recipe_runs

# ===== B41b 追加（§2.1）：todo 74 task 面 + ORCA 真实科学 =====

# 6) ORCA 真实科学（环境要求：OpenMPI bin 在 PATH，否则 mpirun 缺失 → ORCA Startup 失败）
export PATH=/home/redacted-user/openmpi418/bin:$PATH
# 注：isolated .cccp.yaml 需含 executables.orca.{path,ld_library_path}（init answers 不写 ld_library_path，
#     可手工补写——见 §1 B41b 基线；或依赖用户配置）
ACP_RUN_ROOT=/tmp/b41b_runs /tmp/b41b-venv/bin/acp run singlepoint \
  --input water.xyz --method B3LYP --basis def2-SVP --output /tmp/b41b_work/out_sp
# 判据：exit 0；Energy ≈ -76.321 Hartree；WORK/05_SP/sp.out 含真实 SCF

# 7) task 面闭环（每条 = 独立进程，验证持久控制器语义；勿与 serve 同 run_root 并行——实例锁会拒绝）
ACP_RUN_ROOT=/tmp/b41b_runs acp task ingest --xyz-text "$WATER_XYZ" --task-dir /tmp/b41b_work/t1
ACP_RUN_ROOT=/tmp/b41b_runs acp task plan --task-dir /tmp/b41b_work/t1 --workflow optimize
ACP_RUN_ROOT=/tmp/b41b_runs acp task execute --workflow xtb_optimize --input water.xyz --name demo --follow
ACP_RUN_ROOT=/tmp/b41b_runs acp task query   --job <id>          # 新进程 → completed
ACP_RUN_ROOT=/tmp/b41b_runs acp task rerun   --job <id>          # 新进程 → queued attempt 2（注意缺陷 D1：需活控制器收尾）
ACP_RUN_ROOT=/tmp/b41b_runs acp task export  --job <id>          # 新进程 → products=4
# 跨进程控制腿：execute（不 follow，Confsearch）→ 新进程 pause（/proc state T）→ unpause（离开 T）→ cancel
# SIGKILL 腿：execute --follow 后台 → kill -9 控制器 → 新进程 query 存活任务（PASS）；
#             起步窗口内 SIGKILL → 新进程 cancel → "refusing to start a second execution"（缺陷 D3）
# recover 腿（当前 FAIL，缺陷 D2）：acp task recover --task-dir <dir> --workflow optimize --method r2SCAN-3c
#             → 生成 `! default def2-TZVPP` 占位输入 + orca 回落 GNOME → failed
```
