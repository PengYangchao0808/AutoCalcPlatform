# Cache-only 逐资源切换矩阵（B02-6 / plan todo 16）

**Created:** 2026-10-18，基线 HEAD `16b308b`（工作区并发：lane A 前端、lane C 文档）。
**Scope:** ACP Services 计划 `acp-parallel-plan-2-services` todo 16 —— cache-only 逐资源切换的兼容证据与切换矩阵。
**权威测试:** `tests/test_services_cache_only_switch.py`
**兼容面台账:** `tests/baseline/services-evidence/migration_ledger.md`（每资源 1 行，含调用者/期限/退出条件）。

## 1. 版本轴定义（主方案 §38.7）

| 轴 | 取值 | 本文档中的对应物 |
|---|---|---|
| F0 | 冻结的原始前端（本切片前静态文件集） | 旧客户端流程 = 既有 GET 端点，原样调用 |
| F1 | 后续前端版本（lane A §42 拆分） | `docs/reference/current/ACP_Frontend_Closure_20261017.md`（S0 语境） |
| S0 | 冻结的服务/CCCP/配置行为基线（未改动服务） | 本切片前的服务行为（旧 GET 原行为） |
| S1 | 后续服务版本（Services 切片） | 本切片：新增只读 cache-only 路径，旧 GET 保留为回退 |

**本切片只声明 F0＋S1 兼容通道（cache-only 新路径 + 旧 GET 回退）与本仓内可执行证据。**
真实浏览器 S0 联调与 F1＋S1 联合验收属 todo 69/todo 75；本仓库无法启动真实浏览器，故对应组合标 **NOT_VERIFIED**，不以 mock 通过替代。

## 2. 四组合矩阵

| 组合 | 预期能力 | 本切片证据 | 状态 |
|---|---|---|---|
| F0＋S0 | 冻结原运行/缺陷基线与行为证据 | 由 todo 1/todo 15 基线冻结记录承载（本文件不复算） | 已冻结（引用） |
| F1＋S0 | 服务不改，前端兼容 | lane A 前端收口记录 + 既有 S0 API 回归 `tests/test_acp_api_v1.py` | NOT_VERIFIED（浏览器/S0 真机，见 §5） |
| F0＋S1 | 旧 URL/字段/错误/远端读取行为保留；不要求前端升级 | 本文件 §3 每资源 F0 对拍 + `tests/test_services_cache_only_switch.py`；旧 GET 未被改写 | PASS（仓内可执行）／浏览器 NOT_VERIFIED |
| F1＋S1 | 联合联调；无 S1 时记 NOT_VERIFIED | 新只读面 + 旧 GET 回退并存；联合验收归 todo 69/todo 75 | NOT_VERIFIED（待联合切片） |

## 3. 逐资源切换矩阵

对每个资源，**同一可合入切片内**完成：兼容 adapter 就绪 → F0 对拍通过 → 启用新路径 → 保留按资源回退点。

新路径统一为只读 cache-only 面（`acp_services.data.resource_read` + 服务端 `GET /api/v1/jobs/{id}/availability?resource=<kind>` 与 `GET /api/v1/jobs/{id}/prepared/<kind>`），零 SSH/SFTP、零 enqueue、零解析写入。旧 GET 原样保留为回退（兼容通道）。

| 资源 (kind) | 兼容 adapter | 新路径（cache-only） | 回退点（旧 GET，窗口内保留） | F0 对拍证据（冷缓存→真实结果） | 回退演练 | 切换 |
|---|---|---|---|---|---|---|
| catalog | `RESOURCE_ADAPTERS["catalog"]`（availability） | `/availability?resource=catalog`、`/prepared/catalog` | `GET /jobs/{id}/structure-viewer` | `test_f0_catalog_flow_yields_real_entries`：冷缓存下 entries 非空 | `test_fallback_drill_cold_then_legacy_fetch_restores[catalog]`：冷→未准备；旧 `fetch_catalog` 后 ready | 启用 |
| manifest | `RESOURCE_ADAPTERS["manifest"]`（`RESULT/result_manifest.json`） | `/prepared/manifest` | `GET /jobs/{id}/energy-graph`（`_job_read_root`） | `test_f0_manifest_flow_yields_real_nodes`：冷缓存下 nodes 非空 | `test_fallback_drill...[manifest]` | 启用 |
| frame_candidates | `RESOURCE_ADAPTERS["frame_candidates"]`（`RESULT/frame_candidates.json`） | `/prepared/frame_candidates` | `GET /jobs/{id}/frame-candidates` | `test_f0_frame_candidates_flow_yields_real_candidates`：权威文件 revision=3、候选可见 | `test_fallback_drill...[frame_candidates]` | 启用 |
| frequency_modes | `RESOURCE_ADAPTERS["frequency_modes"]`（`RESULT/frequencies/normal_modes.json`） | `/prepared/frequency_modes` | `GET .../entries/{e}/vibrations` | `test_f0_frequency_modes_flow_yields_real_modes`：冷缓存下 available=true、3 个 mode | `test_fallback_drill...[frequency_modes]` | 启用 |
| geometry | `RESOURCE_ADAPTERS["geometry"]`（bounded Range 读） | `/prepared/geometry?relative_path=...` | `GET .../entries/{e}/geometry`（含 `?fetch=1` 懒取） | `test_f0_geometry_flow_yields_real_xyz`：冷缓存下真实 XYZ（3 原子） | `test_fallback_drill...[geometry]` | 启用 |

