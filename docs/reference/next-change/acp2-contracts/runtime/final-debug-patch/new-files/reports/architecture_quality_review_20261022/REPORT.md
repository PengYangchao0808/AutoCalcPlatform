# ACP 架构 debug 后代码质量与验收评估

日期：2026-10-22。结论：**整体验收 FAIL；已验证的核心修复具备分项签收依据，尚不具备无条件关闭缺陷修复批次、完整架构或产品验收的条件。**

本次完成报告追溯、计划验收门核对、当前实现审查及独立全量非慢回归。评估对象是当前工作树，不能仅用 HEAD 代表；HEAD 为 `096cdffe9a3817d8ee443d824830d87ec6e07bc2`。产品代码未由本次审查修改，生产服务未重启。

**审查依据与证据绑定**

阅读了 [debug 报告](../architecture_debug_20261021/REPORT.md)、[第二轮复核](../architecture_recheck_20261021_r2/REPORT.md)、[首轮复核](../architecture_recheck_20261021/REPORT.md)、[整改交付报告](../architecture_remediation_20261020/REPORT.md)及[原验收报告](../architecture_acceptance_20261019/REPORT.md)。验收条件采用[主方案](../../docs/ACP_Architecture_Adjustment_Plan_20261016.md)相关章节，特别是 §7、§12、§14、§17、§29、§35，以及[整改计划 §7.2 的 G1—G6](../../docs/reference/next-change/ACP_Architecture_Acceptance_Remediation_Plan_20261020.md)。同时核对三路并行索引及 Frontend/Services/CCCP 计划、`.omo/plans/acp-architecture-acceptance-remediation.md`、Services closure、支持矩阵和迁移/交接台账。ACP 2.0 文档用于核对后续交接边界，其新增能力不作为当前 debug 的必做事项。

- 当前 19 个相关运行源码/资源与 debug 归档的文件摘要全部一致；归档补丁 SHA-256 为 `ffba7728ba70800c06b3b254acc529bc286b594a30768328e3b39c72b3546863`，校验一致。
- debug 目录 `SHA256SUMS` 登记的 29 个归档文件全部校验通过。旧报告的 PASS/FAIL 保留其原候选与范围。
- 本次全量 JUnit 与原定向集逐项匹配：**759 项均存在，全部通过，无缺项、失败或跳过**。不能再将这 759 项加到全量计数中。
- 本次复验及文件摘要见 [verification-summary.json](evidence/verification-summary.json) 和 [candidate-snapshot.json](evidence/candidate-snapshot.json)。

**本次实际验证**

| 检查 | 结果 | 证据与限制 |
|---|---|---|
| 完整非慢回归 | **10058 passed、4 failed、13 skipped、95 deselected、29 warnings；32m02s；exit 1** | [日志](evidence/nonslow.log)、[JUnit](evidence/nonslow.xml)；失败全部在正式 NMR/vibration 浏览器套件 |
| 原 759 项定向集 | **759/759 PASS** | 从本次全量 JUnit 逐项核对，属于上行子集 |
| 完整 Node 模块 | **329 passed、0 failed；exit 0** | [日志](evidence/node.log)；沙箱外运行，明确指定产品 Python |
| Services / CCCP Analysis / 科学产品 Ruff | **PASS** | [日志](evidence/ruff-roots.log)；不外推为全仓 lint 通过 |
| 同范围 format | **160 个文件通过** | [日志](evidence/format-roots.log) |
| 额外触及的 9 个 Python 实现/验收脚本 | Ruff **PASS**；format **1 个文件失败** | [Ruff](evidence/ruff-touched.log)、[format](evidence/format-touched.log)；`batch/options.py` 的格式债在 HEAD 基线同样存在，不能归为本轮新增 |
| Python 3.11 compileall / git diff --check | **PASS / PASS** | [摘要](evidence/verification-summary.json)；不代表其他 Python 版本已实测 |
| JSON/XLSX 一致性补验 | **2 passed；exit 0** | [日志](evidence/xlsx.log)、[JUnit](evidence/xlsx.xml)；使用已具备 openpyxl 的系统 Python，单列结果，不改写全量的 13 skipped |

