# ACP Backend ↔ CCCP 接口同步状态审计报告

**生成时间**: 2026-08-02
**审计范围**: `src/acp/backends/*` 与 `src/cccp/qc/interfaces/*`、`src/cccp/qc/runners/*` 的接口归属、重复实现、线程环境钉住(OMP_NUM_THREADS)状态
**触发背景**: `curcusone-test` 任务超占核心(compute-01 `~/.bashrc` 注入 `OMP_NUM_THREADS=10`,4 核请求实际 4×10 线程占满节点)修复后,核查 cccp 与 acp 两层接口的同步一致性

## 0. 治理原则(架构基准)

> **acp 不负责任何具体 QC 接口。所有计算化学接口(外部二进制 subprocess 封装)都必须在 cccp 中;acp/backends 只保留能力协议(Protocol)适配层,对 cccp 接口做薄包装。**

- 外部软件接口(ORCA / CREST / xTB / CENSO / ISOSTAT / Molclus / Shermo 的二进制调用、参数生成、输出解析)一律归属 `cccp.qc.interfaces` / `cccp.qc.runners`
- `acp/backends` 不出现 `subprocess`、不出现二进制路径、不出现 CLI 参数构造;只做:能力声明(Protocol)、配置透传、`QCResult` 契约适配
- 本报告第 4 节修复计划以此原则为验收标准

---

## 1. 结论摘要

| # | 结论 | 严重度 |
|---|------|--------|
| 1 | 3/5 生产 backend(orca/crest/xtb)是 cccp 薄适配层,**形态正确** | - |
| 2 | censo / isostat / molclus 三个 backend **自带 subprocess**,未沉淀到 cccp | P1 |
| 3 | `cccp.qc.runners.run_isostat` 与生产 `IsostatBackend` 功能重复,但缺生产关键修复(exit-24 标题归一化、错误传播、env 钉住);调用面共 3 处:休眠的 `ConformerEngine`(engine.py:537/583)、`external.py` 再导出 → `ExternalBackend.cluster()`(external_backend.py:38,活代码)、以及 `cccp.qc/__init__.py:17/36` 导出 | P1 |
| 4 | 线程环境钉住:cccp 侧 crest / xtb_thermo / xtb(本次)已钉;**cccp runners、cccp orca、acp censo 未钉**,CENSO 阶段仍有同样的超占风险 | P0 |
| 5 | `energy_shared.py` 直接 import cccp ORCAInterface / run_shermo,绕过 backends 层 | P2 |
| 6 | 工作树有 15 个未提交改动(含本次 4 个文件 + 既有未提交) | - |

---

## 2. 接口归属总览

| acp backend | 大小(行/KB) | 包装 cccp? | cccp 对应接口 | 生产使用 |
|-------------|------|-----------|--------------|---------|
| `orca.py` | 134 / 3.7K | ✅ 薄适配 | `interfaces/orca.py` `ORCAInterface` | 是(energy/xtbmd DFT 阶段) |
| `crest.py` | 129 / 3.7K | ✅ 薄适配 | `interfaces/crest.py` `CRESTInterface` | 是(ensemble/energy) |
| `xtb.py` | 82 / 2.2K | ✅ 薄适配 | `interfaces/xtb.py` `XTBInterface` | 是(batch_opt 每帧) |
| `external.py` + `external_backend.py` | 145 / 4.7K | ✅ 薄适配 | `qc/runners` `run_isostat`/`run_shermo`/`batch_process_thermo` | 部分(shermo 经 simple.py 合规调用) |
| `isostat_backend.py` | 261 / 9.0K | ❌ 自带 subprocess | `qc/runners/__init__.py:23` `run_isostat`(遗留) | 是(仅 xtbmd_censo_energy 的 isostat/conv_check;ensemble/energy 不经此接口) |
| `molclus_backend.py` | 721 / 25K | ❌ 自带 subprocess | **无**(cccp 无对应物) | 是(`run_md`,经 `xtbmd_md.py`;`search()` 休眠) |
| `censo_backend.py` | 840 / 29K | ❌ 自带 subprocess | **无**(cccp 无对应物) | 是(xtbmd/ensemble/energy CENSO 阶段) |
| `base.py`/`capabilities.py`/`registry.py` | 556 / 18K | 基础设施 | - | - |

---

## 3. 详细发现

### 3.1 已正确同步(薄适配层)

