基准：`main@9eac208e`；已只读核对 `/tmp/astra_review.md` 与源码，未改文件、未跑测试。以下 P0/P1/P2 是修复优先级；执行顺序为 **PR-A → PR-B → sol 独立复验**，本轮不作整体签收。

1. **P0／PR-A：堵住编码绕过，恢复公开报告可读性（发现①）。**
   涉及：[mcp.py:2874](/ACPServer/AutoCalcPlatform/src/acp_services/entrypoints/mcp.py:2874)、[artifacts.py:1035](/ACPServer/AutoCalcPlatform/src/acp_services/data/artifacts.py:1035)、[report.py:99](/ACPServer/AutoCalcPlatform/src/acp/confsearch/report.py:99)。
   改法：抽出共享纯内容策略，在 `resolve_artifact_read` 对完整原始正文检查后才截断、编码；MCP 严格解码 base64 后复核，非法编码结构化拒绝；无法安全检查的二进制本轮拒绝并记录兼容性 residual。不得修改正文后沿用旧 digest。
   报告生成时将 `executed.cwd/argv` 改为白名单字段及受控相对引用，检查 `weight_table.reference` 越界；原始执行事实留内部，公开投影生成后再冻结摘要。
   测试：修改 `test_acp_mcp_ux_p3_artifact.py`、`test_acp_confsearch_report.py`；同一敏感正文 identity/base64 均拒绝，截断前后均不绕过，坏 base64/非 UTF-8 拒绝；安全报告经实际 prepare/read 后字节及摘要一致。
   风险/回滚：部分旧报告、二进制暂不可读；可撤回公开投影变更，必须保留内容拒绝门，禁止恢复 base64 豁免。

2. **P0／PR-A：把 P4 verified 改成可核验的本次执行事实（发现②）。**
   涉及：[report.py:166](/ACPServer/AutoCalcPlatform/src/acp/confsearch/report.py:166)、[report.py:247](/ACPServer/AutoCalcPlatform/src/acp/confsearch/report.py:247)、[censo.py:829](/ACPServer/AutoCalcPlatform/src/cccp/qc/interfaces/censo.py:829)、[censo.py:928](/ACPServer/AutoCalcPlatform/src/cccp/qc/interfaces/censo.py:928)。
   改法：CENSO 每次调用使用受控空工作区，禁止复用旧 part 输出；执行前冻结输入、实际 rcfile/template，执行后记录输出摘要和成员。`_execution_verified` 接收可信根，重算文件摘要并校验配置、part、成员关联；缺失、畸形或不匹配均 `unverified`，附原因。
   `_role_evidence` 要求非空成员表且构象确实存在；方法身份只能来自核验过的实际配置，不能用 requested 补成 executed；旧回执不追认。
   测试：修改 `test_acp_confsearch_report.py:448`、`test_acp_mcp_ux_p4_chain.py:65`，扩充 `test_acp_censo_p5_acceptance.py`；用真实夹具文件计算摘要，覆盖手填摘要、空/缺名单、错成员、同 part 陈旧输出、配置/输出篡改及非零退出。
   风险/回滚：旧结果降级、复用行为改变；可回退执行目录改造，但继续关闭 verified，不能退回形状检查放行。

3. **P1／PR-B：status 零远端副作用，并拒绝旧 attempt 工件（发现③）。**
   涉及：[v1_routes.py:5072](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:5072)、[v1_routes.py:5088](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:5088)、[artifact_descriptors.py:49](/ACPServer/AutoCalcPlatform/src/acp/api/artifact_descriptors.py:49)、[services_data_bootstrap.py:2329](/ACPServer/AutoCalcPlatform/src/acp/services_data_bootstrap.py:2329)。
   改法：仅替换 status/summary 的 `_job_read_root` 调用，复用 cache-only reader；描述符消费已校验身份的 manifest。manifest 无 attempt 字段，须核对既有提交回执/缓存 sidecar 与 fence；无法证明归属即返回空列表和机器可读 unknown 原因，禁止拿当前 attempt 补标签。
   测试：扩充 `test_acp_mcp_ux_p3_status.py`、`test_acp_mcp_ux_p3_descriptors.py`；真实 HTTP 两端点覆盖冷/热缓存、远端终态、本地 rerun、缺身份、旧 attempt、读取中 attempt 切换；断言 fetch/enqueue/write 均为零，当前合法缓存正例通过。
   风险/回滚：历史任务描述符减少；回滚为不提供描述符，保留基础状态，不能恢复隐式 fetch。

