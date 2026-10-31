# Model budget proxy and the one-real-call evidence gate (T-13 / C09)

Status: implemented and self-tested by T-13a. **T-13b is NOT_VERIFIED in this
batch** because neither an approved model provider credential nor B's isolated
Controller is available (see §8). This document is C-owned.

## 1. Release boundary (read this first)

`scripts/acceptance/acp2_experience_budget_proxy.py` is an **acceptance
harness**, not a shipped RPH component. The RPH bundle does not include it and
the product makes **no claim of equivalent pre-request budget enforcement**.
If a future release ships a budget proxy, this statement must be replaced by a
release-scoped one; until then, a deployment that routes DSH straight at a
provider has only the provider's own quota controls.

The proxy is model-provider only. It never talks to the ACP Controller, never
registers an MCP transport, and never starts a scientific process.

## 2. Wire protocol

OpenAI-compatible Chat Completions, stdlib only:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/chat/completions` (also `/chat/completions`) | budget-checked model call |
| `GET` | `/ready` | `200 {"ready": true, "batch_id": ...}` once bound |
| `GET` | `/health` | batch/scenario identity, caps, usage, remaining, refusal counts, last events |
| `GET` | `/v1/models` | approved model list |
| `POST` | `/__stop` | graceful stop (loopback only) |

* Binds **loopback only** (`127.0.0.0/8`, `::1`). A non-loopback `--host` is
  refused before any socket is opened.
* `start` prints `url=<base>/v1 health=<base>/health ready=<base>/ready` and
  writes an atomic state file (`--state-file`) with pid/port/base URL/identity.
* The client (DSH) supplies the provider credential in `Authorization` or
  `x-api-key`; the proxy forwards it and never stores a secret. `--log-file`
  records request/settlement JSONL without header values.
* For streamed requests the proxy adds `stream_options.include_usage=true` so a
  well-behaved gateway returns a usage-bearing final chunk. It does not alter
  message content.

## 3. Budget identity (independent of `run_id`)

One JSON ledger lives at `$ACP2_DATA_ROOT/budget/ledger.json` (lock file
`ledger.json.lock`, `flock`-serialized, atomic `os.replace` writes):

```json
{
  "schema": "acp2.model-budget.ledger.v1",
  "batch_id": "batch-<16 hex>",
  "created_at": "...",
  "updated_at": "...",
  "scenarios": {
    "<scenario_budget_id>": {
      "caps": {"model_requests": 12, "input_tokens": 150000, "output_tokens": 30000},
      "models": ["<approved model id>"],
      "settled": {
        "requests": 0,
        "input_tokens_reported": 0, "input_tokens_estimated": 0,
        "output_tokens_reported": 0, "output_tokens_unknown_events": 0,
        "unknown_usage_events": 0, "input_cap_overshoot_events": 0,
        "output_cap_violations": 0
      },
      "inflight": {"requests": 0, "input_tokens": 0},
      "refused": {"<code>": 0},
      "run_ids": [],
      "events": [{"seq": 1, "kind": "request|refusal", "outcome": "...", "usage": {...}}]
    }
  }
}
```

* `batch_id` is generated once per ledger and never changes; all scenarios and
  reruns in the batch share it.
* `scenario_budget_id` (default `model-live`) is the budget identity. A retry,
  a proxy restart or a new `run_id` **inherits the remaining allowance**; the
  request header `X-ACP2-Run-Id` is recorded for audit and never resets a cap.
* Caps are frozen on first creation. A restart that passes different caps exits
  `2` with `caps_mismatch` — no silent widening or narrowing.
* A proxy crash leaves `inflight` reservations. On the next start they are
  charged as `restart_recovered` (one request, estimated input, unknown output)
  so a crash cannot refund a provider call.

## 3a. Platform mapping (D09 / M1-04b)

The proxy caps above are a **client-side throttle**. They are never a platform
enforcement claim; the platform mapping is frozen in
`src/acp_services/runtime/execution_limits.py::EXECUTION_LIMITS`:

| RPH/plan field | platform meaning | capability |
| --- | --- | --- |
| `wall_seconds` | `ResourceBounds`/timeout → local termination + CCCP `resources.timeout_s` (subprocess level) + LSF `-W` | `walltime_admission_bound` = enforced; `walltime_process_termination` = NOT_VERIFIED until the termination receipt (M1-05/M4-03) |
| `queue_seconds` | queue deadline (absolute, from admission) | NOT_VERIFIED until the expiry→terminal transition |
| `input_tokens` / `output_tokens` | recorded-only usage; the throttle stays in this proxy | recorded-only (`model_token_enforcement` = NOT_VERIFIED) |
| `core_hours` | recorded-only cumulative usage | recorded-only |

The single planner/bridge mapper is `toResourceLimits()` in
`rph/packages/dsh-bundle/skills/models.ts`: it drops none of the four fields and
throws `BudgetShapeError` for an unknown non-empty shape. `wall_seconds = 0`
means "unbounded" and maps to `null` (an unbounded run is never rendered as a
limit), which is why `toPlatformBudget()` returns `timeout_s = null` rather than
inventing one.

## 4. Caps and pre-request interception

Frozen per-scenario caps (plan C budget table):

| Resource | Cap | Refusal |
| --- | --- | --- |
| model requests | 12 | 13th → HTTP 429 `code=model_requests`, not forwarded |
| input tokens | 150,000 | estimate would exceed → 429 `code=input_tokens` |
| output tokens | 30,000 | settled output at cap → 429 `code=output_tokens` |
| concurrency | 1 | second in-flight → 429 `code=concurrency_cap` |
| per-request output | 4,096 | requested `max_tokens` above it → **400** `code=per_request_output_cap` (refused, never clamped) |
| approved model | explicit list | other model → 400 `code=unapproved_model`; **no fallback** |

Checks run **before** the upstream request:

1. one in-flight request per scenario;
2. `settled.requests + inflight.requests + 1 <= 12`;
3. `used_input + estimate <= 150000`, where `estimate = ceil(chars/3) + 32` over
   the serialized `messages` (labelled estimated, never reported as measured);
4. remaining output > 0;
5. requested `max_tokens` <= 4,096.

The wire `max_tokens` is
`min(requested, 4096, output_cap - settled_output)`, so a compliant upstream
cannot make the settled output total exceed 30k. If an upstream returns more
than the wire cap, the event records `output_cap_violation_events`; the
scenario treats a non-zero count as a failure.

Refused requests are recorded with `counted: false` and do not consume budget.
The budget-table rule applies: an expected refusal (the 13th call) is a
negative-test verdict, not overspend; a PASS may use exactly 12 calls.

## 5. Settlement rules

Every forwarded request consumes exactly one model request: the attempt reached
the provider (or was lost in flight) and may have been billed.

| Outcome | When | Usage |
| --- | --- | --- |
| `completed` | 2xx and the response/stream finished (stream: `[DONE]` or usage seen) | reported usage if present, else unknown |
| `upstream_status_<N>` | upstream returned non-2xx (including a wrong model id) | from the body if present, else unknown |
| `upstream_error` | connect/read failure before a response | unknown |
| `upstream_interrupted` | stream ended without `[DONE]` and without usage | reported so far, else unknown |
| `client_disconnected` | writing to the client failed mid-stream | reported so far, else unknown |
| `restart_recovered` | in-flight reservation found at start | estimated input, unknown output |

Usage capture:

* non-streaming: `usage.prompt_tokens`/`completion_tokens` (or
  `input_tokens`/`output_tokens`) from the response body;
* streaming: the last `data:` chunk carrying a `usage` object;
* **missing usage is never fabricated**: input is charged as the pre-request
  estimate and counted in `input_tokens_estimated`; output is left `null` and
  counted in `output_tokens_unknown_events`. `total_tokens_complete` is
  `false`, and `total_tokens` is the sum of *known* values only.

## 6. DSH routing (Web and headless)

The frozen DSH revision supports a custom provider base URL, so the proxy is
reachable from both profiles. Recorded mechanism (proved by `--dump-config`):

```yaml
- id: llm-pi-ai
  config:
    providers:
      acp2-budget-proxy:
        displayName: ACP2 budget proxy
        apiKeyEnv: <credential reference>
        api: openai-completions
        baseURL: http://127.0.0.1:<port>/v1
        models:
          - id: <approved model id>