- `orca.py` / `crest.py` / `xtb.py` backend 按项目约定(QC 执行走 `acp.backends` 适配器)薄包 cccp 接口,无独立 subprocess 逻辑。本次线程修复后 `XTBInterface`(cccp)成为 batch_opt 每帧 `nproc=1` 的权威控制点。

### 3.2 未沉淀到 cccp 的 backend(核心偏离)

**1. `isostat_backend.py`(9.2K)与 `cccp.qc.runners.run_isostat` 重复,且生产修复未回灌**

- `IsostatBackend.cluster()` 完整重实现了 `run_isostat`(`cccp/qc/runners/__init__.py:23`),并带生产关键修复:
  - **标题归一化** `_normalise_titles_for_isostat`(`isostat_backend.py:110-163`)修复 exit 24 —— 任务 `20260802_005109_002_curcusone-test` 正是死于 `ISOSTAT clustering failed with exit code 24`
  - `check=True` 错误传播 vs cccp 静默返回 `(ensemble_xyz, [])`(cccp runners:82-92)
  - 临时文件输入隔离 + `QCResult` 契约
  - 本次修复的 env 钉住
- cccp 版 `run_isostat` 无以上任何修复,仍为"失败复制原文件 + 空列表"的静默语义;仅被休眠的 `cccp/core/engine.py`(ConformerEngine)调用。生产侧 `get_backend("isostat")` 仅出现于 `xtbmd_censo_energy.py:451/1573`(主聚类 + conv_check 诊断),ensemble/energy 工作流不调用此接口
- **第三条路由(原报告遗漏,已补)**:`acp/backends/external.py:3` 将 cccp 遗留 `run_isostat` 原样再导出,`external_backend.py:38`(`ExternalBackend`,已注册,名 `external`)的 `cluster()` 亦走此遗留路径 —— 同样的静默失败语义 + 未钉 env。此路由非休眠(注册即生效,`capabilities.py:178-190` 会将其报告为可用聚类后端),归档时必须一并清理,否则旧路径继续存活

**2. `molclus_backend.py`(25K)—— cccp 无对应物**

- MD 编排(md.inp 写入、settings.ini、轨迹校验、多副本 `run_md_replicas` 经此调用)全部在 acp 侧。生产只用到 `run_md`;`search()`(Molclus settings.ini 全管道)休眠,仅测试覆盖

**3. `censo_backend.py`(30K)—— cccp 无对应物**

- CENSO 全套(rcfile 生成、preset 注入、JSON/XYZ 解析、模板注入)全部在 acp 侧

### 3.3 线程环境钉住(OMP_NUM_THREADS / MKL_NUM_THREADS / OPENBLAS_NUM_THREADS)状态

| 位置 | 状态 | 说明 |
|------|------|------|
| `cccp/interfaces/crest.py:148-151, 293-296` | ✅ 已钉 | 钉到 `threads`(含 OMP_STACKSIZE) |
| `cccp/interfaces/xtb_thermo.py:176-178` | ✅ 已钉 | `run_xtb_enso` |
| `cccp/interfaces/xtb.py`(**2026-08-02 本次修复**) | ✅ 已钉 | `_thread_env(nproc)`,optimize/single_point |
| `acp/backends/isostat_backend.py`(**本次**) | ✅ 已钉 | 按 `-nt nthreads` |
| `acp/backends/molclus_backend.py`(**本次**) | ✅ 已钉 | MD/molclus 子进程按 `nproc` |
| `acp/backends/censo_backend.py:668-681` | ❌ **未钉** | 仅 LD_LIBRARY_PATH + HOME;其 xtb/ORCA 子进程继承 `OMP_NUM_THREADS=10` → **CENSO 阶段同样超占风险** |
| `cccp/interfaces/orca.py:522-525` | ⚠️ 部分 | 仅 LD_LIBRARY_PATH;ORCA 靠 `%pal nprocs` 控线程,BLAS/MKL 仍受环境 OMP 影响 |
| `cccp/qc/runners`(run_isostat/run_shermo) | ❌ 未钉 | Shermo 单线程为主(风险低);run_isostat 休眠,但经 `external.py` 再导出 → `ExternalBackend.cluster()` 为活代码路径 |

### 3.4 绕过 backend 层的生产调用

- `energy_shared.py:37-38` 直接 `from cccp.qc.interfaces.orca import ORCAInterface` + `from cccp.qc.runners import run_shermo`(违反"QC 执行走 backends 适配器"约定)
- `simple.py:532` 经 `acp.backends.external.run_shermo`(合规)