4. **P1／PR-B：拒绝被吞参数，撤销过度 coverage 声明（发现④）。**
   涉及：[submission_contract.py:328](/ACPServer/AutoCalcPlatform/src/acp/api/submission_contract.py:328)、[jobs.py:873](/ACPServer/AutoCalcPlatform/src/acp/scheduler/jobs.py:873)、[options.py:324](/ACPServer/AutoCalcPlatform/src/acp/calculations/batch/options.py:324)、[workflow_contracts.py:173](/ACPServer/AutoCalcPlatform/src/acp/api/workflow_contracts.py:173)及同文件 `:221`。
   改法：复用 `BatchMethodOptions`/effective-config 消费者解析，建立七个镜像字段到有效角色值的明确映射；等价允许、冲突返回带字段路径的 `shadowed_field`，未知角色/角色内字段拒绝，不复制默认值和 fallback 算法。
   两处 `coverage=complete` 暂降为 `partial` 并列 gaps，同步 schema/发现响应；保留 `source_job_id/schema_version`、HTTP 省略 input 等兼容路径。
   测试：修改 `test_acp_mcp_ux_p1_contract.py`、`test_acp_mcp_ux_p2_discovery.py`；七字段参数化覆盖相等/冲突、298.15 对 400、默认值与 TS fallback、未知嵌套键；preflight/submit 错误一致，输入不被修改。
   风险/回滚：旧客户端冲突请求变成显式失败；只可调整兼容映射，不能重新吞值，coverage 保持 partial。

5. **P2／PR-B：交付声明纠偏；SDK 生产整链、真实 QC 标 DEFERRED（发现⑤）。**
   涉及：[test_acp2_runtime_mcp_process.py:994](/ACPServer/AutoCalcPlatform/tests/test_acp2_runtime_mcp_process.py:994)、[test_acp2_mcp_sdk_contract.py:249](/ACPServer/AutoCalcPlatform/tests/test_acp2_mcp_sdk_contract.py:249)、[P3_ACCEPTANCE.md:4](/ACPServer/AutoCalcPlatform/reports/acp2_mcp_ux_20261029/P3_ACCEPTANCE.md:4)及 `:60`。
   本轮改报告：明确 scripted Controller 仅证明协议/适配器，SDK selector 仅证明发现/协商；总状态“部分完成”，生产整链与真实 QC 分别保留 `NOT_VERIFIED`，不能以局部 PASS 覆盖。
   延期解锁测试：真实 SDK→生产 Controller→fixture 科学派发→真实 manifest→prepare/store→read；读取实际报告，覆盖篡改、旧 attempt/generation、未 prepare 拒绝，禁止脚本伪造完成状态/正文；真实 QC 另需专属授权、环境与回执。
   验收：本轮复跑原协议测试并由 sol 核对声明边界；未来整链使用 `pytest -q tests/test_acp2_mcp_sdk_contract.py -m contract`，沿用现有 JUnit 空选择/skip 门。风险是延期被误签收；回滚实现不撤销 residual。

6. **P2／PR-B：防止 reports 再污染；历史重写 DEFERRED（发现⑥）。**
   涉及：[.gitignore:99](/ACPServer/AutoCalcPlatform/.gitignore:99)、[ci.yml:92](/ACPServer/AutoCalcPlatform/.github/workflows/ci.yml:92)；新增 `tests/test_report_residue_guard.py:1`、本次修复 residual 文档。
   改法：以 `git diff --name-status -z 9eac208e HEAD` 检查新增/修改文件，并检查已删除残留前缀不得重入；拒绝 WAL/SHM、运行缓存、QC 临时产物、源码副本。合法新增证据需精确路径、用途、摘要登记；不追删既有合法证据，不以 `.gitignore` 代替门禁。
   测试：残留重入、改名、`git add -f`、新目录复制源码均失败；登记的最小脱敏证据通过。验收命令见 PR-B；sol 另核对增量跟踪清单。
   residual 写明“当前树已删除；历史 blob 仍可达；敏感性未验证”。历史重写须另行协调远端引用、PR、克隆和备份；风险是误拒合法证据，回滚仅修精确规则，不关闭检查。

两 PR 均不得改 f4 baseline、扩大目录豁免、弱化隔离或把 `NOT_VERIFIED` 改成 PASS。[f4:1949](/ACPServer/AutoCalcPlatform/tests/test_f4_scope_audit.py:1949) 的 Amendment X 如需扩展，仅授权指定证据 helper/调用点；teeth 必须包含删除校验调用、绕过摘要比较、采用旧输出、加入无关算法修改均失败的负向变异测试。

**PR-A 验收命令：**
`python -m pytest -q tests/test_acp_mcp_ux_p3_artifact.py tests/test_acp_confsearch_report.py tests/test_acp_mcp_ux_p4_chain.py tests/test_acp_censo_p5_acceptance.py -m "not slow and not contract"`

**PR-B 验收命令：**
`python -m pytest -q tests/test_acp_mcp_ux_p*.py tests/test_acp2_runtime_mcp_process.py tests/test_report_residue_guard.py -m "not slow and not contract"`

**每个 PR 必跑公共门：**
`python -m pytest -q tests/test_f4_scope_audit.py tests/test_acp_services_isolation.py tests/test_cccp_isolation.py tests/test_architecture_invariants.py tests/test_services_doc_sync.py tests/test_services_closure_record.py`
`python scripts/check_grep_gates.py`
最终补跑 `python -m pytest tests/ -q -m "not slow and not contract"`；失败逐项记录，禁止改 marker/skip 换绿。sol 在各 PR 精确 HEAD 复验上述负例、护栏和 residual；回执记录 SHA、命令、计数、skip 原因，仅提交最小脱敏证据。