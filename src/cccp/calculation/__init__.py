"""cccp calculation package (task-layer station root).

Public surface (PEP 562 lazy): contracts (scientific types), the
serializable ``TaskRequest`` envelope, the typed ``TaskResult`` envelope,
scientific progress events, the runtime ``TaskContext``, and the two-step
backend selection records.  The authoritative API specification is
``docs/ACP_CCCP_Task_API_DevDoc.md``.

This initializer is deliberately lazy: importing ``cccp.calculation`` (or
its pure-type modules ``errors``/``contracts``) must never pull in task
execution modules (``cccp.calculation.tasks``) or QC interfaces
(``cccp.qc.*``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from cccp.calculation.context import TaskContext as TaskContext
    from cccp.calculation.context import resolve_context as resolve_context
    from cccp.calculation.contracts import (
        ArtifactRef as ArtifactRef,
    )
    from cccp.calculation.contracts import (
        CASSCFSpec as CASSCFSpec,
    )
    from cccp.calculation.contracts import (
        ElectronicStateSpec as ElectronicStateSpec,
    )
    from cccp.calculation.contracts import (
        ElectronicStateValidation as ElectronicStateValidation,
    )
    from cccp.calculation.contracts import (
        GuessSpec as GuessSpec,
    )
    from cccp.calculation.contracts import (
        JsonObject as JsonObject,
    )
    from cccp.calculation.contracts import (
        JsonValue as JsonValue,
    )
    from cccp.calculation.contracts import (
        OptimizationMode as OptimizationMode,
    )
    from cccp.calculation.contracts import (
        OptimizationSpec as OptimizationSpec,
    )
    from cccp.calculation.contracts import (
        Provenance as Provenance,
    )
    from cccp.calculation.contracts import (
        StructureArtifact as StructureArtifact,
    )
    from cccp.calculation.contracts import (
        StructureRole as StructureRole,
    )
    from cccp.calculation.errors import (
        BackendUnavailableError as BackendUnavailableError,
    )
    from cccp.calculation.errors import (
        CalculationError as CalculationError,
    )
    from cccp.calculation.errors import (
        ProgressCallbackError as ProgressCallbackError,
    )
    from cccp.calculation.errors import (
        TaskCancelledError as TaskCancelledError,
    )
    from cccp.calculation.errors import (
        TaskInputError as TaskInputError,
    )
    from cccp.calculation.errors import (
        UnsupportedCapabilityError as UnsupportedCapabilityError,
    )
    from cccp.calculation.progress import ProgressEvent as ProgressEvent
    from cccp.calculation.progress import ProgressEventKind as ProgressEventKind
    from cccp.calculation.progress import TaskProgressSink as TaskProgressSink
    from cccp.calculation.requests import (
        TASK_OPTIONS_TYPES as TASK_OPTIONS_TYPES,
    )
    from cccp.calculation.requests import (
        TASK_REQUEST_SCHEMA_VERSION as TASK_REQUEST_SCHEMA_VERSION,
    )
    from cccp.calculation.requests import (
        BackendInputFragment as BackendInputFragment,
    )
    from cccp.calculation.requests import (
        BackendInputKind as BackendInputKind,
    )
    from cccp.calculation.requests import (
        CasscfOptions as CasscfOptions,
    )
    from cccp.calculation.requests import (
        CensoLevelOverride as CensoLevelOverride,
    )
    from cccp.calculation.requests import (
        CensoRefineOptions as CensoRefineOptions,
    )
    from cccp.calculation.requests import (
        ClusteringOptions as ClusteringOptions,
    )
    from cccp.calculation.requests import (
        ConformerSearchOptions as ConformerSearchOptions,
    )
    from cccp.calculation.requests import (
        FragmentConflictRule as FragmentConflictRule,
    )
    from cccp.calculation.requests import (
        FrequencyOptions as FrequencyOptions,
    )
    from cccp.calculation.requests import (
        IrcDirection as IrcDirection,
    )
    from cccp.calculation.requests import (
        IrcOptions as IrcOptions,
    )
    from cccp.calculation.requests import (
        MdSamplingOptions as MdSamplingOptions,
    )
    from cccp.calculation.requests import (
        MethodSpec as MethodSpec,
    )
    from cccp.calculation.requests import (
        NmrShieldingOptions as NmrShieldingOptions,
    )
    from cccp.calculation.requests import (
        OptimizeOptions as OptimizeOptions,
    )
    from cccp.calculation.requests import (
        OrcaGradientOptions as OrcaGradientOptions,
    )
    from cccp.calculation.requests import (
        RescueSpec as RescueSpec,
    )
    from cccp.calculation.requests import (
        ScanCoordinateSpec as ScanCoordinateSpec,
    )
    from cccp.calculation.requests import (
        ScanMode as ScanMode,
    )
    from cccp.calculation.requests import (
        ScanOptions as ScanOptions,
    )
    from cccp.calculation.requests import (
        SinglePointOptions as SinglePointOptions,
    )
    from cccp.calculation.requests import (
        StructureInput as StructureInput,
    )
    from cccp.calculation.requests import (
        TaskContractMapping as TaskContractMapping,
    )
    from cccp.calculation.requests import (
        TaskKind as TaskKind,
    )
    from cccp.calculation.requests import (
        TaskOptions as TaskOptions,
    )
    from cccp.calculation.requests import (
        TaskRequest as TaskRequest,
    )
    from cccp.calculation.requests import (
        TaskResources as TaskResources,
    )
    from cccp.calculation.requests import (
        ThermochemistryOptions as ThermochemistryOptions,
    )
    from cccp.calculation.requests import (
        TsSpec as TsSpec,
    )
    from cccp.calculation.requests import (
        XtbPathSearchOptions as XtbPathSearchOptions,
    )
    from cccp.calculation.requests import (
        validate_request as validate_request,
    )
    from cccp.calculation.results import (
        TASK_PAYLOAD_TYPES as TASK_PAYLOAD_TYPES,
    )
    from cccp.calculation.results import (
        TASK_RESULT_SCHEMA_VERSION as TASK_RESULT_SCHEMA_VERSION,
    )
    from cccp.calculation.results import (
        CasscfPayload as CasscfPayload,
    )
    from cccp.calculation.results import (
        CensoRefinePayload as CensoRefinePayload,
    )
    from cccp.calculation.results import (
        CensoRefineRecord as CensoRefineRecord,
    )
    from cccp.calculation.results import (
        ClusterAssignment as ClusterAssignment,
    )
    from cccp.calculation.results import (
        ClusteringPayload as ClusteringPayload,
    )
    from cccp.calculation.results import (
        ConformerEnergy as ConformerEnergy,
    )
    from cccp.calculation.results import (
        ConformerSearchPayload as ConformerSearchPayload,
    )
    from cccp.calculation.results import (
        ErrorKind as ErrorKind,
    )
    from cccp.calculation.results import (
        FrequencyAnalysis as FrequencyAnalysis,
    )
    from cccp.calculation.results import (
        FrequencyPayload as FrequencyPayload,
    )
    from cccp.calculation.results import (
        IrcDirectionResult as IrcDirectionResult,
    )
    from cccp.calculation.results import (
        IrcPayload as IrcPayload,
    )
    from cccp.calculation.results import (
        MdSamplingPayload as MdSamplingPayload,
    )
    from cccp.calculation.results import (
        NmrShielding as NmrShielding,
    )
    from cccp.calculation.results import (
        NmrShieldingPayload as NmrShieldingPayload,
    )
    from cccp.calculation.results import (
        OptimizePayload as OptimizePayload,
    )
    from cccp.calculation.results import (
        OrcaGradientPayload as OrcaGradientPayload,
    )
    from cccp.calculation.results import (
        ScanFrame as ScanFrame,
    )
    from cccp.calculation.results import (
        ScanPayload as ScanPayload,
    )
    from cccp.calculation.results import (
        SinglePointPayload as SinglePointPayload,
    )
    from cccp.calculation.results import (
        TaskPayload as TaskPayload,
    )
    from cccp.calculation.results import (
        TaskResult as TaskResult,
    )
    from cccp.calculation.results import (
        ThermochemistryPayload as ThermochemistryPayload,
    )
    from cccp.calculation.results import (
        XtbPathFrame as XtbPathFrame,
    )
    from cccp.calculation.results import (
        XtbPathSearchPayload as XtbPathSearchPayload,
    )
    from cccp.calculation.selection import (
        BackendSelection as BackendSelection,
    )
    from cccp.calculation.selection import (
        CapabilityRequirement as CapabilityRequirement,
    )
    from cccp.calculation.selection import (
        ProgramRequirement as ProgramRequirement,
    )
    from cccp.calculation.selection import (
        capability_requirement as capability_requirement,
    )
    from cccp.calculation.selection import (
        precheck_runtime as precheck_runtime,
    )
    from cccp.calculation.selection import (
        select_backend as select_backend,
    )
    from cccp.calculation.selection import (
        select_capability as select_capability,
    )
    from cccp.calculation.selection import (
        select_semantic as select_semantic,
    )
    from cccp.calculation.tasks.casscf import (
        run_casscf as run_casscf,
    )
    from cccp.calculation.tasks.frequency import (
        run_frequency as run_frequency,
    )
    from cccp.calculation.tasks.irc import (
        run_irc as run_irc,
    )
    from cccp.calculation.tasks.optimize import (
        run_optimize as run_optimize,
    )
    from cccp.calculation.tasks.singlepoint import (
        run_singlepoint as run_singlepoint,
    )
    from cccp.calculation.tasks.thermochemistry import (
        run_thermochemistry as run_thermochemistry,
    )

_LAZY_EXPORTS: dict[str, str] = {
    "ArtifactRef": "contracts",
    "CASSCFSpec": "contracts",
    "ElectronicStateSpec": "contracts",
    "ElectronicStateValidation": "contracts",
    "GuessSpec": "contracts",
    "JsonObject": "contracts",
    "JsonValue": "contracts",
    "OptimizationMode": "contracts",
    "OptimizationSpec": "contracts",
    "Provenance": "contracts",
    "StructureArtifact": "contracts",
    "StructureRole": "contracts",
    "BackendUnavailableError": "errors",
    "CalculationError": "errors",
    "ProgressCallbackError": "errors",
    "TaskCancelledError": "errors",
    "TaskInputError": "errors",
    "UnsupportedCapabilityError": "errors",
    "ProgressEvent": "progress",
    "ProgressEventKind": "progress",
    "TaskProgressSink": "progress",
    "TASK_OPTIONS_TYPES": "requests",
    "TASK_REQUEST_SCHEMA_VERSION": "requests",
    "CasscfOptions": "requests",
    "FrequencyOptions": "requests",
    "IrcDirection": "requests",
    "IrcOptions": "requests",
    "MethodSpec": "requests",
    "OptimizeOptions": "requests",
    "RescueSpec": "requests",
    "ScanCoordinateSpec": "requests",
    "ScanMode": "requests",
    "ScanOptions": "requests",
    "SinglePointOptions": "requests",
    "StructureInput": "requests",
    "TaskKind": "requests",
    "TaskOptions": "requests",
    "TaskRequest": "requests",
    "TaskResources": "requests",
    "ThermochemistryOptions": "requests",
    "TsSpec": "requests",
    "validate_request": "requests",
    "BackendInputFragment": "requests",
    "BackendInputKind": "requests",
    "CensoLevelOverride": "requests",
    "CensoRefineOptions": "requests",
    "ClusteringOptions": "requests",
    "ConformerSearchOptions": "requests",
    "FragmentConflictRule": "requests",
    "MdSamplingOptions": "requests",
    "NmrShieldingOptions": "requests",
    "OrcaGradientOptions": "requests",
    "TaskContractMapping": "requests",
    "XtbPathSearchOptions": "requests",
    "P2_TASK_CONTRACTS": "requests",
    "fragment_structured_conflicts": "requests",
    "resolve_fragment_conflicts": "requests",
    "CensoRefinePayload": "results",
    "CensoRefineRecord": "results",
    "ClusterAssignment": "results",
    "ClusteringPayload": "results",
    "ConformerEnergy": "results",
    "ConformerSearchPayload": "results",
    "MdSamplingPayload": "results",
    "NmrShielding": "results",
    "NmrShieldingPayload": "results",
    "OrcaGradientPayload": "results",
    "XtbPathFrame": "results",
    "XtbPathSearchPayload": "results",
    "TASK_PAYLOAD_TYPES": "results",
    "TASK_RESULT_SCHEMA_VERSION": "results",
    "CasscfPayload": "results",
    "ErrorKind": "results",
    "FrequencyAnalysis": "results",
    "FrequencyPayload": "results",
    "IrcDirectionResult": "results",
    "IrcPayload": "results",
    "OptimizePayload": "results",
    "ScanFrame": "results",
    "ScanPayload": "results",
    "SinglePointPayload": "results",
    "TaskPayload": "results",
    "TaskResult": "results",
    "ThermochemistryPayload": "results",
    "TaskContext": "context",
    "resolve_context": "context",
    "BackendSelection": "selection",
    "CapabilityRequirement": "selection",
    "ProgramRequirement": "selection",
    "capability_requirement": "selection",
    "precheck_runtime": "selection",
    "select_backend": "selection",
    "select_capability": "selection",
    "select_semantic": "selection",
    "CACHE_SCHEMA_VERSION": "batch",
    "ArtifactRecord": "batch",
    "BatchEntry": "batch",
    "BatchItemResult": "batch",
    "BatchResources": "batch",
    "BatchRunResult": "batch",
    "CacheIdentity": "batch",
    "CacheRecord": "batch",
    "CacheVersionPolicy": "batch",
    "EffectiveTaskParams": "batch",
    "FileSystemCacheStore": "batch",
    "ItemResources": "batch",
    "ItemRunOutcome": "batch",
    "MemoryCacheStore": "batch",
    "cache_identity": "batch",
    "resolve_effective_params": "batch",
    "run_batch": "batch",
    "version_matches": "batch",
    "artifact_ref_from_dict": "results",
    "artifact_ref_to_dict": "results",
    "casscf_payload_from_multireference": "results",
    "run_casscf": "tasks.casscf",
    "run_frequency": "tasks.frequency",
    "run_irc": "tasks.irc",
    "run_optimize": "tasks.optimize",
    "run_scan": "tasks.scan",
    "run_singlepoint": "tasks.singlepoint",
    "run_thermochemistry": "tasks.thermochemistry",
}

__all__ = sorted(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    """Resolve public exports lazily (PEP 562)."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        message = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(message)
    import importlib

    module = importlib.import_module(f".{module_name}", __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Return the lazy public surface."""
    return sorted(set(globals()) | set(__all__))
