"""Runtime planning/recovery use cases — reuse, never re-implement (B05-3).

This module is the Runtime *planning/recovery* seam introduced by plan todo 27.
Like :class:`acp_services.runtime.commands.RuntimeCommands` (todo 25) it is a
single-owner forwarding surface over **explicit injected ports**; it never
imports, lazily imports, ``TYPE_CHECKING``-imports, string-reflects or
dynamically looks up the legacy implementation (see
``tests/test_acp_services_isolation.py`` and
``tests/test_services_injection_contracts.py``).

**Reuse, not a second implementation.** The planning/recovery semantics are
deliberately *not* duplicated here — they stay owned by the injected
implementations and are reached unchanged through the ports:

* two-layer plan/step identity, ``resume_source.json`` resolution, durable
  ``step_result`` completion evidence, publication-only retry and prerequisite
  gating all live inside the injected :class:`PlanExecutionPort` (the legacy
  plan executor); :meth:`RuntimeRecovery.execute` forwards once and returns its
  result untouched — this module writes no identity or checkpoint format;
* the step prerequisite gate is the injected :class:`StepRequirementPort`
  (``blocked`` / ``upstream_failed`` / diagnostic results stay distinct from a
  normal ``completed`` result and are never reported as satisfied);
* publication read/registration is the injected
  :class:`ResultPublicationPort` (idempotent by the stable ``result_id``).

A publication failure therefore stays a publication failure: the next
:meth:`RuntimeRecovery.execute` adopts the durable result and retries only the
publish — this seam never forces a QC recompute. The
``tests/test_services_recovery_semantics.py`` suite proves these semantics still
hold when reached **through** the Runtime seam, and
``tests/baseline/services-evidence/injection_registry.md`` records the migration
/ removal owner for each injected port.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acp_services.contracts.injection import (
    PlanExecutionPort,
    ResultPublicationPort,
    StepRequirementPort,
)

__all__ = ["RuntimeRecovery"]


@dataclass(frozen=True)
class RuntimeRecovery:
    """Injected-port planning/recovery surface (single owner, forwards once).

    A frozen bundle of the three planning/recovery ports the Runtime use cases
    need. Each method calls exactly one port method and returns that call's
    result unmodified. The Runtime does not own planning/recovery state: the
    single owner remains the injected implementation until the migration tasks
    (the ``PlanExecutionPort`` / ``StepRequirementPort`` /
    ``ResultPublicationPort`` registry rows) move it into this package and
    delete the injection.
    """

    planning: PlanExecutionPort
    step_requirements: StepRequirementPort
    publication: ResultPublicationPort

    def execute(
        self,
        plan: Any,
        task_root: Path,
        *,
        plan_fingerprint: str | None = None,
    ) -> Any:
        """Run one plan, reusing the injected identity/resume/publication logic.

        Forwards once to :meth:`PlanExecutionPort.execute`; the injected
        implementation performs the two-layer identity compare, the
        ``resume_source.json`` resolution, durable ``step_result`` adoption and
        the publication-only retry. No format is re-written here.
        """
        return self.planning.execute(plan, task_root, plan_fingerprint=plan_fingerprint)

    def evaluate_prerequisite(self, prior: Any, kind: Any, item_symbols: Any) -> Any:
        """Evaluate one step's prerequisite through the injected gate.

        Returns the injected ``RequirementOutcome`` unchanged — ``blocked`` /
        ``upstream_failed`` / diagnostic outcomes are reported exactly as the
        existing prerequisite table does, never rewritten as satisfied.
        """
        return self.step_requirements.evaluate_prerequisite(prior, kind, item_symbols)

    def register_result_manifest(self, result_dir: Path | str, manifest: Any) -> Path:
        """Register the display manifest through the injected publication port."""
        return self.publication.register_result_manifest(result_dir, manifest)

    def load_scientific_result(self, result_dir: Path | str) -> Any | None:
        """Read the per-step scientific record through the injected publication port."""
        return self.publication.load_scientific_result(result_dir)
