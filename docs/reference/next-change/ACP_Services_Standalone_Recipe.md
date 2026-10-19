# ACP Services Standalone Recipe — 可复现安装配方与 capability 报告（B41a，todo 41）

> **Status**: 配方设计与首批复现记录（B41a）。真实安装与完整 CLI 复现（B41b）延期至 todo 74 交付后执行（见 §6）。
> **Reproduced at**: 2026-10-19，repo `main` @ `e447783`，host python3.11.13（用户 xieningke）。
> **依据**: 主方案 §35-M1（退出门）/§36.2 step7（PASS/FAIL/NOT_VERIFIED 分列）/§36.5 IN-03；并行计划二 B07-7；`docs/reference/current/ACP_Function_Test_Manual.md` §1/§3.0；`scripts/install_systemd.sh`；`scripts/start_acp.sh`。
> 本任务未重启 `acp.service`、未修改真实 run_root（`/var/lib/acp/runs`）、未修改真实 `~/.cccp.yaml`；干净环境复现全部落在 `/tmp/acp_recipe_*`。

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
- **PATH 陷阱（务必记录）**：`command -v orca` 命中的是 GNOME Orca 40.3（`/usr/bin/orca`，Python 脚本，Igalia），**不是** Schatzburg 组 QC ORCA。QC ORCA 位于 `/home/xieningke/orca611/orca`，只能经 `executables.orca.path`（配置）或 `CONFSEARCH_ORCA_PATH`（环境变量）解析。
- QC 二进制均不在 PATH，均位于 `~/.cccp.yaml` / 项目 `./cccp.yaml` 指定路径（7/7 存在且可执行）。
- **CWD 配置泄漏陷阱**：配置合并会读取 CWD 下 `./cccp.yaml`（项目级）。本次 host 仓库根存在未入库的 `cccp.yaml`（含 node-157 与凭据引用），从仓库根目录运行 `acp doctor` 会连真实远程节点；干净环境复现必须在**不含 `cccp.yaml` 的工作目录**中执行，或显式隔离 `HOME`。
- 主机 systemd：`acp.service` enabled + active（unit 生成于 2026-09-06），`/api/status` 200，run_root `/var/lib/acp/runs`；**该运行中进程自报 version 0.1.3（进程启动时加载的代码），本任务按约束未重启服务，故 HTTP 层验证不构成"当前源码已被主机服务加载"的证据**；当前源码行为以干净 venv 实例（自报 0.2.0）为准。

---

## 2. 干净环境复现步骤（install → init → doctor → 首个本地科研闭环）

通用约定：以下命令均在**不含 `cccp.yaml` 的工作目录**执行；`HOME=/tmp/acp_recipe_home` 隔离用户配置；`ACP_RUN_ROOT=/tmp/acp_recipe_runs` 隔离数据目录。判定原则遵循功能测试手册 §0（exit 0 + `RESULT/result_manifest.json` 可被 `load_result_manifest` 解析；`metrics.json` 不作判据）。

