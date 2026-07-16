# ACP — Auto-Calc Platform

Automated computational chemistry pipeline framework. Phase 1 wraps the
conformer-search workflow; Phase 2 will add an API server.

## Module Structure

| Module | Description |
|--------|-------------|
| `acp.core` | Generic domain models (`Structure`, `StructureEnsemble`, `StructureRecord`), workflow execution engine, pluggable registry, and state persistence. No chemistry-specific logic. |
| `acp.backends` | QC backend abstraction layer with capability-based interfaces (`GeometryOptimizer`, `SinglePointCalculator`, `FrequencyCalculator`, `NMRCalculator`, `TSMechanismCalculator`). Wraps ORCAInterface, CrestBackend, XTBBackend from the legacy conformer_search package. |
| `acp.io` | Molecular structure readers/writers. Thin wrapper delegating to `conformer_search.io.input_handler`. |
| `acp.workflows` | Stage-based workflow implementations. `conformer` module wraps ConformerEngine as composable pipeline stages (embed, CREST search, ISOSTAT clustering, DFT optimization, frequency, single-point, Shermo thermo). |
| `acp.api` | API server stubs (Phase 2). Not yet implemented. |
| `acp.cli` | Unified command-line interface with subcommands: `run conformer`, `run nmr` (placeholder). |

## Key Design

- **Generic core**: `acp.core` has no hardcoded chemistry engine names — all engine-specific logic is in `acp.backends` and `acp.workflows`.
- **Adapter layer**: ACP models can convert to/from legacy `conformer_search` types (`ConformerCandidate`, `CandidateSet`) for interop during migration.
- **Stage-based workflows**: Workflows are composed of lightweight `Stage` callables run by a generic `WorkflowRunner`. Each stage is independently testable.
- **Capability-based backends**: Backends declare their capabilities (opt, freq, SP, NMR, TS) via Protocol classes, enabling plug-and-play engine selection.
