# ACP2 最终验收修复计划 Revision 8 独立复审

审查日期：2026-10-24。对象：`.omo/plans/acp2-final-acceptance-remediation.md` Revision 8。

代码候选：`f6dd3c928b4f11dc8c3782c4b15c7130cc856a0b`，tree `2e21cf63824fa9363890268fa8a767fd79691076`。

## 判定

**R8 的总体修复方向和执行顺序合理，上轮主要问题已在计划层面得到实质修正；仍需补入 1 项 P1、2 项 P2，再作为完整实施基线。无需重写整份计划。**

这不是重复 R6 的否决理由，也不是对产品完成验收的签署。本次完整阅读 R8 正文及审计历史，重新追踪真实消费者、读根和存储代码，并运行了三项小型探针。未把历史 focused review 的 CLOSED/PASS 当成独立证明。

本次没有重跑全量回归、安装验收、QC 或真实远端作业；没有修改产品代码、原计划或旧验收报告。新的脚本与结果仅在本报告目录，探针数据在临时目录。原有工作区文件未清理。

## 已认可的修改

以下是“计划要求已充分修正”，不是“实现已经通过”：

| 上轮关注点 | R8 当前要求 | 本轮判断 |
|---|---|---|
| 预检与提交请求不同 | 同一 normalized request；独立可选 `preflight_fingerprint`；副作用前比较；旧客户端兼容；更新旧 JS 断言 | 主设计已解决，新增并发分支见 N2 |
| 普通 optimize/frequency 无 envelope 就无法显示 | execution release / manifest / task record 的分层来源；不编造 release；受控读根 | 普通本地结果的兼容方向成立，远端身份缺口见 N1 |
| 未完成 prepare payload 被当作科学报告 | 只读已发布 READY 报告并核验摘要、attempt、generation、授权 | 原问题已解决 |
| proposal/record 共用同名 key | 加 kind；writer/reader 共用 helper；`create()` 默认接入 | 原来的跨 kind 冲突已解决，分隔符编码缺口见 N3 |
| 从其他源码根证明候选包含静态资产 | 资产合入新冻结候选、重建、验证安装包和实际 URL | 已解决 |
| 直接删除公开 recover 方法 | 保留签名并明确 unsupported，或正式接线；同步契约状态 | 已解决 |
| 验收事实口径 | 回归 FAIL；环境存在与当前候选通过分开；M2 分类；RPH 实现/安装/live 分开 | §0.1 修正合理，落地报告仍是待办 |
| 实施顺序 | 范围前置、共享契约先行、公共文件串行合并、闭环→回归→新制品 | 合理 |

## N1 — P1：当前远端 attempt 也必须验证缓存身份

**计划位置：B1-1 第 90 行，B1-5 第 149–150 行及 provenance 分支。**

R8 只明确拒绝 `root.historical and root.is_remote and not root.catalog_ready`。但 `_ReadRootAdapter.resolve()` 对**当前**远端 attempt 同样可以返回 `found=True, historical=False, catalog_ready=False`，并提供一个仍包含旧 attempt 文件的缓存路径。

代码依据：

- `src/acp/services_data_bootstrap.py:681` 的 `_catalog_ready_for_attempt()` 会检查 manifest sidecar，发现 attempt 不一致便返回 False。
- 同文件 `:1844` 的远端分支仍返回缓存 path；`found=True` 不等于文件已经通过 attempt 身份检查。
- `src/acp/storage/manifest.py:153` 的 `ResultManifest.read()` 直接读文件，不检查 sidecar。
- `src/acp_services/entrypoints/status_projection.py:60` 的 `attempt_release_facts()` 也只读字符串事实，不验证请求的 attempt。

**已运行探针**（`source-probes.json → remote_current_attempt`）：真实 `RemoteStructureCache` + 真实 `_ReadRootAdapter`，缓存 manifest 标记 attempt 1，任务当前 attempt 2：

```text
requested_attempt = 2
root.found = true
root.historical = false
root.catalog_ready = false
R8 historical-only guard rejects = false
direct manifest read = [attempt-one-product]
direct release facts = attempt 1 的 release_digest
```

因此，若按正文规定直接读 manifest/provenance，旧科学产物可能被包装成当前 attempt 2 的视图。**这是对拟议路由的确定性设计风险，不声称尚未实现的新路由已经在线返回错误响应。**

建议追加明确规则：

1. 远端 current 与 historical 都不能仅凭 `root.path` / `found` 使用文件。manifest 和 execution facts 必须分别证明属于请求的 attempt；catalog 的通过也不自动证明任意 envelope/receipt 文件属于同一 attempt。
2. 当前远端缓存不能证明身份时，允许返回真实 task-record 的空/部分视图，或明确 `resource_not_ready`；不得纳入未验证的 manifest products/release facts。历史 attempt 无法证明时继续拒绝。
3. 增加“当前 attempt 2 + attempt 1 manifest/receipt 残留”的负例，以及“manifest 当前、receipt 旧”的混合缓存负例。断言无旧 artifacts、无旧 release provenance、无 GET fetch。

这不要求重做远端缓存系统，也不影响本地普通 legacy 结果的 200 策略。

## N2 — P2：输入变更时需同时处理在途预检，不能只清空页面状态

**计划位置：B1-3 第 117–129 行、B1-8 前端接线与测试。**

R8 的指纹传输能保护服务端拒绝过期请求，但未安排修复真实客户端的在途合并与旧响应回写。

代码依据：

- `science-question.js:604` 使用固定 `identity = manual-<question>`，未传 request revision。
- `science-query.js:216–218` 的 key 由 identity + generation（默认 0）生成，不包含请求体。
- `resource-store.js:77` 对相同 key 的在途请求直接共享 promise。
- `science-question.js:610–619` 在响应到达后无输入 revision 比较，直接写 `preflight.ok/body`；编辑时清空状态不足以阻止旧响应写回。