| 步骤 | 命令 | Expected | Actual | 结果 |
|---|---|---|---|---|
| S1 干净 venv 安装 | `python3.11 -m venv /tmp/acp_recipe_venv && /tmp/acp_recipe_venv/bin/python -m pip install -e '.[api,remote]'` | exit 0；venv 内出现 `acp` console script | acp-0.2.0 + 65 依赖（含 fastapi/uvicorn/paramiko/rdkit/numpy）安装成功，INSTALL_EXIT=0，venv 544M | PASS |
| S2 CLI 入口 | `acp --help` | 打印 usage（run / doctor / init），exit 0 | usage 打印 `positional arguments: {run,doctor,init}`，exit 0 | PASS |
| S3 init 非交互（回答文件） | `HOME=/tmp/acp_recipe_home acp init --config /tmp/acp_recipe_home/.cccp.yaml --answers answers.yaml`（answers：version 1 / usage local / tier standalone / components science-core+local-identity+local-storage / resources.software=7 二进制路径 / cluster.type local / params.run_root=/tmp/acp_recipe_runs） | 渲染版本化计划 → 应用到目标配置 → 输出 PASS/FAIL/NOT_VERIFIED 分列的 doctor 能力报告；不触碰真实 `~/.cccp.yaml` | 计划 v1 应用成功（写入 0644，含回退说明与配置差异清单）；内嵌 doctor 报告 PASS(12)/FAIL(0)/NOT_VERIFIED(0)；exit 0 | PASS |
| S3a init 交互式（TTY 向导） | `acp init` | 交互完成最小配置 | 本任务未执行：交互路径会写真实 `~/.cccp.yaml`（任务约束禁止触碰真实配置），且交互验收不属于 B41a 范围 | NOT_VERIFIED — 原因：TTY 交互向导未在本环境执行；由 B41b（todo 74 之后）真实安装复现 |
| S4 doctor（Standalone 本地配置，无远程节点） | `cd /tmp/acp_recipe_work && HOME=/tmp/acp_recipe_home acp doctor` | 输出能力报告；本地-only 档位不因缺远程节点整体判失败（待确认语义） | 输出 `No remote nodes configured (cluster.nodes is empty).`，exit 1 | FAIL — 原因：`acp doctor` 当前自检范围是远程节点（cli 描述 "Deployment self-check for remote compute nodes"），零节点即 exit 1；本地 Standalone 的 doctor 门应改用 S3 内嵌能力报告或扩展 doctor 本地检查——语义裁定不在本任务，B41b 复核 |
| S4b doctor（已配置远程节点） | `cd <含 cccp.yaml 的项目目录> && acp doctor`（或真实 `HOME`） | 各节点可达、Python 与二进制解析成功 → exit 0 | node-157（10.16.5.157）SSH 认证成功，python 3.13.9；censo 3.0.8 / crest 3.0.2 / xtb 6.7.1 / mpi 4.1.8 / orca / shermo / isostat / molclus 全部解析；`Doctor finished: all checks passed`，exit 0 | PASS |
| S4c 负例探针（错误配置的 typed 呈现） | `acp init --answers answers_bad.yaml`（隔离 HOME，answers 中 `executables.orca.path=/nonexistent/orca`，crest/censo/shermo/isostat/molclus 故意不配置） | 缺失/错误的软件配置必须 typed unavailable（FAIL 或 NOT_VERIFIED），不得假成功 | 未配置的 5 项正确记 `NOT_VERIFIED（未配置；配置完成但科学能力尚未验证）`；但 orca 配置路径不存在时被回落到 PATH，doctor 报 `software:orca — /usr/bin/orca` **PASS**——该二进制实为 GNOME Orca 40.3（`file /usr/bin/orca`：Python script，Igalia），非 QC ORCA；BAD_INIT exit=0 | FAIL — 原因：`resolve_executable` 在配置路径无效时静默回落 PATH，无关二进制被报为 PASS（假成功）；负例门未通过，须在 doctor/解析层修复（B41b 前提之一） |
| S5 首个本地科研闭环 | `HOME=/tmp/acp_recipe_home ACP_RUN_ROOT=/tmp/acp_recipe_runs acp run xtb_optimize --input water.xyz --output out`（water.xyz 取自功能测试手册 §3.0） | exit 0；state.json status=completed；`RESULT/result_manifest.json` 存在且 `load_result_manifest` 非 None | exit 0；Energy -5.070544374391 Hartree；WORK/03_OPT/ 留有真实 xtb 产物（xtb.log、xtbopt.xyz、.xtboptok、charges、wbo）；state=completed；manifest 解析成功（4 products，structure converged） | PASS |
| S6 Web 服务冒烟（干净 venv，备用端口） | `HOME=… ACP_RUN_ROOT=/tmp/acp_recipe_runs acp run serve --host 127.0.0.1 --port 8899 --no-browser` | `/api/status` 200 | 200（version 0.2.0，run_root=/tmp/acp_recipe_runs 隔离生效）；`/` 200（Workbench 静态页）；`/api/workflows` 200 → 15 个工作流；测毕即杀进程，未与 8765 主机服务冲突 | PASS |
| S7a 主机服务状态观测 | `systemctl is-enabled acp; systemctl is-active acp; curl -s http://127.0.0.1:8765/api/status` | enabled + active + HTTP 200 | enabled / active / 200（run_root=/var/lib/acp/runs，进程自报 version 0.1.3，uptime≈23.7h——见 §1 基线说明） | PASS |
| S7b 从脚本重建 systemd unit | `sudo scripts/install_systemd.sh --user <user> --enable` | unit 由脚本生成、daemon-reload、服务启动 | 未执行：任务约束禁止重启服务；现役 unit 生成于 2026-09-06，与当前 `install_systemd.sh` 的逐字一致性未核验 | NOT_VERIFIED — 原因：本任务禁止重启/重建服务；由 B41b 真实安装复现 |
| S8 完整 CLI 复现（B41b） | install → init → doctor → 15 工作流 CLI 全链路 + 进程重启后控制/恢复（todo 74 范围） | todo 74 交付后执行并回填证据 | 未执行（顺序修正，见 §6） | NOT_VERIFIED — 原因：B41b 等待 todo 74 交付；本任务不以首批范围代表完整发行 |

