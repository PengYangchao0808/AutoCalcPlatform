# PES 人工确认（Manual Review）设计文档

**状态**: v1.0（2026-09-03）
**范围**: PESsearch 人工确认选点 → BatchOptimize 批量确认的完整链路

## 1. 目标流程

```
PESsearch 完成（COMPLETED，状态不变）
    ↓
RESULT/pes_search/pes_profile.json   ← 算法能量曲线 + 推荐点（审计保留）
    ↓
用户在 Workbench 能量图上手动增删/修改 TS、INT 选点
    ↓
POST /api/v1/jobs/{job_id}/pes/review
    ↓
写入 RESULT/pes_search/pes_review.json          ← 人工确认权威文件
复制确认结构到 RESULT/structures/*.xyz           ← 带稳定 candidate_id 的 TAG
更新 RESULT/result_manifest.json                ← 只登记当前确认的有效候选
    ↓
BatchOptimize 从 RESULT/result_manifest.json 读取最终选点
```

原则：**PESsearch 负责搜索和推荐；人工确认负责结果定稿；BatchOptimize 只读取定稿后的 RESULT**。
人工确认不混入调度器状态机 —— PES 任务保持 COMPLETED，确认状态只存在于结果文件。

## 2. 结果文件契约

### 2.1 RESULT/pes_search/pes_review.json（schema `pes_review_v1`）

```json
{
  "schema_version": "pes_review_v1",
  "job_id": "20260903_001_PESsearch",
  "status": "confirmed",
  "confirmed_at": "2026-09-03T12:00:00+08:00",
  "revision": 1,
  "profile_sha256": "<pes_profile.json 摘要，审计用>",
  "note": "Stepwise",
  "selected": [
    {
      "candidate_id": "pes_ts_frame_027",
      "frame_index": 27,
      "role": "TS",
      "name": "frame_027__TAG_TS",
      "selection_source": "manual",
      "structure_path": "structures/pes_ts_frame_027.xyz"
    }
  ]
}
```

- `candidate_id` 由服务端从 `role + frame_index` 确定性生成（`pes_ts_frame_027` /
  `pes_int_frame_036`），前端显示名可变，ID 恒定；重复保存幂等，不产生重复候选。
- `revision` 单调递增；请求可带 `expected_revision` 做并发覆盖守卫（不匹配 → 409）。

### 2.2 RESULT/structures/*.xyz

每个确认结构物化为独立 XYZ，第二行 TAG 注释：

```
TAG: TS | candidate_id=pes_ts_frame_027 | source=PESsearch | frame=027 | selection_source=manual
```

坐标体从 scan frame（`WORK/07_PATH/pes_scan_001/scan_frames/frame_NNN.xyz`）原样复制。

### 2.3 RESULT/result_manifest.json

人工确认后，`kind: "structure"` 的 PES 候选条目被整体替换为当前确认集（旧候选
文件保留在磁盘，仅从有效 manifest 移除引用）。条目携带 metadata：

```json
{
  "id": "pes_candidate_pes_ts_frame_027",
  "label": "PESsearch TS candidate pes_ts_frame_027 (manual)",
  "path": "structures/pes_ts_frame_027.xyz",
  "kind": "structure",
  "metadata": {
    "candidate_id": "pes_ts_frame_027",
    "role": "TS",
    "frame_index": 27,
    "source": "PESsearch",
    "selection_source": "manual"
  }
}
```

`Product.metadata` 为本次新增的可选字段（空时不序列化，旧 manifest 完全兼容）。
算法推荐点仍完整保留在 `pes_profile.json` 用于审计，未被确认的点不再进入
BatchOptimize 默认输入。

## 3. 服务与 API

### 3.1 服务层

`src/acp/calculations/pes/review.py`（唯一写入方，all-or-nothing）：

- `save_pes_review(task_root, job_id=..., candidates=..., note=..., expected_revision=...)`
  校验全部选点（frame 越界 / geometry 缺失 / 角色非法 / 路径逃逸任务目录 /
  candidate_id 冲突）通过后才统一写入 structures → review → manifest。
- `load_pes_review(task_root)` 读取（缺失/损坏返回 `None`）。
- 异常：`PesReviewError`（校验失败）、`RevisionConflictError`（并发覆盖）。

### 3.2 REST API（v1）

