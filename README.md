# ACP — 自动化化学计算平台

当前科学执行栈为 `cccp/qc → cccp/backends → cccp/calculation → acp`，平台服务实现根为 `acp_services`；Workbench 与 RPH 并列接入同一 Controller。ACP 2.0 / RPH 的交付范围、真实验证和剩余缺口见[2026-10-28 现状说明](docs/reference/current/ACP_RPH_Status_20261028.md)。

**Auto-Calc Platform (ACP)** 是一个模块化的 Python 计算化学平台，旨在降低量子化学计算的入门门槛，通过 CLI 和 Web 界面让不熟悉计算化学流程的研究人员也能轻松提交任务、查看进度、可视化结果。

首次使用请按 **安装 Python 环境 → 安装 ACP → 准备计算软件 → `acp init` → 启动/验证** 的顺序操作。当前 `acp init` 已提供分步向导、回答文件和计划预览；下面按当前源码说明实际行为。

- [安装（推荐 Conda）](#安装推荐-conda)
- [`acp init` 详细使用方法](#acp-init-详细使用方法)
- [初始化后启动和验证](#初始化后启动和验证)
- [常见问题](#安装与初始化常见问题)
- [MCP 使用指南](#mcp-使用指南) · [ACP / RPH 当前状态](docs/reference/current/ACP_RPH_Status_20261028.md)
- [工作流示例](#快速开始) · [配置与数据目录](#配置) · [开发](#开发)

---

## 安装（推荐 Conda）

### 1. 系统与环境准备

以下命令以 **Linux / WSL2 的 Bash** 为例；涉及本地进程控制、systemd 和远程 LSF 的部署请使用 Linux 环境。WSL2 的源码和计算数据建议放在 Linux 文件系统中，例如 `~/src/AutoCalcPlatform` 与 `~/.local/share/acp/runs`。

项目声明 Python **3.10+**，以下统一使用 **Python 3.11**。首次安装 Conda 可使用 [Miniforge 官方安装说明](https://github.com/conda-forge/miniforge#install)；已有 Miniconda / Anaconda 可直接使用。其他平台的 Conda 安装步骤见 [Conda 官方文档](https://docs.conda.io/projects/conda/en/stable/user-guide/install/index.html)。安装完成后重新打开终端，确认 `conda --version` 可用。

先创建独立环境，科学依赖由 conda-forge 安装，ACP 再通过该环境的 pip 安装：

```bash
conda create -n acp -c conda-forge python=3.11 pip \
  'numpy>=2.1' 'rdkit>=2022.09.1' 'scipy>=1.10' \
  'matplotlib>=3.7' 'pyyaml>=6.0' 'openpyxl>=3.1' -y
conda activate acp

python --version
python -m pip --version
```

之后每次打开终端使用 ACP，先执行 `conda activate acp`。`conda init` 用于初始化 shell 的 Conda 支持，`acp init` 用于配置 ACP，两者用途不同。

### 2. 获取源码并安装 ACP

已获取源码时直接进入仓库根目录；首次获取时执行：

```bash
git clone https://github.com/PengYangchao0808/AutoCalcPlatform.git
cd AutoCalcPlatform

# 推荐：CLI + Web 界面 + SSH/LSF 远程管理
python -m pip install -e '.[api,remote]'

# 检查依赖与命令入口
python -m pip check
acp --help
acp init --help
```

`-e` 表示从当前源码目录安装，修改源码后继续使用同一安装；使用期间请保留该目录。以下安装方式按需求任选，均在仓库根目录、已激活的环境中执行：

| 使用场景 | 安装命令 | 说明 |
|---|---|---|
| 本地计算 CLI | `python -m pip install -e .` | 安装科学工作流及 Python 依赖 |
| CLI + Web | `python -m pip install -e '.[api]'` | 增加 FastAPI、uvicorn 等 |
| CLI + SSH/LSF | `python -m pip install -e '.[remote]'` | 增加 Paramiko，供节点配置与远程执行使用 |
| 日常完整使用（推荐） | `python -m pip install -e '.[api,remote]'` | 包含 Web 与远程依赖 |
| 开发与测试 | `python -m pip install -e '.[dev,api,remote]'` | 增加 pytest、ruff、mypy 等 |

不使用 Conda 时，可用 `python3.11 -m venv .venv`、`source .venv/bin/activate` 创建并激活虚拟环境，再执行上面的 pip 安装命令；pip 会安装科学依赖。

需要读取 Bruker 原始 NMR 数据时，可在所选安装完成后执行 `python -m pip install 'nmrglue>=0.10'`。`nmr` extra 还包含 Open Babel 和 QML；当前项目要求 NumPy ≥ 2.1，而 QML 的现有构建依赖 NumPy < 2，因此不要把 `.[nmr]` 当作通用安装步骤。FCHL/QML 能力需单独核对，缺失时的回退不等同于已验证该能力。

根目录安装方式与独立的 `cccp` / `acp-worker` / `acp-web` 角色发行是两种选择，同一环境请使用其中一种。角色制品构建和安装见 [packaging/README.md](packaging/README.md)。

### 3. 准备外部计算软件

**安装 ACP 或运行 `acp init` 都不会自动安装 ORCA、xTB 等计算程序。** 仅查看帮助、配置或启动界面时可暂时跳过；运行科学任务前，须在实际执行任务的机器上安装对应程序。

| 软件 | 主要用途 | 配置键 |
|---|---|---|
| xTB | `xtb_optimize`、xTB-MD、GFN-xTB 计算 | `executables.xtb.path` |
| CREST | 构象搜索，通常同时需要 xTB | `executables.crest.path` |
| ORCA | 单点、优化、频率、IRC、CASSCF、NMR 等 | `executables.orca.path` |
| CENSO | 构象排序与精修，按协议还需其他计算程序 | `executables.censo.path` |
| ISOSTAT / Molclus | 聚类或相关协议辅助步骤 | `executables.isostat.path` / `executables.molclus.path` |
| Shermo | 热力学修正 | `executables.shermo.path` |
| MPI 启动器 | ORCA 并行运行；版本需与 ORCA 匹配 | `executables.orca.mpi_path` |

软件按各自发行说明安装，ORCA/MPI 还需按其安装说明准备运行库环境。`acp init` 可以探测已有程序，并让你填写未找到程序的**可执行文件绝对路径**，例如 `/opt/orca/orca`，不要填写 `/opt/orca` 目录。当前不使用的软件可直接回车跳过。

### 4. 准备数据目录

源码目录、Conda 环境目录和任务数据目录分别管理。运行 `acp init`，在“任务数据保存目录 run_root”中填写绝对路径或 `~/ACP_runs`；配置保存后，新启动的 CLI / Web 服务会自动读取。普通用户不配置时默认使用 `~/.local/share/acp/runs`。也可临时覆盖：

```bash
mkdir -p "$HOME/.local/share/acp/runs"
export ACP_RUN_ROOT="$HOME/.local/share/acp/runs"
```

建议使用本机原生文件系统，避免把大量 QC 中间文件放在 WSL `/mnt/*` 或网络挂载中。`ACP_RUN_ROOT` 会优先于向导配置；需要恢复读取 init 配置时执行 `unset ACP_RUN_ROOT`。

向导将目录保存到 `deployment.params.run_root`。已有 systemd 服务若设置了固定目录，可运行 `bash scripts/install_systemd.sh --run-root '' --run-root-only` 清除服务覆盖并重启；以后修改 init 配置后只需重启服务。填写向导本身不会移动历史数据，切换前应停止服务并迁移已有任务（见后文“数据目录”）。

## `acp init` 详细使用方法

### 命令与配置文件的含义

```bash
# 推荐：初始化当前用户配置
acp init

# 创建/更新指定配置；父目录必须已存在且可写
mkdir -p "$HOME/.config/acp"
acp init --config "$HOME/.config/acp/workstation.yaml"

# 查看参数；需要排查时增加日志
acp init --help
acp init --log-level DEBUG
```

| 参数 | 含义 |
|---|---|
| `--config PATH` | **要创建或更新的目标文件**；默认 `~/.cccp.yaml` |
| `--answers PATH` | 使用版本化 YAML 回答文件，无交互提示，直接预览并应用 |
| `--plan` | 预览最终初始化计划，不应用该计划；交互资源步骤的写入边界见下文 |
| `--log-level LEVEL` | 日志级别，默认 `INFO`，排查可用 `DEBUG` |

`~/.cccp.yaml` 的名称沿用底层配置约定。`acp init --config X` 表示**写入 X**，而 `acp run <workflow> --config X` 表示**加载 X**。保存到自定义文件后，工作流运行时要显式传 `--config X`，或采用自动加载的用户配置 `~/.cccp.yaml` / 项目配置 `./cccp.yaml`。Web 服务和 `acp doctor` 当前没有 `--config` 参数，请使用它们会自动加载的配置位置。

已有配置会按字段更新并保留其他配置项，实际改写前生成 `<配置文件>.bak-<时间戳>` 备份，再原子替换；YAML 注释和原始排版不会保留。文件不存在时可新建；已有文件如果 YAML 解析失败或根节点不是映射，会拒绝覆盖。父目录需提前创建。

### 交互式向导：单机用户推荐流程

先激活环境并执行 `acp init`，按下面顺序选择。菜单输入数字；带默认值的文本问题可回车接受默认值。**数字菜单不能直接回车选择第一项。**

| 步骤 | 出现的内容 | 单机用户怎么选 |
|---|---|---|
| 1. 使用方式 | 初始化本机部署 / 连接已有 ACP 服务 | 选 `1` 初始化本机部署 |
| 2. 部署档位 | Standalone / Lab Server / Hosted | 选 `1` Standalone |
| 3. 已有资源 | 本地 / 已声明节点 / 声明新集群 | 选 `1` 本地，检查软件表并补路径；返回资源菜单后按 `q` 继续 |
| 4. 组件组合 | 默认科学核心、本地身份、本地存储；可额外选组件 | 仅 CLI 直接回车；需要 Web 界面填写 `1` |
| 5. 参数与秘密 | 数据根、外部访问地址、秘密引用 | 数据根填写绝对路径或 `~/ACP_runs`；直接访问填 `-`；无凭据需求时回车跳过 |
| 6. 计划预览 | 目标文件、差异、组件、外部动作和回退提示 | 核对后选 `1` 应用；选 `2` 取消最终计划 |
| 7. doctor | 配置和能力报告 | 检查 `FAIL`；完成后回到“使用方式”菜单，按 `q` 退出 |

这里有两个不同的 `q`：**资源菜单的 `q` 表示完成资源配置、继续后续步骤；使用方式菜单的 `q` 才是退出整个向导。** Ctrl-C / EOF 会中止向导，已经保存的修改继续保留。

本地检测显示软件的候选安装、实际解析路径和版本。已配置路径、环境变量、PATH/当前 Python 环境以及常规安装位置都会参与解析；多套安装不应靠猜测选择，请显式固定路径。唯一的常规目录扫描命中会自动保存；缺失程序可手动填写可执行文件路径，不使用时回车跳过。MPI 检测项名为 `mpi`，填写的是 `mpirun` 路径，保存到 `executables.orca.mpi_path`。

**保存时机与预览范围：** 本地软件路径、远程节点声明、远端 `~/.cccp.yaml` 和可选符号链接可能在资源步骤中即时保存或执行。最后“取消”、按 `q` 或交互式 `acp init --plan` 都不会撤销这些已发生的操作。需要只查看配置差异而不执行资源步骤时，使用下面的 **`--answers ... --plan`**。

### 部署档位与组件怎么选

| 档位 | 回答文件 `tier` | 当前要求 |
|---|---|---|
| Standalone（推荐起步） | `standalone` 或 `local-single` | 科学核心、本地身份、本地存储；可加 Web 或远程节点 |
| Lab Server | `lab` | 还需 Web、认证、隔离组件及 `database` 资源引用；应用后有额外 Lab 能力检查 |
| Hosted | `hosted` | 当前标为 `NOT_VERIFIED`，拒绝启用 |

“本机部署”指在这台机器配置 ACP，也可以连接远端 LSF 节点；部署档位和计算任务是否远程执行是两项选择。个人连接集群计算时，可以使用 Standalone 配置远程节点，无需因此选择 Lab。

当前可选组件如下；程序会自动补充组件依赖。**选择组件用于记录和校验配置，不会自动安装 pip extra、启动服务或开通外部资源。**

| 交互编号 | 组件名 | 用途 |
|---|---|---|
| 默认启用 | `science-core`、`local-identity`、`local-storage` | 科学核心、本地单用户身份与存储 |
| `1` | `web-ui` | Web 界面，仍需安装 `api` extra 并手动启动 |
| `2` | `mcp-server` | MCP 组件声明 |
| `3` | `authentication` | 认证能力声明 |
| `4` | `isolation` | 多用户隔离能力声明 |
| `5` | `external-iam` | 外部 IAM/OIDC |
| `6` | `object-store` | 对象存储 |
| `7` | `usage-metering` | 用量计量 |
| `8` | `payment` | 支付/账单 |

多个编号用逗号分隔，例如 `1,3,4` 满足 Lab 所需的额外组件选择；Lab 还必须提供已有数据库引用。支付、对象存储、外部 IAM 默认关闭。计划中列出的外部动作不会由最终配置写入自动完成，报告会保留 `NOT_VERIFIED`。

### 配置已有集群或新增远程计算节点

先在管理 ACP 的机器上安装 `remote` extra。远端需准备 SSH 账户、可用的 LSF / Openlava 环境、Python 3.10+ 及计算程序；远端可先创建 Python 3.11 的 Conda 环境。节点初始化会同步代码并通过 pip 安装 [requirements-node.txt](requirements-node.txt) 中的运行依赖，远端需能访问依赖源或已有相应依赖。

在“已有资源”菜单中选择现有节点，可重新探测软件并补填远端路径；禁用或无效的节点会提示先修正配置。新增时选“声明新集群”，按以下顺序填写：

1. 集群类型：`LSF` 或 `Openlava`；当前这个向导没有 Slurm 选项。
2. 主机地址、节点名称、SSH 端口（默认 `22`）、用户名和密码/SSH 私钥路径。私钥路径是 ACP 管理机上的路径。
3. 连接测试；未知主机会显示指纹并询问是否接受，已知主机密钥发生变化会中止。
4. 远端代码目录（默认 `~/acp_code`）、远端任务目录（默认 `~/acp_jobs`）、最大并发任务数（默认 `5`）、LSF 队列（默认 `normal`，请改为实际队列）。
5. Bootstrap：同步代码并安装节点运行依赖。找不到可用 Python 时，可在重试中填写远端 Conda 环境的解释器绝对路径，例如 `/home/alice/miniforge3/envs/acp/bin/python`。
6. 软件检测：缺失时填写**远端机器上**的可执行文件路径，可回车跳过。补填会更新远端用户的 `~/.cccp.yaml`，并可选择创建 `~/bin` 符号链接；这些操作会修改远端文件。
7. 凭据保存与执行模式：密码默认不写 YAML，按提示通过 `ACP_REMOTE_PASSWORD_<节点名规范化值>` 环境变量提供；如果希望经该集群执行，选择“设为 remote”。

Bootstrap 失败可重试或放弃，放弃时节点不保存；已经同步的远端文件或已安装依赖仍可能存在。新增完成会返回资源菜单，可继续添加节点或按 `q` 进入组件选择。配置完成后执行 `acp doctor --node <节点名称>` 检查实际节点。

### 连接已有 ACP 服务

第一步选 `2`“连接已有 ACP 服务”，依次填服务 URL、项目标识（可空）、凭据引用（可空，如 `env:ACP_TOKEN`），再预览和应用。

这一模式只将信息保存到 `deployment.service`，用于记录连接配置；当前向导不会因此创建本地服务器、数据库或集群，也不会将后续普通 `acp run` 命令自动转成远程 API 提交。使用已有服务的 Web 界面时，直接访问其 URL。

### 使用回答文件：可重复、非交互初始化

回答文件是“向导输入”，`--config` 是“生成/更新的配置”，请使用两个不同的文件。下面内容保存为 `init-standalone.yaml`：

```yaml
version: 1
usage: local
tier: standalone
components:
  - science-core
  - local-identity
  - local-storage
  - web-ui
resources:
  cluster:
    type: local
  # 如需固定软件路径，取消以下注释并改成真实的可执行文件路径。
  # software:
  #   orca: /opt/orca/orca
  #   xtb: /opt/xtb/bin/xtb
  #   mpi: /opt/openmpi/bin/mpirun
params:
  public_origin: ""  # 直接访问；反向代理时填浏览器使用的完整 origin
```

该示例使用运行时默认数据目录或 `ACP_RUN_ROOT`。需要自定义数据根时，可在 `params` 内增加 `run_root: ~/ACP_runs`；初始化会保存绝对路径，运行时自动读取。

```bash
# 先预览：不写配置、不运行交互资源步骤和节点 bootstrap
acp init --config "$HOME/.cccp.yaml" --answers init-standalone.yaml --plan

# 核对后应用：无额外确认提示，写配置后运行能力检查
acp init --config "$HOME/.cccp.yaml" --answers init-standalone.yaml
```

回答文件模式也不会自动安装计算程序或 bootstrap 节点。`resources.software` 的路径会写入 `executables`，`resources.cluster` 写入 `cluster`；外部资源引用放在 `resources.database` / `resources.https` / `resources.storage`，保存后归入 `deployment.resources`。`params` 和 `secrets` 分别归入 `deployment.params` 和 `deployment.secrets`。

连接已有服务的回答文件示例：

```yaml
version: 1
usage: connect
service:
  url: https://acp.example.org
  project: demo
  credential_ref: env:ACP_TOKEN
```

需要秘密引用时，本机部署回答文件可增加：

```yaml
secrets:
  - name: db_password
    ref: env:ACP_DB_PASSWORD
```

`secrets` 支持 `env:NAME`、`file:/绝对路径`、`provider:key`，保存的是引用；不要使用明文 `value`。引用的提供方、凭据和资源仍需在运行环境中配置。

相同回答文件再次应用且目标内容相同时，会提示“无需写入（幂等）”；原子配置写入有并发修改检查。回答文件应用返回 `0` 表示应用与检查未发现 `FAIL`；返回 `1` 可能是输入/档位拒绝，也可能是**配置已经写入但后续检查失败**，请阅读输出确认。交互退出或 Ctrl-C 可以返回 `0`，因此退出码本身不代表完成初始化。

## 初始化后启动和验证

推荐安装 `.[api,remote]` 后，先检查配置，再以前台方式启动 Web：

```bash
conda activate acp
acp doctor

# 使用前面设置的 ACP_RUN_ROOT；保持当前终端运行
acp run serve --host 127.0.0.1 --port 8765

# 或显式指定数据目录（先创建并确保可写）
# acp run serve --host 127.0.0.1 --port 8765 --run-root /data/acp/runs
```

浏览器访问 `http://127.0.0.1:8765`。服务运行在另一台机器上时，`127.0.0.1` 指那台机器；可用 SSH 端口转发后访问。`acp init` 保存配置后不会自动启动或重启服务，已有服务需按原部署方式重启，配置和外部访问地址才会重新加载。

`acp doctor` 可检查本地 Standalone 或已配置的远端节点；`acp doctor --node <节点名称>` 检查指定节点。独立的 `doctor` 命令当前会加载远程管理模块，需要 `remote` extra；仅安装核心 CLI 时，可先查看 `acp init` 应用后自带的能力报告。

- `PASS`：该检查项满足条件，例如文件可解析或组件依赖齐全。
- `FAIL`：有明确错误，例如已固定的软件路径不存在，需要修正。
- `NOT_VERIFIED`：未配置或尚未验证，例如未安装的可选程序、外部资源动作。
- Lab 报告还会列出 `UNAVAILABLE`；声明组件和资源引用不代表认证、隔离、数据库或真实 LSF 作业已通过验收。

报告通过后，使用[工作流示例](#快速开始)运行小任务验证科学环境，例如在已安装 xTB 时运行 `acp run xtb_optimize --input molecule.xyz --output /你的计算数据目录/xtb-smoke`（准备真实 XYZ 并替换输出路径）。路径探测和配置检查通过并不代表真实 QC 计算已经验证。

## 安装与初始化常见问题

| 现象 | 排查与处理 |
|---|---|
| 找不到 `acp` 或导入了另一套环境 | 先 `conda activate acp`，再用 `command -v acp`、`python -m pip --version` 核对；也可用 `python -m acp init` |
| `conda activate` 提示先初始化 shell | 执行 `conda init bash`（其他 shell 替换名称），重新打开终端后激活环境 |
| 缺少 FastAPI / uvicorn / Paramiko | 在仓库根目录执行 `python -m pip install -e '.[api,remote]'` |
| 按资源菜单 `q` 后没有退出 | 这是进入下一步；完成/取消后在“使用方式”菜单按 `q` 退出 |
| 最终取消或 `--plan` 后配置仍有变化 | 交互资源步骤可能已保存路径、节点或远端配置；需要纯预览时用 `--answers FILE --plan` |
| 保存自定义配置后 Web / doctor 不生效 | 它们没有 `--config` 参数；使用运行账户的 `~/.cccp.yaml` 或服务工作目录的 `cccp.yaml`，同时检查环境变量覆盖 |
| 向导设置的数据目录没有生效 | 重启服务，检查 `--run-root` / `ACP_RUN_ROOT` 覆盖；旧 systemd 配置可用 `bash scripts/install_systemd.sh --run-root '' --run-root-only` 恢复读取 init 配置 |
| 修改 `~/.cccp.yaml` 后结果仍不符 | 检查当前目录 `cccp.yaml`、显式 `--config`、`CONFSEARCH_*` 和 CLI 覆盖；已有服务需重启 |
| 提示“目标目录不可写或不存在” | 先创建配置文件父目录，使用当前账户可写的位置；不要为初始化普通用户配置而切换到 root |
| 软件路径校验失败 | 填可执行文件而非目录，检查存在性与执行权限；远端软件要填远端路径 |
| Lab 被拒绝或能力未验证 | 核对 `web-ui`、`authentication`、`isolation` 和数据库资源引用，并阅读 Lab 报告；个人部署从 Standalone 开始 |
| SSH 成功但节点 bootstrap 失败 | 核对远端 Python 版本、pip 依赖源、目录写权限和报错尾部；必要时固定 Conda 环境解释器路径 |

---

## 开发状态

最新[ACP2 功能审查（2026-10-28—29）](reports/acp2_function_audit_20261028/REPORT.md)：非慢 Python 回归 12125 PASS / 1 FAIL / 26 SKIP，真实 DSH SDK 7 PASS；浏览器首次 26 PASS / 18 FAIL，其中 10 项在隔离部署 Origin 后复验通过。Workbench 操作遮挡、数值编辑和默认 RPH 装配等问题仍需处理；完整候选尚未放行。后续建议按[修订路线图第 6 节](<docs/RPH_Agent_Three_Phase_Roadmap_20261027(1).md#6-审查后的开发建议2026-10-29>)推进。

截至 2026-10-28（源码基线 `b32f22bb`），四层科学栈、Services 实现根、MCP 研究工具和 RPH 运行时已落地；真实模型、本地 QC、LSF、浏览器与独立安装已有分项验收。RPH 默认产品装配、同一条真实模型＋QC 科研闭环及完整 DA 配方仍有缺口，完整 ACP2 `candidate_full.allowed=false`。

当前事实与证据统一见[现状说明](docs/reference/current/ACP_RPH_Status_20261028.md)；后续 RPH 方向见[三阶段路线图](<docs/RPH_Agent_Three_Phase_Roadmap_20261027(1).md>)。下表的 Phase 0–4 是早期交付历史，不对应 RPH 三阶段，也不能用于推定 Agent 已交付。[旧架构方案](docs/ACP_Architecture_Adjustment_Plan_20261016.md)和[四组分方案](docs/ACP_2.0_Four_Component_Collaboration_Plan_20261020.md)保留设计沿革。

文档入口见 [docs 导航](docs/README.md)。现行契约与下一轮方案附件分别归入 `docs/reference/current/`、`docs/reference/next-change/`；旧方案、审计记录及演示材料统一保存在 `docs/archive/2026-10-16/`，不作为另一套实施路线。

| 阶段 | 状态 | 内容 |
|------|------|------|
| **Phase 0** | ✅ 完成 | 底层 `cccp`（Computational Chemistry Connection Package）QC 接口库 |
| **Phase 1** | ✅ 完成 | 模块化重构 + `acp` 统一模块 + Confsearch/PESsearch/BatchOptimize/irc/scan 计算工作流 + nmr/simple 工作流 + CENSO 集成 |
| **Phase 2** | ✅ 完成 | FastAPI Web 后端 + 任务调度器 + 远程 LSF 执行 |
| **Phase 4** | ✅ 完成 | 极简化重构：mechanism/ 删除，BatchOptimize/irc/scan 独立工作流 |
| **架构整改** | ✅ 完成（2026-10-05 收口） | 四层架构：`cccp/qc`（子进程+科学适配）→ `cccp/backends`（能力适配）→ `cccp/calculation`（任务层唯一执行驻点）→ `acp`（工作流/规划/发布；primitives/backends 为纯兼容转发）|
| **ACP 2.0 / Services / MCP** | 已实现并有分项真实验收；完整放行未通过 | 研究记录、审批/预算/恢复、八个基础工具与协商后解锁的研究工具；研究消费接口按部署装配；四个 Python 发行与独立安装回执 |
| **RPH Agent** | 第一阶段未整体通过；第二阶段已有部分基础；第三阶段待实施 | 默认入口装配、完整真实模型＋QC 轨迹、科研策略、跨 harness 与多 Agent 仍按现状说明逐项记录 |

> 注：conformer / benchmark / ensemble / energy / xtbmd_censo_energy / mechanism / mech-conf / mech-step / mech-confirm / mech-chain / optfreq / optfreqsp / Lowconfirm / Highconfirm 工作流已于 2026-08-28 退役（catalog 中保留为 `status:"retired"` 仅用于历史作业展示）。构象搜索能力统一由 `acp run Confsearch --protocol <4种协议>` 提供；低精度确认由 `acp run BatchOptimize --profile opt_freq` 提供；高精度确认由 `acp run BatchOptimize --profile opt_freq_sp_thermo` 提供。

---

## MCP 使用指南

MCP 是让 AI 客户端**替你跑计算**的一座桥：客户端按 MCP 协议说话，ACP 只收 HTTP，`acp mcp serve` 站在中间做翻译。接好之后，你可以直接说“用 GFN2-xTB 优化乙醇”，客户端会自己完成提交、等待和取结果。

如果这台机器上已经有 Controller 在跑，最短路径只有三步：

1. 在客户端的 MCP 配置里加一条，指向 `acp mcp serve --controller http://127.0.0.1:8765`；
2. 重启客户端，让它调用一次 `acp_capabilities`（对话式客户端里说一句“列一下 ACP 能做什么”就行）；
3. 直接用自然语言提计算需求。

下面把这三步展开，并说明每一步实际发生了什么。

### 1. 先看懂三个角色

```text
你的 AI 客户端（Claude Desktop / DSH / 其他 MCP 客户端）
   │  启动子进程，用 stdin/stdout 收发一行行 JSON-RPC 消息
   ▼
acp mcp serve                 ← 适配器：只做协议转换，无状态
   │  HTTP
   ▼
ACP Controller（acp run serve）← 真正排队、调度、执行
   │
   ▼
CCCP / xTB / ORCA / LSF …
```

| 角色 | 是什么 | 你要做什么 |
|---|---|---|
| Controller | 常驻服务，管任务队列、数据目录和计算软件 | 先启动，记下 URL |
| `acp mcp serve` | 客户端拉起的翻译进程，不启动服务、不碰数据库 | 一般不用手动运行 |
| MCP 客户端 | 你平时用的 AI 工具 | 写一段配置指向适配器 |

两个容易误会的地方：

- **适配器不是服务**。它不保存任务状态，关掉它不会取消任何任务；任务都落在 Controller 上，所以在网页和 CLI 里看到的是同一份任务。
- **适配器不会替你启动 Controller**。Controller 没起来，适配器启动后直接退出（退出码 3）。

### 2. 准备：让 Controller 先跑起来

MCP 入口在 `acp` 命令里：用 `acp` 完整安装（或只装 `acp-web` 角色包）才有；只装 `cccp` 或 `acp-worker` 不会有 `acp mcp serve`。ACP 这一侧不需要 Node、DSH 或模型 API key；这些只在用到对应客户端或模型规划功能时才需要。

已经在跑 Controller 的话，跳过这节，直接用它的地址。没有的话，先初始化（向导和安装步骤里的是同一个），再启动：

```bash
acp init
mkdir -p "$HOME/ACP_runs"
acp run serve --host 127.0.0.1 --port 8765 --run-root "$HOME/ACP_runs"
```

`$HOME/ACP_runs` 换成你有写权限的本机目录——要用原生文件系统，不要放在 `/tmp` 或网络盘上。如果你已经在 `acp init` 里配过 run root，直接 `acp run serve` 就行，不必再传 `--run-root`。再开一个终端确认它活着：

```bash
curl --fail --silent http://127.0.0.1:8765/api/health
```

看到 `"status":"ok"` 就说明 Controller 正常。计算软件的路径、运行目录和远程节点都在 Controller 这一侧配置，MCP 不读这些配置；一个数据根同时只由一个 Controller 使用。

### 3. 接上客户端

多数 MCP 客户端用同一套 `mcpServers` 配置结构。`command` 要写**装有 ACP 那个环境里的** `acp` 绝对路径——桌面应用不一定继承你终端的 PATH，只写 `acp` 很可能找不到：

```json
{
  "mcpServers": {
    "acp": {
      "command": "/opt/acp/venv/bin/acp",
      "args": ["mcp", "serve", "--controller", "http://127.0.0.1:8765"],
      "env": {"ACP2_MCP_LOG_LEVEL": "INFO"}
    }
  }
}
```

配置文件的位置各个客户端不同（例如 Claude Desktop 用的是 `claude_desktop_config.json`），但字段含义一样；改完记得重启客户端，让它重新读配置。

DSH（DeepSeek Harness）不用 `mcpServers` 结构，而是在 profile 的补丁层里加一条 MCP 客户端配置；工具会以 `mcp__acp__<工具名>` 的形式出现：

```yaml
# ~/.dsh/profiles/<profile>/cordis.patch.yml
- insert:
    - id: mcp-acp
      name: "@deepseek-ai/dsh-mcp-client"
      config:
        serverName: acp
        transport: stdio
        command: /opt/acp/venv/bin/acp
        args: [mcp, serve, --controller, http://127.0.0.1:8765]
        env:
          ACP2_MCP_LOG_LEVEL: INFO
```

`failOnStartupError` 默认 `false`，Controller 没起来时 harness 照常启动、只是没有这组工具；想让它启动即报错可以设为 `true`。改完重启 harness 生效。

想先手动确认能不能启动，也可以自己跑一遍：

```bash
acp mcp serve --transport stdio --controller http://127.0.0.1:8765
```

它会安静地停在 stdin 上等输入——这是正常的，不是卡死，也不是聊天窗口。stdout 只走协议帧，诊断日志全部写到 stderr。

#### 认证：本机单人，还是多人共用

- **本机单人**：不设置任何 key，适配器使用 `local-single` 身份，适合自己电脑上的试验。
- **多人共用**：在 MCP 进程的环境里设置平台签发的 `ACP_API_KEY`。适配器启动时拿它向 Controller 换出用户、项目和权限，之后每次工具调用前都会重新校验；key 被撤销后，已经连着的会话也会被拒绝。注意这是 ACP 平台的凭据，不是模型服务的 key。

key 只能通过环境变量传，`acp mcp serve` 没有 `--api-key` 参数；客户端不一定把你终端里的环境变量传给 MCP 子进程，配完记得核对。请求里的 `project_id` 只是提示，改不了身份和权限。

#### Controller 地址与远程访问

地址优先级从高到低：`--controller` → `ACP_CONTROLLER_URL` → 兼容变量 `ACP2_CONTROLLER`。新配置直接写参数最稳；`ACP2_CONTROLLER_URL` 是 RPH 那一层用的变量，MCP 入口不读它。

Controller 在另一台机器时，不用把端口暴露到公网，开一条 SSH 隧道即可：

```bash
ssh -N -L 18765:127.0.0.1:8765 user@controller-host
```

然后配置里写 `--controller http://127.0.0.1:18765`。注意这仍然是“本机 stdio 适配器 → HTTP Controller”：当前没有 MCP Streamable HTTP 服务，不能把 Controller 地址或 `/events` 当成 MCP 的 HTTP/SSE 地址。

#### 排查用的开关

```bash
acp mcp serve --controller http://127.0.0.1:8765 --log-level DEBUG --max-frame-bytes 1048576
```

`--log-level` 默认读 `ACP2_MCP_LOG_LEVEL`，没有就用 INFO。单帧默认上限 1 MiB，最小 1024 字节；轨迹和大日志应该走工件读取（工件就是登记过、能按身份读取的结果文件），不要靠放大帧解决。

### 4. 验证：先问一句“你能做什么”

让客户端调用一次 `acp_capabilities`（大多数客户端启动时会自动列工具，也可以手动让它调）。这一步不建任务、不花预算，只做发现：

```json
{"name": "acp_capabilities", "arguments": {}}
```

正常返回 `status: "ok"`，`data` 里至少有：

- `workflows`：这台 Controller 可用的工作流（当前是 15 个）；
- `backends`：本机计算软件是否可用；
- `limits`：核数、墙钟、读取大小等上限；
- `tools`：当前会话能看到的工具列表。

这一步失败就先去第 7 节排错，通常是 Controller 地址、进程路径或权限问题。

客户端连上后的调用顺序是 `initialize` → `notifications/initialized`，随后才用 `tools/list` 取工具列表。服务器只认 `2025-06-18` 这个协议版本：你请求别的版本，它仍然回答 `2025-06-18`，由客户端自己判断能不能继续；能力以 `tools/list` 的实际结果为准，不要只看版本字符串。

> 第 5、6 节里的 JSON 都是 MCP `tools/call` 的 `params`，给能直接调工具的客户端和调试用；对话式客户端会替你组织这些参数，你只要把需求说清楚。

### 5. 跑通第一道计算

以“用 GFN2-xTB 优化乙醇”为例，顺序是固定的：预检 → 提交 → 轮询 → 取结果。

**① 预检：只校验、不建任务**

```json
{
  "name": "acp_preflight",
  "arguments": {
    "workflow": "xtb_optimize",
    "input": {"source": "CCO", "source_type": "smiles", "charge": 0, "multiplicity": 1},
    "method": {"gfn": 2},
    "resources": {"cores": 1},
    "execution_mode": "local"
  }
}
```

**② 提交：同一份描述，加一个本次操作唯一的 key**

```json
{
  "name": "acp_submit",
  "arguments": {
    "workflow": "xtb_optimize",
    "input": {"source": "CCO", "source_type": "smiles", "charge": 0, "multiplicity": 1},
    "method": {"gfn": 2},
    "resources": {"cores": 1},
    "execution_mode": "local",
    "idempotency_key": "9f1f5e0e-7d2b-4a8e-9b34-2b0f5b1e6c11"
  }
}
```

`idempotency_key` 是这次提交的“重试身份”，也是新人最容易忽略的一项：

- 网络断了要重试：**沿用同一个 key 和同一份请求**，平台会把第一次的结果还给你，不会重复建任务；
- 换了输入、方法或目标：**换一个新 key**；
- 同一个 key 配不同请求会冲突（`revision_conflict`），不要靠不停换 key 去猜第一次是否成功。

提交会立刻返回，不等计算跑完。保存返回里的 `data.reference`（任务引用）和 `data.task_ref`（含 attempt 等信息）。要跑远程，把 `execution_mode` 改成 `remote`，按需带 `target_node` / `node_tags`，并重新预检。示例里的 `charge=0`、`multiplicity=1` 只适用于乙醇，换成别的分子要自己判断。

**③ 查状态**

```json
{"name": "acp_status", "arguments": {"reference": {"kind": "job", "id": "JOB_ID_FROM_SUBMIT"}}}
```

可以按需轮询，查状态不需要调用模型。**关掉客户端不会取消任务**——任务继续跑，重连后用原 reference 继续查。

**④ 取结果：先 prepare，再 read**

任务完成后，先把要读的文件登记成“工件”。读取不是按文件路径，而是按 prepare 返回的身份；prepare 时要给出文件在任务目录内的相对路径，这样读取也能被权限和大小上限管住：

```json
{
  "name": "acp_prepare_artifact",
  "arguments": {
    "purpose": "artifact",
    "job_id": "JOB_ID_FROM_SUBMIT",
    "artifact_id": "result_manifest",
    "relative_path": "RESULT/result_manifest.json",
    "idempotency_key": "REPLACE_WITH_A_NEW_UUID"
  }
}
```

`artifact_id` 只是这次登记的名字，真正读哪个文件由 `relative_path` 决定。prepare 可能立刻完成，也可能返回一个待处理的 `operation_ref`——那就用 `acp_status` 查这个 operation。拿到完整 `artifact_ref` 后再读：

```json
{
  "name": "acp_read_artifact",
  "arguments": {
    "artifact_ref": {
      "task_id": "JOB_ID_FROM_SUBMIT",
      "attempt": 1,
      "artifact_id": "ARTIFACT_ID_FROM_PREPARE",
      "generation": 1,
      "digest": "sha256:REPLACE_WITH_THE_RETURNED_64_HEX_DIGEST"
    },
    "max_bytes": 65536,
    "encoding": "identity"
  }
}
```

`task_id`、`attempt`、`artifact_id`、`generation`、`digest` 必须整组照抄返回的身份，缺一个都读不了；`max_bytes` 上限 1 MiB。没 prepare 就读会得到 `resource_not_ready`，这时应该回头 prepare，而不是反复重试读取。读取不会触发计算，也不会自动远程拉取。

想中途撤销：

```json
{"name": "acp_cancel", "arguments": {"reference": "JOB_ID_FROM_SUBMIT"}}
```

取消是请求，不是立刻生效；之后查一次状态确认终态。

**看懂返回**：工具结果同时有 `content` 和 `structuredContent`。判断成败看 `structuredContent.status`、`error` 和 `isError`——工具失败也可能以**正常的 JSON-RPC 响应**返回，别只看有没有 JSON-RPC `error`。

**八个基础工具一览**

| 工具 | 干什么 | 关键参数 |
|---|---|---|
| `acp_capabilities` | 问平台能做什么 | 可传 `{}`；协商研究扩展也用它 |
| `acp_preflight` | 提交前模拟，零副作用 | `workflow`、`input` |
| `acp_submit` | 提交任务 | `workflow`、`input`、`idempotency_key` |
| `acp_status` | 查任务或准备操作的状态 | `reference` |
| `acp_cancel` | 请求取消 | `reference`，可带 `expected_revision` |
| `acp_prepare_artifact` | 把结果登记成可读工件 | `purpose=artifact` 时还要 `job_id`、`artifact_id`、`relative_path` |
| `acp_read_artifact` | 读已登记的工件 | 完整 `artifact_ref` |
| `acp_analyze` | 显式调用已注册的分析 | `analysis_id`、`input_refs`、`explicit=true` |

`acp_analyze` 必须显式触发：传 `explicit=true` 和已经准备好的完整工件引用。`vibration.analyze` 读已有频率证据、不启动计算；`vibration.compute` 是计算能力，要按 `frequency` 任务提交，不能当只读分析。任务 `completed` 只说明任务结束，科学结论是否成立要另外核对分析结果。

### 6. 进阶：研究扩展（协商后解锁更多工具）

研究工具默认**不可见**，要先做一次应用层协商（和 MCP `initialize` 的协议版本协商是两件事）：

```json
{
  "name": "acp_capabilities",
  "arguments": {
    "research_extension": {"id": "acp.research.v1", "version": "1.0.0"},
    "client": {"name": "my-research-client", "version": "1"}
  }
}
```

看返回 `data.extensions[]` 里对应项的 `implemented`、`negotiated` 是否为 true、`refusal` 是否为空。确认成功后**重新拉一次 `tools/list`**，工具列表会从 8 个增加：

- 固定多出 10 个 `acp.research.v1` 提案/审批工具；
- 如果这个入口还接上了研究消费接口（本仓库当前的 `acp mcp serve` 已接入），会再多 10 个研究消费工具（模型规划、用量、研究记录、证据读取、预算、检查点、就绪检查），总数 28 个。

没接消费接口的部署只看到前 18 个；具体以你客户端实际拿到的 `tools/list` 为准。

版本号写错不会让会话崩掉，但那一次 `acp_capabilities` 调用会失败（`isError=true`，`unsupported_capability`，`rule=extension_version_mismatch`），工具列表停在 8 个。

| 研究工具 | 干什么 | 必需参数 |
|---|---|---|
| `acp_research_proposal_create_v1` | 建提案 v1，并请求与计划绑定的审批 | `project_id`、`proposal`、`plan` |
| `acp_research_proposal_read_v1` | 读某个提案版本 | `proposal_ref` |
| `acp_research_proposal_update_cas_v1` | 按 revision 更新提案；计划变了会开新一轮 | `proposal_ref`、`expected_revision`、`proposal`、`plan` |
| `acp_research_approval_request_v1` | 请求审批，不替人做决定 | `proposal_ref`、`plan_digest` |
| `acp_research_approval_query_v1` | 查 pending/approved/rejected/expired/revoked | 无；通常带引用 |
| `acp_research_approval_decide_v1` | 由有权限的身份批准或拒绝 | `approval_ref`、`decision`、`expected_revision` |
| `acp_research_approval_revoke_v1` | 撤销审批 | `approval_ref`、`expected_revision` |
| `acp_research_wait_v1` | 有界等待 approval/saga/proposal/record 事件 | `kinds` |
| `acp_research_snapshot_v1` | 按 `kind:ref` 读快照 | `refs` |
| `acp_research_resume_v1` | 按持久研究状态恢复 | `proposal_ref`、`plan_digest` |

消费工具是另外 10 个 `acp_research_model_*` / `acp_research_record_*` / `acp_research_evidence_read_v1` / `acp_research_budget_read_v1` / `acp_research_checkpoint_v1` / `acp_research_readiness_v1`，对应 Controller 的 `/api/v2/model/*` 与 `/api/v2/research/*`，用来读写研究记录、证据视图和预算。普通计算用不到它们。

有界等待长这样：

```json
{"name": "acp_research_wait_v1", "arguments": {"kinds": ["approval", "saga"], "max_wait_s": 20, "limit": 100}}
```

返回的 cursor 是不透明字符串，原样存、原样传，别自己解析或拼接；游标 900 秒后过期，过期会得到明确的快照重置，按服务端规则处理，不会静默跳到最新位置。CAS 冲突时重读 revision，再决定怎么合并。

**研究提交**仍然走 `acp_submit`，但要带上已批准计划对应的 `proposal_ref` 和 `plan_digest`。只带其中一部分也会进入研究校验，不匹配就拒绝；有效预算由服务端按审批决定，客户端自己填的数不算。

> 现状边界：研究工具和恢复契约已经实现，但 `rph research` 默认的模型、writer、证据读取和预算装配还没完成。工具可见、握手成功，都不等于“完整的自主科研 Agent 已经可用”，当前范围见 [ACP / RPH 现状说明](docs/reference/current/ACP_RPH_Status_20261028.md)。

### 7. 排错

先跑只读探针。它只做 `initialize`、工具发现和 `acp_capabilities`，不提交任务。新建一个 `mcp-launch.json`（`command` 用绝对路径，`cwd` 是已存在的目录）：

```json
{
  "command": "/opt/acp/venv/bin/acp",
  "args": ["mcp", "serve", "--controller", "http://127.0.0.1:8765"],
  "cwd": "/ACPServer/AutoCalcPlatform",
  "env": {"ACP2_MCP_LOG_LEVEL": "INFO"}
}
```

然后：

```bash
python3.11 docs/reference/next-change/examples/acp_mcp_stdio_probe.py --launch mcp-launch.json --out /tmp/acp-mcp-probe
```

`--out` 必须是一个还不存在的目录。探针只验证未协商的八工具路径，不验证研究扩展和真实计算。

| 现象 | 大概原因 |
|---|---|
| 客户端说找不到 `acp` | 桌面应用的 PATH 与终端不同，改绝对路径 |
| 退出码 2 | 缺 Controller 地址、transport 不是 stdio，或参数非法 |
| 退出码 3 | Controller 连不上或不健康；查 URL、`/api/health`、SSH 隧道 |
| 退出码 4 | 适配器启动异常，或 `ACP_API_KEY` 在 Controller 端解析失败；看 stderr |
| 退出码 78 / `mutually_exclusive_distributions` | 根发行与角色发行混装，重建环境 |
| 进程起来没反应 | 它在等协议输入，正常；用客户端或探针握手 |
| 协商后还是 8 个工具 | 扩展没协商成功（看 `refusal`），或客户端缓存了旧列表 |
| 日志出现 `server/discover` unknown method | 新版客户端的探测，会自动回退到 `initialize`，不用管 |
| `access_denied` | key、项目归属或动作权限不对；请求字段扩不了权 |
| `backend_unavailable` | 这台 Controller 上缺计算软件；先看 `acp_capabilities.data.backends`，再查 Controller 进程的 PATH 与配置 |
| `input_invalid` / `idempotency_key_required` | 参数不符合 Schema；submit 必须带 key，未知字段不会被静默忽略 |
| `invalid_parameter` | 参数不合法，例如 prepare 工件时漏了 `relative_path`；按 `tools/list` 的 Schema 补齐 |
| `revision_conflict` | 同一个 key 被不同请求复用，或 attempt/工件/revision 过期 |
| `resource_not_ready` | 先 prepare，再查 operation，最后用返回的完整引用读 |
| `approval_required` / `budget_exhausted` | 缺匹配审批或预算不足；改客户端字段绕不过去 |
| `unsupported_capability` | 看扩展的 `refusal`；注册、启用、可执行是三件事 |

干净 EOF 或 Ctrl-C/SIGTERM 关闭适配器返回 0；取消计算要显式调 `acp_cancel`。更早的环境与验收步骤见[旧版 MCP/DSH 手册](docs/reference/next-change/ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md)，当前命令以本节和 `acp mcp serve --help` 为准。

本节按 2026-10-29 的当前源码与本机 Controller 实测核对，包括实际完成的握手、`tools/list` 和扩展协商。示例只说明 MCP 的调用方式；各工作流本身的执行完整度，以 `acp_capabilities` 返回的能力和各自的验收记录为准。

---

## 功能概览

### 1. Confsearch — 统一构象搜索 + 能量排名 `acp run Confsearch`
- SMILES / XYZ / GJF / LOG / OUT / SDF / MOL / ORCA INP 多格式输入
- 四种协议（实际阶段，以代码为准）：
  - `xtb-crest`：CREST 构象搜索（GFN-xTB 级）+ xTB 能量排序。**纯 xTB 协议，无 DFT 精修阶段**（非 `screen` 精修策略仅告警并按 `screen` 处理）
  - `xtb-md`：xTB-MD 采样 + 构象提取（同样**无 DFT 精修**）
  - `censo-crest`：CREST 搜索 + CENSO 排序/精修（DFT 级别由 `--preset` 决定）
  - `xtbmd-censo`：xTB-MD 采样 + ISOSTAT 聚类 + CENSO 排序/精修
- 资源档位：`--profile light|default|high`
- 精修策略：`--refinement-policy screen|rank1|cumulative-99|all`（`screen` 为默认且在所有协议下都**不追加**精修候选）
- 统一产物：`confsearch_manifest.json` 供下游 PESsearch 消费

### 2. PESsearch — 势能面搜索 `acp run PESsearch`
- 从 Confsearch manifest 出发，搜索反应路径（PEB引导扫描 / 直接TS猜测）
- 输出：`RESULT/pes_search/pes_profile.json` + TS/中间体候选结构
- 输入方式：`--from-job <Confsearch job id>` 或 `--from-artifact RESULT/confsearch/confsearch_manifest.json`
- 人工确认（2026-09-03 起）：Workbench 能量与轨迹查看器上手动增删/修改 TS、INT 选点后
  `POST /api/v1/jobs/{id}/pes/review` 写入 `RESULT/pes_search/pes_review.json`，
  物化 `RESULT/structures/*.xyz` 并更新 `RESULT/result_manifest.json`；
  历史 mechanism 任务保持只读（410）。详见 `docs/reference/current/ACP_PES_Manual_Review_DevDoc.md`

#### 能量与轨迹查看器（Energy & Trajectory）

Workbench "能量与轨迹"标签页是 PES 扫描、几何优化、构象搜索、独立扫描的统一可视化入口：
- 统一 `TrajectoryFrame` 契约，所有视图共享节点/标注数据形状
- 通用帧操作：查看结构 / 锁定 / 导出 XYZ / 保存为候选（TS / INT / NONE）
- xtb-md / xtbmd-censo 协议额外提供采样历史三视图：能量轨迹、采样空间（MDS 二维散点）、覆盖度（饱和度指标 + 累计唯一曲线）
- 优化视图收敛面板：RMS/MAX 梯度和位移 vs 阈值达标判定
- IRC / NEB 视图已注册（IRC 能量曲线已接线：`irc_trajectory_v1` 正/反向双 series，运行中自动刷新，可从历史 `WORK/<stage>/ORCA` 轨迹只读回填；NEB 投影仍未实现）。IRC 逐帧结构浏览与路径动画由下方结构查看器提供

详见 `docs/reference/current/ACP_Energy_Trajectory_Viewer_DevDoc.md`

#### 结构查看器（Structure Viewer）

Workbench "结构查看器"标签页（原 3D + 构象集合合并）在选中任务后自动加载结构目录：
- 按工作流自动解析：Confsearch 构象（能量 + Boltzmann 权重）、PES 推荐/人工确认（严格分组）、
  BatchOptimize 条目（含失败末帧）、scan 帧、IRC 正/反向逐帧、退役任务只读兼容模式
- 虚频可视化：频率列表、位移箭头、振动动画、TS 一阶鞍点证据提示（只读证据，判定归 Batch/IRC）
- 轻量几何编辑：键长/键角/二面角 + 撤销/重做 + 碰撞警告 + 另存为结构资产
- 结构叠合 RMSD（同序直配 / RDKit MCS 唯一映射 / 无法证明则清除跨结构测量）
- IRC 路径动画、大体系线框降级、视图状态（相机/样式/测量）本地持久化

结构查看器端点：`GET /api/v1/jobs/{id}/structure-viewer` · `GET .../entries/{e}/geometry` ·
`GET .../entries/{e}/vibrations` · `GET .../structure-viewer/overlay?entry_a=&entry_b=`

详见 `docs/reference/current/ACP_Structure_Viewer_DevDoc.md`

### 3. BatchOptimize — 批量优化确认 `acp run BatchOptimize`
- 对 PESsearch 候选或其他结构进行 per-item Opt/TS + 频率 + 单点能 + 热力学修正
- 四种 profile：`--profile opt_only|opt_freq|opt_freq_sp|opt_freq_sp_thermo`
- 支持 `--items-file` 批量输入、`--from-job` 继承上游产物（读取
  `<PES任务>/RESULT/result_manifest.json` 中人工确认的有效候选）、
  `--select pes_ts_frame_027,...` 按稳定 candidate_id 筛选
- 前端 batch_structures 提交在 runner 层自动物化为 items 文件（2026-09-03 起）
- TS 候选自动识别（TAG: TS），频率分析 + IRC 连通性检查

### 4. IRC — 端点验证 `acp run irc`
- 对 TS 结构运行 IRC（正向 + 逆向），发现反应端点
- 必填互斥组：`--ts-provenance <文件路径>`（手工路径）或 `--ts-provenance-json <内联JSON>`（调度器专用）；建议同时显式 `--input-role transition_state`
- 输出：`RESULT/irc/irc_forward.xyz` + `RESULT/irc/irc_reverse.xyz`
- 端点分类：connectivity fingerprint + mapped heavy-atom RMSD

### 5. Scan — 势能面扫描 `acp run scan`
- 柔性坐标扫描（distance/angle/dihedral），逐步优化 + 单点能
- 默认**不启用 ScanTS**（普通柔性扫描）；需 TS 导向扫描时显式加 `--scants`
- 输出：`RESULT/trajectories/scan_trajectory.json` + 能量曲线
- 坐标格式：`--coordinate atom1,atom2,start,end`

### 6. 简单 ORCA 工作流 — `acp run singlepoint|optimize|frequency|...`
- 单点能计算（singlepoint）
- 几何优化（optimize）
- 频率计算（frequency）
- xTB 优化（xtb_optimize）

### 6a. PES2TS 执行统一 — `acp run XtbPathSearch | OrcaGradient`
- `XtbPathSearch`：GFN2-xTB PATH metadynamics（消费冻结 `pes2ts_xtb_path_request_v1` payload，`--path-config`）
- `OrcaGradient`：ORCA 单点解析梯度 EnGrad（消费冻结 `pes2ts_orca_gradient_request_v1` payload，`--gradient-config`）
- 两者均为本地专用（不在远程允许集）

### 7. NMR 化学位移预测 — `acp run nmr` ✅
- GIAO + Boltzmann 平均 + DP4/DP5 立体归属
- Goodman DP5 模型 + FCHL 原子表示（可选）

### 7. Web 服务 — `acp run serve`
- FastAPI 后端（`/api/status`, `/api/backends`, `/api/workflows`, `/api/v1/...`）
- 任务提交、分子上传、任务管理 REST API
- ACP Workbench 前端（暗色主题，实时轮询）
- systemd 服务管理
- 兼容提示：`GET /api/workflows` 现恰好返回 14 个 active 工作流（合成 `fake` id 不再公开列出，内部提交受理不变）；外部脚本请直接消费响应内容，勿硬编码旧的 15 项固定列表

### 8. 远程 LSF 执行 ✅
- SSH/SFTP 多节点连接池
- LSF 脚本生成 + bsub 提交 + bjobs 监控
- 增量代码同步 + 结果拉取
- 磁盘压力 + 保留期清理

### 9. 任务队列操作 ✅（2026-08-17 起）
- **暂停 / 恢复**：运行中任务可暂停（`PAUSED`），本地 SIGSTOP/SIGCONT 进程组冻结/复活、远程 LSF `bstop`/`bresume`；**暂停不释放内存/磁盘配额**，适合临时让出算力
- **断点续算**：失败/取消任务按检查点继续（`continue` 操作）
- **重新运行**：原地重跑（同任务目录、attempt+1；旧 attempt 回执归档至 `WORK/00_RUNTIME/attempts/`，不再另起 `{name}__rerun` 新任务）
- **清除**：单任务级联删除（jobs + stage_tasks + artifacts + tasks 索引行），支持按状态/项目/时间批量清除
- **任务详情**：阶段 stepper、错误详情（error + stderr 尾部）、产物摘要、恢复操作建议
- **分组浏览**：按分子/备注/任务类型/提交批次分组（默认分子折叠）；多维筛选（状态/工作流/标签/分子/批次，同维并集、跨维交集）；排序（创建时间/完成时间/最近活动/名称自然排序）；手动标签、归档、批量操作；整项目计数不受分页影响
- **分子管理**：分子别名解析（大小写不敏感匹配）、合并建议、分组显示名设置
- **来源链路**：只读浏览任务上下游依赖关系（BFS ≤10 跳）

队列操作端点：`GET /api/v1/jobs/{id}/detail` · `POST /api/v1/jobs/{id}/pause|unpause|continue|rerun` · `POST /api/v1/jobs/purge`

---

### 旧入口退役映射表

| 旧入口 | 替代方案 | 说明 |
|--------|----------|------|
| `acp run ensemble` | `acp run Confsearch --protocol censo-crest --refinement-policy screen` | CREST+CENSO P+S |
| `acp run energy` | `acp run Confsearch --protocol censo-crest --refinement-policy rank1` 或 `cumulative-99` | 构象能量排名 |
| `acp run xtbmd_censo_energy` | `acp run Confsearch --protocol xtbmd-censo` | xTB-MD+CENSO 全链路 |
| `acp run mechanism` | `acp run PESsearch` → `acp run BatchOptimize` → `acp run irc` | 机理研究拆分为独立阶段 |
| `acp run Lowconfirm` | `acp run BatchOptimize --profile opt_freq` → 粗优化确认 | 退役 |
| `acp run Highconfirm` | `acp run BatchOptimize --profile opt_freq_sp_thermo` → 精细优化确认 | 退役 |
| `acp run optfreq` | `acp run optimize` + `acp run frequency` 或 `acp run BatchOptimize` | 优化+频率 |
| `acp run optfreqsp` | `acp run BatchOptimize --profile opt_freq_sp` | 优化+频率+单点 |

---

## 包结构

```
src/
├── acp/                          # 工作流/规划/发布层 (~247 .py)
│   ├── cli.py                    # 统一命令行入口：run/doctor/serve；retired 子命令拦截
│   ├── catalog.py                # WORKFLOW_CATALOG + METHOD_META + METHOD_SCHEMAS（≈4.3k 行）
│   │
│   ├── confsearch/               # 统一构象搜索 + 能量
│   │   ├── engine.py             # ConfsearchEngine：协议调度、质量门控
│   │   ├── contracts.py          # 协议特定约束和质量门控
│   │   ├── manifest.py           # confsearch_manifest.json 产物
│   │   ├── profiles.py           # light / default / high 资源档位
│   │   ├── selection.py          # 候选筛选与排序
│   │   ├── protocols/            # 四种协议实现（xtb_crest / xtb_md / censo_crest / xtbmd_censo）
│   │   └── shared/               # 协议共享工具
│   │
│   ├── calculations/             # 规划/发布层（执行在 cccp.calculation 任务核心）
│   │   ├── contracts.py          # CalculationPlan / CalculationRequest / Checkpoint 等冻结数据类
│   │   ├── checkpoint.py         # 原子 JSON checkpoint 写入/加载（plan fingerprint 校验）
│   │   ├── executor.py           # CalculationPlanExecutor：dispatch 到 cccp.calculation.run_* + checkpoint resume
│   │   ├── plans.py              # build_simple_plan / build_batch_plan / build_irc_request
│   │   ├── primitives/           # 纯兼容转发 → cccp/calculation/tasks/* + ACP 发布契约
│   │   ├── result_publication.py # RESULT/result_manifest.json 统一注册入口
│   │   ├── pes/                  # PESsearch 核心：scan + engine + contracts + validation + path_analysis + atom_mapping
│   │   ├── batch/                # BatchOptimizeEngine + models + loaders（底层 cccp.calculation.batch）
│   │   ├── irc/                  # IRC endpoint discovery + validation（转发 cccp/calculation/irc_endpoints）
│   │   └── tsmode/               # 选虚频 → 定向 OptTS 引擎
│   │
│   ├── compat/                   # 遗留布局只读兼容层（legacy/ manifests + layouts）
│   ├── results/                  # 统一结果清单读取 + TrajectoryFrame 帧契约 + 结构查看器
│   ├── storage/                  # 统一 v2 结果清单写入 (result_manifest.json)
│   │
│   ├── core/                     # 共享核心机制（无化学逻辑）
│   │   ├── models.py             # Structure, StructureRecord, StructureEnsemble
│   │   ├── workflow.py           # Stage, WorkflowSpec, WorkflowRunner
│   │   ├── state.py              # WorkflowState, EventLog (JSONL)
│   │   ├── registry.py           # 兼容 shim（→ cccp/core/registry）
│   │   ├── config.py             # 配置加载/合并
│   │   └── paths.py              # run_root 解析 + 慢文件系统哨兵
│   │
│   ├── backends/                 # 纯 re-export shim（→ cccp/backends）+ 隔离的 legacy batch 面
│   │
│   ├── workflows/                # 工作流编排（14 active）
│   │   ├── nmr.py                # NMR 化学位移预测（GIAO + DP4/DP5）
│   │   ├── simple.py             # 简单工作流 (singlepoint/optimize/frequency/scan/irc/xtb_optimize)
│   │   ├── pes_search.py         # PESsearch · batch_optimize.py · irc.py · tsmode.py
│   │   ├── xtb_path.py           # XtbPathSearch（pes2ts 冻结 payload）
│   │   ├── orca_gradient.py      # OrcaGradient（pes2ts 冻结 payload）
│   │   ├── registry.py           # 工作流注册表（CLI 子命令 → WorkflowSpec）
│   │   └── ensemble/energy/xtbmd 退役引擎 — 仍作 Confsearch 协议引擎复用
│   │
│   ├── chem/                     # 化学逻辑（RDKit embedding + composition）
│   ├── intake/                   # 数据摄入（models / parsers 6 格式 / storage）
│   ├── io/                       # 分子结构 I/O（StructureReader/Writer，thin cccp wrapper）
│   │
│   ├── api/                      # FastAPI 服务
│   │   ├── server.py             # FastAPI app 工厂 + static 托管
│   │   ├── routes.py             # /api/status, /api/backends
│   │   ├── v1_routes.py          # v1 任务/分子/文件 API
│   │   ├── v2_routes.py + v2_structure_sources.py   # v2 API + 结构来源组织
│   │   └── schemas.py            # Pydantic 模型
│   │
│   └── scheduler/                # 任务调度器（jobs/manager/runner/store/stage_tasks/tasks + remote/ LSF 远程执行）
│
└── cccp/             # Computational Chemistry Connection Package（QC 底座，≈86 .py）
    ├── config.py                 # 6 源 YAML 配置（读 ~/.cccp.yaml，回退 ~/.conformer_search.yaml）
    ├── software.py               # 可执行解析单点（resolve_executable / discover_all）
    ├── version.py                # __version__（4 处同步：__init__ ×2 + acp/__init__ + pyproject）
    ├── core/                     # ProtocolSpec + CandidateSet + registry（ConformerEngine dormant）
    ├── qc/                       # ① QC 子进程层
    │   ├── interfaces/           # ORCA / CREST / xTB / CENSO / ISOSTAT / Molclus 子进程封装
    │   ├── runners/ + cluster/   # Shermo/ISOSTAT 运行器；Local + LSF 适配器
    │   └── hessian_policy / method_meta / resolved_spec / shermo_adapter /
    │       thermo_normalize / translation / keyword_registry   # 共享科学适配（ qc/ 顶层）
    ├── backends/                 # ② 能力 Protocol 适配层（ORCA/CREST/xTB/CENSO/Isostat/Molclus；无 subprocess）
    ├── calculation/              # ③ 任务层 — 唯一执行驻点
    │   ├── tasks/                # 14 个任务核心（singlepoint/optimize/frequency/scan/irc/casscf/
    │   │                         #   thermochemistry/conformer_search/md_sampling/clustering/
    │   │                         #   censo_refine/nmr_shielding/xtb_path_search/orca_gradient）
    │   ├── requests.py + results.py   # typed options / payload 契约
    │   ├── _common.py + context.py + errors.py + selection.py + progress.py
    │   └── batch.py              # 通用批量执行器（并发/缓存/进度）
    ├── io/                       # MolecularInputHandler
    ├── pipeline/                 # PipelineExecutor（thin）
    └── utils/                    # 文件 I/O, 常量, 几何工具, 溶剂映射
```

### 设计原则

- **四层架构（2026-10-05 收口）**: ① `cccp/qc` 子进程接口 + 共享科学适配 → ② `cccp/backends` 能力 Protocol 适配（无 subprocess）→ ③ `cccp/calculation` 任务层（**唯一执行驻点**，14 个任务核心）→ ④ `acp` 工作流/规划/发布。`acp.calculations.primitives` 与 `acp.backends` 为纯兼容转发/shim；`cccp` 禁止导入 `acp`
- **core/ 只放通用机制**：数据模型、工作流引擎、状态管理、注册表——不含任何化学特定逻辑
- **能力协议**：QC 后端通过 Protocol 声明能力（GeometryOptimizer, FrequencyCalculator 等），实现体在 `cccp/backends/`；子进程封装在 `cccp/qc/interfaces/`
- **函数式 Stage 管道**：工作流由 Stage 函数组装，支持灵活组合
- **统一入口**：所有计算经 `acp run <workflow>` 触发（`python -m cccp` 不可用）；14 个工作流的执行全部经 `cccp.calculation` 任务核心

---

## 快速开始

### ACP 新入口（推荐）

```bash
# 查看帮助
acp --help

# === Confsearch — 统一构象搜索 + 能量 ===
acp run Confsearch --input "CCO" --protocol xtb-crest --refinement-policy screen --output ./out
acp run Confsearch --input "CCO" --protocol censo-crest --profile light --output ./out
acp run Confsearch --input "CCO" --protocol xtbmd-censo --refinement-policy rank1 --output ./out
acp run Confsearch --input "CCO" --protocol xtb-md --refinement-policy cumulative-99 --output ./out

# === PESsearch — 势能面搜索 ===
acp run PESsearch --from-job 20260823_001_Confsearch --output ./pes_out
acp run PESsearch --from-artifact RESULT/confsearch/confsearch_manifest.json --output ./pes_out

# === BatchOptimize — 批量优化确认 ===
acp run BatchOptimize --from-job 20260823_002_PESsearch --output ./batch_out
acp run BatchOptimize --items-file structures.xyz --profile opt_freq_sp_thermo --output ./batch_out

# === IRC — 端点验证（provenance 必填：文件用 --ts-provenance） ===
acp run irc --input ts_structure.xyz --input-role transition_state \
    --ts-provenance prov.json --output ./irc_out

# === Scan — 势能面扫描（默认不启用 ScanTS；显式 --scants 开启） ===
acp run scan --input "CCO" --coordinate 3,4,1.0,3.0 --output ./scan_out

# === 简单 ORCA 工作流 ===
acp run singlepoint --input molecule.xyz --method "wB97X-D4" --basis "def2-TZVPPD"
acp run optimize --input molecule.xyz --method "r2SCAN-3c"
acp run frequency --input molecule.xyz
acp run xtb_optimize --input molecule.xyz

# === PES2TS 执行统一（本地专用） ===
acp run XtbPathSearch --path-config request.json --output ./path_out
acp run OrcaGradient --gradient-config request.json --output ./grad_out

# === NMR 化学位移预测（运行时必须恰好提供 --spectrum 或 --bruker 之一） ===
acp run nmr --input "CCO" --spectrum exp_spectrum.txt --output ./nmr_results
acp run nmr --input "CCO" --spectrum exp_spectrum.txt --nmr-method mPW1PW91 --nmr-basis "6-311G(d)"

# === 初始化向导（详见前文 acp init 详细使用方法） ===
acp init

# === Web 服务 ===
acp run serve --port 8765
```

### 底层 QC 库（cccp）

`cccp`（Computational Chemistry Connection Package）是 `acp` 之下的 QC 接口库，
不再提供独立 CLI 入口——所有计算均通过 `acp run <workflow>` 触发。

---

## CLI 选项

```
acp run Confsearch --input <SMILES或文件路径>
                   --output <输出目录>
                   --protocol <xtb-crest|xtb-md|censo-crest|xtbmd-censo>
                   --profile <light|default|high>
                   --refinement-policy <screen|rank1|cumulative-99|all>
                   --nproc --mem --config ...

acp run PESsearch --from-job <Confsearch job id>
                  --from-artifact <confsearch_manifest.json 路径>
                  --output <输出目录>
                  --nproc --mem --config ...

acp run BatchOptimize --from-job <PESsearch job id>
                      --from-artifact <result_manifest.json 路径>
                      --items-file <structures.xyz 路径>
                      --profile <opt_only|opt_freq|opt_freq_sp|opt_freq_sp_thermo>
                      --output <输出目录>
                      --nproc --mem --config ...

acp run irc --input <TS 结构文件路径> \
            --ts-provenance <irc_ts_source_v1 JSON 文件> \
            --input-role transition_state \
            --output <输出目录>
            [--direction <forward|reverse|both>]
            [--nproc --mem --config ...]

acp run scan --input <SMILES或文件路径> \
             --coordinate <atom1,atom2,start,end> \
             [--scants]
             [--geom-maxiter <N>]
             --output <输出目录>
             [--nproc --mem --config ...]

acp run singlepoint|optimize|frequency|xtb_optimize --input <结构文件路径（.xyz/.gjf/.com/.inp）>
                  --output <输出目录>
                  --method <method> --basis <basis>
                  --nproc --mem --config ...

acp run XtbPathSearch --path-config <pes2ts_xtb_path_request_v1 JSON> --output <输出目录>
acp run OrcaGradient --gradient-config <pes2ts_orca_gradient_request_v1 JSON> --output <输出目录>
                      --nproc --mem --config ...

acp run nmr --input <SMILES或文件路径> --output <输出目录>
            (--spectrum <实验谱文件> | --bruker <Bruker原始数据目录>)   # 运行时二选一（必填）
            [--nuclei "1H,13C"] [--nmr-method mPW1PW91] [--nmr-basis "6-311G(d)"]

acp run serve [--host <host>] [--port <port>] [--reload]
```

- **IRC provenance 必填互斥组**：文件路径用 `--ts-provenance`；`--ts-provenance-json` 仅供调度器注入**内联 JSON 文本**（非文件路径），二者必选其一（缺失 → exit 2）。`--step` 仅为兼容保留，对 ORCA 计算无影响。
- **scan**：默认**不启用 ScanTS**，`--scants` 为显式 opt-in（多坐标同步/显式网格扫描不可用）；`--geom-maxiter` 每点几何优化迭代上限（映射 ORCA `%geom MaxIter`，默认 200，与 `optimize` 复用同一选项）。
- **nmr**：运行时必须恰好提供 `--spectrum` 或 `--bruker` 之一。

---

## 配置

### 配置文件方式

首次配置推荐使用前文的 [`acp init` 向导或回答文件](#acp-init-详细使用方法)。使用自定义配置文件时，初始化与计算命令要指向同一文件：

```bash
# 创建/更新配置（父目录需已存在）
acp init --config my_config.yaml

# 再加载该配置运行计算
acp run Confsearch --input "CCO" --config my_config.yaml
```

### 配置合并顺序（后覆盖前）

1. Python 内置默认值 `_get_default_config()`（**唯一权威源**）
2. `~/.cccp.yaml`（用户目录）
3. `./cccp.yaml`（项目目录）
4. `--config` 文件（命令行指定）
5. `CONFSEARCH_*` 环境变量
6. CLI 参数（`--nproc`, `--mem` 等）

> ⚠️ `config/defaults.yaml` 仅供参考——Python 内置函数 `_get_default_config()` 是唯一权威默认值源。

### 数据目录（run_root）——两层目录约定

**安装目录 ≠ 数据目录**：所有任务产物（`WORK/`、`RESULT/`、QC 中间文件）与任务索引 `acp_jobs.db` 都落在数据目录 run_root，必须位于**原生文件系统**（WSL 下不要放 `/mnt/*`——9p 网络挂载会让 QC 子进程 I/O 慢约 1000 倍）。

解析优先级：`--run-root` CLI 参数 > `ACP_RUN_ROOT` 环境变量 > `acp init` 保存的 `deployment.params.run_root` > 平台默认（root → `/var/lib/acp/runs`；普通用户 → `~/.local/share/acp/runs`）。向导支持绝对路径和 `~/`，保存时展开为绝对路径；相对配置路径会被拒绝，避免服务工作目录改变后将任务写到错误位置。

```bash
# 查看生效的数据目录
acp run serve                       # 启动时打印生效 run_root；落在慢文件系统会 WARNING
ACP_RUN_ROOT=/data/acp/runs acp run serve

# 迁移历史数据（旧树保留只读归档，DB 自动备份 + 路径改写 + 校验）
python scripts/migrate_run_root.py ./ACP_runs /var/lib/acp/runs --dry-run
python scripts/migrate_run_root.py ./ACP_runs /var/lib/acp/runs
```

启动哨兵 `acp.core.paths.check_run_root_safety` 会对慢文件系统（9p/nfs/cifs/fuse 等）、与安装目录重叠、磁盘空间不足打警告（只警告不阻断；`ACP_ALLOW_SLOW_FS=1` 豁免）。

---

## 开发

### 运行测试

```bash
# 运行所有测试
pytest tests/ -v

# 运行特定模块测试
pytest tests/test_acp_backends.py -v
pytest tests/test_acp_workflows_nmr.py -v
pytest tests/test_acp_mechanism_study.py -v

# 运行 CENSO 集成测试
pytest tests/test_acp_censo_p5_acceptance.py -v

# 运行远程执行测试
pytest tests/test_remote_phase*.py -v

# 按标记筛选
pytest -m "not slow" -v
```

当前测试状态：**247 测试文件，≈5.7k 收集用例**，涵盖 cccp 任务层 + ACP 核心 + 工作流 + API + 调度器 + 远程执行 + 架构守护（`test_architecture_invariants.py`：唯一基元定义/依赖方向/能力证据表）

### 代码质量

- core/ 不含任何化学特定逻辑 ✅
- 所有 `__init__.py` 仅含 re-exports ✅
- 配置源已统一（3→1）✅
- FunnelRunner 已清除；`cccp/pipeline/` PipelineExecutor 保留为 thin 编排（dormant）✅
- 原子化文件写入（os.replace 防崩溃）✅
- 预提交：ruff lint + ruff-format + mypy (strict)

---

## 文件统计

| 项目 | 文件数 | 代码行数 |
|------|--------|----------|
| `src/acp/` | ~247 | ~105,000 |
| `src/cccp/` | 86 | ~35,000 |
| `tests/` | 247 | ~130,000 |
| 合计 | ~580 | ~270,000 |

---

## 架构图

```
┌───────────────────────────────────────────────────────────────────┐
│  CLI                                                               │
│  acp run Confsearch|PESsearch|BatchOptimize|XtbPathSearch|         │
│         OrcaGradient|irc|scan|nmr|simple|tsmode|casscf|serve       │
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│  WorkflowRunner (acp/core/workflow.py) + 工作流编排 (acp/workflows) │
│  计算计划驱动管道；单步经 CalculationPlanExecutor，多步走 batch engine │
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│  cccp/calculation — 任务层（唯一执行驻点，tasks/ 14 任务核心）        │
│  typed options/payload 契约 + 通用批量执行器 batch.py               │
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│  cccp/backends — QC 能力适配层（acp/backends 为纯 re-export shim）  │
│  ORCA / CREST / xTB / CENSO / ISOSTAT / Molclus                    │
│  能力协议：GeometryOptimizer / SinglePointCalculator / ConformerSearcher│
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│  cccp/qc — QC 子进程层（interfaces/runners/cluster + 科学适配）      │
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│  Scheduler (acp/scheduler/)                                        │
│  Job Manager → Local Runner / Remote LSF Runner                    │
│  Provenance / Artifacts / Store / Stage Tasks                      │
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│  FastAPI Server (acp/api/)                                         │
│  /api/status, /api/backends, /api/workflows, /api/v1/...          │
│  ACP Workbench Frontend                                            │
└───────────────────────────────────────────────────────────────────┘
```

---

## 链接

| 资源 | 位置 |
|------|------|
| Web 服务 | http://127.0.0.1:8765（启动 `acp run serve` 后；WSL 用户优先使用 127.0.0.1 避免 IPv6 问题）|
| 前端仪表盘 | `frontend/ACP_Workbench_v2.html`（v1 `ACP_Workbench.html` 为遗留只读）|
| 开发文档 | `docs/`（CENSO 集成、MethodMeta、Simple Workflows） |
| 三层架构设计 | [设计阶段文档](docs/reference/next-change/ACP_Three_Tier_Architecture_20261016.md)（旧 HTML 阐释版未包含在当前检出中） |
| 产品内核可视化手册 | 历史 v1.5 HTML 未包含在当前检出中；现有材料见[归档索引](docs/archive/2026-10-16/README.md) |

---

## 系统服务

```bash
sudo systemctl restart acp          # Reload after code changes
sudo systemctl start acp            # Start
sudo systemctl stop acp             # Stop
sudo journalctl -u acp -f          # Tail logs
```

- **Service**: `acp.service`
- **Config**: `/etc/systemd/system/acp.service`
- **User**: `<user>`
- **URL**: http://127.0.0.1:8765
- **Reload reminder**: After any code modification, run `sudo systemctl restart acp`.

HTTPS 反向代理部署需配置浏览器访问的公网地址，避免代理转发后的 HTTP 被同源校验误拒绝。
运行 `acp init`，在本机部署的“参数与秘密”步骤填写“外部访问地址”；回车保留已有地址，
输入 `-` 恢复直接访问模式。地址保存到 `~/.cccp.yaml` 的 `deployment.params.public_origin`，
服务以同一用户启动时自动读取；已运行的服务需重启。迁移后重新运行向导修改地址即可。

自动化初始化可使用 `acp init --answers init-answers.yaml`：

```yaml
version: 1
usage: local
tier: standalone
params:
  public_origin: https://acp.example
```

也可通过部署环境覆盖该配置（适用于容器或已有 systemd 服务）：

```bash
# 配置现有服务；保留原主 unit，生成独立配置并重启
bash scripts/install_systemd.sh --origin-only --public-origin https://acp.example

# 迁移后改为新域名或 IP:端口，无需修改代码
bash scripts/install_systemd.sh --origin-only --public-origin https://new.example:9443

# 改为直接访问 ACP 时恢复请求 Host/scheme 校验
bash scripts/install_systemd.sh --origin-only --public-origin ''
```

脚本生成 `/etc/systemd/system/acp.service.d/public-origin.conf`，其中的
`ACP_PUBLIC_ORIGIN` 是部署配置，不随代码绑定域名。手动或容器启动时可直接设置该环境变量。
优先级为 `ACP_PUBLIC_ORIGIN` 环境变量 > `acp init` 保存的配置 > 直接按请求 Host/scheme 校验；
环境变量显式设为空字符串时恢复直接访问。
配置须为完整的 `http://主机[:端口]` 或 `https://主机[:端口]`，不含业务路径；未设置时保持直接访问方式。
配置后浏览器写请求仅接受该公网地址；无 Origin 的本地机器调用仍保留。其他域名、协议或端口仍会被拒绝；
服务不从请求的 `Origin` 或未经信任的 `X-Forwarded-*` 头推断公网地址。

---

## 许可证

MIT License