完整回归命令：

```bash
PYTHONPATH=src:. <venv>/bin/python -m pytest -m 'not slow' -q --disable-warnings \
  --junitxml=/tmp/acp-architecture-assessment-20261022-nonslow-normalized.xml
```

13 项跳过原因已逐项保存在验证摘要：qml 2 项、openbabel 1 项、真实 Bruker fixture 1 项、openpyxl 2 项、需 integration 开关的场景 4 项、真实 SSH 凭据 3 项。其中 openpyxl 是声明的核心依赖，两个跳过不构成 XLSX 通过证据；本次已在具备该依赖的解释器补验这两项。真实科学/集群层的跳过继续保持 NOT_VERIFIED。

环境诊断另行留存：初次 Node 运行的 Python 子进程被沙箱以 EPERM 阻止；在允许的环境重跑后通过。初次未统一源码搜索路径的 Python 运行混用了旧安装元数据 `0.1.3` 与源码元数据 `0.2.0`，触发证据字节比较失败并被终止；按原报告的 `PYTHONPATH=src:.` 统一后，原失败单项复验通过，再执行上述完整回归。[原失败](evidence/provenance-before.log)、[规范环境复验](evidence/provenance-after.log)。这些诊断运行不作为完整验收结果，也没有将其失败改写为 NOT_VERIFIED。

**仍阻止收口的事项**

1. **正式浏览器功能仍为 FAIL。** 本次实际失败为 `test_nmr_canvas_survives_success`、`test_vibration_cold_start_arrows_in_real_browser`、`test_vibration_animation_frames_combo_hide_remount_teardown`、`test_vibration_two_instances_do_not_share_state`。Chromium 为 `153.0.8010.12`；三个振动场景无法创建真实 scene instance，NMR 在访问缺失实例时失败。debug 已归档 GPU 原生崩溃诊断，本次失败形态与其一致；本次未重新进行原生 GPU 定位，因此不把四个失败直接认定为四项新增产品缺陷。验收事实仍是四项失败，不能用 Node 通过或 DOM 生命周期反例代替像素、箭头、动画与导出断言。

2. **新增异常捕获违反既定代码规则。** 相对 HEAD，`src/acp/scheduler/manager.py` 的无异常变量 `except Exception:` 从 42 处增至 44 处；新增驻点为 `_purge_job_records` 的准备操作过期分支（2032 行）与 `_expire_bodyless_succeeded_operations`（5020 行）。两处有 debug 日志，但仍不满足根 AGENTS 的“新代码禁裸 `except Exception:`”及主方案 §29.3 的要求。应窄化为明确的存储/缓存/准备操作异常，并保留可观察的失败与重试语义。这是本次确认的代码规范缺口；Ruff 当前选择的规则不会自动证明该约束满足。

3. **异常死亡后的自动恢复尚未交付完整。** `AttemptLockExecutionRight` 现在必须有绑定完整 claim 的父进程、全部执行子进程停止证明才能接管；默认拒绝已关闭原重复执行风险。生产 `worker_bootstrap.py` 构造该端口时没有注入 `recovery_check`，全源码搜索也未发现生产核验适配器。因此“保守拒绝接管”可签收，“生产自动恢复完成”仍为 OPEN。这与正常领取、持权执行的通过证据分开判断。

4. **独立发行与完整默认链仍有已登记缺口。** 当前根配置仍发行同时包含 acp/acp_services/cccp 的单 wheel，独立 CCCP、worker、Web/static 角色配置未交付；G4 是代码交付 OPEN，不能归为缺少外部软件。`DEFAULT_ENVELOPE_WORKFLOWS` 只有 `singlepoint`，8 项科学闭包不等于 8 项默认链，更不等于 15/15 全覆盖。保留其他工作流的 legacy 路径符合本批范围，不能据此认定其现有运行损坏，也不能宣称全量迁移已通过。

