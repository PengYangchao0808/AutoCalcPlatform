"""
CENSO recipe module — Part0–Part3 funnel execution.

Each part is a pure pipeline stage:
  Part0: cheap prescreening (xTB energy window)
  Part1: low-cost DFT SP screening / reranking
  Part2: DFT geometry optimization + free-energy evaluation
  Part3: high-level refinement (final SP + Shermo)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from conformer_search.ensemble.candidate_set import FunnelRecordSet, FunnelRecord
from conformer_search.utils.constants import HARTREE_TO_KCAL

logger = logging.getLogger(__name__)

# Canonical energy keys
KEY_XTB = "xtb_sp"
KEY_LOWCOST = "low_cost_dft_sp"
KEY_R2SCAN = "r2scan3c_sp"
KEY_FINAL_E = "final_sp"
KEY_FINAL_G = "final_gibbs"

# JSON snapshot directory name
FUNNEL_DIR = "funnel"


def _snapshot_dir(work_dir: Path) -> Path:
    return work_dir / FUNNEL_DIR


def write_snapshot(records: FunnelRecordSet, work_dir: Path,
                   stage_index: int, stage_name: str,
                   selection_rule: dict[str, Any] | None = None,
                   rejected: list[FunnelRecord] | None = None,
                   protocol: str = "") -> None:
    """Write a per-stage JSON snapshot for traceability."""
    out_dir = _snapshot_dir(work_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stage_index:02d}_{stage_name}.json"
    import json
    payload: dict[str, Any] = {
        "protocol": protocol,
        "stage_index": stage_index,
        "stage_name": stage_name,
        "selection_rule": selection_rule or {},
        "counts": {
            "active": len(records),
            "rejected": len(rejected) if rejected else 0,
        },
        "records": records.to_dicts(),
    }
    if rejected:
        payload["rejected_records"] = [r.to_dict() for r in rejected]
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def write_funnel_trace(records: FunnelRecordSet, work_dir: Path,
                       protocol: str, stage_names: list[str],
                       stage_counts: list[dict[str, int]]) -> None:
    """Write a human-readable funnel trace markdown file."""
    out_dir = _snapshot_dir(work_dir)
    lines = [
        f"# Funnel Trace — {protocol}",
        "",
        "## Stage Summary",
        "| Stage | Input | Kept | Dropped |",
        "|---|---:|---:|---:|",
    ]
    for i, (name, counts) in enumerate(zip(stage_names, stage_counts)):
        kept = counts.get("active", 0)
        dropped = counts.get("rejected", 0)
        inp = kept + dropped
        lines.append(f"| {name} | {inp} | {kept} | {dropped} |")
    lines.append("")
    lines.append("## Final Ensemble")
    for r in records:
        w = f"{r.boltzmann_weight:.4f}" if r.boltzmann_weight else "-"
        e = r.energies.get(KEY_FINAL_G) or r.energies.get(KEY_FINAL_E) or 0.0
        lines.append(
            f"- {r.conformer_id}: energy={e:.6f} Ha, weight={w}"
        )
    (out_dir / "funnel_trace.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def run_part0(records: FunnelRecordSet, window_kcal: float | None,
              work_dir: Path | None = None,
              n_keep: int | None = None,
              protocol: str = "") -> FunnelRecordSet:
    """Part0: cheap prescreening via xTB energy window (or topN)."""
    if window_kcal and window_kcal > 0:
        selected, rejected = records.select_by_window(
            KEY_XTB, window_kcal, stage="part0_prescreen"
        )
    elif n_keep and n_keep > 0:
        selected, rejected = records.select_top_n(
            KEY_XTB, n_keep, stage="part0_prescreen"
        )
    else:
        return records
    if work_dir:
        write_snapshot(selected, work_dir, 0, "part0_prescreen",
                       {"energy_key": KEY_XTB, "window_kcal": window_kcal},
                       rejected, protocol=protocol)
    logger.info("Part0: %d -> %d (dropped %d)",
                len(records) + len(rejected), len(selected), len(rejected))
    return selected


def run_part1(records: FunnelRecordSet, window_kcal: float | None,
              work_dir: Path | None = None,
              protocol: str = "") -> FunnelRecordSet:
    """Part1: low-cost DFT SP screening / reranking."""
    updated = FunnelRecordSet(list(records))
    updated.relative_energies(KEY_LOWCOST).stable_sort(KEY_LOWCOST)
    if window_kcal and window_kcal > 0:
        selected, rejected = updated.select_by_window(
            KEY_LOWCOST, window_kcal, stage="part1_screening"
        )
    else:
        return updated
    if work_dir:
        write_snapshot(selected, work_dir, 1, "part1_screening",
                       {"energy_key": KEY_LOWCOST, "window_kcal": window_kcal},
                       rejected, protocol=protocol)
    logger.info("Part1: %d -> %d (dropped %d)",
                len(records) + len(rejected), len(selected), len(rejected))
    return selected


def run_part2(records: FunnelRecordSet, window_kcal: float | None,
              work_dir: Path | None = None,
              protocol: str = "") -> FunnelRecordSet:
    """Part2: DFT geometry optimization + free-energy evaluation."""
    updated = FunnelRecordSet(list(records))
    updated.relative_energies(KEY_R2SCAN).stable_sort(KEY_R2SCAN)
    if window_kcal and window_kcal > 0:
        selected, rejected = updated.select_by_window(
            KEY_R2SCAN, window_kcal, stage="part2_optimization"
        )
    else:
        return updated
    if work_dir:
        write_snapshot(selected, work_dir, 2, "part2_optimization",
                       {"energy_key": KEY_R2SCAN, "window_kcal": window_kcal},
                       rejected, protocol=protocol)
    logger.info("Part2: %d -> %d (dropped %d)",
                len(records) + len(rejected), len(selected), len(rejected))
    return selected


def run_part3(records: FunnelRecordSet,
              cutoff: float | None = None,
              temperature: float = 298.15,
              work_dir: Path | None = None,
              protocol: str = "") -> FunnelRecordSet:
    """Part3: high-level refinement + Boltzmann weighting + cutoff."""
    updated = FunnelRecordSet(list(records))
    updated.relative_energies(KEY_FINAL_G).stable_sort(KEY_FINAL_G)
    if cutoff and cutoff > 0:
        selected, rejected = updated.select_by_boltzmann_cutoff(
            KEY_FINAL_G, cutoff, temperature=temperature,
            stage="part3_refinement"
        )
    else:
        selected = updated
        rejected = []
    if work_dir:
        write_snapshot(selected, work_dir, 3, "part3_refinement",
                       {"energy_key": KEY_FINAL_G, "boltzmann_cutoff": cutoff},
                       rejected, protocol=protocol)
    logger.info("Part3: %d -> %d (dropped %d)",
                len(records) + len(rejected), len(selected), len(rejected))
    return selected


__all__ = [
    "run_part0", "run_part1", "run_part2", "run_part3",
    "write_snapshot", "write_funnel_trace",
    "KEY_XTB", "KEY_LOWCOST", "KEY_R2SCAN", "KEY_FINAL_E", "KEY_FINAL_G",
    "FUNNEL_DIR",
]
