# ACP 2.0 三轨执行计划复评报告（第六轮）

**日期**：2026-10-22  
**结论**：计划可以进入分阶段实施。R5 的两处 P2 等待环、子场景回执身份、搜索生产者、文档同步时点和打包基线问题，已形成可执行的修订方向。本轮未发现新的 S0；仍有 **2 项局部 S1**，应在相关 C Harness/研究循环验收前修正，不阻塞无依赖的科学契约和 Runtime 开发。

本报告承接 [R5 评审](ACP_2.0_Parallel_Plans_Review_R5_20261022.md)。本轮的主要结论是收口，后续应把重点转向实际注册表、依赖图和测试证据，不建议继续扩充同规模的规划文字。

## 1. 版本与检查范围

| 文件 | 行数 | SHA-256 |
| --- | ---: | --- |
| [A 科学轨](../../../.omo/plans/acp2-plan-a-cccp-science.md) | 807 | `7473ab3d1756de45b8a3c41e92afe48e201bce891d45d6d247532acd5aefb0cc` |
| [B Runtime 轨](../../../.omo/plans/acp2-parallel-plan-b-runtime.md) | 548 | `30cc367899a2607799a694e6ddef47cd6909cebc09028a36ba54e43d6b7a567f` |
| [C RPH/Workbench 轨](../../../.omo/plans/acp2-plan-c-rph-workbench.md) | 682 | `2a7d1dacc92feeaf0779e422d5c1725cebdd0a3e0dce6030b04c1aa81dc991a6` |
| [主计划](../../ACP_2.0_Four_Component_Collaboration_Plan_20261020.md) | 292 | `e789936fd65c13752e70edabc4983bb608431f821ce34a45cbfb631f28c71986` |
| [总索引](ACP_2.0_Parallel_Execution_Index_20261020.md) | 250 | `a5b2702022c34f00cadfc3ae60c24feec5b547e9073be6226639d8873f38f98e` |
| [服务器验收手册](ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md) | 542 | `e26a0b2a680a7207d54db1a3f5b730af901f0af289b9149583b014cc1466bdf5` |

三份 `.omo` 计划均较 R5 更新；主计划、总索引和手册本轮未改。新版 B00/B03 已明确承担 H1 前的规范性同步，因而本报告区分“同步责任与时间已修正”和“文档已经同步”两种状态，不把未来交付计成已完成。

本轮检查包括：R5 六项修订、P2/P4 关键外部依赖、公共回执键、角色解释器选路、搜索父结果和打包命令。对计划中的原始 `c_accept()` 做了隔离执行：临时解释器桩只打印被选角色，不调用产品 Harness。对缺参数情况设置一秒超时，避免错误解析无限运行。

依赖验证使用手工转写的关键局部图，而非全计划自动提取。因此“未发现新的 S0”不等于全局依赖图已经被机械证明无环。未运行产品 pytest、真实模型、浏览器、QC、LSF，未启动或重启服务。

## 2. R5 关闭状态

| R5 项 | 当前状态 | 依据及边界 |
| --- | --- | --- |
| R5-01 P2 提交/审计等待环 | **已闭合** | C T-34 的矩阵和正文均改为 B20a-ready；T-35 改为 B20a-exec 工件；B20b 单向收集 C T-35/A 回执。局部图可排序。 |
| R5-02 公共回执身份冲突 | **已闭合** | B01 注册 namespaced phase；C T-06 将 mode/subcase 归一化后写回执；A 也按公共四元键引用。fixture/live、control/search、nmr/thermo/selectivity 分别可得到 2/2/3 个不同键。 |
| R5-03 T-04 缺 Harness 的 route 验收 | **主要前置问题已闭合，函数仍有残留** | T-04 改用隔离桩，正式 route.json 移到 T-07，不再等待未来 Harness；但原始 wrapper 未落实新增的 dry-run 优先与缺参数退出要求，见 R6-01。 |
| R5-04 搜索生产者与范围 | **生产职责已闭合，汇总命令需修正** | A 的 fixture/契约任务产出 search-contract；B20a-search 生产执行证明；A-20 单列搜索；C T-32 只等 control；H4 明确要求 search。父结果命令和失败传播见 R6-02。 |
| R5-05 规范性文档同步太晚 | **计划时点已闭合，待执行** | B00/B03 要求 H1 前同步路径、枚举、入口与范围；B25b 仅整理已发布内容。主计划与手册当前尚未同步，H1 实施时仍须实际完成。 |
| R5-06 打包基线落后 | **已闭合** | GAP-13 承认已有角色构建；B25a 审计扩展现有配置；验收改用 scripts/build_release.py 生成三角色 manifest，根构建仅补充。 |