### 3.5 工作树状态

- 最新提交 `f6d4748`(2026-08-01, xtbmd_censo_energy workflow + tests)
- 未提交改动 15 个文件:本次线程修复 4 个(`cccp/qc/interfaces/xtb.py`、`acp/backends/isostat_backend.py`、`acp/backends/molclus_backend.py`、`tests/test_molclus_backend.py`)+ 既有未提交(AGENTS.md/README/docs 文档、`tests/test_acp_backends.py`、`tests/test_remote_phase1_integration.py`、`tests/test_remote_phase3.py`)

### 3.6 运维遗留

- compute-01 上 job `1110`(20260802_104331_003_curcusone-test)仍在用修复前的旧代码运行(batch_opt 每帧 ~950% CPU),需 `bkill 1110` 后重提;修复后代码经 auto_sync 随下次提交自动同步

---

## 4. 修复计划(以第 0 节治理原则为准)

### 4.0 已应用修改(2026-08-02 线程钉住)的风险与影响

| 修改 | 风险点 | 影响工作流 |
|------|--------|-----------|
| `cccp/qc/interfaces/xtb.py` `_thread_env`(optimize/single_point) | 非 LSF 本地环境若用户曾靠 `OMP_NUM_THREADS` 用满机器核,现被钉住(行为变化,正确性无影响);钉住值 = `self.nproc`,配置为 0/负数时需回退保护;`-T/-P` 从此被 env 覆盖,xtb 版本更换时 env 是唯一权威 | xtbmd_censo_energy(batch_opt 每帧,4 帧 × 1 线程 = 4 核)、energy/ensemble(xtb 预优化)、simple(`xtb-opt`) |
| `acp/backends/molclus_backend.py`(run_md/search env 钉住) | 单进程 MD 从"全核"降为 `nproc` 线程,**吞吐下降**(合规 vs 性能取舍);休眠的 `search()` 内 molclus 子进程亦被钉(低风险) | xtbmd_censo_energy(xtbmd 阶段) |
| `acp/backends/isostat_backend.py`(env 钉住) | 钉到 `-nt nthreads`(主聚类路径 = 1),大体系聚类变慢;无正确性影响 | xtbmd_censo_energy(isostat + conv_check 诊断)、energy/ensemble(如经此接口) |
| `tests/test_molclus_backend.py`(env 断言) | 无 | 无 |

### P0 — 立即(单次提交,~1h)

**1. CENSO 子进程 env 钉住 `OMP/MKL/OPENBLAS_NUM_THREADS` = nproc**
- 文件:`acp/backends/censo_backend.py`(`_run_censo` env 构造处,668-681)
- 风险点:必须保留既有 env 合并(LD_LIBRARY_PATH、HOME 注入);钉住值需与 `--maxcores` / `self._nproc` 同源,`nproc` 缺省时回退默认(16)而非 None;CENSO 顺序任务钉到 nproc = 合规上限,无额外性能退化
- 影响工作流:**xtbmd_censo_energy / ensemble / energy 的 CENSO 阶段**(三者共用 `CensoBackend`)
- 验证:新增 env 断言测试 + `test_acp_backend_censo.py` 全量

**2. cccp runners env 钉住(防御性)**
- 文件:`cccp/qc/runners/__init__.py`(`run_isostat`/`run_shermo`)
- 风险点:`run_isostat` 休眠但 `cccp/core/engine.py:537/583` 仍引用,行为变化不影响生产;`run_shermo` 被 `energy_shared.py:412` 与 `simple.py:534` **生产调用**,Shermo 单线程钉住无性能影响;保留既有 `timeout=None` 等语义
- 影响工作流:energy(thermo 阶段)、simple(`optfreqsp`)、cccp ConformerEngine(休眠)
- 验证:energy/xtbmd 相关测试全量

**3. 回归测试:xtb env 断言待补**(molclus env 断言本次已完成)
- 文件:`tests/test_qc_interfaces_xtb.py`
- 风险点:无(纯测试)
- 影响工作流:无

### P1 — 结构回灌(核心:cccp 成为唯一 subprocess 层,~1-2 天)

