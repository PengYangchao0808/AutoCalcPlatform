# ACP 2.0 三轨执行计划复评报告（第五轮）

**日期**：2026-10-22  
**评审结论**：四组分架构及三轨职责继续成立；R4 的 P4 选择性依赖环已经解除。当前仍为**有条件通过**：P2 新增了明确的交叉等待，公共回执尚未覆盖新增子场景身份，C 环境脚本与自身验收要求还有一处可复现的不一致。建议修正这些执行问题后推进相应阶段，不再扩大架构设计范围。

本报告承接 [R4 评审报告](ACP_2.0_Parallel_Plans_Review_R4_20261022.md)。这里的“闭合”表示计划文本已形成一致设计，不表示产品、集群或科学实测已经通过。

## 1. 评审版本与实际验证

| 文件 | 行数 | SHA-256 |
| --- | ---: | --- |
| [A 科学轨](../../../.omo/plans/acp2-plan-a-cccp-science.md) | 801 | `e2655422a8c547facdebc2350b4c63949b3c5084b4f301456510ebd90f6a8530` |
| [B Runtime 轨](../../../.omo/plans/acp2-parallel-plan-b-runtime.md) | 548 | `c4cf98867dcd55f2659ec80b152afa16582248a25683fcaaf489b93618a7a043` |
| [C RPH/Workbench 轨](../../../.omo/plans/acp2-plan-c-rph-workbench.md) | 682 | `2fc752cca50b27c9bea82dc3c4b24f53d16654da0ca0ef76add08acc5050a575` |
| [主计划](../../ACP_2.0_Four_Component_Collaboration_Plan_20261020.md) | 292 | `018247dd1df0df9d8ce6fce93d44d0bdf9ee051a565de9d67090d98e73aa3e38` |
| [总索引](ACP_2.0_Parallel_Execution_Index_20261020.md) | 250 | `eae69e072e185e42b6e0d5be9bdaa0a5d767b18f9bd40c275a7f96a94d4c59a7` |
| [服务器验收手册](ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md) | 542 | `91c738a3a3580c264b843c62dbcb78f63a900f5d6eb6c1dad9fcabcad7e1bd66` |

三份 `.omo` 计划和总索引均已更新；主计划与手册摘要仍与 R4 一致。工作区有其他开发修改，本轮只新增评审报告，不将当前未提交工作区等同于正式候选。

本轮完成了以下检查：

- 对照 R4 八项发现，检查正文、依赖矩阵、验收命令和跨轨消费方。
- 从当前 C 计划提取原样 `c_accept()`，在临时目录中以只打印环境角色的 shell 桩替代 Python，检查六种情况。未执行真实 Harness。
- 将明确的 P2/P4 依赖手工转写为小图，用 Python 标准库 `graphlib.TopologicalSorter` 检查；这是局部图验证，不是全量依赖自动提取。
- 按 B01 定义的公共唯一键，核对 C 新增 mode/subcase 对应的回执是否可区分。
- 查阅工作区新增的角色打包文件和 [2026-10-22 修复报告](../../../reports/architecture_repair_20261022/REPORT.md)，判断旧 GAP 描述是否仍代表当前状态。

未运行产品 pytest、模型、浏览器、QC 或 LSF；未重启服务。后文引用修复报告中的测试状态时，仅作为已有报告的记录，不宣称本轮复验了那些结果。

## 2. R4 关闭状态

