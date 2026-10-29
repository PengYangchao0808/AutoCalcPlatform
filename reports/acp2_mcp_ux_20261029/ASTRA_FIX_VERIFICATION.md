**结论：当前 HEAD 不予合并；允许继续作为一个 PR 修补，不必拆回两份。**
已核对 `823b5ce6`、工作区干净，逐文件检查 `git diff 9eac208e..HEAD`（20 文件）。全程只读、未跑测试；运行结果、远端 CI/PR 状态均 **NOT_VERIFIED**。

1. **发现①：FAIL，绕过开关修了，完整内容边界没修。**
   - [mcp.py:2905](/ACPServer/AutoCalcPlatform/src/acp_services/entrypoints/mcp.py:2905) 已严格解码 base64、拒绝坏编码，并检查解码正文；这部分有效。
   - [artifacts.py:1035](/ACPServer/AutoCalcPlatform/src/acp_services/data/artifacts.py:1035) 仍先截断，MCP 只检查片段：32 字符随机令牌截成 31 字符即可避开长度检测；新增测试只是“片段内已有完整路径”，没覆盖边界绕过。
   - 非 UTF-8 仍以 `surrogateescape` 放行，未落实不可检查字节拒绝。
   - [report.py:36](/ACPServer/AutoCalcPlatform/src/acp/confsearch/report.py:36) 对越界 `weight_table.reference` 原样返回；`_publish_execution` 仅替换 cwd，外部可执行文件绝对路径及其他字段仍可进入报告。公开报告可读性未闭合。

2. **发现②：FAIL，仍把不足证据标成 verified。**
   - [report.py:167](/ACPServer/AutoCalcPlatform/src/acp/confsearch/report.py:167) 已检查 rc、重算 JSON/XYZ hash、要求非空成员；但 input/rcfile **仍只检查 64-hex 形状**。
   - [测试:450](/ACPServer/AutoCalcPlatform/tests/test_acp_confsearch_report.py:450) 仍填 `"a"*64` / `"b"*64`，输出文件只是 `{}` 和未证明成员的 XYZ，却断言 verified；P4 chain 也保留假摘要，仅证明转发。
   - 同 part 旧输出仍可被新 rc=0 收养；未核对文件内成员、模板、enabled_parts 与 final_part 一致性。
   - cwd 来自证据自身，未绑定可信任务根；绝对 artifact 路径、`../` 未拒。缺 cwd 会变成 `Path(".")`；畸形 rc/读文件异常也未统一降为 unverified。

3. **发现③：PARTIAL。**
   - [status/summary](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:5047) 改走 `cache.job_root()`，静态调用链确认该描述符路径不再 fetch；真实 HTTP 零 fetch 回归 **NOT_VERIFIED**。
   - attempt 仍取当前 record 贴到旧 manifest 上。P3 文档已明确登记这一限制，**不是完全静默**。
   - 但 `remote_cache_only` 不等于 attempt 未验证，本地没有提示，新增 `artifacts_unknown_reason` 也未进入 MCP status 投影。

4. **发现④：PARTIAL，存在实际兼容回归。**
   - 显式 INT 字段冲突现会拒绝；缺字段/None、默认值冲突及未知角色/嵌套键仍漏拒。
   - **会拒合法 Workbench 操作**：[wizard.js:3232](/ACPServer/AutoCalcPlatform/frontend/js/workbench/features/wizard.js:3232) 固定顶层温度为 298.15，同时保留用户设置的角色温度；用户设 INT=400 时，新校验直接拒绝。压力、缩放因子同理。
   - `coverage=partial+gaps` 对默认值缺口基本诚实，但不能豁免这次回归，也未列明未知嵌套字段缺口。

5. **发现⑤：PARTIAL；发现⑥：PARTIAL。**
   - P3 已承认 scripted Controller/SDK 整链未交付，但 P3/P4 总标题仍 PASS，P3 仍写 base64 豁免；新增 ASTRA 文件是旧审查和计划，不是修复验收。
   - P2 把 SDK 回执文字改成 `partial`，实际 SDK fixture/assertion 仍是 `complete`，该新声明无对应实证。
   - [residue guard](/ACPServer/AutoCalcPlatform/tests/test_report_residue_guard.py:9) 能挡固定目录重入及四类后缀，包括强制跟踪文件；换目录的源码副本、普通数据库和其他 QC 残留仍漏过。旧污染目录当前已无跟踪文件；历史敏感性 **NOT_VERIFIED**。
   - 另有证据诚信问题：修改了历史 `h1-r2.json` 的摘要；F4 新增“不论位置、含摘要字段名即放行”，teeth 只查字符串存在，不能证明校验仍有效。

合并前最小项，按优先级：
1. **封住完整正文边界**：截断前检查、不可检查编码拒绝；公开报告限制为安全字段及受控相对引用，补边界负例。
2. **立即停止伪 verified**：本轮可统一降为 unverified，并纠正测试/P4 声明；完整执行绑定可延期，无须现在重建执行系统。
3. **修复兼容与证据声明**：Workbench 镜像取角色有效值；撤销历史回执改写、收窄 F4 豁免，更新当前验收及 residual，不能把旧测试记录改写成新结果。

**可 DEFERRED**：完整执行绑定、attempt 身份闭合、默认值/未知字段覆盖、SDK 生产整链、真实 QC、残留门禁扩展与历史清理；须逐项写明未实现及影响。前提是关闭绕过、停止错误 verified、消除本次 Workbench 回归；满足后可合并一个“有限修复”PR，不能签收整体闭环。