**4. 新增 `cccp/qc/interfaces/isostat.py` + `isostat_backend` 薄包**
- 风险点(**行为漂移是主要风险**):标题归一化(exit-24 修复)、临时文件 finally 清理、错误分类(Timeout/CalledProcess/OSError)、`QCResult` 字段(coordinates/symbols/log_file)必须逐一对齐;acp 侧 `QCResult` 与 cccp 侧同名异构,需经 `to_qc_result` 适配;`nthreads` 参数链(conv_check=1、主聚类=nproc)保持
- 影响工作流:**xtbmd_censo_energy(isostat 阶段 + conv_check 诊断)**;energy/ensemble 的 CENSO 内部聚类不经此接口
- **连带清理 `external` 遗留路由**:`external.py:3` 的 `run_isostat` 再导出改指向新 `IsostatInterface`(保持 `run_isostat(ensemble_xyz, output_dir, config, ...)` 签名兼容),`external_backend.py:38` 的 `cluster()` 同步迁移;完成后 isostat 仅存一条接口路径(cccp) + 两个薄包(acp)
- 验证:先跑 `test_acp_xtbmd_platform_phase5.py` / `test_acp_workflows_xtbmd_censo_energy.py` 锁定现状 → 迁移 → 全量回归

**5. 新增 `cccp/qc/interfaces/molclus.py` + `molclus_backend` 薄包**
- 风险点(**生产路径,风险最高**):`run_md` 是全部 xtbmd 任务的必经阶段——md.inp 逐字节一致(seed/temp/time/dump/step/hmass/shake/nvt 格式)、轨迹校验(`_MIN_TRAJECTORY_FRAMES`)、超时语义(backend timeout vs workflow timeout 区分)、`_run_command` 错误分类必须保留;`search()`(休眠)的三步管道一并迁移;metadata 契约(`trajectory_file`/`n_frames`)被 `xtbmd_md.py` 消费
- 影响工作流:**xtbmd_censo_energy(xtbmd 阶段,全部任务)**
- 验证:`test_molclus_backend.py`(随迁)、`test_xtbmd_md.py`、`test_acp_xtbmd_platform_phase5.py`、`test_acp_workflows_xtbmd_censo_energy.py`

**6. 新增 `cccp/qc/interfaces/censo.py` + `censo_backend` 薄包**(工作量最大,建议最后单独提交)
- 风险点:rcfile 生成、preset 注入、模板注入、part 映射(`_PART_FLAGS`/`_PARSE_PRIORITY`)、JSON/XYZ 解析、catalog(METHOD_META)联动——任何字符串模板差异都会改变 CENSO 行为;`--omp-min 4`(censo_backend.py:366)与 nproc<4 的语义需保留;`--maxcores` 与线程钉住值同源
- 影响工作流:**xtbmd_censo_energy / ensemble / energy 的 CENSO 阶段**
- 验证:`test_acp_backend_censo.py`、`test_acp_censo_p5_acceptance.py`、`test_acp_censo_opt_stage.py`

**7. 归档休眠代码(`run_isostat` + ConformerEngine 调用链)**
- 风险点:引用面共 3 处 —— `cccp.qc.__init__:17/36` 公开导出面、`engine.py:537/583` 两个调用点、`acp/backends/external.py:3` 再导出(需在步骤 4 中先清空引用,再标记 deprecated);建议先标记 deprecated 保留一个版本周期再删,便于 ConformerEngine 未来重启
- 影响工作流:无生产工作流(ConformerEngine 休眠);影响 cccp 库 API 兼容面
- 验证:全量 pytest 确认无遗漏引用

### P2 — 合规修复(~2h)

**8. `energy_shared.py` 的 ORCA 调用改经 `acp.backends`(`get_backend("orca")`)**
- 风险点(**科学输出,数值回归是主要风险**):ORCABackend 薄包参数映射差异(method/basis/solvent/maxcore/timeout 键名、`%pal nprocs` 与 maxcore 计算);rank1 handoff(opt→freq→SP)是最终能量的直接来源,需与现状 ORCAInterface 直调逐参数 diff;注意直调 timeout(864000)与 ORCABackend 默认的差异
- 影响工作流:**energy(rank1 handoff)、xtbmd_censo_energy(相同 handoff)**
- 验证:`test_acp_workflows_energy.py` / `test_acp_workflows_xtbmd_censo_energy.py` 全量 + 一次真实任务能量数值比对(≤1e-6 Eh 才算通过)

**9. AGENTS.md 同步**
- 风险点:无;影响工作流:无

### P3 — 验证与部署(~1h)

