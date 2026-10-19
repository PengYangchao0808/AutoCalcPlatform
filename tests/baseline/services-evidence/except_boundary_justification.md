# `except Exception:` boundary justification — Services plan (F2 follow-up, todo 83)

**Created:** 2026-10-19
**Source finding:** `.omo/evidence/acp-parallel-plan-2-services/f2-code-quality.md` — LOW finding
(`20 plan-introduced except Exception: sites`, ANTI #8 literal rule).
**Scope:** the 20 plan-introduced sites enumerated by F2. Line numbers below are the F2
locations at review HEAD `0bfb0bd`; current line numbers are shown where narrowing changed
the file.
**Policy:** ANTI #8 forbids *new* bare `except Exception:` in general. This record keeps the
sites that are genuinely boundary duties and narrows the retry/best-effort boundaries that
could otherwise absorb a programming error as a retryable outcome.

## Narrowing applied (only two files, todo 83)

Both files now define `_PROGRAMMING_ERRORS = (TypeError, AttributeError, NameError)` and
re-raise those before the generic handler. Expected failures (I/O, SQLite, transport,
store rejections) keep their previous handling; no behavior changed for them.

| File | Sites narrowed | Effect |
|---|---|---|
| `src/acp_services/data/operations.py` | worker `lease_next` loop, executor failure recording, failure-record persistence | `TypeError` / `AttributeError` / `NameError` propagate instead of being logged and retried/recorded as a retryable failure |
| `src/acp/services_data_bootstrap.py` | transfer fetcher factory, optional `file_info` metadata probe | programming errors propagate instead of being downgraded to "no fetcher" / "no metadata" |

The remaining 15 sites are documented below and were **not rewritten** (per todo 83 scope:
other lanes own those files / their behavior is already non-swallowing).

## Per-site table (20 sites)

| # | Site (F2 → current) | Category | Why it cannot mask a programming error | Current handling |
|---|---|---|---|---|
| 1 | `src/acp_services/data/operations.py:849` → `:854` | transactional rollback + raise | The handler re-raises unconditionally after `ROLLBACK`; nothing is swallowed. | `ROLLBACK`, then `raise` |
| 2 | `src/acp_services/data/operations.py:960` → `:965` | transactional rollback + raise | Same: rollback only, exception re-raised. | `ROLLBACK`, then `raise` |
| 3 | `src/acp_services/data/operations.py:1013` → `:1018` | transactional rollback + raise | Same: rollback only, exception re-raised. | `ROLLBACK`, then `raise` |
| 4 | `src/acp_services/data/operations.py:1129` → `:1134` | transactional rollback + raise | Same: rollback only, exception re-raised (after the typed `IntegrityError` branch). | `ROLLBACK`, then `raise` |
| 5 | `src/acp_services/data/operations.py:1186` → `:1191` | best-effort boundary duty (wake signal) | `queue.Queue.put` on an unbounded queue does not reject; the branch is `# pragma: no cover` and any surprise is not state authority. | `pass` (deliberate no-op for a signal that is never the source of truth) |
| 6 | `src/acp_services/data/operations.py:1211` → `:1216/:1218` | worker loop / retry boundary | **Narrowed:** programming errors re-raise; expected store/transport errors are still logged and the loop continues. | `_PROGRAMMING_ERRORS` → `raise`; else `logger.warning(..., exc_info=True)` |
| 7 | `src/acp_services/data/operations.py:1221` → `:1228/:1230` | worker loop / executor retry boundary | **Narrowed:** a programming error in the injected executor is not recorded as a retryable execution failure; expected execution failures still follow retry policy. | `_PROGRAMMING_ERRORS` → `raise`; else record failure (retryable) |
| 8 | `src/acp_services/data/operations.py:1257` → `:1266/:1268` | best-effort boundary duty (failure recording) | **Narrowed:** programming errors surface; only store rejections/expected errors are logged without killing the worker. | `PrepareOperationError` → info log; `_PROGRAMMING_ERRORS` → `raise`; else warning log |
| 9 | `src/acp_services/data/transfer.py:747` | cache reconcile | A broken cache listing is logged with traceback and skipped; the reconcile report remains truthful for the other job ids. A programming error is not represented as "cache empty" for the affected job. | `logger.warning(..., exc_info=True)`, `continue` |
| 10 | `src/acp_services/data/helpers.py:58` | transactional cleanup + raise | The handler only removes the temp file and then re-raises; the original exception is never suppressed. | close/unlink temp, then `raise` |
| 11 | `src/acp_services/data/import_.py:1143` | transactional rollback + raise | Rollback only, then re-raise; the idempotent winner branch is separately typed (`sqlite3.IntegrityError`). | `ROLLBACK`, then `raise` |
| 12 | `src/acp_services/runtime/outbox.py:276` | notifier | The event is already durably appended/marked delivered; the notifier is a post-durability side channel and failures are logged with traceback. It cannot hide a state-transition error. | `logger.debug(..., exc_info=True)` |
| 13 | `src/acp_services/runtime/projections.py:269` | background refresh | Durable task state is untouched; refresh failure is logged with traceback and the next invalidation retries. | `logger.warning(..., exc_info=True)` |
| 14 | `src/acp/services_data_bootstrap.py:734` → `:738/:740` | best-effort boundary duty (transport adapter) | **Narrowed:** factory programming errors re-raise; transport failures still degrade to "no fetcher configured" (a classified transient source error). | `_PROGRAMMING_ERRORS` → `raise`; else warning log + `return None` |
| 15 | `src/acp/services_data_bootstrap.py:779` → `:785/:787` | best-effort boundary duty (optional metadata) | **Narrowed:** `file_info` programming errors re-raise; an ordinary unsupported/failed metadata probe still yields revision 0 instead of failing the read. | `_PROGRAMMING_ERRORS` → `raise`; else `return None` |
| 16 | `src/acp/scheduler/manager.py:3137` | shutdown cleanup | Shutdown must continue releasing ownership; the failure is logged with traceback. A programming error here surfaces on the next use/restart rather than being silently treated as success. | `logger.debug(..., exc_info=True)` |
| 17 | `src/acp/scheduler/manager.py:3172` | best-effort boundary duty (outbox delivery) | Delivery is post-commit and append-only; the row remains pending in durable authority and the next sweep retries. Failure is logged with traceback, never converted into a state transition. | `logger.debug(..., exc_info=True)`, `return None` |
| 18 | `src/acp/scheduler/manager.py:3180` | best-effort boundary duty (outbox sweep) | Outer sweep wrapper; same durable-pending invariant as #17, logged with traceback. | `logger.debug(..., exc_info=True)` |
| 19 | `src/acp/scheduler/manager.py:4878` | worker loop / enqueue boundary | Failure to persist the prepare operation is logged with traceback and returns; the operation is not claimed as enqueued and the next terminal transition/startup recovery can retry. | `logger.warning(..., exc_info=True)`, `return` |
| 20 | `src/acp/scheduler/manager.py:4884` | best-effort boundary duty (wake signal) | Unbounded queue `put` (never rejects); latency signal only, authoritative work is in the DB. `# pragma: no cover`. | `pass` |
| 21 | `src/acp/scheduler/manager.py:4922` | startup recovery | Recovery failure is logged with traceback; startup continues so the service is not bricked, and the durable operation rows remain undecided for the next retry. | `logger.warning(..., exc_info=True)` |

> Note: F2's literal LOW list has 20 sites (7 operations + 1 transfer + 1 helpers + 1
> import_ + 1 outbox + 1 projections + 2 bootstrap + 6 manager). Row 7 (`operations.py`
> `_execute`, F2 `:1221`) is an adjacent typed `except Exception as exc:` site that F2's
> should-fix explicitly names in the retry boundary (`1211,1221,1257`); it is included here
> because todo 83 narrows that boundary. 20 listed sites + 1 adjacent typed site = 21 rows.

## Guard expectations

- No `except:` without an exception type exists in `src/acp_services/`.
- Transactional sites (rows 1–4, 10, 11) re-raise; they cannot suppress.
- Narrowed sites (rows 6–8, 14–15) re-raise `TypeError` / `AttributeError` / `NameError`
  while preserving expected-failure handling.
- Remaining best-effort sites always log `exc_info=True` (except the two deliberate
  no-op wake-signal puts, rows 5 and 20) and never write a success state.