| 端点 | 说明 |
|------|------|
| `POST /api/v1/jobs/{job_id}/pes/review` | 确认选点；body `{note?, expected_revision?, candidates:[{frame_index, role, candidate_id?, name?}]}` |
| `GET /api/v1/jobs/{job_id}/pes/review` | 当前确认状态（未保存时 `{"status": "pending"}`） |

前置校验：job 必须是 PESsearch（400）；必须存在 canonical pes_profile（404）；
POST 要求 COMPLETED（409）；**历史 mechanism 任务（legacy s2 manifest）返回 410 保持只读**。
旧 `POST /jobs/{id}/s2/review` 保留为 410 兼容接口，新前端不再调用。

`GET /jobs/{id}/energy-graph` 会把 `pes_review.json` 投影进
`metadata.review`（status/decided_at/revision）与 annotations（`saved: true`、
`selection_source: "manual"`、稳定 candidate_id），页面重开即恢复已确认状态。

## 4. 前端（ACP_Workbench_v2.html）

- 工具栏：`确认 PES 选点`（原"保存候选"）+ `确认并进入 BatchOptimize`。
- 保存走 `POST /pes/review`，携带 `expected_revision`；409 时提示冲突。
- 确认对话框区分：**算法推荐点**（selection_source=algorithm）、**当前工作区选点**
  （未保存）、**已保存最终选点**（candidate_id 徽标）；手动新增点显示
  "保存后生成候选 ID"，保存后用服务端返回的稳定 ID 刷新界面。
- `确认并进入 BatchOptimize`：先保存，再从
  `/jobs/{id}/files/RESULT/structures/<candidate_id>.xyz` 拉取结构文本，
  预填 Batch 表单（batchPreviewItems，来源标记 `pes_candidate`）并打开任务创建
  弹窗 —— **只预填，不自动提交**，保留用户最后确认机会。
- 历史 mechanism 任务继续显示只读提示，不受影响。

## 5. BatchOptimize 对接

- **manifest 输入**：`load_items_from_result_manifest()` 直接读取 metadata 中的
  `candidate_id` / `role`（TAG 注释作为回退），人工确认候选即最终输入。
- **CLI**：`acp run BatchOptimize --from-job <PES_JOB_ID>`（本次补齐）等价于
  `--from-artifact <PES_JOB_DIR>/RESULT/result_manifest.json`；支持
  `--select pes_ts_frame_027,pes_int_frame_036 --profile opt_freq_sp_thermo`。
- **batch_structures 物化**（runner 层，§6 关键修复）：
  1. API `create_job` 对 BatchOptimize 的 `source_type: "batch_structures"`
     payload 把条目里的 `source_id`（`job_<id>:<rel_path>`）经
     StructureSourceService 内联为 `xyz` 文本（本地与远程源任务均支持）；
  2. runner `materialize_job_input` 在启动子进程前把 payload 物化为
     `inputs/batch_items.json`（schema `batch_structures_v1`，保留
     name/tag/candidate_id/charge/multiplicity/include）；
  3. `_build_cmd` 走既有 `--items-file` 分支。
  由此 PES 确认候选、手动上传 XYZ、其他任务结果、混合来源的批量任务
  都不会再在 runner 阶段因输入格式失败。
- loaders 的 batch request 条目现支持条目级 `charge` / `multiplicity` / `include`。

## 6. 迁移与兼容说明

- 历史 mechanism 任务：`RESULT/mechanism/s2_path_manifest.json` 只读兼容不变，
  PES review 端点对其返回 410；不执行任何写入。
- 旧 PES 任务（pes_profile_v2，无 pes_review.json）：GET 返回 pending，保存即建立确认状态。
- `result_manifest.json` 旧格式（products 无 metadata）：读入不受影响；
  一旦人工确认写入，manifest 中 PES 结构条目会携带 metadata 并按确认集整体替换。
- 升级部署后建议 `sudo systemctl restart acp`（服务非 --reload 模式）。

## 7. 测试

| 文件 | 覆盖 |
|------|------|
| `tests/test_acp_pes_review.py` | 服务层：幂等、revision 冲突、越界/角色/路径逃逸拒绝、manifest 替换、batch loader 集成、Product.metadata 往返 |
| `tests/test_acp_api_pes_review.py` | GET/POST 端点、400/409/410/422、energy-graph 投影 |
| `tests/test_acp_batch_materializer.py` | batch_structures 物化往返、`--from-job` 解析与 CLI 解析、API source_id 内联 |