| R4 项 | 本轮判断 | 依据 |
| --- | --- | --- |
| R4-01 P4 选择性循环与名称冲突 | **已闭合** | A-25a → C T-40a → B23b → A-25b → C T-40b 已三轨同步；C 入口只等 B23a；统一引用 `experience:cross-domain-live` / `selectivity`。局部图可拓扑排序。 |
| R4-02 B02/Chromium 前置倒置 | **已闭合** | B02a 提供 venv/extra，C T-08 安装并验证 Chromium；B02b package smoke 独立，不再阻塞 C 环境交付。 |
| R4-03 phase/readiness/预检分支 | **大部分闭合** | readiness 已统一；B01 增加显式 phase 和 `da-preflight`。C 仍有 `preflight` 字面量残留；新增 mode/subcase 的身份问题见 R5-02。 |
| R4-04 环境选路与 SOURCE | **核心问题已修复** | 四种正常选路实测符合新约定；A_SOURCE 与 INTEGRATION_SOURCE 已分离。缺 Harness 时的 route 回执未实现，见 R5-03。 |
| R4-05 搜索能力与 DA 时钟 | **时钟闭合，搜索交付未完整闭合** | 三轨已采用同一 `13380/900/120` run spec；C 区分 T-30a/b，但 search 生产者及阶段门仍需落实，见 R5-04。 |
| R4-06 P4 新增范围的预算 | **计划分配已闭合** | NMR/thermo 默认复用，selectivity 限一个新 TaskRef，逐子行记步骤/QC/用量；实际可行性由代表性计时和真实工件验证。 |
| R4-07 P2 MCP 与表单阶段 | **已闭合** | C 消费 B03 已选 MCP 扩展；B18 使用 C T-17 最小表单，完整动态表单保留在 T-38/H5。 |
| R4-08 主计划/索引/手册同步 | **部分闭合** | 索引不再硬编码历史 BASE_REV；其余同步被安排到 B25b/P5，晚于相关 H1/H4 决策，见 R5-05。 |

需要明确区分：原来的 P4 环确实修复了；本轮的 P2 环来自新版依赖关系，不应把两者混称为“原问题没改”。

## 3. 剩余发现

S0 表示会阻塞相应阶段；S1 表示应在相关接口或阶段冻结前修正；S2 表示基线和实施效率改进。发现不要求停止无依赖的静态开发。

| 编号 | 优先级 | 主题 | 最迟修正点 |
| --- | --- | --- | --- |
| R5-01 | **S0** | P2 提交与证据签收出现新的交叉等待 | P2 派发前 |
| R5-02 | S1 | 新增 mode/subcase 未进入公共回执身份；phase 名仍有残留 | B01/C Harness 冻结前 |
| R5-03 | S1 | C T-04 要求缺 Harness 时写 route.json，给出的函数没有实现 | C T-04 验收前 |
| R5-04 | S1 | 搜索被列为本期范围，但缺明确生产任务与整场景汇总规则 | P2 范围和搜索契约冻结前 |
| R5-05 | S1 | 影响早期执行的范围/文档同步被推迟到 P5 | H1/H4 前 |
| R5-06 | S2 | 打包 GAP 与新修复基线脱节，可能重复实施已有能力 | B00/B02 重新冻结基线时 |

### R5-01：P2 的两处依赖环应直接修改消费方前置

**位置**：[B 计划](../../../.omo/plans/acp2-parallel-plan-b-runtime.md)第 160—173、388—400 行；[C 计划](../../../.omo/plans/acp2-plan-c-rph-workbench.md)第 174—175、525、533—535 行。

第一处发生在提交前。B20a 已拆成：

- `B20a-ready`：在 C24a 提交前交付执行接口与预检。
- `B20a-exec`：在 C24a 提交后执行并产出事实。

但 C T-34/C24a 的矩阵和 `Blocked by` 从原来的 B18 改成了**整个 B20a**。按整项完成解释，就变成：C 等 B20a 完成，B20a-exec 等 C 提交。B 侧虽已定义 ready 子交付，C 侧尚未消费它。

第二处更加直接：B20b 的矩阵和正文前置包含 `C-T35`；C T-35 的矩阵和正文前置包含 `B20b`。局部图检查确认 `B20b → C-T35 → B20b` 存在环，与外部软件是否可用无关。

**建议直接替换为以下关系**，不再增加新的汇总层：

```text
B18 / B19a / A 静态契约
  → B20a-ready
  → C T-34（唯一批准、提交）
  → B20a-exec（科学执行事实及受权工件）
  → A-17～A-19（科学审计）

A-17～A-19 + 平台故障证据 → A-20（科学签收）
B20a-exec + A-17～A-19 → C T-35 → C T-36
B20a-exec + A-20 + C T-35 → B20b（收集 A/C 回执）
B20b + A-20 + C T-36 → B20c（H4 总签收）
```

具体最小修改：C T-34 依赖 `B20a-ready`；C T-35 消费 `B20a-exec` 工件和 A-17～A-19，不等待 B20b。B20b 可继续等待 C T-35 与 A 的签收。这与 B20b“记录各方审计回执”的职责一致。