```

* **Web**: Settings → Models → *Custom model API* writes the same fields to
  `$DSH_HOME/profiles/web/cordis.patch.yml`.
* **headless**: the same provider row in
  `$DSH_HOME/profiles/headless/cordis.patch.yml` (headless does not read the
  web profile's managed credential; supply its credential through the launch
  environment or the profile's `.env`).
* `baseURL` must point at the proxy, not at the provider, or the budget is not
  enforced. The acceptance selftest applies the patch to both profiles and
  asserts the effective config contains the proxy base URL, `api:
  openai-completions` and the provider id.

## 7. Evidence and self-test

`a-proxy-selftest/<run_id>/` (all from the controlled local stub; **zero real
model requests**):

* `selftest.json` — 56 checks (lifecycle, 12-then-13th, per-request cap,
  streaming/non-streaming usage, missing usage, interruption, upstream error,
  restart inheritance, run-ID independence, input/output caps, concurrency,
  unapproved model, crash recovery, caps mismatch, Web/headless routing);
* `ledger.json`, `proxy-*.jsonl`, `state-*.json`, `routing-*.yml`,
  `routing.json`.

`b-real-call/<run_id>/` — `prerequisites.json` when the real call cannot run.

Run it directly:

```bash
"$ACP2_C_PYTHON" scripts/acceptance/acp2_experience_budget_proxy.py selftest \
  --out "$ACP2_EVIDENCE/C/model-live/a-proxy-selftest/<run_id>" \
  --data-root "$ACP2_EVIDENCE/C/model-live/a-proxy-selftest/<run_id>" \
  --dsh-env "$ACP2_DATA_ROOT/acceptance-env-c.sh"