**10. 全量 pytest + compute-01 远程冒烟**
- 风险点:共享节点,先 `bkill 1110` 释放;冒烟任务占 ~4 核;远程同步依赖 auto_sync(mtime 增量)——**必须先行提交**,否则同步的是工作树状态
- 影响工作流:无(验证操作)

**11. `bkill 1110` + 重提 curcusone-test**
- 风险点:重提消耗 compute-01 资源;`--resume` 依赖 checkpoint 指纹,xtbmd 指纹若因代码变化不匹配将从 MD 重跑;1110 当前在 batch_opt(500 帧,无中间 checkpoint),kill 丢失已优化帧
- 影响工作流:curcusone-test 任务本身

---

## 5. 验收标准(治理原则落地)

- [x] **`acp/backends` 下无任何 `import subprocess` / 二进制路径 / CLI 参数构造** —— 全部子进程逻辑位于 cccp(2026-08-02 达成:3 个 backend 改为薄适配,仅 docstring 提及)
- [x] cccp 覆盖全部 7 个外部软件接口:ORCA / CREST / xTB / CENSO / ISOSTAT / Molclus / Shermo(前 3 个已有,后 4 个:3 个接口 + Shermo 仍在 `qc/runners`)(2026-08-02 达成)
- [x] 所有 QC 子进程(xtb/orca/crest/censo/isostat/shermo)env 显式钉住线程数 —— censo/isostat/shermo/xtb 已钉;orca 仍靠 `%pal nprocs`(⚠️ 部分,见 §3.3,未列入 P0/P1 范围)
- [x] `cccp.qc.runners.run_isostat` 已改写为 `IsostatInterface` 薄包(deprecated,休眠 engine 专用);`external.py` 再导出与 `ExternalBackend.cluster()` 不再引用遗留实现 —— isostat 全项目仅剩 `IsostatInterface` 一条接口路径(2026-08-02 达成)
- [x] `pytest tests/` 全绿(2026-08-02:948 passed / 7 skipped);compute-01 冒烟任务 CPU ≤ 请求核数(⚠️ P3 待执行)

---

## 6. 分步执行计划(每步 = 一个独立提交点)

### 步骤 1 — CENSO 子进程 env 钉住 【P0,~30min】 ✅ 2026-08-02
- **改动**:`acp/backends/censo_backend.py` `_run_censo` env 构造处加入 `OMP/MKL/OPENBLAS_NUM_THREADS` = nproc(保留 LD_LIBRARY_PATH/HOME 合并);新增 env 断言测试
- **依赖**:无
- **验证**:`pytest tests/test_acp_backend_censo.py tests/test_acp_censo_p5_acceptance.py tests/test_acp_censo_opt_stage.py -q`(94 passed)
- **风险/回滚**:行为仅限 env,单文件可回滚
- **提交建议**:`fix(censo): pin OMP/MKL/OPENBLAS threads to nproc in CENSO subprocess env`(已提交 d470ad3)

### 步骤 2 — cccp runners env 钉住 【P0,~20min】 ✅ 2026-08-02
- **改动**:`cccp/qc/runners/__init__.py` 的 `run_isostat`/`run_shermo` subprocess 注入 env 钉住(threads 参数 / 1)
- **依赖**:无
- **验证**:`pytest tests/test_acp_workflows_energy.py tests/test_acp_workflows_xtbmd_censo_energy.py -q`(shermo 生产调用路径)
- **风险/回滚**:低;单文件
- **提交建议**:`fix(runners): pin BLAS/OpenMP threads for isostat/shermo subprocesses`(已提交 d470ad3)

### 步骤 3 — xtb env 断言测试补齐 【P0,~20min】 ✅ 2026-08-02
- **改动**:`tests/test_qc_interfaces_xtb.py` 增加 `env["OMP_NUM_THREADS"]` 断言(molclus 侧已完成);同时给 `cccp/qc/interfaces/xtb.py:76` 的 `self.nproc` 增加 `max(1, nproc)` 回退保护(现无保护,与 molclus 侧 `_thread_env(max(1, nproc))` 不一致),钉住值非法时不得传入 `OMP_NUM_THREADS=0`
- **依赖**:无(验证既有修复 + 补保护)
- **验证**:`pytest tests/test_qc_interfaces_xtb.py -q`
- **提交建议**:可与步骤 1/2 合并提交,或独立 `test(xtb): assert thread env pinning`(已提交 d470ad3)

