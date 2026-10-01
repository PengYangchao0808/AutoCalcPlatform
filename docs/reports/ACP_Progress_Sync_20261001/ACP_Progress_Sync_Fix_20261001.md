# ACP 进度同步修复记录

日期：2026-10-01。对应任务：`20260930_110815_003_BatchOptimize`。

## 修复结果

已修改计算输入生成、BatchOptimize 阶段上报、调度阶段观察、API 投影与工作台显示。测试完成后，在确认运行、暂停、排队和 pending 任务均为零时重启 `acp` 服务。服务恢复为 `active`，健康接口为 `ok`。

原任务已于修复加载前完成；本次未重跑任务、未修改其计算输入输出，也未将旧结果转换成新的电子态计算结果。

## 问题与处理

| 原报告问题 | 修复 |
|---|---|
| F01 OPT 内隐藏执行数值频率，已收敛仍显示优化 | BatchOptimize 的 TS 优化明确传入 `calculate_frequencies=False`。优化返回后立即完成 OPT，由独立 FREQ 步骤负责频率计算与阶段上报。其他既有 TS 接口调用默认行为保留。 |
| F02 内置频率与 profile 的独立频率重复 | BatchOptimize 频率只由 profile 中的显式步骤执行；`opt_only` 不再附带最终频率验证，也不会将初始/重算 Hessian 的频率当作最终频率。 |
| F03 TS OPT 遗漏 SCF/电子态配置 | TS 输入接入通用输入块生成器，支持 HFTyp、GuessMix、NoUseSym、MO 读取、SCF 收敛策略与迭代限制、辅助基组、资源与额外输入块，并返回电子态诊断 metadata。保留 TS Hessian、Trust、TS_Mode 与几何迭代设置。 |
| F04 不确定进度错误显示 0% 或阶段比例 | 前端判断同时读取 `progress_state`；不确定进度显示动画，右侧进度条读取任务 progress，阶段序号单独显示。暂停状态遵守同一判断。 |
| F05 四个执行阶段映射到六个目录阶段 | BatchOptimize catalog 与执行器的四类 profile 对齐；API 输出 `stage_order`；前端按阶段 ID 匹配，并兼容旧任务中的 prepare/finalize 记录。 |
| F06 阶段数据库状态和时间未同步 | StageTaskObserver 镜像 ProgressReporter 的状态、时间、错误和说明。详情与 BatchOptimize 阶段列表读取实际状态文件，旧任务不必改写历史数据库才能正确显示。批次切换期间允许当前结构的状态覆盖前一结构的完成状态。 |
| F07 缓存返回旧数据库字段 | 缓存只保存状态文件与最新事件的解析结果；每次响应重新合并当前 JobRecord，避免 attempt/result/updated_at 被旧缓存覆盖。 |
| F08 多结构进度回退、实时指标丢失 | 按“结构数 × profile 步骤数”累计已结算工作单元，处理失败、缓存命中与后续结构；优化指标更新保留 batch_item 上下文。百分比表示批次工作单元处理进度，不表示计算成功率。 |

## 验证

所有运行使用 ACP 的 WSL Python 环境；ORCA 输入及计算边界测试使用模拟输出，没有发起新的生产量化计算。下表中的测试集合有重叠，不应累计为独立测试总数。

| 验证集合 | 结果 |
|---|---|
| 进度、批次、优化轨迹、阶段计划、TS 接口和 TS Mode 输入，含初版专项回归 | 250 passed |
| 新增批次切换及前端回归后的专项集合 | 173 passed |
| 最终 API、专项回归、任务状态显示验证 | 53 passed |
| 前端、API、通用 ORCA 输入、组合方法、基组与电子态兼容集合 | 初次 566 passed、3 failed、1 skipped；其中两项是旧 BatchOptimize 六阶段断言，已更新为实际阶段并复测通过。剩余一项见下文。 |
| Node 执行真实前端函数 | 不确定进度、暂停进度、当前 FREQ 标签、四阶段顺序以及旧六阶段数据兼容均通过。已纳入专项 pytest。 |

兼容集合剩余失败：`test_orca_optimize_parses_mocked_run_into_qcresult`。本机存在 `/usr/bin/orterun`，通用运行环境构建先调用 `orterun --version`，随后调用 ORCA；原测试把整个 `subprocess.run` 替换为同一 mock，却断言总调用次数为 1，实际为 2。失败不在本次修改的 TS 输入路径；未扩大本次修复去修改通用 MPI 环境逻辑。另有 1 项环境相关跳过，以及 FastAPI 测试客户端弃用提示。

## 服务复核

重启后调用生产服务的 `/summary`、`/detail`、`/tasks`：

- 摘要状态 `completed`，progress `1.0`，progress_state `determinate`。
- stage_order 为 optimize、frequency、single_point、thermochemistry。
- 详情与阶段列表均只显示上述四个阶段，状态全部 completed，均包含实际起止时间。
- 旧任务 OPT 结束 14:57:24，显式 FREQ 结束 15:02:41，SP 结束 15:04:31，热化学结束 15:04:31；这些是历史执行时间，修复没有改变其含义。
- 完整服务复核快照保存在同目录 `fix_service_verification.json`。

## 使用与限制

刷新工作台页面即可加载新前端。之后启动的 BatchOptimize TS 计算使用新的显式阶段边界及 SCF/电子态设置。

旧任务的 OPT 已经以先前输入执行完成。修复无法追溯改变其 RHF/UHF、BS 设置或频率结果；应核验原结果后，再决定是否重算。本次没有自动提交重算任务。真实 ORCA 的新电子态计算尚未以生产计算验证；本次已验证输入内容、调用边界、状态流转与现有工作流兼容测试。
