# ACP — Auto-Calc Platform

Automated computational chemistry pipeline framework. Built on top of the
`cccp` (Computational Chemistry Connection Package) QC interface library;
ships a unified `acp` CLI, stage-based workflows, FastAPI server, and scheduler.

## Module Structure

| Module | Description |
|--------|-------------|
| `acp.core` | Generic domain models (`Structure`, `StructureEnsemble`, `StructureRecord`), workflow execution engine, pluggable registry, and state persistence. No chemistry-specific logic. |
| `acp.backends` | QC backend abstraction layer with capability-based interfaces (`GeometryOptimizer`, `SinglePointCalculator`, `FrequencyCalculator`, `NMRCalculator`, `TSMechanismCalculator`). Wraps ORCAInterface, CrestBackend, XTBBackend from the legacy cccp package. |
| `acp.io` | Molecular structure readers/writers. Thin wrapper delegating to `cccp.io.input_handler`. |
| `acp.workflows` | Stage-based workflow implementations. Confsearch engine provides unified conformer search via 4 protocols (xtb-crest, xtb-md, censo-crest, xtbmd-censo). |
| `acp.api` | API server stubs (Phase 2). Not yet implemented. |
| `acp.cli` | Unified command-line interface with subcommands: `run conformer`, `run nmr` (placeholder). |

## Key Design

- **Generic core**: `acp.core` has no hardcoded chemistry engine names — all engine-specific logic is in `acp.backends` and `acp.workflows`.
- **Adapter layer**: ACP models can convert to/from legacy `cccp` types (`ConformerCandidate`, `CandidateSet`) for interop during migration.
- **Stage-based workflows**: Workflows are composed of lightweight `Stage` callables run by a generic `WorkflowRunner`. Each stage is independently testable.
- **Capability-based backends**: Backends declare their capabilities (opt, freq, SP, NMR, TS) via Protocol classes, enabling plug-and-play engine selection.
