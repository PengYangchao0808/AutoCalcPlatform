# core/ — Conformer Engine & Protocols

## OVERVIEW
Central orchestration: protocol definitions, candidate management, engine, state tracking, funnel dispatch. The hub of the conformer search pipeline.

## STRUCTURE
```
core/
├── __init__.py         # Re-exports 14 symbols (ProtocolSpec, ConformerEngine, etc.)
├── engine.py           # ConformerEngine — main orchestrator (624 lines)
├── protocols.py        # ProtocolSpec, FunnelPolicy, HandoffPolicy (277 lines)
├── candidates.py       # ConformerCandidate, CandidateSet, Boltzmann weighting (329 lines)
├── state_manager.py    # ConformerStateManager — stage checkpointing (237 lines)
└── funnel.py           # FunnelRunner — stage dispatch (172 lines)
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Pipeline hub | `engine.py` | ConformerEngine — all stages flow through here |
| Protocol tuning | `protocols.py` | `resolve_protocol_spec()`, `SUPPORTED_PROTOCOLS` |
| Candidate models | `candidates.py` | `CandidateSet` with Boltzmann, RMSD, energy sorting |
| State persistence | `state_manager.py` | Save/restore `conformer_state.json` per molecule |
| Stage dispatch | `funnel.py` | `FunnelRunner` delegates to engine methods |

## CONVENTIONS
- Comprehensive `__all__` in `__init__.py` (14 symbols) — add new exports here
- `FunnelRunner` uses `engine: Any` to avoid circular import with `ConformerEngine`
- `@dataclass(frozen=True)` for `ProtocolSpec`, `FunnelPolicy`, `HandoffPolicy`

## ANTI-PATTERNS
- **Protocol config unreachable**: `resolve_protocol_spec()` reads `config['step1']['protocol_stack']`, NOT `config['protocols']`. YAML protocol config is bypassed. Edit `_get_default_protocol_config()` to change protocol behavior.
- **ext = benchmark**: Both call `_run_ext_protocol()`. Only SP functional differs (wB97X-D4 vs DLPNO-CCSD(T)).
