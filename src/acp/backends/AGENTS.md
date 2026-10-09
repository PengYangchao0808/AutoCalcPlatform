# acp/backends/ — Compat Re-Export Layer + Legacy Batch Surface

## OVERVIEW
Since plan todo 12 the QC backend implementations live in `cccp/backends/` (see `src/cccp/backends/AGENTS.md`). This package is a **pure re-export shim layer**: every historical `acp.backends.*` import path keeps working with `A is B` identity over the cccp objects. Two things deliberately remain here:

1. `batch.py` — the **quarantined legacy backend-direct batch entry** (`batch_single_point`/`BatchSpResult`/`BatchSpFrameResult`). It is a sanctioned legacy surface with **no production consumers** (since todo 17 production batch paths run through `cccp.calculation.batch` + the `run_singlepoint` task core); the `legacy_batch_quarantine` gate forbids production modules from importing/executing it (only `acp/backends/__init__.py` re-export + tests + `tests/baseline/recovery_fixtures/generate_fixtures.py` are allowlisted).
2. `__init__.py` — package-level re-exports (incl. the legacy batch names).

## STRUCTURE
```
backends/
├── __init__.py          # Compat re-export of cccp.backends + legacy batch names
├── batch.py             # LEGACY backend-direct batch SP (quarantined; no production consumers since todo 17)
├── matrix.py … external.py   # Pure re-export shims → cccp.backends.* (12 modules)
└── AGENTS.md            # this file
```

## CONVENTIONS
- Shims are pure re-exports: docstring + `from cccp.backends.X import …` + `__all__` — no logic, no defaults, no subprocess, no call-form `get_backend(`/`require_backend(` (subject to the `compat_forwarders_are_pure` gate)
- Implementation changes belong in `cccp/backends/`; never edit a shim beyond adjusting re-exported names
- Patching backend-internal helpers in tests targets the cccp module path (e.g. `cccp.backends.external_backend.resolve_executable`)

## ANTI-PATTERNS
- **Never re-implement here**: the shim layer must stay logic-free
- **Never promote `batch.py` to production**: it is the legacy backend-direct execution face; the generic executor is `cccp/calculation/batch.py` (production has run through it since todo 17)
- **Never delete legacy public API** (`batch_single_point`/`BatchSpResult`/`BatchSpFrameResult`) to green a gate
