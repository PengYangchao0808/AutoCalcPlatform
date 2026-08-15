# core/ — Conformer Engine & Protocols

## OVERVIEW
Central orchestration: protocol definitions, candidate management, engine, state tracking. The hub of the (now dormant) conformer search pipeline — `ConformerEngine` is legacy; active workflows live in `acp/workflows/` and use `acp/backends` + `cccp.qc.interfaces` directly.

## STRUCTURE
```
core/
├── __init__.py         # Re-exports 14 symbols (ProtocolSpec, ConformerEngine, etc.)
├── engine.py           # ConformerEngine — main orchestrator (~1900 lines; largest cccp file)
├── protocols.py        # ProtocolSpec, FunnelPolicy, HandoffPolicy; _get_default_protocol_config() (610 lines)
├── candidates.py       # ConformerCandidate, CandidateSet, Boltzmann weighting
└── state_manager.py    # ConformerStateManager — stage checkpointing
```
**Removed**: `funnel.py` (FunnelRunner) — dead code cleared; `core/specs.py`, `core/spec_adapter.py`, `core/method_resolution.py` removed in the 2026-07-13 reverse-sync.

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Pipeline hub | `engine.py` | ConformerEngine — dormant; do not extend for new workflows |
| Protocol tuning | `protocols.py` | **`_get_default_protocol_config()` — THE authoritative protocol source** (anti-pattern #4: YAML `protocols` section unreachable) |
| Candidate models | `candidates.py` | `CandidateSet` with Boltzmann, RMSD, energy sorting |
| State persistence | `state_manager.py` | Save/restore `conformer_state.json` per molecule |

## CONVENTIONS
- Comprehensive `__all__` in `__init__.py` (14 symbols) — add new exports here
- `@dataclass(frozen=True)` for `ProtocolSpec`, `FunnelPolicy`, `HandoffPolicy`
- Gas-constant R defined here at `candidates.py:130` (0.001987204) — see root ANTI-PATTERN #12 (R duplicated 3× with different precision)

## ANTI-PATTERNS
- **Protocol config unreachable**: `resolve_protocol_spec()` reads `config['step1']['protocol_stack']`, NOT `config['protocols']`. YAML protocol config is bypassed. Edit `_get_default_protocol_config()` to change protocol behavior.
- **Dormant engine, retained code**: ConformerEngine + run_isostat legacy wrapper kept only for historical/upstream-compat reasons — new features must NOT be added here
