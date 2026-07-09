# acp/core/ — Shared Core Mechanism

## OVERVIEW
Generic domain models and workflow infrastructure with no chemistry-specific logic. 5 files, ~869 lines. Structure, WorkflowRunner, Registry, State, Config.

## STRUCTURE
```
core/
├── __init__.py     # Re-exports 12 symbols
├── models.py       # Structure, StructureRecord, StructureEnsemble, JobSpec (329 lines)
├── workflow.py     # Stage, WorkflowSpec, WorkflowRunner, WorkflowContext, WorkflowResult
├── state.py        # WorkflowState, EventLog (JSONL persistence)
├── registry.py     # Generic Registry[T] with register/get/list
└── config.py       # ACP config facade (thin wrapper over conformer_search.config)
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Data models | `models.py` | Structure(frozen), StructureRecord, StructureEnsemble, JobSpec |
| Boltzmann weighting | `models.py` | `StructureEnsemble.window_select()`, `.sort_by_energy()`, `.global_minimum()` |
| Legacy conversion | `models.py` | `.to_conformer_candidate()`, `.to_candidate_set()`, `.from_candidate_set()` |
| Workflow engine | `workflow.py` | WorkflowSpec + WorkflowRunner pipe stages |
| State persistence | `state.py` | WorkflowState with stage tracking, JSONL event log |
| Generic registry | `registry.py` | Registry[T] class for pluggable backends |
| Config facade | `config.py` | Thin wrapper around conformer_search.config |

## CONVENTIONS
- **No chemistry imports**: core/ never imports from backends/, workflows/, or conformer_search.qc.*
- **`@dataclass(frozen=True)`**: Structure is frozen; StructureRecord and StructureEnsemble are mutable
- **Numpy arrays**: Coordinates are read-only numpy arrays (`setflags(write=False)`)
- **`__all__`**: 12 exported symbols in `__init__.py`
- **pyright suppression**: `models.py` and `config.py` have `# pyright:` comments (locally used tool)
- **Backward compat**: Models have `.to_conformer_candidate()` / `.to_candidate_set()` for legacy interop

## ANTI-PATTERNS
- **Config re-export**: `config.py` is a thin wrapper — consider removing if acp uses `conformer_search.config` directly
- **Legacy coupling**: `models.py` imports `ConformerCandidate` and `CandidateSet` from `conformer_search.core.candidates` under TYPE_CHECKING guard
- **HARTREE_TO_KCAL duplication**: Defined in `models.py` (line 19) AND `conformer_search/utils/constants.py` — keep in sync
- **pyright comments without pyright configured**: Tool was used locally but not in project tooling