子节点编号也应一并消歧：B 第 173 行把 `23a/23b` 定义为逻辑 B23a/B23b，但数值 todo 23 实际是 B20b，逻辑 B23 是 todo 27。建议机器 ID 使用 `B.B20a.ready`、`B.B20a.exec`、`B.B23.ready` 等完整语义名；显示编号保留在单独字段。

**关闭条件**：C 矩阵、正文、B 子节点注册表和总依赖表完全一致；上述局部图可排序；B20a-ready 的验收不能要求 C 已提交。

### R5-02：不同目录仍可能对应同一个公共回执 ID

**位置**：[B 计划](../../../.omo/plans/acp2-parallel-plan-b-runtime.md)B01 第 188 行；[C 计划](../../../.omo/plans/acp2-plan-c-rph-workbench.md)T-04/T-06 第 223、243 行，T-30 第 487 行，T-31 第 497 行，T-40 第 587 行。

B01 的公共回执唯一键仍为 `(owner, scenario, phase, run_id)`。C 新增 `mode` 和 `subcase`，放在扩展字段与目录层级中，但多个验收命令使用相同 run_id、相同 scenario、默认 `phase=main`：

| 场景 | 同一 run_id 下的结果 | B 公共键的区分能力 |
| --- | --- | --- |
| evidence-browser-live | mode=fixture、mode=live | 两个结果对应同一个键 |
| research-loop-live | subcase=control、subcase=search | 两个结果对应同一个键 |
| cross-domain-live | subcase=nmr、thermo、selectivity | 三个结果对应同一个键 |

本轮按计划声明的键构造记录，每组都只得到一个公共键。不同文件路径能避免文件覆盖，却不能保证公共索引、去重、引用及 verified 判断不会混淆结果。C 自有 `scenario_key` 没有被 B01 声明为公共键的一部分；T-31 的 fixture PASS 尤其不能被错误选作 live 终态。

**建议选定一种共同表示**。较小的改法是保留四元键，把这些可独立签收的子行登记成唯一的 namespaced phase，例如 `experience:fixture`、`experience:live`、`experience:control`、`experience:search`、`experience:nmr`、`experience:thermo`、`experience:selectivity`；`mode/subcase` 仍可保留作显示元数据。若继续坚持 phase 与 mode 正交，则需由 B01 正式扩充身份规则、升级 Schema，并让 A/B/C 的 writer、reader、聚合器共同执行，不能仅改 C。

parent 汇总还需显式声明必需子行、接受的 evidence kind、允许的 scope、唯一终态以及缺失时的结果。`scenario_key` 应统一是否带 `experience:` 前缀；当前 search 与 selectivity 的写法不同。

另外，B 使用 `--phase da-preflight`，C 第 87、273、523 行仍写 `preflight`。T-09 甚至要求该字面量，否则记 `phase_dispatch` 缺口。应直接改为 B 的注册名，或在 B01 登记且测试兼容别名，避免“公共注册表优先”和命令字面量各自成为权威。

**关闭条件**：构造上述 2/2/3 份真实形状的样例，共同 validator 和索引能全部保存并唯一引用；H4 只接受 T-31 的 live 终态；未知 phase 不隐式进入执行。

### R5-03：C T-04 的新 route 验收与给出的函数不一致

**位置**：[C 计划](../../../.omo/plans/acp2-plan-c-rph-workbench.md)T-04 第 223、227 行及 T-05/T-06 前置。

新验收明确要求：Harness 尚未交付时可以返回 2，但必须先写 `<run_id>/route.json`，记录解释器、产品路径、Harness 路径和测试版本。给出的函数仍包含：

```bash
[ -f "$src/scripts/acceptance/acp2_experience.py" ] || return 2
```

该检查之前没有 route 写入。隔离执行结果如下；这些 exit code 仅为包装函数测试，不能记成产品场景 PASS。

| 输入/条件 | 观察结果 |
| --- | --- |
| `baseline 60 --dry-run` | C 解释器，符合约定 |
| `evidence-browser-live 60 --mode fixture` | C 解释器，符合约定 |
| `mcp-live 60 --dry-run` | C 解释器，符合约定 |
| `mcp-live 60` | 集成解释器，符合约定 |
| `mcp-live 60 --mode live --dry-run` | 集成解释器；与“dry-run 优先使用 C 环境”文字不一致 |
| Harness 文件不存在 | 返回 2，实际产生的 route.json 数量为 0 |