已闭合的问题不需要再补一段原则说明。实施时把这些约定放入公共契约和实际验收即可。

## 3. 核心依赖与回执复核

### 3.1 P2/P4 的原等待环已解除

当前 P2 的关键关系为：

```text
B20a-ready → C T-34 → B20a-exec → A 科学审计
B20a-exec + A 科学审计 → C T-35 → C T-36
A-20 + C T-35 + B20a-exec → B20b
B20b + A-20 + C T-36 → B20c
```

B20a-search 为独立的搜索执行证据生产者，A-20 和 C 的搜索子结果消费其证据。这里没有把完整 B20a 作为 C T-34 的先决条件，也没有让 C T-35 等待收集自身回执的 B20b。

P4 仍保持：`A-25a/B23a → C T-40a → B23b → A-25b → C T-40b`。本轮对这些局部关系进行拓扑排序成功。后续 B00/B03 的全量依赖验证器仍需覆盖其他边，尤其把新子交付和对应 readiness 登记进去。

### 3.2 子场景身份已经能够区分

新版 B01 保持公共键 `(owner, scenario, phase, run_id)`，通过注册的 phase 区分可独立签收的子行。

| 场景 | 注册 phase | 同一 run_id 下不同公共键数 |
| --- | --- | ---: |
| evidence-browser-live | experience:fixture / experience:live | 2 |
| research-loop-live | experience:control / experience:search | 2 |
| cross-domain-live | experience:nmr / experience:thermo / experience:selectivity | 3 |

这消除了 R5 指出的身份冲突。数字是对计划声明的键做的静态组合检查，不代表尚未交付的 writer、validator 或持久索引已运行通过。

## 4. 剩余两项修订

### R6-01 · S1：wrapper 的实际分支尚未实现验收要求

**位置**：[C 计划](../../../.omo/plans/acp2-plan-c-rph-workbench.md)T-04 第 223/227 行、T-07 第 257 行。

将正式 route.json 移到 T-07 是正确的，T-04 的前置问题已经解决。但当前内嵌函数仍保留 R5 时的两个行为：

1. `mode=live` 是选集成环境的独立充分条件，没有被 `dry_run=0` 共同约束。
2. `--mode/--phase/--subcase` 分支直接读 `$2` 并 `shift 2`，没有先检查参数个数。只剩一个参数时，shift 失败但 `$#` 没变，循环无法前进。

实际隔离执行结果：

| 输入 | 实际结果 | 计划要求 |
| --- | --- | --- |
| `baseline 60 --dry-run` | C 环境 | 一致 |
| `evidence-browser-live 60 --mode fixture` | C 环境 | 一致 |
| `mcp-live 60 --dry-run` | C 环境 | 一致 |
| `mcp-live 60` | 集成环境 | 一致 |
| `mcp-live 60 --mode live --dry-run` | **集成环境** | 应为 C 环境 |
| `mcp-live 60 --mode` | **一秒内不返回，被测试器终止** | 应立即返回 3 |
| `mcp-live 60 --phase` | **一秒内不返回，被测试器终止** | 应立即返回 3 |
| `mcp-live 60 --subcase` | **一秒内不返回，被测试器终止** | 应立即返回 3 |
| Harness 缺失时调用 baseline | 返回 2 | 符合新版约定，不再要求此时写 route.json |

缺参数的非终止行为也可从 shell 代码直接推导；测试仅给它设置了有界观察窗口。组合 dry-run 的问题只证明解释器选路错误，未证明它已经造成模型或计算副作用。

**最小修改建议**：

- 在读取场景/timeout 前检查至少两个位置参数；每个带值选项在读 `$2` 前检查值存在，错误立即返回 3。
- 将 `dry_run=0` 放到整个 live 分支的共同条件外层。即只有非 dry-run 且“显式 live 或默认 live”才选择集成环境。
- `route_probe` 应调用待验的实际 wrapper，或与其共用同一解析/选路实现，再用解释器桩观察结果。不要另写一份正确的选路逻辑，只测桩而漏测计划中的函数。

