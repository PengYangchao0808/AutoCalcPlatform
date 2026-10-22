# Packaging rollout receipts — B 路应用回执（rolling，todo 40 B40a/B40b）

- **Owner:** B 路集成写者（`single_writer_map.md`：`pyproject.toml`/`requirements-node.txt`/共享集成测试为 B 写权）
- **Created:** 2026-10-19 · Plan: `.omo/plans/acp-parallel-plan-2-services.md` todo 40（W8 B07-6）
- **性质:** 逐集成批滚动登记。**「补丁就绪 (ready)」与「集成已应用 (applied)」分开**：
  ready = C 侧规格 + `git apply --check` PASS（lane-C 文件登记）；applied = B 在本仓实际应用 + 验证命令回执（本文件）。
- **写权:** 本文件只由 B 侧追加/回填；C 侧补丁与规格仍归 `tests/baseline/cccp-evidence/`（只读引用）。

## 0. 状态总表（B40a / B40b 分开登记）

| 子项 | 内容 | 补丁就绪 (ready) | 集成已应用 (applied) | 回执 |
|---|---|---|---|---|
| **B40a** | C39a 首批 package-data（`"cccp.analysis.nmr" = ["model_assets/*"]`）+ T55 打包测试跟随改指（`t55-c39a-packaging-test.patch`） | ✅ lane-C `patches/c39a-pyproject-first-batch.patch`（sha256 `0059eb6b…f3450`）+ `patches/t55-c39a-packaging-test.patch`，`git apply --check` 均 0 | ✅ **见 §1.1–§1.4**（一个集成批，与 T55 资产迁移同批语义） | §1 行 1–2 |
| **B40a** | HN-05 节点依赖同步（`typing-extensions>=4.1` core+node 同 pin；C39a §4 本批自身零增量，HN-05 为 B07-6 的 C 侧节点输入，其 S6 行点名本条） | ✅ `node_dependency_sync_request.md` §3 diff 文本（provider 交付 `87ab288`，状态 `delivered`） | ✅ **见 §1.5**（两端同一提交落地） | §1 行 3 + §1.5 |
| **B40b** | 完整发行：独立 CCCP 发行配置（`packaging/cccp/pyproject.toml`）、M2 多 wheel 归属（root include 收窄 + `cccp==0.2.0`）、LICENSE/notice/`py.typed`、extras 拆分 | ✅ lane-C `patches/t39-01-pyproject-ownership.patch` + `patches/t39-02-cccp-pyproject.patch` + `release_manifest.md`（C39b 冻结裁决） | ❌ **未应用 — gate OPEN**：等完整 C08 / HN-04（C 侧 `handoff_registry.md` HN-04 = `pending`）；声明范围内迁移完成后才拆 wheel | §2 |

## 1. B40a 集成批应用回执（applied）

**C 迁移 → B 应用 → 验证**（每行一个 C 迁移）：

| # | C 迁移（请求） | 补丁 sha256 | B 应用 | 应用退出码 | 验证 |
|---|---|---|---|---|---|
| 1 | C39a / T56 首批 package-data（`build_spec_first_batch.md` §1b） | `0059eb6b20d0ea05b231fdf3e8d0b854718b166789d59e6f2dd1ff23621f3450` | `git apply tests/baseline/cccp-evidence/patches/c39a-pyproject-first-batch.patch`（基线 blob `25404ebd…` 与 C 记录一致，**无漂移、无需重写 hunk**） | `APPLY_C39A=0`（前置 `git apply --check` = `CHECK_EXIT=0`） | §1.1 wheel 构建 + §1.2 namelist |
| 2 | T55 打包测试跟随（`build_spec_first_batch.md` §1d companion；资产迁移本身已在 lane-C 提交落地） | `t55-c39a-packaging-test.patch`（C 侧文件） | `git apply tests/baseline/cccp-evidence/patches/t55-c39a-packaging-test.patch`（同批） | `APPLY_T55=0`（前置 check = 0） | §1.3 pytest |
| 3 | HN-05 / T42 节点依赖同步（`node_dependency_sync_request.md` §3.1+§3.2 diff；C 侧只交文档未动 live 文件） | provider 交付 `87ab288157cf8ffe8a58ca17248efc34d8a42ef6`（C-side docs 提交，本仓无 patch 文件——按 §3 diff 文本应用） | `pyproject.toml` core `"typing-extensions>=4.1"` + `requirements-node.txt` 末行同 pin（**两端同一提交**，S1 要求） | 应用脚本 exit 0（锚点唯一断言通过） | §1.5 |

**T55 状态核对（应用时实测）:** `src/acp/nmr/models/` 已空（HEAD 中不存在条目）、`src/cccp/analysis/nmr/model_assets/` 8 文件在 HEAD、`src/cccp/analysis/nmr/__init__.py` 在场 → C39a 新 glob **非 inert**（资产已迁移，规则即时生效）；无“源码可跑而安装包漏资产”窗口。`tests/test_packaging_assets.py` 仍指旧路径的 2 个 RED 测试随本批一并改指 → 绿。

