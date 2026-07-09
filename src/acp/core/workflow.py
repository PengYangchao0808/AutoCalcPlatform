"""Generic stage-based workflow execution engine.

Defines core abstractions for composable workflow pipelines.
Stages are lightweight callable wrappers, not heavy class hierarchies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from acp.core.models import StructureEnsemble
from acp.core.state import WorkflowState

StageFunc = Callable[..., StructureEnsemble]


@dataclass
class Stage:
    """A single workflow stage: name + callable + params."""

    name: str
    func: StageFunc
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowSpec:
    """Named collection of stages forming a complete workflow."""

    name: str
    stages: list[Stage]
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowContext:
    """Execution context injected into every stage function."""

    work_dir: Path
    state: WorkflowState
    config: dict[str, Any]
    backends: dict[str, Any]
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowResult:
    """Result of executing a WorkflowSpec."""

    status: str  # "running" | "completed" | "failed"
    ensemble: StructureEnsemble | None = None
    stages_completed: list[str] = field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkflowRunner:
    """Executes WorkflowSpec stages sequentially, tracking state."""

    def __init__(self, context: WorkflowContext) -> None:
        self.context = context
        self.state = context.state

    def run(
        self,
        spec: WorkflowSpec,
        initial_data: StructureEnsemble | None = None,
    ) -> WorkflowResult:
        """Run all stages in *spec*, propagating *initial_data* through the pipeline."""
        data = StructureEnsemble() if initial_data is None else initial_data
        completed_stages: list[str] = []

        for stage in spec.stages:
            self.state.set_stage(stage.name)
            try:
                data = stage.func(self.context, data, **stage.params)
                self.state.complete_stage(stage.name)
                completed_stages.append(stage.name)
            except Exception as exc:
                self.state.fail_stage(stage.name, str(exc))
                return WorkflowResult(
                    status="failed",
                    ensemble=data,
                    stages_completed=completed_stages,
                    error=str(exc),
                )

        return WorkflowResult(
            status="completed",
            ensemble=data,
            stages_completed=completed_stages,
        )
