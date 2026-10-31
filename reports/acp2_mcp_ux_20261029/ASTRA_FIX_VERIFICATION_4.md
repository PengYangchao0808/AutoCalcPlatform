只读复验 HEAD `3ead5686`；未改文件、未跑测试。

1. **第一组关闭**：越界及非字符串引用返回 `None`，不再回显（[report.py:42](/ACPServer/AutoCalcPlatform/src/acp/confsearch/report.py:42)、[report.py:49](/ACPServer/AutoCalcPlatform/src/acp/confsearch/report.py:49)）。
   **第二组部分关闭**：F4 摘要区间增加 `refine_ensemble` 边界约束，已有函数外负例（[test_f4_scope_audit.py:1968](/ACPServer/AutoCalcPlatform/tests/test_f4_scope_audit.py:1968)、[同文件:3216](/ACPServer/AutoCalcPlatform/tests/test_f4_scope_audit.py:3216)）；但 P4 仍把“完整正文预扫描（截断前）”列为 DEFERRED（[P4_ACCEPTANCE.md:78](/ACPServer/AutoCalcPlatform/reports/acp2_mcp_ux_20261029/P4_ACCEPTANCE.md:78)），与本轮说明不符。

2. **暂不能作为有限修复 PR 合并**。唯一最小项：修正上述 P4 第 78 行，将完整正文预扫描移出 DEFERRED，与 [P3_ACCEPTANCE.md:70](/ACPServer/AutoCalcPlatform/reports/acp2_mcp_ux_20261029/P3_ACCEPTANCE.md:70) 对齐。

3. **DEFERRED 总体保守，但尚不完全准确**：完整绑定、attempt、SDK 生产整链、真实 QC 等仍明确未闭合；预扫描属于已实现却仍列延期的过期声明。