**适用性:** 严格按 C39a §3 序列的第 1、2 步执行（第 3、4 步 = t39-01/t39-02 属 B40b，**未执行**）。两补丁均 `git apply` 干净应用，**未重写 hunk**。

### 1.1 出树 wheel 构建（never in repo）

```bash
# 复制到出树 staging（仓库零污染；git status 仅 pyproject.toml + 测试两处预期修改）
STAGE=/tmp/opencode/b40a-build
rm -rf "$STAGE" /tmp/b40a-wheel && mkdir -p "$STAGE" /tmp/b40a-wheel
cp pyproject.toml README.md LICENSE "$STAGE/"; cp -r src "$STAGE/src"
PYTHONPATH=/tmp/opencode/builddeps python3.11 -m build --wheel --no-isolation \
    --outdir /tmp/b40a-wheel "$STAGE"
# BUILD_EXIT=0 → acp-0.2.0-py3-none-any.whl (46,133,398 B)
```

- 工具链：`python 3.11.13` · `build 1.6.1` · `setuptools 65.5.1` · `wheel 0.48.0`（与 lane-C proof 同版）。
- 构建日志：`/tmp/opencode/b40a-build.log`（出树临时，不入库）。
- **构建 ×2**：第 1 次（C39a+T55 批后）；第 2 次（HN-05 应用后复建，`BUILD_EXIT=0`，namelist 与第 1 次**全等**——依赖行只进 METADATA）。当前 wheel sha256 见 `wheel_namelist_manifest.json`（`build.note` 记录复建原因）。
- wheel sha256 与 namelist 明细 → 机器可读记录 `tests/baseline/services-evidence/wheel_namelist_manifest.json`（被角色矩阵测试消费）。

### 1.2 wheel namelist 检查（回执摘录）

| 检查 | 结果 |
|---|---|
| 8/8 `cccp/analysis/nmr/model_assets/*`（含 `LICENSE-DP5`/`NOTICE.md`） | **PASS**（missing = NONE） |
| sha256 对 C39a §2 摘要（`atomic_reps.gz`/`frag_reps.gz` 抽查） | **PASS**（digest OK ×2） |
| 旧位 `acp/nmr/models/*` 残留条目 | **NONE**（T55 已清空，无双份资产） |
| RECORD 重复路径（同文件被双份安装） | **0 / 545 entries** |
| 顶层包 | `acp`(280) + `acp_services`(70 py) + `cccp`(189) — 单 wheel M1 态 |
| console script | 恰好 `acp = acp.cli:main`；无 `cccp` script、无 `python -m cccp` |

### 1.3 打包测试

```bash
python3.11 -m pytest tests/test_packaging_assets.py -q        # 7 passed
python3.11 -m pytest tests/test_services_packaging_roles.py -q # 9 passed（新增角色矩阵门）
```

### 1.4 可复跑验证命令（合并后重跑）

```bash
REPO=$PWD
git apply --check tests/baseline/cccp-evidence/patches/c39a-pyproject-first-batch.patch   # 回归应 FAIL(已应用)
python3.11 -m pytest tests/test_packaging_assets.py tests/test_services_packaging_roles.py -q
# wheel 重验：复用 §1.1 出树命令 + wheel_namelist_manifest.json 中的 build.command
```

### 1.5 HN-05 节点依赖同步应用回执（行 3）

**请求:** `tests/baseline/cccp-evidence/node_dependency_sync_request.md`（HN-05/T42，`delivered`，provider `87ab288`）§3.1/§3.2 diff — `typing-extensions>=4.1` 同 pin 加入 `requirements-node.txt` 与 `pyproject.toml` core（两端必须同一提交，S1）。**C 未动 live 文件**（写权 §4.8），本回执即 B 应用。

**为何属本 todo:** 其 S6 行点名「plan-2 B07『与 C08 联合调整 wheel/角色依赖、模型/notice 和节点依赖』——本请求即 B07 该条的 C 侧输入」；是真实漂移修复（`assert_never` 模块级 import 于 `acp run` dispatch 必经路径，缺则 5 工作流节点 `ModuleNotFoundError`），非候选组件、非 C08 科学依赖结论变更。

**应用后验证（§3.4 对应，本机执行）:**

| # | 命令 | 结果 |
|---|---|---|
| 1 | `python3.11 -m pytest tests/test_packaging_assets.py tests/test_services_packaging_roles.py -q` | **16 passed**（S1/S2/S3 同步守护 + 角色矩阵全绿） |
| 2 | `python3.11 -m pip install --dry-run --ignore-installed -r requirements-node.txt` | **exit 0**（闭包解析含 `typing_extensions-4.16.0`；日志 `/tmp/opencode/b40a-node-dryrun.log`） |
| 3 | `python3.11 -c "import typing_extensions, numpy, rdkit, scipy, matplotlib, openpyxl, nmrglue, yaml; …"` | **OK** |
| 4 | 真节点 `pip install --user -r` + `acp run xtb_optimize` 冒烟 | **NOT_VERIFIED**（本机无 `xtb`、未动真节点；S7 节点安装回执归 B06/T54 消费链） |