```

`c_accept model-live 300` runs T-13a and then T-13b. T-13b needs an approved
provider/model credential (`ACP2_MODEL_PROVIDER`/`ACP2_MODEL_ID`, a configured
`llm-pi-ai` route, or a stored DSH credential) **and** a reachable isolated
Controller on `$ACP2_CONTROLLER` (default `http://127.0.0.1:18765`).

## 8. This batch (2026-10-22)

* T-13a: PASS — 56/56 selftest checks, stub upstream 35 local calls, 12 allowed
  then the 13th refused per scenario, `batch_id=batch-…`, web+headless routing
  proved by `--dump-config`.
* T-13b: `NOT_VERIFIED` — `no_model_credential|controller_unavailable`.
  `$ACP2_DSH_HOME/profiles/{web,headless}/cordis.patch.yml` are empty, no
  credential reference resolves, no model credential is stored, and
  `GET http://127.0.0.1:18765/api/health` returns connection refused.
  No real model request was made; `usage.model_calls=0`.
* Unblock: provision an approved provider credential for both profiles and
  start B's isolated Controller, then rerun `c_accept model-live 300`. The
  three real facts (model request in the proxy ledger, `mcp__acp__acp_capabilities`
  tool call, tool result), the deliberate tool-error sub-run and the runtime
  counterexample are then asserted by the same scenario.

## 9. Unresolved risks

* The input cap is enforced on a conservative **estimate**; a provider that
  reports far more prompt tokens than the estimate can overshoot by one
  request. The overshoot is counted in `input_cap_overshoot_events`; a
  non-zero count is a scenario failure.
* A gateway that ignores `max_tokens` can exceed the per-request cap; the
  violation is recorded (`output_cap_violation_events`) and fails the scenario.
* The proxy refuses an unapproved model but cannot prove that DSH's model
  picker never offered another provider; the config snapshot and the refusal
  log are the evidence.
