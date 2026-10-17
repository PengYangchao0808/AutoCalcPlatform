# ACP API/网页响应审计复现材料

主文档：[缺陷复测 issue](../../../history/ACP_API_Web_Response_Issue_Audit_20261012.md)。

基线 `0e3c97839ea6ee20c5ca89d5d11f8fbf90075932`，日期2026-10-12。此目录属于审计材料，不接入默认CI；若修复了对应问题，描述当前缺陷的探针预期失败，应更新审计版本，并将正确行为放入正式回归测试。

## 文件

| 文件 | 用途 |
| --- | --- |
| `verify_backend.py` | 17项后端探针：SSE读量/线程阻塞、结构目录重复构建、详情XYZ读取、日志扫描、200MiB过滤、远端缓存、SSH/SFTP预算、资源体积、本地/远程Range、线程额度、预取与索引 |
| `verify_frontend.py` | 20项浏览器探针（19项功能/故障路径＋1项环境对照）：真实Workbench/JS，HTTP由Playwright拦截，无生产API或SSH |
| `backend_evidence.json` | 运行时读取数量/字节、资源字节、接口行为；故障注入延迟明确标记 |
| `frontend_evidence.json` | 界面文字、DOM复用、请求预算/缺失deadline、SSE事件监听与环境对照 |
| `test_results.json` | 既有与新增测试运行的结果、耗时、失败原因分类 |

## 复跑

在仓库根目录，使用已安装ACP及dev/api/remote依赖的Python 3.11环境；浏览器探针另需Playwright和Chromium。命令会更新此目录对应的evidence JSON。

```bash
pytest -m 'not slow' -q docs/archive/2026-10-16/evidence/reports/ACP_API_Response_Audit_20261012/verify_backend.py --disable-warnings --junitxml=/tmp/acp-api-response-probes.xml
pytest -m slow -q docs/archive/2026-10-16/evidence/reports/ACP_API_Response_Audit_20261012/verify_frontend.py --disable-warnings --junitxml=/tmp/acp-api-response-frontend-probes.xml
```

上述命令单独收集docs中的探针，不加载tests目录的慢测门控；如果与tests一起收集，需要`--run-slow`。Chromium或TestClient被执行沙箱阻止时，应使用正常授权的测试环境，不将无法启动记作功能通过。

既有测试命令：

```bash
pytest -m 'not slow' -q tests/test_api_sse_backlog.py tests/test_acp_api_v1.py tests/test_acp_api_structure_viewer.py tests/test_acp_remote_catalog_prefetch.py tests/test_acp_api_v2_structure_sources.py tests/test_acp_structure_source_indexer.py tests/test_frontend_sync.py tests/test_remote_phase1.py --disable-warnings --junitxml=/tmp/acp-api-response-existing.xml
pytest -m 'not slow' -q tests/test_acp_execution_target.py::test_remote_poll_transport_failure_keeps_status tests/test_job_state_transitions.py::test_stale_terminal_cas_registers_zero_side_effects tests/test_job_state_transitions.py::test_terminal_side_effects_retry --disable-warnings --junitxml=/tmp/acp-api-response-state-tests.xml
pytest -m slow --run-slow -q tests/test_frontend_startup_browser.py --disable-warnings --junitxml=/tmp/acp-api-response-browser-existing.xml
```

## 结果解释与限制

既有非慢测707项、状态专项3项、后端探针17项通过。浏览器探针20项执行通过，其中环境对照仅记录标准鼠标点击在当前headless环境能否通过稳定性等待；不把记录到的失败解释为ACP缺陷。其余探针的通过表示**复现了断言中的现象**，包括缺陷和反证。

原首屏浏览器套件1项通过、4项因`Locator.click`等待可见/稳定超时失败。空白页面上的普通按钮也出现相同等待；独立对照的rAF在100ms内未触发，而直接派发点击事件可正常执行处理器。Workbench按钮DOM的尺寸及可见性正常；目录API永久pending时，新建项目处理器仍可打开弹窗。因此没有将这4个失败算入平台缺陷，也没有将原套件报成全绿。当前条件不足以验证原生鼠标交互流畅度、帧率及绘制耗时。

后端压测材料是小型合成数据与故障注入，不是生产性能压测：最终SSE的约20/126ms是单次热缓存记录，复跑会变化；80ms阻塞是两次40ms注入；HTTP自身超时探针将预算缩短至40ms以验证错误转换，实际默认仍为8000/60000ms。真实节点延迟、断连恢复、并发吞吐、首个任务完整显示和大体系帧率没有实测。

文件、数据库与日志均使用临时目录；没有真实QC、bsub/bstop/bresume、远端文件修改或生产任务操作。只读核对本机systemd unit文件用于说明部署入口，未改配置或重启服务。
