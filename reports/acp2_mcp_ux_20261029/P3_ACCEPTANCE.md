# P3 验收记录（闭环：计数 / status / artifact）

> 执行：2026-10-29T17:58–18:05 +08:00
> 状态：**部分完成，待外部验收**

## 已完成

| 项 | 变更 | 验收 |
|---|---|---|
| P3-01 构象计数 | `engine.py`：`conformers_kept=len(entries)`，新增 `conformers_selected=len(selected)`；i18n 中英新增 `live.conformers_selected` | `test_acp_confsearch_progress.py` 3 passed；screen 下 kept=2/selected=0 |
| P3-02 status 投影 | workflow 从 `spec.workflow` 回退；透传 `current_stage/progress/progress_state/stage_*/live_status/latest_event/snapshot_version` | `test_acp_mcp_ux_p3_status.py::test_progress_fields_pass_through` |
| P3-03 operation 引用 | `project_operation_status` 保留 `artifact_ref`（含 digest/generation）与同名字段 | `test_operation_status_keeps_artifact_ref` |
| P3-06 encoding 标签 | `ArtifactRead.to_dict`：非 UTF-8 / 截断多字节 → `encoding="base64"`，不再谎报 identity | 两个 to_dict 用例 |
| P3-07 摘要作用域 | `_tool_read_artifact` 前置校验：脱敏改变正文 → `artifact_body_redacted`；未截断且 hash 不匹配 → `artifact_digest_mismatch`；截断切片豁免 | 5 个 `_verify_artifact_body` 用例 |

## P3-04/05 产物发现

- 新增 `src/acp/api/artifact_descriptors.py`：从 `RESULT/result_manifest.json` 投影
  `resource_id / relative_path(RESULT/…) / kind / label / size_bytes=None / prepared=False`；
  拒绝绝对路径与 `..`；上限 50，超出 `artifacts_truncated=true`。
- `V1JobRecordModel` 增加 `artifacts` / `artifacts_truncated`；`get_job`、`get_job_summary`
  通过 `_with_artifact_descriptors` 附加，空清单不改变原响应。
- 描述符不是 `ArtifactRef`：prepare 仍是产生可读引用的唯一步骤。

## P3-08 端到端闭环

- 已有单元/HTTP 断言覆盖 status 发现、operation artifact_ref、read 的编码与摘要校验。
- **未完成**：真实 SDK 的 submit → status → prepare → read 全链 fixture 仍为 `NOT_VERIFIED`
  （需要可执行任务 + prepare 服务装配）；不得用现有分段测试冒充整链 PASS。

## 证据

- `evidence/p3/p3_batch.txt`：命令、cwd、模块路径、文件哈希、exit code。
- 批次：P3 status/artifact/descriptors + confsearch progress + v1 science status + services mcp + mcp process + P1/P2 契约 + api → **159 passed，exit 0**。
