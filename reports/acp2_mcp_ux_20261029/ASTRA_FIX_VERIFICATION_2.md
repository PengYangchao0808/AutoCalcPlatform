**仍不予合并，包括作为“有限修复”PR。** 已核对 HEAD `3cba191c`、干净工作区及 `9eac208e..HEAD` 的 stat/逐文件 diff（25 文件）；未改文件、未跑测试，本轮运行结果均 **NOT_VERIFIED**。

1. **完整正文边界：FAIL。**
   [artifacts.py:1049](/ACPServer/AutoCalcPlatform/src/acp_services/data/artifacts.py:1049) 仍先截断；MCP 检查不到完整正文，新增负例没有覆盖敏感串跨截断边界。
   [mcp.py:2930](/ACPServer/AutoCalcPlatform/src/acp_services/entrypoints/mcp.py:2930) 仅拒绝**截断的**非 UTF-8；完整非 UTF-8 仍经 `surrogateescape` 放行。
   [report.py:49](/ACPServer/AutoCalcPlatform/src/acp/confsearch/report.py:49) 越界引用原样返回；[report.py:283](/ACPServer/AutoCalcPlatform/src/acp/confsearch/report.py:283) 是排除少数字段，并非安全白名单，外部绝对 argv 路径仍可泄露。

2. **停止伪 verified：FAIL。**
   input/rcfile 已增加实际摘要重算，这点确有修复；但 [report.py:188](/ACPServer/AutoCalcPlatform/src/acp/confsearch/report.py:188) 信任证据自报路径，未证明本次执行、成员或配置绑定，最终仍返回 `True`。
   [测试:450](/ACPServer/AutoCalcPlatform/tests/test_acp_confsearch_report.py:450) 仍用 `{}`、未证明成员的 XYZ 和不匹配的配置构造“执行事实”；字节一致不等于执行已验证。
   [P4:68](/ACPServer/AutoCalcPlatform/reports/acp2_mcp_ux_20261029/P4_ACCEPTANCE.md:68) 宣称绑定“本次”输入，超过实现能证明的范围。

3. **兼容与证据声明：PARTIAL。**
   [submission_contract.py:397](/ACPServer/AutoCalcPlatform/src/acp/api/submission_contract.py:397) 已豁免 Workbench 三个固定默认值；原误拒路径静态解除，但采用硬编码特例，未统一有效值解析。
   [h1-r2.json:20](/ACPServer/AutoCalcPlatform/reports/acp2_implementation/bb45a46/integration/receipts/h1-r2.json:20) 历史摘要改写**没有撤销**。
   [F4:1969](/ACPServer/AutoCalcPlatform/tests/test_f4_scope_audit.py:1969) 改为匹配摘要调用文本后放行邻近行，仍未限定所属函数；teeth 仍只查字符串。
   SDK fixture/assertion 已同步 `partial`，但 [P2:27](/ACPServer/AutoCalcPlatform/reports/acp2_mcp_ux_20261029/P2_ACCEPTANCE.md:27) 的“8 passed”没有本轮对应回执，不能据此认定新 HEAD 通过。

**仍阻塞的最小项，仅两组：**

- **封正文边界**：完整正文检查后再截断；不可检查编码统一拒绝；报告白名单与受控相对引用；补跨边界负例。
- **撤销超额证明**：本轮统一降为 `unverified` 并同步测试/P4；恢复历史回执、限定 F4 豁免到具体函数/调用点；验收区分历史结果与当前未验证状态。无需本轮重建完整执行绑定。

**DEFERRED 诚实性：部分诚实。** attempt 限制明确写于 [P3:44](/ACPServer/AutoCalcPlatform/reports/acp2_mcp_ux_20261029/P3_ACCEPTANCE.md:44)，SDK 生产链明确为 NOT_VERIFIED，真实 QC 明确未提交；未见这些项直接改成 PASS。可是完整绑定延期却继续输出 `verified`，属于实质过度声明；“历史回执改写 DEFERRED”也不能掩盖本 PR 已改写历史回执。

**满足上述条件后，报告应补一句 residual：** 完整执行/成员/模板绑定、attempt 归属及 MCP 未验证提示、默认值与未知嵌套字段覆盖、SDK 生产整链、真实 QC、残留门禁扩展与历史清理均未闭合；历史 blob 仍可达、敏感性 NOT_VERIFIED，局部修复不代表整体验收通过。