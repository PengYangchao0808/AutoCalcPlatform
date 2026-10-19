# ACP Services 结构化日志与诊断导出契约 — B08-6（todo 47）

**Status:** additive 观测契约；不改动结算/恢复/发布任何权威路径。
**Created:** 2026-10-19。
**Owner:** B 路（`src/acp_services/**`）。Consumers: 运维证据（todo 69）、质量收口（todo 66）、机器接口（todo 42/43）。
**Module:** `src/acp_services/entrypoints/diagnostics.py`（stdlib + 同包 `machine_output`；**不得** import `acp`）。
**Checker:** `tests/test_services_logging.py`。
**Authority:** 无 —— 日志/监控/trace **永不**成为结算、恢复或计量的权威；权威仍在持久存储/CAS。

---

## 1. 结构化日志（JSON 行）

一行日志 = 一个 JSON 对象，顶层键集固定为 `LOG_RECORD_KEYS`（缺值以 `null` 占位，
消费者按形状而非键存在性分支）：

| 字段 | 类型 | 语义 |
|---|---|---|
| `schema_version` | int | 日志线协议版本（当前 1） |
| `time` | str(ISO-8601 UTC) | 记录时间 |
| `level` | str | 日志级别名 |
| `logger` | str | logger 名 |
| `message` | str | **已脱敏**人类消息 |
| `job_id` | str\|null | 业务关联（持久） |
| `attempt` | int\|null | 业务关联（重试递增） |
| `operation` | str\|null | 业务关联（工作流/入口名） |
| `error_code` | str\|null | 稳定错误码（**不翻译**；取自 `machine_output.ErrorCode`） |
| `trace_id` | str\|null | 诊断 trace（32 位小写 hex，脱敏网保留） |
| `span_id` | str\|null | 诊断 span（16 位小写 hex） |

- 业务关联优先取 `extra={"business": BusinessContext}`（或扁平 `job_id`/`attempt`/`operation`）。
- trace 取 `extra={"trace": TraceContext}`（或扁平 `trace_id`/`span_id`）。
- 附加结构化字段经 `extra={"structured": {...}}`，字符串值同样脱敏。
- `exc_info` 的 traceback 以 `exception` 字段输出，**整体脱敏**。
- 默认输出仍为旧文本格式；结构化仅在显式 opt-in 时启用（见 §5）。

## 2. 默认脱敏（无泄露）

复用 `machine_output` 的**唯一**脱敏实现（不复制常量/正则）：

- 绝对/`~` 主机路径 → `<redacted-path>`；
- `key=secret`/`key: secret`（`password`/`token`/`api_key`/…）→ `<redacted>`；
- `Bearer <token>`、URL query 秘密参数、URL userinfo、PEM 私钥块 → `<redacted>`；
- 32+ 位 base64 类不透明串 → `<redacted>`；**32+ 位小写 hex 摘要保留**（科学/trace 身份）；
- 映射**键**保留（我方结构化参数名），仅字符串**值**脱敏。

诊断包额外一层：任何键名等于秘密词表（`password`/`token`/`api_key`/`authorization`/…）的值直接置 `<redacted>`，与值文本形态无关。

## 3. 业务关联（持久） vs trace/span（诊断）

- `BusinessContext(job_id, attempt, operation)`：**持久**。重试复用同一 `job_id`/`operation`，仅 `attempt` 递增（`with_attempt`）。它只是观测句柄，不驱动任何状态机。
- `TraceContext(trace_id, span_id, operation, linked_operation, retry_of_trace)`：**诊断专用**。重试经 `new_trace(..., retry_of=prev)` 开**新的** trace（新 `trace_id`），并在 `retry_of_trace` 记录被替代 trace、在 `linked_operation` 保留既有操作身份。trace 永不改写业务状态。

## 4. i18n 不变性（枚举 token 不翻译）

- `canonical_request` 只丢弃 locale 选择器与人类文本字段（`locale`/`language`/`note`/`message`/`label`/`title`/…），**保留全部科学字段与枚举 token**（`method`/`basis`/`workflow`/`charge`/`multiplicity`/`steps`）。
- `request_hash` = `"v2:" + sha256(canonical_json)[:32]`；`request_plan` 为 `canonical_request` 的纯函数。
- 因此**同一请求 zh/en 产出相同 hash 与相同计划**；中英切换不改变科学结果。
- `localize_error(code, params, locale)` 只翻译人类 `message`；`code` 与结构化 `params` 跨语言恒等。未知 locale 回退 `en`。

## 5. OTel 可选适配（默认关，不常驻）

- `OtelAdapter`（Protocol）+ 默认 `NoOpOtelAdapter`。默认 `otel_enabled() is False`、`get_otel_adapter()` 为 NoOp。
- `enable_otel(adapter)` 为**显式** opt-in（要求 `adapter.enabled=True`）；`disable_otel()` 复位。本模块**不 import OTel SDK、不启线程/HTTP 服务**。
- `ACP_OTEL_ENABLED` / `ACP_OTEL_ENDPOINT` 仅用于 `otel_settings()` 报告，**不自动激活**。

## 6. 诊断导出包

`build_diagnostic_bundle(...)` → 脱敏 dict：

| 字段 | 语义 |
|---|---|
| `schema_version` | 包协议版本（1） |
| `kind` | `"acp_services_diagnostics"` |
| `generated_at` | ISO-8601 UTC |
| `otel` | `otel_settings()`（默认 `enabled=false`） |
| `logs` | 日志条目（JSON 字符串被解析为对象） |
| `capabilities` | 系统能力；QC 二进制仅布尔可用性，**不含可执行文件路径** |
| `recent_operations` | 最近操作记录 |

`diagnostic_bundle_json` 序列化（`ensure_ascii=False`）；`export_diagnostic_bundle` 经
`os.replace` **原子**写盘。整包（含 capabilities/operations）经脱敏网 + 秘密键剥离。

## 7. 接入点（additive）

- CLI：`src/acp/cli.py::setup_logging` 新增关键字 `structured`（默认由 `ACP_LOG_FORMAT=json` 决定）；未启用时旧 `basicConfig` 行为**逐字节不变**；启用时 JSON 写 **stderr**（保持 stdout 机器信封干净）。
- API：`src/acp/api/server.py::_maybe_configure_structured_logging`（`create_app` 顶部调用）；`ACP_LOG_FORMAT` 未设时**不触碰任何 logger**（返回 `False`）。

## 8. 明确不做（Must NOT）

- 不把日志/监控/trace 作为结算、计量或恢复的权威。
- 不泄露秘密/主机路径/URL 参数。
- 不翻译科学标识符（方法/基组/工作流/错误码/参数）。
- 不为 OTel 启动常驻服务或引入硬依赖。

<!-- SERVICES_STATUS_SYNC:todo70 -->
## 实施状态同步（2026-10-19 / todo 70）

- **delivered:** `src/acp_services/entrypoints/diagnostics.py` + `machine_output.py` 的结构化日志/诊断导出契约与脱敏规则。
- **verified:** 模块级 `tests/test_services_mcp.py` / `test_services_machine_output.py` / `test_services_logging.py`（closure L1-09，82 passed）。
- **NOT_VERIFIED:** 默认入口绑定与生产观测组件接入（closure L2-14）；OTel 为可选项，未启用。
- **limits:** 日志/监控/trace **永不**成为结算、恢复或计量权威；本契约当前只证明模块正确，不证明生产遥测已上线。
- **authority:** 三态/三层以 `docs/reference/current/ACP_Services_Closure_20261019.md` 为准（L1-09 PASS；L2 默认入口 `NOT_VERIFIED`）。
