# P0 验收记录（基线 / C1 wire / C5 补丁）

> 执行：2026-10-29T13:35–13:55 +08:00
> 分支：`acp2/mcp-ux-remediation` @ fd2087c7（隔离 worktree）
> 状态：**待外部复验**；下列命令均已实际执行，日志在 `/ACPServer/acp_mcp_ux_20261029/evidence/`。

## P0-01 基线与隔离

- 证据目录：`/ACPServer/acp_mcp_ux_20261029/baseline/`（README.md 为索引入口）
- 源工作区：main @ fd2087c7 + **70 个已跟踪修改 + 2070 个未跟踪文件**（另有本计划 1 个）
- 隔离：`git worktree add -b acp2/mcp-ux-remediation ../worktree fd2087c7`
  - worktree HEAD = fd2087c7；6005 个文件与 HEAD 逐一核对一致；ignored 0
- 内容归档：未跟踪 2071 个（136 MB）、已修改 70 个（679 KB），逐文件哈希比对 `ARCHIVE_CONTENT_MATCH`
- 元数据：权限/属主/大小清单；无符号链接；ignored 7720 路径仅列名
- 前后一致：P0 结束后复采（`baseline/after_p0/`）与基线对比结果见 comparison；stash 0、staged 空
- 未证明：采集时点之前的历史状态（标 `NOT_VERIFIED`）

## P0-02 C1 wire 回归

- 改动：`tests/test_acp2_runtime_mcp_process.py`
  - fake Controller 记录 `GET /api/v1/jobs/*` 路径（`status_paths`）
  - 新增 `test_status_reference_object_and_stringified_json_are_distinct`
- 断言：
  - `reference="known-job"` 与 `reference={"kind":"job","id":"known-job"}` → **同一 URL**，且返回 **同一 task_id="known-job"**
  - `reference='{"kind":"job","id":"known-job"}'`（字符串）→ 解码后路径精确等于 `/api/v1/jobs/{"kind":"job","id":"known-job"}`，`resource_not_ready`，不被当对象
  - 请求总数恰为 3；`submit_posts == 0`
- 结果：`2 passed`（定向）；整模块 `20 passed in 19.07s`
- 结论：C1 的**服务端对象路径可用**。真实客户端转换链未验证（`NOT_VERIFIED`），本步**不声称根因**。

## P0-03 C5 补丁核验

- 新增测试：`tests/test_acp_mcp_ux_c5_probe.py`（两条 discovery 路径 × 配置有/无）
- 最小补丁闭包：`src/acp/api/routes.py`（`_backend_probe_config` + 两处调用）+ `src/cccp/backends/capabilities.py`（`backend_status(config=…)`）

**隔离前后对照**（同一测试、同一 cwd/PATH/解释器，仅切换补丁；运行上下文逐项落盘）：

| 运行 | 结果 | 证据 |
|---|---|---|
| 干净 HEAD | 2 failed, 2 passed；exit 1 | `evidence/p0-03/c5_head_run.txt`（cwd、命令、模块路径、包指纹、源码哈希、完整栈） |
| 仅打 C5 补丁 | 4 passed；exit 0 | `evidence/p0-03/c5_patched_run.txt`（同上） |

**活体探测**（临时的独立 `acp run serve` 进程，非生产服务、未重启生产）：

| 运行 | PATH / 配置 | `/api/backends` xtb | `/api/capabilities` xtb | 证据 |
|---|---|---|---|---|
| HEAD 代码 | `/usr/bin:/bin` + `cccp.yaml` 钉住 xtb | False（path 已解析） | False | `live_head.txt` |
| 补丁代码 | 同上 | **True** | **True** | `live_patched.txt` |
| 补丁代码 | 无配置 + 隔离 HOME | False（path 为空） | False | `live_patched_noconfig.txt` |

记录口径：HEAD/patched 两次记录 cwd、命令、`PYTHONPATH`、模块 `__file__`、源码 SHA-256、health、配置哈希、退出码；无配置负例**补记**加载源码哈希（`routes.py`/`capabilities.py`/测试文件）与 `run_root`。
受控说明：负例为排除用户配置与 PATH 扫描，隔离了 HOME 并显式补入 site-packages，**不是严格盲测**；`143=128+SIGTERM` 是主动停止，不作优雅清理证明。
结论：在 PATH 缺失条件下，配置钉住的二进制只有补丁代码能正确识别；去掉配置后两条路径都报缺失。

## 证据位置

- 基线：`/ACPServer/acp_mcp_ux_20261029/baseline/`（含 `after_p0/` 结束快照）
- 测试日志：`/ACPServer/acp_mcp_ux_20261029/evidence/{p0-02,p0-03}/`
- worktree 变更：`tests/test_acp2_runtime_mcp_process.py`、`tests/test_acp_mcp_ux_c5_probe.py`、本计划文档、本记录


## 本轮变更清单

- `docs/reference/next-change/ACP_MCP_UX_Remediation_Plan_20261029.md`（新增，计划）
- `reports/acp2_mcp_ux_20261029/P0_ACCEPTANCE.md`（新增，本记录）
- `tests/test_acp2_runtime_mcp_process.py`（修改：fake Controller 记录 status 路径 + C1 测试）
- `tests/test_acp_mcp_ux_c5_probe.py`（新增：两条 discovery 路径 × 配置有/无）

## 复现命令

```bash
cd /ACPServer/acp_mcp_ux_20261029/worktree
PYTHONPATH="$PWD/src" /usr/bin/python3.11 -m pytest tests/test_acp2_runtime_mcp_process.py -q
PYTHONPATH="$PWD/src" /usr/bin/python3.11 -m pytest tests/test_acp_mcp_ux_c5_probe.py -q
# C5 前后对照：先打补丁跑再 git checkout 还原
cp /ACPServer/AutoCalcPlatform/src/acp/api/routes.py src/acp/api/routes.py
cp /ACPServer/AutoCalcPlatform/src/cccp/backends/capabilities.py src/cccp/backends/capabilities.py
```

## 边界（不冒充 PASS）

- P0-01：历史时点的 ignored 文件与权限变化未快照（`NOT_VERIFIED`）
- P0-02：未穿过真实 SDK / 故障客户端；只证明 stdio→HTTP 路径
- P0-03：生产 systemd 服务未复验（不重启生产）；临时独立进程已在 PATH 最小 + 配置有/无条件下验证，结果见上表