T-04 在 T-06 创建 Harness 之前执行；T-06 又经 T-05 依赖 T-04。因此原样执行该函数无法满足 T-04 的新验收，不能用“等 Harness 创建后再写”解决。

**建议**：将选路记录做成不依赖产品 Harness 的轻量 helper，在缺文件检查之前执行；或直接让 T-04 的隔离测试使用解释器桩验证选路，把正式 route 记录移到 T-07。二选一即可，不必给 wrapper 增加另一套完整回执实现。

`--dry-run` 与 `--mode live` 同时给出时，明确选择 dry-run 优先，或作为冲突参数返回 3。还应保护缺参数的 `--mode/--phase/--subcase`，防止 `shift 2` 失败后解析循环无法前进。

**关闭条件**：在 Harness 不存在时仍能完成 T-04 规定的选路验证；正常四种选路保持正确；所有结果明确是路由诊断而非 live 验收。

### R5-04：T-30b 已声明本期搜索，但生产任务和门控还未落实

**位置**：[C 计划](../../../.omo/plans/acp2-plan-c-rph-workbench.md)T-03 第 213/217 行、T-30 第 483—487 行、T-32 第 505 行、T-36 第 543—547 行；[A 计划](../../../.omo/plans/acp2-plan-a-cccp-science.md)第 144、515—519 行；[B 计划](../../../.omo/plans/acp2-parallel-plan-b-runtime.md)B20a 第 388/392 行。

本轮已正确禁止用控制循环 PASS 证明搜索 PASS，并为 search 补充输入、候选来源、生成步骤、筛选理由和验证器字段。剩余问题是：

1. C T-30b 写“本期范围”，但科学输入与判据只通过 `A-req-04` 请求；A 当前主要规定区分搜索与已知候选，没有把该搜索交付明确绑定到某个 todo 的产物和验收。B 的 `B-req-15` 也没有明确对应的搜索执行 proof 生产子任务。请求编号不是已经落实的生产者。
2. C T-32 仍等待整个 T-30。若 control 已完成而 search 暂不可用，会重新阻塞本可独立验证的会话恢复。C T-31 已改为只等 T-30a，T-32 应采用同样粒度。
3. H4 要求 `research-loop-live` 整场景 PASS，但 T-30 现在只生成 control/search 两个子结果，没有规定如何得到父场景结果，也没有明确 search 是否为 H4 必需子行。

**建议**：默认保留“搜索本期在范围内”，为 A 的输入/生成契约与判据、B 的实际执行证明各登记明确生产子任务；C 只负责驱动和保存轨迹。T-32 消费 T-30a 的 task/record refs，不等搜索。父结果按注册表聚合；search 作为必需行未验证时，不能借 control 的 PASS 关闭完整搜索能力。

如果真实意图是首期只交已知候选验证链，则把 search 明确列为延期，并同步 H4 与主计划范围，不能同时写“本期必需”又允许它永久未验证而宣称全范围完成。

**关闭条件**：搜索请求有 A/B 生产任务、产物与 readiness；control 成功可继续恢复测试；H4 对 search 的处理只有一种解释。

### R5-05：早期契约与范围的同步不应等到 B25b

**位置**：[B 计划](../../../.omo/plans/acp2-parallel-plan-b-runtime.md)B25b 第 478 行、B26b 第 502 行；[主计划](../../ACP_2.0_Four_Component_Collaboration_Plan_20261020.md)第 168/208 行；[总索引](ACP_2.0_Parallel_Execution_Index_20261020.md)第 44—55 行。

总索引已将硬编码 BASE_REV 改成必须由 B00 提供，修订正确。其他 R4 同步事项虽然已有明确 owner，却被放进 P5 的 B25b，包括 phase/readiness、角色环境、P4 顺序、H4/H6 必需行和 CDXML 范围。

这些信息在 H1、P2 或 P4 已经被使用。等到发行安装阶段才消除冲突，会让执行者先按互相矛盾的版本工作，再在末尾补文档。主计划和手册的摘要仍与 R4 相同，也说明本轮只是安排了未来同步，尚未完成范围对齐。

