# ACP 架构复核后的 debug 与验证

日期：2026-10-21。用户授权在[第二轮复核](../architecture_recheck_20261021_r2/REPORT.md)完成后直接 debug。

**代码已修复：原复核的 4 项功能缺陷、验收脚本误报，以及补测中新发现的 BatchOptimize 参数丢失。最终定向集成 759 passed。候选新服务与新、旧前端两格均 PASS；旧服务两格真实 FAIL。正式 NMR/vibration 像素渲染仍有 4 FAIL，已定位 Chromium GPU 原生崩溃，不能宣告全量验收通过。**

## 1. 已完成的修改

| 项目 | 修复与结果 | 主要文件/测试 |
|---|---|---|
| R2-01 孤儿计算仍活时接管 | 父 PID 死亡不再自动换锁；必须由可信核验端口确认父进程与全部执行子进程停止，证明绑定完整 claim 摘要；核验/复读/替换保留 flock。未知 host 保守拒绝 | `src/acp_services/runtime/attempt_freeze.py`；`tests/test_services_attempt_recovery.py` |
| R2-02 全局 1,000 行截断 | 新增按 job/attempt/resource/scope 原子过期成功 operation 的 API；manager 不再全局扫描后过滤 | `src/acp_services/data/operations.py`；`src/acp/scheduler/manager.py`；`tests/test_services_prepare_recheck_fixes.py` |
| R2-03 辅助文件瞬断后漏重试 | 主 catalog 命中只跳过主文件下载；继续发现/补齐同 manifest 引用的 frequency modes；保留 identity/fence 和 optional 404 规则 | `src/acp/services_data_bootstrap.py`；同上 Data 回归 |
| R2-04 block 身份遗漏选点策略 | generation/reader 共用完整块参数推导，包含 key_points、retain_key_points 等全部参数；算法版本提升至 3；旧版本 2 可读但不可当当前缓存收养 | `src/cccp/analysis/bounded/{identity,manifest,multiresolution}.py`；`tests/test_cccp_bounded_multiresolution_identity.py` |
| R2-05 harness 误报/缺普通场景 | 实际 Chromium 执行真实 F bundle 并调用生产队列刷新；核验本次队列响应而非旧缓存；七类合法普通 CLI 输入各跑科学成功/失败两例；独立 API fixtures 亦可执行；FAIL/NV 三态严格聚合；冻结补丁可叠加到 F1，避免只测旧 HEAD | `scripts/acceptance/{four_combinations,compatibility_checks,ordinary_legacy_cli}.py`；`tests/test_acceptance_{compatibility_checks,ordinary_legacy_cli}.py` |
| 补测新增：BatchOptimize 丢弃 --method/--basis | flat→typed 迁移同时接受 canonical optimization_method/optimization_basis 与历史 functional/basis，canonical 优先，角色覆盖顺序保留 | `src/acp/calculations/batch/options.py:181`；`tests/test_batch_cli_method_forwarding.py` |

同时更新六列 Services 台账，登记停止核验端口与定向重建 API，正确更新状态计数；**没有修改护栏判据或删除兼容面**。恢复测试原先“父 PID 死亡即接管”的断言改为默认拒绝，另用绑定的已核验无子进程证明保留双恢复者竞争正例。

复核时的用户修改保留；未改原计划、旧验收报告或冻结复核记录，未重启生产服务、未发布、未提交代码。测试在临时端口/数据目录中执行；本轮创建的四组合临时 worktree 已移除。

## 2. 独立反例复验

- **执行权**：[orphan-after.json](evidence/orphan-after.json) 中旧子进程仍活，但 `successor_acquired=false / not_held`，不会启动新计算。
- **缓存重建**：[data-after.json](evidence/data-after.json) 中 1,000 条前序成功记录后，目标创建新 epoch、queued、pending=1；不会过期其他 attempt/resource/scope。
- **辅助产物**：同一记录显示首次 modes 瞬断后真实下载次数为 2，正文存在，最终 fetched；暖缓存不重复下载、404 optional、晚 attempt 拒绝有正式回归。
- **身份**：[bounded-after.json](evidence/bounded-after.json) 中 3/5 点块 identity 不同，跨策略拼接被 reader 拒绝，正常两种策略均可收养。
- **浏览器验收器**：新增实际 Chromium 反例证明 HTTP 200 的错误 JS 会 FAIL，旧 rows 仍可见但本次队列 502 时仍 FAIL；真实当前 F1/S1 临时页面/API 联动见 [browser-live.json](evidence/browser-live.json)。

## 3. 最终验证

