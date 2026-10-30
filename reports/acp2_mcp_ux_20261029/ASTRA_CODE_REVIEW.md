**目标完成度：部分完成，不予整体签收。** 合并已存在，但 P1/P3/P4 仍有实质缺口，sol PASS 与 CI 全绿不能替代缺失的验收。

审计基准为 `208194c1`；实际工作区已在清理分支 `b95ff9ed`，两者源码及测试相同。全程只读、未跑测试。

1. **高｜base64 成为脱敏绕过开关。** [mcp.py:2904](/ACPServer/AutoCalcPlatform/src/acp_services/entrypoints/mcp.py:2904) 跳过 base64 内容检查，随后原样序列化；调用方可以主动选择该编码。同一含主机路径的正文，identity 被拒，base64 却可取回。P4 又把绝对 `cwd/argv` 写进报告，默认读取因此受阻。  
   **最小修复：** 在编码前统一执行内容策略；公开报告使用受控相对引用，不能用编码决定是否脱敏。

2. **高｜P4 仍可把不充分证据标成 verified，测试固化了漏拒。** [report.py:166](/ACPServer/AutoCalcPlatform/src/acp/confsearch/report.py:166) 仅检查摘要形状；[report.py:261](/ACPServer/AutoCalcPlatform/src/acp/confsearch/report.py:261) 在构象名单缺失或为空时直接放行。[测试:460](/ACPServer/AutoCalcPlatform/tests/test_acp_confsearch_report.py:460) 仍手填 `"a"*64`、`"b"*64` 并断言 verified/executed，与验收记录“已整改”冲突。  
   [censo.py:881](/ACPServer/AutoCalcPlatform/src/cccp/qc/interfaces/censo.py:881) 也只限制 part 名称，未排除同一 part 的旧产物；execution 未冻结 rcfile/template，无法完整核验方法身份。  
   **最小修复：** 强制构象成员匹配，将输出及配置摘要绑定本次执行；用实际夹具文件计算摘要，补缺名单、同 part 陈旧输出和配置篡改拒绝用例。

3. **高｜status 新增了远端读取副作用，attempt 只是贴标签。** [v1_routes.py:5073](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:5073) 调用 `_job_read_root`，其 [687 行](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:687) 对终态远端任务直接 `fetch_catalog`，违反计划的冷缓存零 fetch 要求。[artifact_descriptors.py:97](/ACPServer/AutoCalcPlatform/src/acp/api/artifact_descriptors.py:97) 未验证 manifest 所属 attempt。  
   **最小修复：** 改用 cache-only、校验 attempt 的读根；冷缓存返回未知/空列表，补真实 HTTP status 的零 fetch 和旧 attempt 拒绝断言。

4. **高｜P1 明知参数被吞仍放行，P2 却宣称 complete。** [submission_contract.py:330](/ACPServer/AutoCalcPlatform/src/acp/api/submission_contract.py:330) 只检查角色值是对象，并豁免七个顶层镜像；例如角色温度 298.15、顶层 `temperature=400` 可通过，随后 [jobs.py:873](/ACPServer/AutoCalcPlatform/src/acp/scheduler/jobs.py:873) 提前返回，400 被静默丢弃。  
   **最小修复：** 复用消费者解析后的有效值检查冲突，至少明确返回告警；未补齐前撤销 `coverage=complete`。Batch 的 `source_job_id/schema_version` 和 HTTP 省略 input 已保留，不能拿兼容性作为吞值免责理由。

5. **中｜必需的 SDK 整链验收没有交付。** [闭环测试:994](/ACPServer/AutoCalcPlatform/tests/test_acp2_runtime_mcp_process.py:994) 使用 scripted Controller；完成状态、描述符及拒绝响应由 fixture 编排，正文仅为 `hello chain`，无法验证生产 manifest→prepare→store 链。[P3 记录:60](/ACPServer/AutoCalcPlatform/reports/acp2_mcp_ux_20261029/P3_ACCEPTANCE.md:60) 明确承认真实 SDK 整链未交付。  
   **最小修复：** 用真实 SDK、生产 Controller/Data 服务和 fixture 科学派发贯通整链，读取实际报告；不得用 selector 测试或协议转发测试替代。

6. **中｜reports 残留入库是交付违约，删除提交不能抹去历史。** `208194c1` 跟踪了该残留目录 **2,028 个文件**，包含数据库 WAL、QC 临时文件及源码副本；定位入口为 [README.md:1](/ACPServer/AutoCalcPlatform/reports/acp2_dev_batch_20261029/README.md:1)。`b95ff9ed` 只清理后续树，原 blob 仍由历史提交可达。  
   **最小修复：** 合入清理、增加残留防入库检查，并明确记录历史污染；若要求历史也无残留，须另行协调历史重写。是否含敏感数据、远端 PR #40 最新状态均**未验证**。

本轮未独立核验远端 CI、原有 70 项修改的逐项完整性或真实 QC；以上不将它们判作失败，也不据此宣称完整交付。