**步骤小结**：S1/S2/S3/S4b/S5/S6/S7a 全部 PASS（真实观测，命令可复跑）；S4 与 S4c 为 FAIL（doctor 本地-only 语义、错误配置回落 PATH 假成功——均如实记录不粉饰）；S3a/S7b/S8 为 NOT_VERIFIED（各附原因与复现条件）。**"向导跑完"不作为任何步骤的验收依据**（§8）。

---

## 3. Capability 报告（installed / configured / enabled / verified 四态分列）

数据来源：本任务在 §2 各步的直接观测 + 配置文件读取（只读）。每格只陈述本状态；跨状态信息放"证据"列。

| 组件 | installed | configured | enabled | verified | 证据 |
|---|---|---|---|---|---|
| ACP CLI（science-core） | YES — venv `/tmp/acp_recipe_venv/bin/acp`（acp-0.2.0 editable）；host `~/.local` acp 0.2.0 | YES — 本次 init 写入 `/tmp/acp_recipe_home/.cccp.yaml`；host `~/.cccp.yaml`（2026-09-06） | YES — tier standalone 必选组件 science-core 已启用（init 计划 deployment.components） | PASS — S2 help、S3 init、S5 闭环均 exit 0 | §2 S1–S5 |
| 本地 QC 软件：xtb | YES — `/home/xieningke/xtb-dist/bin/xtb` 可执行（6.7.1） | YES — executables.xtb.path 已配置 | YES — science-core 组件选中 | PASS — S5 真实闭环执行（xtb.log / xtbopt.xyz 产物） | §2 S5 |
| 本地 QC 软件：crest / censo / shermo / isostat | YES — 4/4 配置路径可执行 | YES — executables.{crest,censo,shermo,isostat}.path 已配置 | YES — science-core 组件选中 | NOT_VERIFIED（原因：仅 `--version` 冒烟 exit=0（crest/censo/shermo/isostat），本任务未跑对应科学任务；科学级验证属 B41b/功能手册 §3） | `--version` exit 码记录 |
| 本地 QC 软件：orca(6.1.1) / molclus | YES — `/home/xieningke/orca611/orca`、`/home/xieningke/molclus_1.14_Linux/molclus` 可执行 | YES — executables.orca.path（含 ld_library_path）/ molclus.path 已配置 | YES — science-core 组件选中 | NOT_VERIFIED（原因：`orca --version` exit=2、`molclus --version` exit=24（--version 非支持旗标），未执行任何 ORCA/molclus 科学任务；且 PATH 上的 orca 是 GNOME Orca 40.3，负例探针 S4c 证明错误配置会回落 PATH 假成功，不得当作 QC ORCA 证据） | exit 码记录 + `file /usr/bin/orca` + §2 S4c |
| Web 服务（FastAPI + Workbench 静态） | YES — uvicorn 0.54.0 + fastapi 0.146.0（venv）；acp.api + frontend 静态文件在源码树 | YES — unit Environment=ACP_RUN_ROOT/ACP_HOST/ACP_PORT；serve 参数默认 127.0.0.1:8765 | YES — 主机 acp.service enabled；本次临时实例（8899）已启动并回收 | PASS — S6：/api/status 200（0.2.0）、/ 200、/api/workflows 200（15 项）；S7a：主机 8765 /api/status 200 | §2 S6/S7a |
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
| 干净 venv 安装（S1） | 已满足 | §2 S1 PASS |
| CLI 入口（S2） | 已满足 | §2 S2 PASS |
| init 最小配置（非交互回答文件，S3） | 已满足 | §2 S3 PASS（12 PASS / 0 FAIL / 0 NOT_VERIFIED） |
| init 交互向导（S3a） | 未满足 | NOT_VERIFIED（原因：TTY 路径留给 B41b） |
| doctor 门（本地-only 语义，S4） | 未满足 | FAIL（原因：零远程节点 exit 1；需语义裁定或扩展本地检查） |
| doctor 门（含节点场景，S4b） | 已满足 | §2 S4b PASS |
| 负例探针（错误配置 typed 呈现，S4c） | 未满足 | FAIL（原因：无效配置路径回落 PATH 假成功，见 §2 S4c） |
| 首个本地科研闭环（S5） | 已满足 | §2 S5 PASS（真实 xtb 产物 + manifest 解析） |
| Web 服务可服务（S6/S7a） | 已满足 | §2 S6/S7a PASS |
| systemd unit 可重建（S7b） | 未满足 | NOT_VERIFIED（原因：禁止重启服务） |
| 真实安装复现（B41b） | 未满足 | NOT_VERIFIED（原因：延期至 todo 74 之后，见 §6） |