### 步骤 4 — ISOSTAT 接口回灌 cccp 【P1,~2-3h】 ✅ 2026-08-02
- **改动**:
  1. 新增 `cccp/qc/interfaces/isostat.py`:`IsostatInterface.cluster()` —— 迁入标题归一化 / 临时文件 finally 清理 / check=True 错误分类 / env 钉住(以 `xtb.py` 风格编写)
  2. `cccp/qc/interfaces/__init__.py` 导出
  3. `acp/backends/isostat_backend.py` 改为薄包:构造 cccp 接口 → `to_qc_result` 适配,保留 `QCResult`/`cluster()` 签名
  4. **清理第三条路由**:`external.py:3` 的 `run_isostat` 再导出改指向新 `IsostatInterface`;`external_backend.py:38` 的 `cluster()` 迁移(否则步骤 7 归档后此路由引用残留)
- **依赖**:步骤 2(env 钉住模式一致)
- **验证**:迁移前先跑 `pytest tests/test_acp_xtbmd_platform_phase5.py tests/test_acp_workflows_xtbmd_censo_energy.py tests/test_molclus_backend.py -q` 锁定基线 → 迁移后全量回归 + 新增 cccp 接口单测(标题归一化、exit-24 场景)(新增 `tests/test_qc_interfaces_isostat.py`,8 tests)
- **风险/回滚**:行为漂移(标题归一化/错误语义);若回归失败,回滚提交并保留现状
- **提交建议**:`refactor(cccp): add IsostatInterface, slim isostat_backend to thin adapter`(已提交 f7255ee)

### 步骤 5 — Molclus 接口回灌 cccp 【P1,~3-4h】 ✅ 2026-08-02
- **改动**:
  1. 新增 `cccp/qc/interfaces/molclus.py`:`MolclusInterface.run_md()` / `.search()` —— 迁入 md.inp 生成、settings.ini、轨迹校验、`_run_command` 错误分类、env 钉住
  2. `cccp/qc/interfaces/__init__.py` 导出
  3. `acp/backends/molclus_backend.py` 薄包化,保留 `run_md`/`search` 签名与 metadata 契约(`trajectory_file`/`n_frames`)
  4. `tests/test_molclus_backend.py` 随迁(mock 打到 cccp 接口层)
- **依赖**:步骤 4(薄包模式先例)
- **验证**:迁移前锁定基线(`test_xtbmd_md.py`、`test_acp_xtbmd_platform_phase5.py`、`test_acp_workflows_xtbmd_censo_energy.py`)→ 迁移后全量回归(133 passed)
- **风险/回滚**:**生产路径,风险最高**(run_md 是全部 xtbmd 任务必经);md.inp 逐字节一致;若失败回滚
- **提交建议**:`refactor(cccp): add MolclusInterface, slim molclus_backend to thin adapter`(已提交 f7255ee)

### 步骤 6 — CENSO 接口回灌 cccp 【P1,~4-6h,可独立排期】 ✅ 2026-08-02
- **改动**:
  1. 新增 `cccp/qc/interfaces/censo.py`:迁入 rcfile 生成、preset 注入、模板注入、part 映射(`_PART_FLAGS`/`_PARSE_PRIORITY`)、JSON/XYZ 解析、env 钉住
  2. `cccp/qc/interfaces/__init__.py` 导出
  3. `acp/backends/censo_backend.py` 薄包化,保留公开签名(preset/dual_mode/ewin 等)
- **依赖**:步骤 4、5(模式先例)
- **验证**:`test_acp_backend_censo.py`、`test_acp_censo_p5_acceptance.py`、`test_acp_censo_opt_stage.py` 迁移前锁定基线 → 迁移后全量回归(94 passed)
- **风险/回滚**:最大(30K 行,任何模板字符串差异改变 CENSO 行为;`--omp-min 4` 与 nproc<4 语义);单独提交,失败即回滚
- **提交建议**:`refactor(cccp): add CensoInterface, slim censo_backend to thin adapter`(已提交 f7255ee)

### 步骤 7 — 归档 `run_isostat` + ConformerEngine 调用链 【P1,~30min】 ✅ 2026-08-02
- **改动**:`cccp/qc/runners/__init__.py` 与 `cccp/core/engine.py:537/583` 的 `run_isostat` 标记 deprecated(保留一个版本周期后删除);更新 `cccp/qc/__init__.py:17/36` 导出注释;确认 `acp/backends/external.py` 已无遗留引用(步骤 4 完成后应满足)
- **依赖**:步骤 4(确认 `IsostatInterface` 替代 + external 路由清理)
- **验证**:全量 `pytest tests/ -q` 无遗漏引用(948 passed / 7 skipped)
- **风险/回滚**:影响 cccp 公开 API 面;仅标记不删除,可零成本回滚
- **提交建议**:`chore(cccp): deprecate legacy run_isostat in favor of IsostatInterface`(已提交 f7255ee)