5. **真实产品与部署证据尚未齐备。** 当前候选没有新增的 M1 GUI/CLI 真实科学、真实 LSF/SSH/SFTP、生产发布及相应性能/回退验收证据。身份/配额生产接线、MCP 默认入口、项目包入口、计量接线、A04 及前端 prepare/availability 消费为明确交接项，不能列为本轮 debug 漏做，也不能当作整产品已经具备的能力。

**四组合应如何解释**

本次校验归档及源码一致性，没有重新启动四组 worktree。可采信其已声明范围的结果，不能扩大范围。

| 组合 | 归档结果 | 判断 |
|---|---|---|
| F0/S0 | FAIL | 冻结旧服务的 BatchOptimize 丢参；属于旧基线事实 |
| F1/S0 | FAIL | 同一旧服务缺陷；不据此认定新前端引入回归 |
| F0/S1 | PASS | 支持修复服务与旧前端在已测范围兼容 |
| F1/S1 | PASS | 支持修复服务与新前端在已测范围联动 |

七类普通场景验证的是实际 CLI parser/handler/request 构建及受控科学调用边界；不证明七类后台调度全过程、真实 QC 和科学发布。四格原始总结果仍是 FAIL，不改写为 all_pass；旧基线缺陷也不要求凭空消失。正式签收应保留原始结果，并明确兼容范围与已知基线差异。

**按计划给出的验收决定**

| 门 | 当前判定 | 可作出的声明 |
|---|---|---|
| G1 正确性 | 已测核心修复可分项通过 | 759 项正式反例及相关回归全部通过；本次未复现新的 P1 科学正确性反例 |
| G2 正式回归与质量 | **FAIL** | 完整回归有 4 项浏览器失败，另有新增异常捕获规范缺口 |
| G3 默认执行链 | singlepoint 已测范围可分项签收；完整范围 OPEN | 本地及受控远端证据有效；自动恢复、更多工作流与真实远端不能一并关闭 |
| G4 独立交付 | **OPEN** | 缺少独立角色打包配置及干净环境安装/运行证据 |
| G5 功能验收 | 浏览器 **FAIL**；真实科学/集群 **NOT_VERIFIED** | 两格 S1 的受控兼容 PASS 不替代这些门 |
| G6 发布运行 | **NOT_VERIFIED** | 当前不能宣布生产验收或发布完成 |

代码质量已有实质改善：完整科学结果持久化、配置/资源传递、执行权保护、Data 定向重建与辅助文件重试、派生身份版本化均有实现和正式反例，旧兼容面仍保留。主要薄弱点仍是组合边界和默认入口的真实运行证据。manager 6555 行、v1_routes 7716 行、services_data_bootstrap 2328 行说明后续仍需按职责收敛；行数本身不作为拒收原因，不要求为本次验收另起一次大重构。

**建议的签收顺序**

先处理两处新增异常捕获，并在受支持的 Chromium/WebGL 环境让四项正式渲染断言通过，再冻结包含当前修复和新增测试的完整候选；代码或资源有变动时复验受影响门。随后按实际声明范围关闭恢复核验、独立角色发行和真实科学/远端门。六项明确延期能力继续按原交接计划推进。

后续 2.0 的总索引仍提供 `096cdff` 开工示例；最新 Runtime 实施计划的 B00/B02 已明确要求另行冻结 `final_debug_candidate`，将两个旧 SHA 作为历史参考。这项前置必须实际完成：直接从裸 `096cdff` 创建开发分支会遗漏当前未提交修复。这里是尚待完成的基线交接，不重复登记为新的产品功能缺陷。

本次全量测试改写的 `e2e-task-root.txt` 已恢复为测试前已有的用户内容，没有归档其正文。归档文件摘要见 [SHA256SUMS](SHA256SUMS)。