**退出门一状态：OPEN（部分达成）。** 已满足项不因未满足项回退；S3a/S4/S7b/B41b 四项清零前不得宣称"首批范围退出门已过"。**"向导结束"不计入任何门项**（§8）。

---

## 5. 退出门二（完整发行退出门 / Full-release exit gate）

**定义**：完整发行 = 15 个 active 工作流（Confsearch / PESsearch / BatchOptimize / XtbPathSearch / OrcaGradient / XtbConstrainedOpt / irc / scan / tsmode / casscf / nmr / singlepoint / optimize / frequency / xtb_optimize）逐项真实验收 + 完整发行验收（真实 CLI 全流程 todo 74、四组合联合验收 todo 75、发布终验 todo 76、发行清单 todo 40）。

| 门项 | 当前状态 | 依据 |
|---|---|---|
| 15 工作流 CLI 注册可枚举 | 部分 | /api/workflows 200 → 15 项（S6/S7a）——仅"可枚举"，非"已验收" |
| 15 工作流逐项真实验收 | 未满足 | 本任务仅真实验收 xtb_optimize 1 项（S5）；其余 14 项未在本任务跑 |
| 完整 CLI 复现（todo 74） | 未满足 | NOT_VERIFIED（原因：B41b 依赖，见 §6） |
| 联合验收与发布终验（todo 75/76） | 未满足 | 未开始（W14 收口任务） |
| 发行清单与文件归属（todo 40 B40b） | 未满足 | 未完成（B40b 依赖声明范围内迁移） |

**退出门二状态：OPEN（保持开放）。** 按并行计划共同约束 §8：**未进入范围的能力保持开放，不阻塞已满足的独立交付，也不计作完成**。任何"首批范围"材料（含本文档）**不得代表完整发行**。

---

## 6. B41a / B41b 顺序拆分

- **B41a（本文档，已完成）**：配方设计、四态 capability 报告、两级退出门定义、首批复现记录（含 NOT_VERIFIED 标注）。**不依赖完整发行**（评审 P1-3 顺序修正：B41a 配方设计可先做）。
- **B41b（延期，未开始）**：真实安装与完整 CLI 复现——在真实干净机器/干净账户上按本配方执行全部步骤（含 S3a 交互向导、S4 doctor 语义复核、S7b unit 重建），并与 todo 74 的完整 CLI 全流程合并验证。**B41b 必须在 todo 74 交付后执行**；**T74 不等待本任务**（todo 74 不以 B41a/B41b 为前置）。
- B41b 完成后回填本文档 §2 各 NOT_VERIFIED 行的 Actual 与结果；回填不得删除原始 NOT_VERIFIED 记录（审计痕迹保留）。
- 依赖关系：B41a blocked by todos 35—38（已完成）；B41b blocked by todos 74、40、32—34（按 T34a 声明范围）；本任务 blocks todo 69（分层验收）。

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
/tmp/acp_recipe_venv/bin/python -m pip install -e '/ACPServer/AutoCalcPlatform[api,remote]'

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
```

B41b（todo 74 之后）在真实干净机器上重复以上步骤并加上：交互向导（S3a）、systemd unit 重建（S7b）、15 工作流真实 CLI 全链路与进程重启后的控制/恢复（S8）。