- **目标（C39b 冻结裁决，`tests/baseline/cccp-evidence/release_manifest.md` §2）:**
  - `cccp` 独立发行独占 `cccp/**`（含 `model_assets/*`、`cccp/py.typed`）；**无** console script、**永不** `python -m cccp`。
  - `acp` 发行独占 `acp/**` + `acp_services/**`（直到 ACR 拆分）；`include = ["acp*", "acp_services*"]`；`package-data {"acp" = ["py.typed"]}`；core 依赖增 `cccp==0.2.0`。
  - 每个安装路径恰好一个 owner — **零重复文件归属**（C39b `t39-build-proof.txt` §4 双零泄漏已证机制）。
  - M1 过渡例外：`cccp` 制品尚未上索引前，单 wheel 可继续捆绑 `cccp*`（= 现状，本文件 §1 态）。
- **Gate 状态:** **OPEN（未应用）** — 等完整 C08 / HN-04（C 侧 HN-04 `pending`；B 侧权威表尚无 HN-04 行）。声明范围内迁移完成前**不拆 wheel、不改归属**；`packaging/cccp/pyproject.toml` 不创建。
- **角色依赖矩阵（本批锁定，`tests/test_services_packaging_roles.py` 守护）:**

| 角色 | 依赖面 | 成员 |
|---|---|---|
| 科学最小（core） | `[project].dependencies` | `numpy≥2.1.0 rdkit pyyaml scipy matplotlib openpyxl` + `typing-extensions≥4.1`（HN-05，节点 dispatch 必需）（必含、且**不含**任何 extra/候选组件） |
| Web/API | extra `api`（仅此处） | `fastapi uvicorn python-multipart zipstream-ng` |
| 远程/LSF | extra `remote` | `paramiko` |
| NMR 可选科学 | extra `nmr` | `nmrglue openbabel qml`（`nmrglue` 有界上节点=节点清单特例，非 core） |
| 开发/浏览器 | extra `dev` / `browser` | `pytest pytest-cov ruff mypy pre-commit httpx` / `playwright` |
| 候选组件（**永不进依赖**） | — | PostgreSQL 族、对象存储、Slurm/Podman、Lago/支付、外部 IAM（plan L71 候选；C 侧 `release_manifest.md` §1.2 确认无 Web/IAM/billing 私有算法依赖） |
| 节点 | `requirements-node.txt` | core 科学子集 + `nmrglue`；`qml`/`openbabel` 永禁；HN-05 +`typing-extensions` 后与 core 逐字同步（S1 守护 PASS） |

- **静态资源发行缺口:** 科学侧 8/8 资产已随单 wheel 发行（§1.2）。Web 发行独立静态资源（`frontend/**` 不入 wheel）为**已声明缺口**：归 todo 41/76 发行链交付（缺陷修复或缩小发行声明），本文件只登记不关闭。
- **`include` 匹配 ≠ 角色隔离:** 独立发行（无旧实现的安装制品）验证归 todos 72/77，本文件不以其代替。

## 3. 滚动续记约定

后续每批（C 迁移 → B 应用）在 §1 表追加一行：C 迁移编号与补丁 sha → `git apply` 退出码 → 构建/测试回执 → B 侧集成提交。B40b 解闸时在 §0/§2 就地更新为 applied 并附 M2 双 wheel 构建回执（复用 C39b §3.3 V0–V4 命令）。

## 2026-10-22 独立角色候选发行补验

本次增加 `packaging/roles/{cccp,worker,web}` 与 `scripts/build_release.py`，
在出树暂存中构建同版本 `cccp`、`acp-worker`、`acp-web` 的 wheel / sdist。
根单发行安装保持兼容；上文 B40b 的“根发行默认归属切换未应用”是其原范围，
不改写历史 manifest 的 `future_split_ownership.applied`。独立角色是显式构建/安装路径，
不与根 `acp` 单发行混装。

本机已验证三个纯净环境的科学核心受控调用、worker 冻结组/执行权/回执与无 jobs DB、
Web 包内 HTML/JS 的真实 HTTP 与 ETag 重验证；资产全量字节和零重复文件归属通过。
三个源码包均可重建出相同包内容，版本和依赖文件未遗漏。具体制品摘要、
依赖快照及命令见 [修复报告](../../../reports/architecture_repair_20261022/REPORT.md)。

这些结果关闭独立角色候选的本机安装门；真实 QC、LSF、委派 cgroup 及生产发布
仍按各自证据保留 NOT_VERIFIED，未据探针将产品或生产门改成 PASS。
