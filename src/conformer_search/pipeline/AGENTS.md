# pipeline/ — Execution Orchestration

## OVERVIEW
Thin orchestration layer. `PipelineExecutor` wraps `ConformerEngine` methods for stage-by-stage pipeline execution.

## STRUCTURE
```
pipeline/
├── __init__.py    # Re-exports 1 symbol (PipelineExecutor)
└── executor.py    # PipelineExecutor — 3 methods, 78 lines
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Pipeline execution | `executor.py` | `PipelineExecutor.run()` — delegates to engine |
| DFT optimization | `executor.py` | `execute_final_opt_sp()` |
| Handoff | `executor.py` | `execute_handoff()` |

## NOTES
- **Anemic subpackage**: 78 lines, 3 methods, all delegate to `engine.<method>()`. Adds an abstraction layer but minimal independent logic.
- Consider merging into `core/` in a future refactor.
- No `if __name__ == '__main__'` block — not a standalone entry point.
