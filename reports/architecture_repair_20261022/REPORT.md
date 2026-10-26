# 架构质量复核后的修复与验证

日期：2026-10-22。**本轮代码修复与正式回归通过，可按已声明范围签收；不代表全部架构、真实部署或生产验收已完成。** 最终冻结候选完整非慢回归为 **10082 passed、0 failed、11 skipped、95 deselected、28 warnings，32 分 22 秒**。未发布、未重启生产服务。

本次承接 [质量复核报告](../architecture_quality_review_20261022/REPORT.md)，修复其中的代码缺口，并补充独立安装和真实科学验证。原 debug、复核及历史 FAIL / NOT_VERIFIED 报告均保留。

## 已完成的修改

| 问题 | 修复与验证 |
|---|---|
| 正式浏览器 4 项失败 | 固定 `playwright==1.63.0`，Linux 验收统一采用 Vulkan / SwiftShader 软件后端。前端、向导与四组合 Harness 共用 `browser_runtime.py`。原 NMR、振动像素、箭头、动画、多实例及导出断言未放宽，正式浏览器相关 17 项通过。 |
| 新增宽泛异常捕获 | manager 两处准备操作清理分支改为明确的 `OSError` / `sqlite3.Error` / `PrepareOperationError`，使用 warning 保留可观察性。相对 HEAD 的裸 `except Exception:` 数量恢复为 42，没有增加。 |
| 异常死亡仅保守拒绝，缺生产核验适配器 | 增加 `CgroupAttemptRecovery`，接入现有 `AttemptLockExecutionRight` 与 worker 装配。显式配置委派 cgroup 后，先绑定执行范围再计算；仅在原 holder 已死、内核确认整个子树为空、路径/inode/设备/boot ID 与完整 claim 一致时接管。旧锁、未配置、子进程尚存、证据变化均继续拒绝。真实委派环境仍未验证。 |
| 独立 CCCP / worker / Web 缺少发行制品 | 增加三个角色配置、出树构建脚本和安装验收工具。三个 wheel / sdist 均已构建，安装路径没有重复归属；全部暂存源码、模型、notice、前端资源均进入制品。源码包可以重新构建出相同包内容。三个全新虚拟环境分别验收。根单发行安装方式保留。 |
| 真实独立 worker 暴露的工件落点错误 | `output_dir` 缺省时，QC 接口曾将文件写到启动 CWD。worker 现在显式绑定 `work_dir`，相对路径也以该目录为基础，冻结计划原文不变。增加两个 worker 共用启动目录的 3 项隔离反例，并重新构建和安装发行包。真实 xTB 再次执行，所有工件位于任务目录。 |
| 后续基线遗漏未提交修复 | 后续计划不再默认从裸 `096cdff` 开工；交付包含既有 debug 和本轮修复的可重建 overlay、完整文件摘要与还原证明。未替用户提交工作树。 |

