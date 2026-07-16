# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnusedCallResult=false, reportUnannotatedClassAttribute=false, reportImplicitStringConcatenation=false
"""Benchmark meta-protocol for cross-protocol conformer comparisons."""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from acp.core.models import zip_strict
from acp.workflows.conformer import run_conformer_search
from conformer_search.config import load_config
from conformer_search.utils.constants import HARTREE_TO_KCAL

logger = logging.getLogger(__name__)

BENCHMARK_LEVELS: dict[str, list[str]] = {
    "quick": ["zero", "lite"],
    "standard": ["lite", "full", "ext"],
    "strict": ["zero", "lite", "full", "ext", "benchmark"],
}

_R_HARTREE = 8.314462618 / 2625500.0
_SUMMARY_JSON_NAME = "benchmark_summary.json"
_SUMMARY_TABLE_NAME = "benchmark_summary.txt"


def _coerce_float(value: Any) -> float | None:
    """Convert a numeric-like value to ``float`` when possible."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pair_key(protocol_a: str, protocol_b: str) -> str:
    """Return a stable label for a protocol pair."""
    return f"{protocol_a} vs {protocol_b}"


def _spearman_rank_correlation(rank_a: list[int], rank_b: list[int]) -> float:
    """Compute Spearman correlation for two equal-length rank vectors."""
    n = len(rank_a)
    if n < 2:
        return 1.0

    d_sq_sum = sum((a - b) ** 2 for a, b in zip_strict(rank_a, rank_b))
    return 1.0 - (6.0 * d_sq_sum) / (n * (n * n - 1))


def _normalized_protocols(protocols: list[str]) -> list[str]:
    """De-duplicate protocols while preserving order."""
    normalized: list[str] = []
    seen: set[str] = set()
    for protocol in protocols:
        name = protocol.strip()
        if not name or name in seen:
            continue
        normalized.append(name)
        seen.add(name)
    return normalized


class BenchmarkRunner:
    """Run multiple conformer workflows and compare their final ensembles."""

    def __init__(self, config: dict[str, Any], protocols: list[str], output_dir: Path):
        self.config = dict(config)
        self.protocols = _normalized_protocols(protocols)
        self.output_dir = Path(output_dir)
        self.last_summary: dict[str, Any] | None = None

    def run(
        self,
        input_xyz: Path,
        charge: int = 0,
        multiplicity: int = 1,
    ) -> dict[str, Any]:
        """Execute the benchmark suite and return the full summary payload."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        base_config = load_config(overrides=self.config)
        benchmark_name = Path(input_xyz).stem
        shared_input = self.output_dir / "shared_input.xyz"
        shutil.copyfile(input_xyz, shared_input)

        temperature = float(base_config.get("thermo", {}).get("temperature_k", 298.15))
        protocol_results: dict[str, dict[str, Any]] = {}
        last_ensemble_input: Path | None = None

        for protocol in self.protocols:
            protocol_output_dir = self.output_dir / protocol
            protocol_output_dir.mkdir(parents=True, exist_ok=True)
            protocol_input = (
                last_ensemble_input
                if last_ensemble_input is not None and "reference" in protocol.lower()
                else shared_input
            )
            started_at = time.monotonic()

            try:
                result = run_conformer_search(
                    input_source=str(protocol_input),
                    output_dir=protocol_output_dir,
                    protocol=protocol,
                    config=base_config,
                    name=benchmark_name,
                    charge=charge,
                    multiplicity=multiplicity,
                )
                walltime_seconds = time.monotonic() - started_at

                if result.status != "completed":
                    raise RuntimeError(result.error or f"{protocol} did not complete successfully")

                protocol_results[protocol] = self._build_success_result(
                    protocol=protocol,
                    input_source=protocol_input,
                    result=result.metadata,
                    walltime_seconds=walltime_seconds,
                    temperature=temperature,
                )
                resolved_ensemble = self._resolve_final_ensemble_path(result.metadata)
                if resolved_ensemble is not None and resolved_ensemble.exists():
                    last_ensemble_input = resolved_ensemble
            except Exception as exc:
                walltime_seconds = time.monotonic() - started_at
                logger.exception("Benchmark protocol %s failed", protocol)
                protocol_results[protocol] = self._build_failure_result(
                    input_source=protocol_input,
                    walltime_seconds=walltime_seconds,
                    error=str(exc),
                )

        summary = {
            "input": str(Path(input_xyz).resolve()),
            "shared_input": str(shared_input),
            "protocols": protocol_results,
            "metrics": self._compute_metrics(protocol_results),
        }
        self.last_summary = summary

        summary_path = self.output_dir / _SUMMARY_JSON_NAME
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        table_path = self.output_dir / _SUMMARY_TABLE_NAME
        table_path.write_text(self.format_summary_table(summary), encoding="utf-8")

        return summary

    def format_summary_table(self, summary: dict[str, Any] | None = None) -> str:
        """Return a human-readable benchmark summary table."""
        summary_data = self.last_summary if summary is None else summary
        if summary_data is None:
            raise ValueError("No benchmark summary is available")

        metrics = summary_data["metrics"]
        protocols = summary_data["protocols"]
        delta_g = metrics.get("deltaG_vs_reference", {})

        lines = [
            "Benchmark summary",
            "=" * 104,
            f"Input: {summary_data['input']}",
            f"Reference protocol: {metrics.get('reference_protocol') or '-'}",
            (f"Global-minimum agreement: {'yes' if metrics.get('global_min_agreement') else 'no'}"),
            "",
            (
                f"{'Protocol':<16} {'Success':<8} {'Global min':<16} "
                f"{'n_conf':>8} {'dG_ref(kcal)':>14} {'Walltime(s)':>12}"
            ),
            "-" * 104,
        ]

        for protocol in self.protocols:
            data = protocols.get(protocol, {})
            success = "yes" if data.get("success") else "no"
            global_min_id = data.get("global_min_id") or "-"
            n_conformers = data.get("n_conformers", 0)
            delta_value = delta_g.get(protocol)
            delta_text = "-" if delta_value is None else f"{delta_value:.3f}"
            walltime = float(data.get("walltime_seconds", 0.0))
            lines.append(
                f"{protocol:<16} {success:<8} {global_min_id:<16} "
                f"{n_conformers:>8} {delta_text:>14} {walltime:>12.3f}"
            )

        failures = [
            (protocol, protocols[protocol].get("error"))
            for protocol in self.protocols
            if protocol in protocols and not protocols[protocol].get("success")
        ]
        if failures:
            lines.extend(["", "Failures:"])
            for protocol, error in failures:
                lines.append(f"  - {protocol}: {error}")

        pair_sections = [
            ("Rank Spearman", metrics.get("rank_spearman", {})),
            ("Boltzmann overlap", metrics.get("boltzmann_overlap", {})),
        ]
        for title, values in pair_sections:
            if not values:
                continue
            lines.extend(["", f"{title}:"])
            for pair_name, value in values.items():
                lines.append(f"  {pair_name}: {value:.4f}")

        return "\n".join(lines)

    def _build_success_result(
        self,
        *,
        protocol: str,
        input_source: Path,
        result: dict[str, Any],
        walltime_seconds: float,
        temperature: float,
    ) -> dict[str, Any]:
        """Normalize one successful protocol result into the benchmark schema."""
        normalized_ensemble = self._normalize_ensemble(result.get("candidates", []), temperature)
        global_min_id = normalized_ensemble[0]["conformer_id"] if normalized_ensemble else None
        global_min_energy = (
            normalized_ensemble[0]["energy_hartree"]
            if normalized_ensemble
            else _coerce_float(result.get("global_min_energy"))
        )

        ensemble_xyz = self._resolve_final_ensemble_path(result)
        return {
            "protocol": protocol,
            "input_source": str(input_source),
            "success": True,
            "global_min_id": global_min_id,
            "global_min_energy": global_min_energy,
            "global_min_xyz": result.get("global_min_xyz"),
            "n_conformers": len(normalized_ensemble),
            "walltime_seconds": round(walltime_seconds, 6),
            "normalized_ensemble": normalized_ensemble,
            "ensemble_xyz": str(ensemble_xyz) if ensemble_xyz is not None else None,
            "error": None,
        }

    def _build_failure_result(
        self,
        *,
        input_source: Path,
        walltime_seconds: float,
        error: str,
    ) -> dict[str, Any]:
        """Return the normalized payload for a failed protocol."""
        return {
            "input_source": str(input_source),
            "success": False,
            "global_min_id": None,
            "global_min_energy": None,
            "global_min_xyz": None,
            "n_conformers": 0,
            "walltime_seconds": round(walltime_seconds, 6),
            "normalized_ensemble": [],
            "ensemble_xyz": None,
            "error": error,
        }

    def _normalize_ensemble(
        self,
        candidates: list[dict[str, Any]],
        temperature: float,
    ) -> list[dict[str, Any]]:
        """Convert legacy candidate metadata to a common benchmark format."""
        normalized: list[dict[str, Any]] = []

        for fallback_index, candidate in enumerate(candidates):
            energy_hartree = self._candidate_energy(candidate)
            if energy_hartree is None:
                continue

            conformer_id = self._candidate_id(candidate, fallback_index)
            weight = _coerce_float(candidate.get("weight"))
            normalized.append(
                {
                    "conformer_id": conformer_id,
                    "energy_hartree": energy_hartree,
                    "boltzmann_weight": weight if weight is not None else 0.0,
                }
            )

        normalized.sort(key=lambda item: (float(item["energy_hartree"]), str(item["conformer_id"])))
        self._ensure_weights(normalized, temperature)
        return normalized

    def _compute_metrics(self, results: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Compute benchmark metrics across all protocol runs."""
        success_map = {protocol: bool(data.get("success")) for protocol, data in results.items()}
        global_min_map = {protocol: data.get("global_min_id") for protocol, data in results.items()}
        metrics: dict[str, Any] = {
            "global_min_id": global_min_map,
            "deltaG_vs_reference": {protocol: None for protocol in results},
            "rank_spearman": {},
            "boltzmann_overlap": {},
            "n_conformers": {
                protocol: int(data.get("n_conformers", 0)) for protocol, data in results.items()
            },
            "walltime_seconds": {
                protocol: float(data.get("walltime_seconds", 0.0))
                for protocol, data in results.items()
            },
            "success": success_map,
            "reference_protocol": None,
            "reference_energy_hartree": None,
            "global_min_agreement": False,
        }

        successful = {
            protocol: data for protocol, data in results.items() if bool(data.get("success"))
        }
        if not successful:
            return metrics

        reference_protocol = self._reference_protocol(successful)
        reference_energy = _coerce_float(successful[reference_protocol].get("global_min_energy"))
        metrics["reference_protocol"] = reference_protocol
        metrics["reference_energy_hartree"] = reference_energy

        successful_minima = {
            data.get("global_min_id")
            for data in successful.values()
            if data.get("global_min_id") is not None
        }
        metrics["global_min_agreement"] = len(successful_minima) <= 1

        if reference_energy is not None:
            for protocol, data in successful.items():
                global_min_energy = _coerce_float(data.get("global_min_energy"))
                if global_min_energy is None:
                    continue
                metrics["deltaG_vs_reference"][protocol] = round(
                    (global_min_energy - reference_energy) * HARTREE_TO_KCAL,
                    6,
                )

        successful_protocols = list(successful)
        for index, protocol_a in enumerate(successful_protocols):
            for protocol_b in successful_protocols[index + 1 :]:
                pair_name = _pair_key(protocol_a, protocol_b)
                spearman = self._pairwise_spearman(successful[protocol_a], successful[protocol_b])
                if spearman is not None:
                    metrics["rank_spearman"][pair_name] = round(spearman, 6)

                overlap = self._pairwise_boltzmann_overlap(
                    successful[protocol_a], successful[protocol_b]
                )
                metrics["boltzmann_overlap"][pair_name] = round(overlap, 6)

        return metrics

    def _reference_protocol(self, successful: dict[str, dict[str, Any]]) -> str:
        """Select the protocol used as the energy reference."""
        return min(
            successful,
            key=lambda protocol: float(
                successful[protocol].get("global_min_energy") or float("inf")
            ),
        )

    def _candidate_energy(self, candidate: dict[str, Any]) -> float | None:
        """Return the comparison energy for a normalized candidate."""
        for key in ("g_conc", "gibbs_energy", "energy"):
            energy = _coerce_float(candidate.get(key))
            if energy is not None:
                return energy
        return None

    def _candidate_id(self, candidate: dict[str, Any], fallback_index: int) -> str:
        """Return a stable conformer identifier from legacy candidate metadata."""
        source_file = candidate.get("source_file")
        if isinstance(source_file, str) and source_file:
            return Path(source_file).stem

        index = candidate.get("index")
        if isinstance(index, int):
            return f"conf_{index:03d}"

        return f"conf_{fallback_index:03d}"

    def _ensure_weights(
        self,
        normalized: list[dict[str, Any]],
        temperature: float,
    ) -> None:
        """Normalize or reconstruct Boltzmann weights for a protocol ensemble."""
        if not normalized:
            return

        current_weights = [float(item["boltzmann_weight"]) for item in normalized]
        total_weight = sum(current_weights)
        if total_weight > 0.0:
            for item in normalized:
                item["boltzmann_weight"] = float(item["boltzmann_weight"]) / total_weight
            return

        energies = [float(item["energy_hartree"]) for item in normalized]
        min_energy = min(energies)
        boltzmann_factors = [
            pow(2.718281828459045, -((energy - min_energy) / (_R_HARTREE * temperature)))
            for energy in energies
        ]
        factor_sum = sum(boltzmann_factors)

        if factor_sum <= 0.0:
            equal_weight = 1.0 / len(normalized)
            for item in normalized:
                item["boltzmann_weight"] = equal_weight
            return

        for item, factor in zip_strict(normalized, boltzmann_factors):
            item["boltzmann_weight"] = factor / factor_sum

    def _resolve_final_ensemble_path(self, result: dict[str, Any]) -> Path | None:
        """Infer the final ensemble XYZ written by the conformer workflow."""
        global_min_xyz = result.get("global_min_xyz")
        if not isinstance(global_min_xyz, str) or not global_min_xyz:
            return None

        molecule_dir = Path(global_min_xyz).parent
        ensemble_xyz = molecule_dir / "finalDFT" / "all_conformers.xyz"
        if ensemble_xyz.exists():
            return ensemble_xyz
        return None

    def _pairwise_spearman(
        self,
        protocol_a: dict[str, Any],
        protocol_b: dict[str, Any],
    ) -> float | None:
        """Compute Spearman rank correlation on the shared conformer IDs."""
        order_a = [item["conformer_id"] for item in protocol_a.get("normalized_ensemble", [])]
        order_b = [item["conformer_id"] for item in protocol_b.get("normalized_ensemble", [])]
        common_ids = set(order_a) & set(order_b)
        if len(common_ids) < 2:
            return None

        filtered_a = [conformer_id for conformer_id in order_a if conformer_id in common_ids]
        filtered_b = [conformer_id for conformer_id in order_b if conformer_id in common_ids]
        rank_map_a = {conformer_id: index + 1 for index, conformer_id in enumerate(filtered_a)}
        rank_map_b = {conformer_id: index + 1 for index, conformer_id in enumerate(filtered_b)}
        ordered_common = sorted(common_ids)
        ranks_a = [rank_map_a[conformer_id] for conformer_id in ordered_common]
        ranks_b = [rank_map_b[conformer_id] for conformer_id in ordered_common]
        return _spearman_rank_correlation(ranks_a, ranks_b)

    def _pairwise_boltzmann_overlap(
        self,
        protocol_a: dict[str, Any],
        protocol_b: dict[str, Any],
    ) -> float:
        """Compute overlap between two Boltzmann-weighted ensembles."""
        weights_a = {
            item["conformer_id"]: float(item["boltzmann_weight"])
            for item in protocol_a.get("normalized_ensemble", [])
        }
        weights_b = {
            item["conformer_id"]: float(item["boltzmann_weight"])
            for item in protocol_b.get("normalized_ensemble", [])
        }
        union_ids = set(weights_a) | set(weights_b)
        if not union_ids:
            return 0.0

        return sum(
            min(weights_a.get(conformer_id, 0.0), weights_b.get(conformer_id, 0.0))
            for conformer_id in union_ids
        )


__all__ = ["BENCHMARK_LEVELS", "BenchmarkRunner"]