**已运行探针**（`client-probe.json`）：加载真实 ResourceStore、experience validator、science-query，仅请求 adapter 为受控探针：

```text
第一次请求输入 XYZ-A 尚未完成
第二次请求输入 XYZ-B，identity 不变
实际 adapter calls = 1
第二次收到 plan.input.source = XYZ-A
第二次收到 fingerprint = fingerprint-A
```

这是**在途合并**，不是完成后永久复用缓存；后者经代码核对并不存在。本次也不把受控 adapter 探针当 live 验收。

影响：编辑后重试可能显示旧预检成功，随后被新的服务端 `preflight_stale` 拒绝；只有旧请求返回、没有第二次请求时，也可能重新点亮提交按钮。由于 R8 已安排服务端比较，不能据此声称旧参数会绕过检查执行。

建议追加：

1. 每次影响规范请求的编辑推进一个 input revision；传给 `science.preflight()` 的 generation，或使用覆盖完整请求内容的稳定 key。
2. `runPreflight()` 捕获发起时 revision，完成/失败回调只更新仍匹配的页面状态；编辑时取消/失效对应在途请求。只改客户端 key 不足以防旧 catch 回调覆盖新状态。
3. 测试“预检 A 未完成→改输入→预检 B→乱序完成”和“预检 A 未完成→改输入→A 完成”。新输入必须收到新预检；旧响应不得恢复提交资格。

## N3 — P2：新增研究 key 仍有分隔符碰撞

**计划位置：B1-7 第 217–225 行，尤其 `raw = f"{project_id}:{kind}:{document_id}"`。**

加入 kind 解决了上轮简单的 proposal/record 同名冲突，但 `_ID_RE` 允许冒号，项目及文档身份没有采用无歧义编码。先拼接再散列无法解决拼接阶段的碰撞。

**已运行探针**（`source-probes.json → r8_key_delimiter_collision`）：按 R8 原样实现拟议 key 算法，再交给真实 `ResearchResourceStore`：

| project | kind | document_id |
|---|---|---|
| `a` | proposal | `prop-x:proposal:prop-y` |
| `a:proposal:prop-x` | proposal | `prop-y` |

两者均生成 `a:proposal:prop-x:proposal:prop-y`，第二次 create 抛 `ResearchResourceConflictError`。

范围限定：这两个 proposal_id 符合 proposal schema 的字符规则，Data store 接受这样的 project 字符串；当前常规 `ProjectStore.create_project()` 自动生成 UUID，所以**这不是已证明的普通 UI 跨项目漏洞，也不意味着读取授权被绕过**。它是公开 Data helper 对允许输入无法保证命名空间隔离的设计问题。

建议对 `[project_id, kind, document_id]` 做无歧义 JSON/长度前缀编码后统一散列，或先分别转义各段；不可继续对有歧义的 raw 串散列。增加上述分隔符用例，并明确 `create()` 在缺少/空文档 id 时拒绝，不生成多个无身份文档共用的默认 key。

若选择通过严格限制所有 project/document id 来避免歧义，则需把限制落实到所有 reader/writer 的契约入口并说明兼容性，不能只依赖当前 UI 恰好生成 UUID。

## 两项实施说明建议（非本轮阻断发现）

1. **默认方法不要混合展示值与提交值。** B1-2 第 105 行要求将 functional 与可选 basis 组合成 method 值，但当前 `jobBodySpec()` 把该值原样放入 `levels.*.functional`，并另发 basis；`method_flags.py` 也分别生成 `--method` 与 `--basis`。建议明确 `defaults.method.value` 保持 functional，组合形式只做显示 label；若另定组合语法，必须在唯一 builder 中拆分并测试。这里没有运行 QC，不据此断言所有组合字符串必然被 ORCA 拒绝。
2. **范围化批准结论由实际 PASS 集合生成。** §4 选项 B 的固定文案包含 “local Workbench approved”，而 B1-8 允许面板延期、仅直接客户端调用。最终应逐项写明 manual submission、research read API、panel、artifact consumer 哪些已验收；直接客户端通过不能自动扩大成整个 Workbench 通过。这是收窄文案，不是要求扩充发布范围。

## 最小修订与验证建议

在 R8 中补入 N1 的远端 current 身份门、N2 的在途请求版本门、N3 的无歧义 key 编码及相应测试即可；保留现有范围决策、实现顺序和证据规则。随后按已选范围实施，定向→全量回归→冻结新候选→安装验收。

**本轮结论与上轮的区别：R6 的核心数据来源和兼容性设计需要调整；R8 已完成这些调整，剩余问题集中于新规则的身份与并发边界。不能只复查文末列出的三项机械条件便给整体 PASS，但也没有依据要求推翻新版架构。**

## 本轮证据

- `probe_sources.py` / `source-probes.json`：真实 read-root/cache/store；拟议 key 算法单独标注。
- `probe_client.cjs` / `client-probe.json`：真实 JS 客户端模块，在途合并探针。
- `review-context.json`：计划与源码指纹、审查边界。
- `SHA256SUMS`：本目录审查文件摘要。

复现（仓库根目录，Node 使用本机固定版本）：

```bash
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 ACP_RUN_ROOT=/tmp/acp2-r8-review-run /usr/bin/python3.11 reports/acp2_final_acceptance_review_r8_20261024/probe_sources.py
/ACPServer/acp2-tools/node-v22.19.0-linux-x64/bin/node reports/acp2_final_acceptance_review_r8_20261024/probe_client.cjs
```

重跑时将结果另存新目录，保留本轮 JSON 与摘要作为审计记录。