浏览器修复是验收运行配置的修复，未替换 vendor、伪造 canvas 或改为 DOM 断言。默认 ANGLE 在本机失败，Vulkan 软件路径通过相同正式场景；不宣称所有 GPU 环境均已验证。配置背景可参见 [Chromium SwiftShader 文档](https://github.com/chromium/chromium/blob/main/docs/gpu/swiftshader.md?plain=1)。

资源计划从 `runtime.isolation` 抽出为不依赖控制/存储的纯模块 `runtime.cgroup`，isolation 旧路径保留同对象导出。worker 未引入 ACP 或 jobs DB 依赖，也未增加第二个执行权实现。

## 验证结果

| 检查 | 结果 / 范围 |
|---|---|
| 最终冻结候选完整非慢回归 | **10082 passed、0 failed、11 skipped、95 deselected**；[日志](evidence/nonslow-final.log)、[JUnit](evidence/nonslow-final.xml)、[验证摘要](evidence/verification-summary.json)；原 759 项反例全部存在且通过 |
| 正式浏览器、向导、兼容检查 | **17 passed**；[日志](evidence/browser-official.log)、[JUnit](evidence/browser-official.xml) |
| 停止核验、恢复、worker、依赖隔离 | **278 passed**；[日志](evidence/runtime-final.log)、[JUnit](evidence/runtime-final.xml) |
| 最后工件落点修复及相关反例 | **224 passed**；包括新增 3 项落点反例，最终全量已重新覆盖 |
| 架构、台账、打包、F4/grep、markers、编辑、跨版本恢复 | **393 passed**；该定向集在最后工件补强前执行，最终全量已覆盖最新代码 |
| 源码 overlay 还原后的新增反例 | **18 passed**；还原文件 1,906 个，摘要漂移为零 |
| 正式 Node 模块入口 | **329 passed、0 failed**；[日志](evidence/node-full.log) |
| Services / CCCP Analysis / products 及新增落点测试 | Ruff 与 format **PASS**；不外推为全仓 lint 已通过 |
| 其他本轮修改文件、compileall、diff whitespace | **PASS**；原 `batch/options.py` 基线格式债不作为本轮新增修复 |
| 三角色独立安装与源码包重建 | **PASS**；角色导入隔离、模型/静态资源、worker 冻结组/执行权/回执、无 jobs DB、Web HTTP / ETag 分别核验 |
| 真实 xTB 6.7.1 | 源码 CLI、独立 Web 角色 CLI、独立 worker 均完成 H₂ 优化；能量 `-0.982686139534 Eh`。CLI 规范结构与能量清单一致，worker 工件在任务目录 |

定向计数属于全量的子集或补验，不相加充作全量测试数。正式 Node 命令为 `ACP_PYTHON=/opt/acp/venv/bin/python node frontend/js/module_tests/run_all.js`。

最后全量命令：

```bash
PYTHONPATH=src:. /usr/bin/python3.11 -m pytest -m 'not slow' -q --disable-warnings \
  --junitxml=/tmp/acp-repair-nonslow-final.xml
```

使用具备 openpyxl 的系统 Python 3.11，核心 XLSX 能力已实际执行。11 项跳过为 qml 2 项、Open Babel 1 项、真实 Bruker 数据 1 项、需 integration 开关的场景 4 项、真实 SSH 凭据 3 项，逐项原因保存于验证摘要；95 项慢测被筛除，均不计 PASS。本次 Python 实测版本为 3.11，不外推其他版本。

最终全量还核对了 4 项正式浏览器、15 项 cgroup 恢复与 3 项工件落点反例，全部通过且没有跳过。运行源码、测试、配置和发行输入无摘要漂移，原有测试标记文件已恢复。

第一轮全量曾得到 `10077 passed、11 skipped、95 deselected`；该轮期间仍在补强候选，随后真实 worker 验证发现工件落点问题，因此重新冻结并执行最后全量。第一轮结果仅作过程记录。沙箱内临时端口受限或 TestClient 停滞的补验已改在允许环境运行；不将这些诊断运行冒充最终验收。

真实 worker 的首次回执虽为 completed，但工件路径指向启动目录，独立检查将该次场景记为 **FAIL**，保留 [原始反例](evidence/real-worker-before-fix.json)及工件。测试生成的 CWD scratch 已归档移出仓库根。修复后检查实际工件路径、文件存在性、能量与无 jobs DB，才记 PASS。真实 QC 证明仅限本次 xTB 小分子，不外推 ORCA、GUI 或 LSF。

## 冻结候选与复现

- Git 基线：`096cdffe9a3817d8ee443d824830d87ec6e07bc2`，仅是 overlay 的还原起点。
- 候选身份：`sha256:bb45a46c93d241d03ffae2952a78a2736a637a3f2c8d69b72d2bd0c4fafc3e12`，这是源码候选摘要，**不是 Git commit**。
- [candidate-overlay.tar.gz](candidate-overlay.tar.gz) 包含 tracked 补丁、144 个新增/计划文件及清单；补丁包括此前未提交 debug 修改与本轮修复，不只包含本轮新增行。
- [文件清单](evidence/candidate-snapshot.json)、[tracked 补丁](evidence/tracked-changes.patch)、[还原证明](evidence/reconstruction.json)。从基线和 overlay 实际还原后，1,906 个登记文件全部匹配。
- 用户原有 `e2e-task-root.txt` 不进入源码证据，测试结束后恢复测试前内容；不提交、不归档其正文。

验证期间两份后续 2.0 `.omo/plans` 文档被外部继续更新，本次没有覆盖。归档保留冻结时版本；[变动登记](evidence/post-freeze-plan-drift.json)单列后续计划差异。运行源码、测试、配置和发行输入保持冻结；这两份后续计划不被本轮正式测试消费，后续开发时应另行登记其最新版。

在拥有上述基线历史的仓库中创建独立 checkout，再应用 overlay；不要覆盖现有工作树：

```bash
git worktree add --detach /可写绝对路径/acp-final-review 096cdffe9a3817d8ee443d824830d87ec6e07bc2
tar -xzf candidate-overlay.tar.gz -C /新建的临时overlay目录
git -C /可写绝对路径/acp-final-review apply /临时overlay目录/tracked-changes.patch
cp -a /临时overlay目录/new-files/. /可写绝对路径/acp-final-review/
```

用 `candidate-snapshot.json` 逐文件校验 SHA-256 后再构建或测试。后续开发应将审查后的候选落为实际 commit，并登记完整 SHA；本次没有自动创建提交或开发分支。

发行配置与命令见 [packaging/README.md](../../packaging/README.md)。最终制品暂存于 `/tmp/acp-repair-release-v2/`；持久证据记录其 SHA-256 和完整构建输入，不把临时路径当作已发布仓库。制品需从对应源码候选重建，不混用同版本的较早 wheel。离线验收使用 `--no-index`、最终角色制品目录及只含第三方依赖的 wheelhouse，三个环境均不继承系统包，也不注入源码 PYTHONPATH。

## 当前可以关闭与仍需部署验证的范围

| 门 | 本次范围 |
|---|---|
| G1 | **已测修复范围 PASS**；原 759 项反例全部通过，新增 worker 工件落点反例通过 |
| G2 | **PASS（本轮声明范围）**；最终完整非慢、Node、正式浏览器及所选质量/架构/恢复护栏通过；不宣称全仓 lint 或未执行环境已通过 |
| G3 | 停止核验适配器已接入，本地恢复须显式委派 cgroup。历史无边界锁不自动接管；真实委派恢复仍 OPEN。其余 workflow 的 legacy 路径保留，未扩大默认集合 |
| G4 | 三角色在声明的本机安装/运行范围通过；独立 worker 另有真实 xTB 证据。根默认发行切换与生产分发分别登记 |
| G5 | 正式浏览器与 xTB CLI/worker 分项通过；完整 M1 GUI/ORCA、适用真实远端及四组合仍沿各自范围验收，不改写旧 F0 的 FAIL |
| G6 | **NOT_VERIFIED / 未发布**。未改生产 DB、未重启服务，仍受既有 F1—F4 / todo 76 发布门约束 |

身份/配额、MCP 默认入口、项目包、计量、A04、前端 prepare/availability 等既有明确交接事项不在本次缺陷修复内。真实委派 cgroup、完整 M1、LSF 和生产回退/性能门缺少实际证据时，不能宣布全部架构或产品验收通过。

本目录的证据与候选包摘要见 [SHA256SUMS](SHA256SUMS)。