### 3.1 逐资源新路径断言（缓存命中）

| 资源 | 断言 | 测试 |
|---|---|---|
| catalog | 已准备 root → `availability=ready`，零 fetch | `test_serve_catalog_reports_ready_from_prepared_root` |
| manifest | 已准备字节 = 原字节，零 fetch | `test_serve_file_resource_reads_prepared_bytes[manifest]` |
| frame_candidates | 同上 | `test_serve_file_resource_reads_prepared_bytes[frame_candidates]` |
| frequency_modes | 同上 | `test_serve_file_resource_reads_prepared_bytes[frequency_modes]` |
| geometry | 同上 + 有界 Range 读 | `test_serve_file_resource_reads_prepared_bytes[geometry]`；`test_new_path_serves_prepared_geometry_cache_only` |

### 3.2 冷缓存负路径（回退必要性）

| 断言 | 测试 |
|---|---|
| 五资源冷缓存全部 `not_prepared` 且零网络 | `test_cold_cache_serve_reports_not_prepared_without_network` |
| 新路径冷缓存 409 `not_prepared`，零新增 operation | `test_new_path_cold_cache_is_409_not_prepared_with_no_side_effects` |
| 回退后（旧 `fetch_catalog`/`fetch`）新路径恢复 served | §3 回退演练 5 例 |

## 4. 负例（对拍有意义性）

| 负例 | 期望 | 测试 |
|---|---|---|
| 未做对拍直接切换（删除 `legacy_get` 回退） | F0＋S1 兼容判定 **FAIL** | `test_negative_control_switch_without_fallback_fails_f0_s1_compat` |
| 关闭某资源的新路径开关 | `serve` 返回 `disabled`、不服务 | `test_disabled_resource_reports_disabled_and_does_not_serve` |
| `resource_read.py` 出现 fetch/enqueue/网络入口 | 断言 FAIL | `test_resource_read_module_has_no_fetch_or_enqueue_entry_point` |

**"只返回相同 200/409 不算兼容通过"**：§3 F0 对拍断言的是**真实结果**（entries/nodes/候选/modes/XYZ 字节），不是状态码。回退演练断言的是"冷→未准备→旧回退→恢复 served"，非仅状态码。

## 5. 边界与未验证项（诚实声明）

- **真实浏览器 / S0 真机 / F1＋S1 联合验收：NOT_VERIFIED。** 本环境无 Playwright；四组合的浏览器执行归 todo 75（B13-7），本切片只提供仓内可执行的 F0＋S1 兼容证据。
- **F0 客户端工件**：以既有 GET 端点流程（`tests/test_acp_api_structure_viewer.py`/`test_acp_api_v1.py` 的 fixture 树）作为 F0 旧客户端流程的仓内替身，调用路径与原客户端一致；无真实旧版前端 bundle 可执行，故浏览器级等价 **NOT_VERIFIED**。
- **远端几何懒取（`?fetch=1`）**：作为已登记过渡副作用保留；本切片未删除。
- **未宣称"全量 cache-only"**：仅逐资源切换并各自保留回退点；删除旧语义不在本切片。

## 6. 期限与退出条件

- 兼容面窗口：直至四组合（F0/F1×S0/S1）矩阵齐备且旧 GET 零消费者证据登记（接收 todo 69 至 todo 75）。
- 窗口内**不得**删除旧 GET 或 `_job_read_root` 的远端同步预取副作用（其副作用=受限过渡项，见 migration_ledger）。
- 退出后删除 per-resource 兼容面台账行与旧 GET 回退（删除须在 `handoff_registry.md` 记录理由）。