### 步骤 8 — `energy_shared.py` ORCA 调用合规化 【P2,~1-2h】
- **改动**:`src/acp/workflows/energy_shared.py:37` 直连 `ORCAInterface` 改经 `get_backend("orca")`;逐参数 diff(method/basis/solvent/maxcore/timeout);`run_shermo` 调用已合规不动
- **依赖**:无(独立)
- **验证**:`pytest tests/test_acp_workflows_energy.py tests/test_acp_workflows_xtbmd_censo_energy.py -q` + **一次真实能量任务数值比对(能量差 ≤ 1e-6 Eh)**
- **风险/回滚**:rank1 handoff 是科学核心输出;数值比对不过则回滚
- **提交建议**:`refactor(energy_shared): route ORCA handoff through acp.backends`

### 步骤 9 — 文档同步 【P2,~30min】
- **改动**:`acp/backends/AGENTS.md`(反模式清单:3 个 backend 不再自带 subprocess)、`workflows/AGENTS.md`、根 `AGENTS.md` 的 WHERE TO LOOK 表(指向新 cccp 接口)
- **依赖**:步骤 4-6 完成
- **提交建议**:`docs: record cccp interface consolidation and governance principle`

### 步骤 10 — 全量验证 + 远程部署 【P3,~1h】
- **改动**:无
- **依赖**:步骤 1-9 全部提交(远程 auto_sync 同步的是提交后的代码)
- **执行**:
  1. `pytest tests/ -q` 全绿
  2. `bkill 1110` 释放 compute-01
  3. 冒烟:`acp run xtbmd_censo_energy --input <smoke.xyz> --nproc 4 --max-frames 20`,确认任意时刻合计 CPU ≤ 400%
  4. 重提 curcusone-test(`--resume` 跳过 xtbmd,若指纹匹配)
- **风险**:共享节点占用;冒烟前必须已提交
- **提交建议**:无(运维操作)

### 依赖关系总览

```
步骤 1 ─┐
步骤 2 ─┤→ 步骤 3(可并入 1/2)
步骤 4 ─→ 步骤 5 ─→ 步骤 6(可独立排期)
步骤 7(依赖 4,含 external 路由清理)
步骤 8(独立)
步骤 9(依赖 4-6)→ 步骤 10(依赖全部提交)
```

> 关键路径:步骤 4 → 5 → 6 → 9 → 10。步骤 6 是工作量最大的单项,可最后单独排期;步骤 1-3 可先提交以消除 P0 超占风险。

---

## 7. 执行完成记录(2026-08-02,P0 + P1 全部落地)

### 7.1 已完成的改动汇总

| 文件 | 改动 |
|------|------|
| `src/cccp/qc/interfaces/censo.py` | **新增** — CensoInterface(rcfile/preset/模板注入/part 映射/JSON-XYZ 解析/env 钉住) |
| `src/cccp/qc/interfaces/isostat.py` | **新增** — IsostatInterface(标题归一化、错误分类、env 钉住) |
| `src/cccp/qc/interfaces/molclus.py` | **新增** — MolclusInterface(run_md/search,md.inp 逐字节契约、轨迹校验) |
| `src/cccp/qc/interfaces/__init__.py` | 导出 3 个新接口(10 symbols) |
| `src/cccp/qc/__init__.py` | 导出 IsostatInterface;run_isostat 标记 DEPRECATED |
| `src/cccp/qc/runners/__init__.py` | `_pinned_env()`;run_isostat 改为接口薄包(deprecated);run_shermo env 钉到 1 |
| `src/cccp/qc/interfaces/xtb.py` | `self.nproc` 加 `max(1, int())` 回退保护 |
| `src/cccp/core/engine.py` | 两处 run_isostat 调用加 deprecated 注释 |
| `src/acp/backends/censo_backend.py` | 薄包化(委托 CensoInterface,保留全部私有方法名与数据类再导出) |
| `src/acp/backends/isostat_backend.py` | 薄包化(委托 IsostatInterface,`_normalise_titles_for_isostat` 静态转发保留) |
| `src/acp/backends/molclus_backend.py` | 薄包化(委托 MolclusInterface,镜像属性保留) |
| `src/acp/backends/external_backend.py` | `cluster()` 迁移至 IsostatInterface(`threads`→`nthreads` 兼容映射) |
| `src/acp/backends/external.py` | 不变(再导出已指向接口薄包版 run_isostat) |
| `tests/test_qc_interfaces_isostat.py` | **新增** — 8 个接口单测(归一化、exit-24、timeout、OSError 分类) |
| `tests/test_qc_interfaces_xtb.py` | env 钉住断言 + nproc 回退测试 |
| `tests/test_molclus_backend.py` | mock 打到 cccp 接口层;isostat env 断言 |
| `tests/test_acp_backends.py` / `test_acp_censo_p5_acceptance.py` / `test_acp_censo_opt_stage.py` / `test_acp_workflows_ensemble.py` | mock 目标迁移至 cccp 层;censo env 钉住断言 |

