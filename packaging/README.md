# 独立角色发行

`scripts/build_release.py` 从同一源码树出树构建三个互不重叠的 wheel 和源码包：

| 发行名 | 安装内容 | 依赖 / 入口 |
|---|---|---|
| `cccp` | `cccp/**`、科学模型、LICENSE/notice、`py.typed` | 科学依赖；Python API，无 CLI |
| `acp-worker` | `acp_services/**` | 同版本 `cccp`；`acp-worker` 或 `python -m acp_services.worker_bootstrap` |
| `acp-web` | `acp/**`、`acp_web_assets/**` | 同版本 `cccp` / `acp-worker`、Web/远端依赖；`acp` |

版本取现有 `src/cccp/version.py`，构建阶段生成 VERSION 与角色依赖文件。
每个输出目录必须为空，防止旧制品混入 manifest。构建机先安装声明的
`setuptools>=65.0` 和 `wheel`，再执行：

```bash
python scripts/build_release.py --out /绝对路径/新的发行目录
```

输出 `release-manifest.json` 记录各角色源码及制品 SHA-256。源码包包含
VERSION、依赖文件、MANIFEST.in 和资源，可独立重新构建。

在仓库外创建不继承系统包的虚拟环境，选择相应角色安装：

```bash
python -m venv /绝对路径/新的角色环境
/绝对路径/新的角色环境/bin/python -m pip install \
  --find-links /绝对路径/发行目录 'acp-worker==0.3.0'
```

上述依赖版本是当前示例，以 manifest 的版本为准。根 `pyproject.toml`
保留现有单发行安装方式以兼容已有部署；一个环境选择根 `acp` 发行或这组
独立角色，避免把两个发行的同路径文件混装。

验收工具：

```bash
# 用构建环境从三个源码包构建 wheel，比较包内容和资源：
python -m pip wheel --no-deps --no-build-isolation --wheel-dir /临时/重建目录 \
  /发行目录/cccp-*.tar.gz /发行目录/acp-worker-*.tar.gz /发行目录/acp-web-*.tar.gz
python scripts/acceptance/check_release_artifacts.py \
  --artifacts /发行目录 --roundtrip-wheels /临时/重建目录 --output /证据/artifacts.json

# 将 PYTHONPATH 清空，在仓库外用每个角色的解释器运行；--role 为 cccp/worker/web：
env -u PYTHONPATH /角色环境/bin/python /源码绝对路径/scripts/acceptance/release_role_probe.py \
  --role worker --output /证据/worker.json
```

CCCP 探针使用明确的受控 backend；worker 使用显式 `--probe-executor`，
验证冻结组、执行权、回执、无 ACP/Web 安装、无 jobs DB 与重复投递拒绝。
Web 探针启动隔离临时服务，验证安装后的 HTML、JS 及 ETag 重验证。
这些结果不替代真实 QC、真实 LSF 或生产发布。

worker 为缺省输出目录显式使用 `--work-dir`，相对输出目录也基于该目录，
避免科学接口采用启动进程的 CWD。冻结计划原文与身份不因运行位置补齐而改写；
已有显式绝对输出路径保持其声明。真实 xTB 独立 worker 的工件落点反例及修复回执
另见本次修复报告。

本地自动恢复需要运维显式委派 cgroup v2 根，并在启动 worker 时设置
`ACP_EXECUTION_CGROUP_ROOT` 或 `--execution-cgroup-root`，同时启用
`--attempt-lock`。资源计划沿用现有 isolation 契约。绑定失败即拒绝科学执行；
未配置时正常领取仍可用，但异常死亡接管必须另有停止证明。历史无边界锁、
仍有后代进程、路径/inode/设备/boot ID 不符均不能自动接管。
此功能处理意外进程死亡，不声明同 UID 恶意程序的隔离能力；真实委派环境尚待验收。

## B25b 干净安装验收（2026-10-23）

B25b 在仓库外的四个干净 venv 中实际安装并运行了 `cccp`、`acp-worker`、`acp-web` 与根
`acp` 四个发行，数据根为 `/tmp/opencode/b25b`，Lab/MCP 使用随机 loopback 端口与独立
`ACP_RUN_ROOT`。证据与可复跑摘要位于
`reports/acp2_implementation/bb45a46/B/B25/`（`summary.json`、`install-results.json`、
`installed-probes.json`、`cccp-sample.json`、`lab-health.json`、`mcp-tools.json`、
`assets-audit.json`、`a-c-evidence.json`、`static-assets-status.json`）。

```bash
# 安装角色（仓库外；不继承 PYTHONPATH / 不使用 editable checkout）
python3.11 -m venv /tmp/opencode/b25b/venvs/web
/tmp/opencode/b25b/venvs/web/bin/python -m pip install \
  --find-links reports/acp2_implementation/bb45a46/B/B25/release \
  reports/acp2_implementation/bb45a46/B/B25/release/acp_web-0.3.0-py3-none-any.whl

# 独立 CCCP 科学样例（无 Controller / 无 acp import）
/tmp/opencode/b25b/venvs/cccp/bin/python /path/to/cccp-sample.py

# Lab 与 stdio MCP（隔离端口与 run root）
/tmp/opencode/b25b/venvs/web/bin/acp run serve --host 127.0.0.1 --port 18786 \
  --run-root /tmp/opencode/b25b/data/lab-run-root --no-browser
/tmp/opencode/b25b/venvs/web/bin/acp mcp serve --controller http://127.0.0.1:18786
```

验收场景（fixture 由 `$ACP2_PACKAGE_FIXTURE` 指定）：

```bash
python scripts/acceptance/acp2_runtime.py --scenario package-install \
  --fixture "$ACP2_PACKAGE_FIXTURE" \
  --out reports/acp2_implementation/bb45a46/B/B25 --timeout 1800
```

结果：`PASS`（exit 0）；四个干净 venv 导入均解析到各自 `site-packages`；CCCP 受控
单点在无 Controller 下完成；`acp --help` 在 web/root 均可用；Lab `/api/health` 返回
`status=ok`；MCP `initialize` 协商 `2025-06-18` 并列出 8 个 P1 工具；资产、NOTICE、
LICENSE-DP5、`py.typed`、样例配置、schema、SDK 与 AGENTS 文档均在安装面或发行目录可见；
四处版本位置均为 `0.3.0`。

**发行修正记录：** B25a 的首版 `acp_web` wheel 在 `OriginPolicy` 构造处即失败
（`server.py` 传入 `public_origin`，而当时的 `boundary.py` 尚未接受该参数），因此 B25a
把 Web HTTP 探针如实记为 `NOT_VERIFIED(candidate_origin_policy_mismatch)`。B25b 从干净
HEAD + 同一 `public_origin` 修正重建了 0.3.0 四发行，原始制品保存在
`release-b25a-original/` 与 `root-build-b25a-original/`，新制品摘要见
`release/release-manifest.json` 与 `version-locations.json`。该修正只涉及启动参数兼容，
不改变科学代码、工具许可或版本号。

A/C 原始证据：A-27b 与 A-28 已按登记路径汇总（不重签科学结论）；C33/C34 未发布，
B25b 记录 `NOT_VERIFIED` 与预期路径。C 的 `experience/static-assets.manifest.json`
未发布，静态资产只读挂载记录为 `NOT_VERIFIED(c_static_assets_absent)`。C35 不是 B25b
前置。未重启 `acp.service`，未触碰 `/var/lib/acp/runs`。
