"""ConfsearchEngine — the single Confsearch entry point (plan §14).

All upper layers (CLI, scheduler, API, NMR conformer generation, the
mechanism S1 stage) construct a :class:`ConfsearchRequest` and call
:meth:`ConfsearchEngine.run`; none of them re-implement CREST/CENSO/xTB
orchestration or conformer dedup.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .contracts import (
    PURE_XTB_PROTOCOLS,
    ConformerEntry,
    ConfsearchRequest,
    ConfsearchResult,
    ProtocolOutcome,
    validate_request,
)
from .manifest import (
    build_manifest_payload,
    confsearch_result_dir,
    write_conformer_geometries,
    write_ensemble_table,
    write_manifest,
)
from .profiles import profile_overlay
from .protocols import PROTOCOL_RUNNERS
from .protocols.censo_crest import run_rph_parity  # noqa: F401  (re-export)
from .selection import select_for_refinement
from .shared.boltzmann import boltzmann_weights, relative_energies_kcal
from .shared.provenance import input_block, provenance_block

logger = logging.getLogger(__name__)


class ConfsearchEngine:
    """Run one Confsearch request through its protocol and write the S1 manifest."""

    def run(self, request: ConfsearchRequest) -> ConfsearchResult:
        """Execute the request; never raises for workflow-level failures.

        Protocol/argument errors raise ``ValueError`` (fail fast at the
        boundary); execution failures are reported via
        ``ConfsearchResult.status == "failed"`` + ``error``.
        """
        validate_request(request)
        overlay = profile_overlay(request.protocol, request.profile)
        try:
            outcome = self._run_protocol(request, overlay)
        except Exception as exc:  # noqa: BLE001 - surfaced via result
            logger.exception("Confsearch protocol %s failed: %s", request.protocol, exc)
            return ConfsearchResult(
                status="failed",
                protocol=request.protocol,
                profile=request.profile,
                refinement_policy=request.refinement_policy,
                error=str(exc),
            )

        try:
            return self._finalize(request, outcome)
        except Exception as exc:  # noqa: BLE001 - surfaced via result
            logger.exception("Confsearch finalization failed: %s", exc)
            return ConfsearchResult(
                status="failed",
                protocol=request.protocol,
                profile=request.profile,
                refinement_policy=request.refinement_policy,
                error=str(exc),
            )

    def _run_protocol(self, request: ConfsearchRequest, overlay: dict[str, Any]) -> ProtocolOutcome:
        if request.backend == "rph-parity":
            return run_rph_parity(request, overlay)
        runner = PROTOCOL_RUNNERS[request.protocol]
        return runner(request, overlay)

    # ── finalization: entries → unified tree → manifest ─────────────────

    def _finalize(
        self,
        request: ConfsearchRequest,
        outcome: ProtocolOutcome,
    ) -> ConfsearchResult:
        entries = self._build_entries(outcome)
        confsearch_dir = self._confsearch_dir(request)
        write_conformer_geometries(confsearch_dir, entries, outcome.records)
        write_ensemble_table(confsearch_dir, entries)

        selected = select_for_refinement(
            request.refinement_policy,
            entries,
            threshold=float((request.levels or {}).get("refinement_threshold") or 0.99),
        )
        gates = self._quality_gates(entries, outcome, selected)

        payload = build_manifest_payload(
            protocol=request.protocol,
            profile=request.profile,
            refinement_policy=request.refinement_policy,
            backend=request.backend,
            input_block=input_block(request.input_source, request.charge, request.multiplicity),
            sampling=dict(outcome.sampling),
            conformers=entries,
            selected_conformers=selected,
            refinement=self._refinement_block(request, outcome, selected),
            provenance=provenance_block(
                request.protocol,
                request.profile,
                request.refinement_policy,
                request.backend,
                extra={"temperature_k": outcome.temperature_k},
            ),
            quality_gates=gates,
        )
        manifest_path = write_manifest(confsearch_dir, payload)
        logger.info("Confsearch manifest written: %s", manifest_path)
        return ConfsearchResult(
            status="completed",
            protocol=request.protocol,
            profile=request.profile,
            refinement_policy=request.refinement_policy,
            conformers=entries,
            selected_conformers=selected,
            manifest_path=manifest_path,
            quality_gates=gates,
            metadata=dict(outcome.workflow_metadata),
        )

    def _confsearch_dir(self, request: ConfsearchRequest) -> Path:
        from acp.io.structures import StructureReader
        from acp.workflows._helpers import resolve_task_output_root, sanitize_job_name

        reader = StructureReader()
        structure = reader.read(
            request.input_source,
            charge=request.charge,
            multiplicity=request.multiplicity,
            name=request.name,
        )
        safe_name = sanitize_job_name(structure.id)
        mol_dir = resolve_task_output_root(request.output_dir.resolve(), safe_name)
        confsearch_dir = confsearch_result_dir(mol_dir)
        confsearch_dir.mkdir(parents=True, exist_ok=True)
        return confsearch_dir

    @staticmethod
    def _build_entries(outcome: ProtocolOutcome) -> list[ConformerEntry]:
        """Rank records by free energy (falling back to energy) and weight them."""
        records = list(outcome.records)

        def sort_key(record: dict[str, Any]) -> float:
            value = record.get("free_energy_hartree")
            if value is None:
                value = record.get("energy_hartree")
            return float(value) if value is not None else float("inf")

        records.sort(key=sort_key)

        energies = [
            record.get("free_energy_hartree") or record.get("energy_hartree") for record in records
        ]
        weights = boltzmann_weights(energies, outcome.temperature_k)
        relative = relative_energies_kcal(energies)

        entries: list[ConformerEntry] = []
        for index, record in enumerate(records):
            entry = ConformerEntry(
                conf_id=f"conf_{index + 1:04d}",
                geometry="",
                energy_hartree=record.get("energy_hartree"),
                free_energy_hartree=record.get("free_energy_hartree"),
                relative_energy_kcal=relative[index],
                boltzmann_weight=weights[index] if weights[index] is not None else 0.0,
                rank=index + 1,
            )
            entries.append(entry)
        return entries

    @staticmethod
    def _refinement_block(
        request: ConfsearchRequest,
        outcome: ProtocolOutcome,
        selected: list[str],
    ) -> dict[str, Any]:
        completed = bool(outcome.refined_conf_ids) if selected else True
        if request.protocol in PURE_XTB_PROTOCOLS:
            completed = True  # nothing to refine — protocol energies are final
        artifacts: list[str] = []
        for key in (
            "thermo_csv",
            "boltzmann_table",
            "ensemble_thermo_json",
            "global_min_xyz",
        ):
            value = outcome.workflow_metadata.get(key)
            if isinstance(value, str):
                artifacts.append(value)
        return {
            "policy": request.refinement_policy,
            "completed": completed,
            "refined_conf_ids": list(outcome.refined_conf_ids),
            "selected_conformers": list(selected),
            "artifacts": artifacts,
        }

    @staticmethod
    def _quality_gates(
        entries: list[ConformerEntry],
        outcome: ProtocolOutcome,
        selected: list[str],
    ) -> dict[str, Any]:
        """Confsearch G1 checks (plan §15)."""
        weight_sum = sum(entry.boltzmann_weight or 0.0 for entry in entries)
        relative_valid = all(
            entry.relative_energy_kcal is None or entry.relative_energy_kcal >= -1e-6
            for entry in entries
        )
        ranked = [entry.rank for entry in entries]
        gates: dict[str, Any] = {
            "input_valid": True,
            "at_least_one_conformer": len(entries) > 0,
            "dedup_completed": bool(outcome.sampling.get("method")),
            "energy_ranking_valid": relative_valid and ranked == list(range(1, len(entries) + 1)),
            "boltzmann_weights_valid": abs(weight_sum - 1.0) < 1e-3,
            "refinement_consistent": not selected or bool(outcome.refined_conf_ids),
        }
        gates["G1"] = "PASS" if all(gates.values()) else "FAIL"
        return gates


__all__ = ["ConfsearchEngine"]