| 检查 | 结果 | 证据 |
|---|---|---|
| worker/chain/recovery、Data、bounded、legacy CLI、batch、harness、Services/科学架构护栏、文档台账 | **759 passed, 2 warnings，85.95s** | [日志](evidence/integration-final.log)、[JUnit](evidence/integration-final.xml) |
| BatchOptimize 独立批次 | **128 passed**（包含于最终集成，不能再相加） | [日志](evidence/batch.log) |
| 七类普通 CLI 独立批次 | **7 passed**，各包含 completed/failed 两例（包含于最终集成） | [日志](evidence/ordinary-legacy.log) |
| 全部 Node 模块 | **329 passed**；本轮 debug 未再改 JS | [日志](evidence/node-all-outside.log) |
| 正式 NMR/vibration 像素渲染套件 | **4 failed，保留 FAIL** | [原始日志](evidence/browser-tests-outside.log)、[JUnit](evidence/browser-tests.xml) |

Ruff 对本次新增/修复的相关 Python 实现和独立测试通过，`git diff --check` 通过。首轮集成曾因我新增台账行后未同步汇总计数而出现 `622 passed / 1 failed`；已修正为真实 65 行、50 CLOSED、5 RETAINED、10 OPEN，并在最终 759 项中通过，没有放宽测试。

### 四组合

本次真正创建 F0/F1 detached worktree，启动 S0/S1 两个隔离服务，四格分别组合实际静态 bundle 与服务。F0/S0 固定在 `878e3557b3b593a0c42adf842e80fe1408db395e`；F1/S1 为 `096cdffe9a3817d8ee443d824830d87ec6e07bc2` 加 [candidate-source.patch](evidence/candidate-source.patch)，补丁 SHA256 **`ffba7728ba70800c06b3b254acc529bc286b594a30768328e3b39c72b3546863`**。19 个运行源码/资源文件摘要见 [candidate-source.json](evidence/candidate-source.json)，执行后无源码漂移。

| 组合 | 结果 | 说明 |
|---|---|---|
| F0/S0 | **FAIL** | 冻结旧服务的 BatchOptimize 原有 --method 丢参，两种科学状态用例均正确暴露；其他步骤通过 |
| F1/S0 | **FAIL** | 同一旧服务缺陷；新前端不能修复旧服务 CLI 参数迁移 |
| F0/S1 | **PASS** | 旧前端 + 修复服务：实际页面/队列/API 与七类普通 CLI 场景通过 |
| F1/S1 | **PASS** | 新前端 + 修复服务：同上 |

原始四格、每例命令/输入/接收参数/退出码、bundle/config 摘要见 [four-combinations](evidence/four-combinations/summary.json)。总结果为 **FAIL / all_pass=false / exit 1**，没有把旧基线已知缺陷抹掉或改成 NV。

七类验证范围是 **实际普通 CLI parser/handler/请求构建 + 受控科学调用边界**，完整输入及成功/失败路径均执行。helper 子进程只导入目标 worktree 产品模块，并核验来源；`cases.command` 记录等价 CLI argv，实际调用入口由 `execution_entry` 明示为该子进程内 `acp.cli.main(argv)`，实际 driver 命令另有记录。它们不证明真实 QC、七类后台调度全过程或科学发布；这些字段明确保留 NOT_VERIFIED。普通 API demo 的真实服务生命周期另行通过，不能替代七类科学执行证据。

可复现四组合（先将归档补丁复制到当前机器的稳定路径）：

```bash
/opt/acp2/venv/bin/python scripts/acceptance/four_combinations.py \
  --f0-revision 878e3557b3b593a0c42adf842e80fe1408db395e \
  --f1-revision 096cdffe9a3817d8ee443d824830d87ec6e07bc2 \
  --candidate-patch reports/architecture_debug_20261021/evidence/candidate-source.patch \
  --out /tmp/acp-four-combination-reproduce
```

预期旧 S0 两格仍 FAIL，不能将该命令的 exit 1 当作新服务出现同一缺陷。

## 4. 仍未关闭的边界

1. **异常死亡后的生产自动恢复核验端口尚未接入。** 默认不再根据死父 PID 自动重算；需要 controller/可信核验方证明原执行及所有子进程已停止。此项在台账保持 OPEN，不能声称自动恢复已完整交付；正常领取及持权运行不受该恢复前置条件影响。
2. **浏览器像素渲染环境仍失败。** 当前 Chromium `153.0.8010.12` GPU 进程反复 `SIGSEGV / SEGV_ACCERR / exit_code=139`，随后 WebGL context lost。显式 SwiftShader 及完整 Chromium new-headless 均复现；不修改产品、vendor 或测试断言绕过。原生诊断见 [GPU 日志](evidence/acp-debug-browser-gpu-diagnostic.log)、[完整 Chromium 日志](evidence/acp-debug-browser-full-chromium.log)。NMR 乱序响应的 DOM/实例生命周期反例已通过，但不等于像素断言通过。
3. **没有补跑全量 non-slow、真实 QC/LSF 或生产发布。** 本次只对上述修复及相关架构/恢复/兼容面做定向验证；不能据此宣告真实环境或全量架构验收完成。

归档摘要见 [SHA256SUMS](SHA256SUMS)。