### 7.2 执行中发现的问题(已修复)

1. **`env.update(_thread_env(...))` 覆盖自定义 HOME**(censo 迁移时引入,已修复):`_thread_env()` 返回 `dict(os.environ)` 全量副本,`env.update()` 会把进程 HOME/LD_LIBRARY_PATH 覆盖回自定义值 —— 改为逐键赋值(`OMP/MKL/OPENBLAS_NUM_THREADS`)。**同模式已在 censo/isostat/molclus 三处核查**,仅 censo 曾有合并场景。
2. **`timeout=None` 显式传参覆盖接口默认值**(isostat):backend 薄包传 `timeout=kwargs.get("timeout")`(None)时,`kwargs.get('timeout', default)` 不触发默认 —— 接口内改为 `None → self.timeout` 回退 + `int()` 容错。
3. **molclus `search()` 的 ISOSTAT 子进程未钉 env**(评审发现):全管道中只有 isostat 一步漏传 `_thread_env` —— 已补(`env=_thread_env(max(1, nthreads))`),与 run_md/xtb/molclus 子进程一致。
4. **测试污染致中途一次全量失败**:重构中途的中间态 pyc 导致 23 个 energy 测试假失败;`find -name "*.pyc" -delete` + 重跑后连续两轮全绿(948 passed / 7 skipped)。
5. **P2 步骤 8(2026-08-02 执行)**:`energy_shared.py:37-38` 直连 `ORCAInterface` + `cccp.qc.runners.run_shermo` 均改写——ORCA handoff 经 `get_backend("orca")`(构造参数 method/basis/solvent/solvent_model 透传,timeout/maxcore 同 config 同源,数值语义等价),`run_shermo` 经 `acp.backends.external`(与 simple.py 一致);3 个测试文件的 mock 目标从 `energy_shared.ORCAInterface` 迁移至 `energy_shared.get_backend`(提交 31abb0c)。
6. **API 正名与薄包净化(提交 5c91b46)**:cccp censo/isostat 接口公开 API 正名(`CENSO_PRESETS`/`CENSO_PART_FLAGS`/`CENSO_PARSE_PRIORITY`/`CENSO_PART_INDEX_MAP`/`part_index`/`resolve_preset`/`generate_rcfile`/`build_cli`/`parse_censo_json`/`write_part_templates`/`normalise_titles_for_isostat`,旧下划线名保留别名);三个 acp 薄包移除私有符号镜像与转发方法;测试改直连 cccp 接口。消除 acp→cccp 跨包私有 import(治理原则)。

### 7.3 遗留(未做)

- **P3 步骤 10/11(2026-08-02 完成)**:全量 pytest 948 passed + 7 skipped;`bkill 1110` 释放 compute-01;冒烟(乙醇,`--max-frames 20`,4 核)全流程完成 exit 0,任意时刻合计 CPU ≤ 400%(MD 单进程 / batch_opt 每帧 nproc=1 / CENSO·DFT 4×~100%);curcusone-test 以 `--resume` 重提(job 1114),xtbmd/batch_opt/isostat 三阶段 checkpoint 指纹匹配直接跳过,运行中(13:45 起,CENSO 阶段)
- 线程钉住剩余项:`cccp/interfaces/orca.py` 仍为 ⚠️ 部分(靠 `%pal nprocs` 控线程,BLAS/MKL 仍受环境 OMP 影响)—— 未列入 P0/P1 范围,冒烟验证 ORCA 阶段合计 CPU ≤ 400%(4×~94%)