**建议**：将规范性同步分配到产生契约的时点：B00/B03 更新基线、路径、公共枚举、P2 入口；ADR-008/H4 前明确 CDXML 和搜索范围；B23 前更新 P4 子交付和必需行。B25b 只负责将已经冻结的内容整理成实际安装/升级手册，并核对最终版本。

A 当前明确说 CDXML 延期若影响主计划承诺需 owner 确认，这一事实应保留。范围讨论不阻塞现有 MOL/SDF/SMILES 路线的开发，但未确认的删减不能被计为原主计划已经全部满足。

**关闭条件**：每个阶段开始前，其范围与执行契约已有一致权威版本；P5 做复核而非首次决定早期范围。

### R5-06：打包计划需要吸收 10 月 22 日的新基线

**位置**：[B 计划](../../../.omo/plans/acp2-parallel-plan-b-runtime.md)GAP-13 第 60 行、B25a 第 468—472 行；[现有打包说明](../../../packaging/README.md)；[本机修复报告](../../../reports/architecture_repair_20261022/REPORT.md)第 14、63—76、85 行。

B 仍把角色打包配置和 `scripts/build_release.py` 当成尚不存在的新增任务，并写“关闭 debug G4 OPEN”。当前工作区已经存在这三个角色配置和构建工具；修复报告也记录了本机三角色构建/安装与独立 worker xTB 分项证据。

这不表示 ACP 2.0 的发行验收已经完成：新增科学契约、RPH、MCP 和升级路径仍需集成。但 B25a 应改成“审计并扩展已有角色构建、更新本期资产与依赖、重建 ACP 2.0 候选”，而非再次从空白创建。

B00 的 final_debug_candidate 应评估现有 repair overlay、逐文件快照与后续计划漂移记录。修复报告已经说明归档中的部分计划不是当前最新版；不能把旧 overlay 里的规划文件覆盖本轮三份计划。

构建命令也应明确调用角色构建工具：当前打包说明使用 `scripts/build_release.py --out <new-directory>`；B25a 仅写根目录 `python -m build` 成功，不能单凭这一条证明三个角色制品已生成。验收应读取角色 release manifest，核对三角色的 wheel/sdist、资产、依赖和互不重叠的文件所有权。

**关闭条件**：GAP 区分历史基线与本次冻结事实，已有打包代码得到复用；旧证据按适用范围引用，不升级为本轮未测能力的 PASS。

## 4. 最小收口建议

这轮无需再新增大段原则。建议直接修改以下位置，并用少量公共契约样例固定结果：

1. **先修 P2 四处前置**：C T-34 的矩阵和正文改为 B20a-ready；C T-35 的矩阵和正文改为 B20a-exec 工件。检查 B20b 保持单向收集回执。
2. **冻结唯一回执身份**：让 T-30、T-31、T-40 的子行可由公共索引唯一定位，并明确父场景聚合；统一 da-preflight 名称。
3. **让 T-04 原样可执行**：移除对未来 Harness 的诊断依赖，或补轻量 route 写入；用相同隔离选路检查复核。
4. **明确搜索交付与阶段门**：A/B 认领搜索输入和执行证明，恢复测试只等 control；完整搜索签收仍等 search。
5. **前移规范性文档同步，更新实际基线**：吸收现有角色打包和修复快照，P5 保留完整安装/升级实测。

关闭上述问题后，可以按阶段实施；不应再以文档数量或 todo 数量衡量准备程度。后续评审重点应转向实际公共注册表、机器可检查的交付物图、真实命令输出及科学证据，而不是继续在三份计划中重复追加规则。

## 5. 复核清单

- [ ] C T-34 仅等待 B20a-ready，C T-35 不等待汇总自身回执的 B20b。
- [ ] 公共键能区分 fixture/live、control/search、nmr/thermo/selectivity；父场景不会择取任意一份 PASS。
- [ ] B/C 使用同一个 DA 预检 phase；机器子节点 ID 不混用逻辑编号和 todo 编号。
- [ ] Harness 缺失时 T-04 的诊断验收可独立完成；dry-run 优先级有测试。
- [ ] search 的 A/B 生产者、父结果和 H4 必需性明确，control 不被 search 阻塞。
- [ ] H1/H4/P4 所需范围与文档在使用前同步，P5 只复核发行事实。
- [ ] B00/B25 使用当前修复基线和既有角色构建能力，保留旧证据范围及计划漂移记录。