**关闭条件**：上述表中所有行符合预期；非法参数不启动 Harness、不发生循环；T-07 使用相同 wrapper 生成正式 route 诊断。

### R6-02 · S1：研究父场景缺少 run ID，且会把已执行失败汇总成未验证

**位置**：[C 计划](../../../.omo/plans/acp2-plan-c-rph-workbench.md)T-30 第 487 行，公共退出码/三态规则，T-36 第 547 行。

control/search 两条命令均带 `ACP2_RUN_ID=research-loop-live-r1`，但新增父结果命令是：

```bash
c_accept research-loop-live 120 --phase aggregate
```

前面 `ACP2_RUN_ID=... c_accept ...` 的临时赋值不会成为下一条命令的持久环境。当前 wrapper 对非 main phase 明确要求已有 run ID。本轮在未预设该变量的环境中执行父命令，实际返回 **3**，没有生成父场景结果。

应与其他多阶段示例保持一致：

```bash
ACP2_RUN_ID=research-loop-live-r1 \
  c_accept research-loop-live 120 --phase aggregate
```

另一个问题是该行规定“两个子行均 PASS 才 PASS，否则记 NOT_VERIFIED(search_capability)”。这个 else 覆盖了子行已经执行且 FAIL 的情况，会与三轨“已执行失败不得降级为 NOT_VERIFIED”相冲突。

建议把父结果规则冻结为：

| 必需子行状态 | 父结果 |
| --- | --- |
| 全部 PASS，引用/摘要/用量等父级断言也通过 | PASS |
| 任一已执行子行 FAIL，或父级校验失败 | FAIL |
| 没有 FAIL，但必需子行未执行、缺前置或 NOT_VERIFIED | NOT_VERIFIED，列出具体缺项 |
| CLI 缺参数/不合法组合 | 退出 3，不生成伪造的场景 PASS |

DEFERRED 仍是 scope，不新增为 result 状态。若 owner 批准缩小范围，按已冻结的 required-child 清单汇总，并保留延期说明；不能把延期项改成 PASS。

**关闭条件**：父命令从同一 run ID 读取两个子结果；至少覆盖 `PASS/PASS`、`PASS/NOT_VERIFIED`、`PASS/FAIL` 三组；H4 不能把 control 单独通过或 search 已失败的运行签成完整通过。

## 5. 两处可随实现澄清的措辞

这些不构成新的架构阻塞，可在 B01/C Harness 的共同样例中一次解决。

- **B01 第 192 行的别名正负例**：当前长句把“deployed 归一化为 integration_ready”和“preflight 归一化为 da-preflight”放在 rejects 列表中，但正文允许读取别名。应拆成“读取别名后接受规范值”和“冲突/未知值拒绝”两类断言，避免测试作者写反。
- **H4 对父结果 evidence_kind 的要求**：C T-36 笼统要求六项 `evidence_kind=live`，同时接受 aggregate 父结果。公共契约应明确父结果用 `aggregate`，其必需执行子行用 `live`，或明确另一种一致表示。不要为了满足汇总中的字面量检查而把聚合记录伪装成新执行。

## 6. 实施建议与评审收口

本轮建议允许推进基线冻结、公共契约、科学静态能力及 Runtime 的既定开发。上述两项修订规模有限：R6-01 在 C T-04/T-07 冻结前修复，R6-02 在 T-30/H4 验收前修复即可，不要求所有轨暂停。

后续应把已有计划转为以下三个实际交付物：

1. **B01 的唯一注册表和公共回执校验器**：用三轨真实形状的样例检验 phase、唯一键、父子结果和 scope/result。
2. **B00/B03 的机器可检查依赖表**：包含 B20a-ready/exec/search、T-30a/b、B23a/b 等实际产物，不再以整项完成替代早期可消费状态。
3. **冻结候选上的最小真实路径**：按既定阶段验证一个 Controller、一次提交、同一 TaskRef、多入口读取与恢复；真实搜索和 DA 科学结论继续按独立证据门判断。

主计划、索引和手册的规范性同步仍是 H1 的实际工作，不能因为本轮认可了其执行安排就跳过。CDXML/search 的范围确认也应保留真实记录；本报告不替代范围批准或科学验收。

架构与协作设计已经具备实施基础。接下来最有价值的评审对象应是实际契约文件、代码和运行证据；除非出现新的范围变化，不建议再为这些已闭合主题增加一轮同规模规划。
