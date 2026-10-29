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

## 待完成（P3 剩余）

- P3-04 有界产物描述列表（manifest v2 → prepare 参数投影）。
- P3-05 描述列表进入 `V1JobRecordModel` 并以 status 暴露。
- P3-08 SDK 端到端闭环 fixture（submit → status 发现 → prepare → read）。

## 证据

- `evidence/p3/p3_batch.txt`：命令、cwd、模块路径、文件哈希、exit code。
- 批次：P3 status/artifact + confsearch progress + v1 science status + services mcp + mcp process + P1/P2 契约 → **139 passed，exit 0**。
