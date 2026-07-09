"""
Ensemble Candidate model — pure selection operations atop the legacy CandidateSet.

All energies in Hartree; all windows in kcal/mol (converted via HARTREE_TO_KCAL).
All selection functions return (selected, rejected) for full traceability.

Author: QCcalc Team
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from conformer_search.utils.constants import HARTREE_TO_KCAL

# Boltzmann constant in Hartree/(mol·K)
GAS_CONSTANT_HARTREE = 8.314462618 / 2625500.0


# ---------------------------------------------------------------------------
# FunnelRecord — per-conformer data tracked across pipeline stages
# ---------------------------------------------------------------------------

StatusLiteral = Literal["active", "rejected", "failed", "final_survivor"]


@dataclass
class FunnelRecord:
    """Single conformer record with provenance tracking across all pipeline stages."""

    conformer_id: str
    xyz_path: Path | None = None
    input_order: int = 0
    source_backend: str = ""

    status: StatusLiteral = "active"
    removal_reason: str | None = None

    energies: dict[str, float | None] = field(default_factory=dict)
    relative_kcal: dict[str, float | None] = field(default_factory=dict)
    boltzmann_weight: float | None = None

    current_geometry_level: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if isinstance(self.xyz_path, str):
            self.xyz_path = Path(self.xyz_path)

    def append_history(self, stage: str, decision: str, reason: str,
                       energy_key: str | None = None,
                       energy_hartree: float | None = None,
                       relative_kcal: float | None = None) -> None:
        self.history.append({
            "stage": stage,
            "decision": decision,
            "reason": reason,
            "energy_key": energy_key,
            "energy_hartree": energy_hartree,
            "relative_kcal": relative_kcal,
        })

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "conformer_id": self.conformer_id,
            "xyz_path": str(self.xyz_path) if self.xyz_path else None,
            "input_order": self.input_order,
            "source_backend": self.source_backend,
            "status": self.status,
            "removal_reason": self.removal_reason,
            "energies": {k: v for k, v in self.energies.items() if v is not None},
            "relative_kcal": {k: v for k, v in self.relative_kcal.items() if v is not None},
            "boltzmann_weight": self.boltzmann_weight,
            "current_geometry_level": self.current_geometry_level,
            "history": self.history,
        }
        return d


# ---------------------------------------------------------------------------
# FunnelRecordSet — container with pure selection operations
# ---------------------------------------------------------------------------


class FunnelRecordSet:
    """Ordered collection of FunnelRecords with chainable selection.

    All selection methods return (selected_set, rejected_records).
    Missing-energy records are always rejected by selection functions.
    """

    def __init__(self, records: list[FunnelRecord] | None = None):
        self.records: list[FunnelRecord] = list(records) if records else []

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self):
        return iter(self.records)

    def __getitem__(self, index: int) -> FunnelRecord:
        return self.records[index]

    # -- Record provenance helpers ------------------------------------------

    def mark(self, record: FunnelRecord, status: StatusLiteral,
             reason: str, stage: str = "") -> FunnelRecord:
        record.status = status
        record.removal_reason = reason
        if stage:
            record.append_history(stage, status, reason)
        return record

    def _valid(self, records: list[FunnelRecord],
               energy_key: str) -> list[FunnelRecord]:
        """Return records with non-None energy for *energy_key*."""
        return [r for r in records if r.energies.get(energy_key) is not None]

    # -- Energy math --------------------------------------------------------

    def _assert_valid(self, records: list[FunnelRecord],
                      energy_key: str) -> None:
        if not self._valid(records, energy_key):
            raise ValueError(
                f"No valid energies for key {energy_key!r} — "
                "cannot perform selection"
            )

    def relative_energies(self, energy_key: str,
                          relative_key: str | None = None) -> FunnelRecordSet:
        target = relative_key or energy_key
        valid = self._valid(self.records, energy_key)
        if not valid:
            return self
        min_energy = min(r.energies[energy_key] for r in valid)  # type: ignore[misc]
        for r in self.records:
            e = r.energies.get(energy_key)
            if e is not None:
                r.relative_kcal[target] = (e - min_energy) * HARTREE_TO_KCAL
            else:
                r.relative_kcal[target] = None
        return self

    def stable_sort(self, energy_key: str) -> FunnelRecordSet:
        self.records.sort(
            key=lambda r: (
                r.energies.get(energy_key) if r.energies.get(energy_key) is not None else float("inf"),
                r.input_order,
            ),
        )
        return self

    # -- Selection functions (all return (selected, rejected)) --------------

    def select_by_window(
        self, energy_key: str, window_kcal: float, stage: str = ""
    ) -> tuple[FunnelRecordSet, list[FunnelRecord]]:
        self.relative_energies(energy_key).stable_sort(energy_key)
        selected, rejected = [], []
        for r in self.records:
            delta = r.relative_kcal.get(energy_key)
            if delta is None:
                rejected.append(self.mark(r, "rejected", "missing_energy", stage))
            elif delta <= window_kcal:
                selected.append(self.mark(r, "active",
                                          f"delta<={window_kcal} kcal", stage))
            else:
                rejected.append(self.mark(r, "rejected",
                                          f"delta>{window_kcal} kcal", stage))
        return FunnelRecordSet(selected), rejected

    def select_top_n(
        self, energy_key: str, n: int, stage: str = ""
    ) -> tuple[FunnelRecordSet, list[FunnelRecord]]:
        self.stable_sort(energy_key)
        valid = self._valid(self.records, energy_key)
        if n >= len(valid):
            return self, []
        rank = 0
        selected, rejected = [], []
        for r in self.records:
            if r.energies.get(energy_key) is None:
                rejected.append(self.mark(r, "rejected", "missing_energy", stage))
            elif rank < n:
                selected.append(self.mark(r, "active", f"top{rank+1}", stage))
                rank += 1
            else:
                rejected.append(self.mark(r, "rejected",
                                          f"rank>{n}", stage))
        return FunnelRecordSet(selected), rejected

    def select_rank1(
        self, energy_key: str, stage: str = ""
    ) -> tuple[FunnelRecordSet, list[FunnelRecord]]:
        return self.select_top_n(energy_key, 1, stage)

    def select_rank1_with_top2_fallback(
        self, energy_key: str, gap_kcal: float, stage: str = ""
    ) -> tuple[FunnelRecordSet, list[FunnelRecord]]:
        self.stable_sort(energy_key)
        valid = self._valid(self.records, energy_key)
        if not valid:
            return FunnelRecordSet(), list(self.records)
        # Rank1 always selected
        rank1 = valid[0]
        if len(valid) == 1:
            return self.select_rank1(energy_key, stage)
        gap = (valid[1].energies[energy_key] - rank1.energies[energy_key]) * HARTREE_TO_KCAL  # type: ignore[operator]
        if gap <= gap_kcal:
            return self.select_top_n(energy_key, 2, stage)
        return self.select_rank1(energy_key, stage)

    def select_by_boltzmann_cutoff(
        self, energy_key: str, cutoff: float,
        temperature: float = 298.15, stage: str = ""
    ) -> tuple[FunnelRecordSet, list[FunnelRecord]]:
        self.relative_energies(energy_key).stable_sort(energy_key)
        valid = self._valid(self.records, energy_key)
        if not valid:
            return FunnelRecordSet(), list(self.records)
        min_e = min(v.energies[energy_key] for v in valid)  # type: ignore[misc]
        raw = []
        for r in valid:
            w = math.exp(
                -((r.energies[energy_key] or 0.0) - min_e)
                / (GAS_CONSTANT_HARTREE * temperature)
            )
            r.boltzmann_weight = w
            raw.append((r, w))
        total = sum(w for _, w in raw)
        for r, w in raw:
            r.boltzmann_weight = w / total if total > 0 else 0.0
        cumulative = 0.0
        selected, rejected = [], []
        for r in self.records:
            if r.boltzmann_weight is None or r.energies.get(energy_key) is None:
                rejected.append(self.mark(r, "rejected", "missing_energy", stage))
            else:
                cumulative += r.boltzmann_weight
                selected.append(self.mark(r, "final_survivor",
                                          f"boltzmann_cutoff<={cutoff}", stage))
                if cumulative >= cutoff:
                    break
        # Remaining records after cutoff
        for r in self.records[len(selected):]:
            if r not in rejected and r.status == "active":
                rejected.append(self.mark(r, "rejected",
                                          f"boltzmann_cumulative>{cutoff}", stage))
        return FunnelRecordSet(selected), rejected

    # -- I/O -----------------------------------------------------------------

    def to_dicts(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self.records]

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"records": self.to_dicts()}, indent=2, default=str),
            encoding="utf-8",
        )

    def write_csv(self, path: Path,
                  energy_keys: list[str] | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        keys = energy_keys or ["xtb_sp", "low_cost_dft_sp", "r2scan3c_sp",
                               "final_sp", "final_gibbs"]
        fieldnames = [
            "conformer_id", "status", "removal_reason",
            *[f"E_{k}" for k in keys],
            *[f"rel_{k}" for k in keys],
            "boltzmann_weight",
        ]
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in self.records:
                row = {
                    "conformer_id": r.conformer_id,
                    "status": r.status,
                    "removal_reason": r.removal_reason or "",
                }
                for k in keys:
                    row[f"E_{k}"] = r.energies.get(k) or ""
                for k in keys:
                    row[f"rel_{k}"] = r.relative_kcal.get(k) or ""
                row["boltzmann_weight"] = r.boltzmann_weight or ""
                writer.writerow(row)


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------


def records_from_paths(
    paths: list[Path],
    energy_key: str = "xtb_sp",
    energies: list[float] | None = None,
) -> FunnelRecordSet:
    records = []
    for i, path in enumerate(paths):
        r = FunnelRecord(
            conformer_id=f"conf_{i:03d}",
            xyz_path=path,
            input_order=i,
        )
        if energies and i < len(energies):
            r.energies[energy_key] = energies[i]
        records.append(r)
    return FunnelRecordSet(records)


__all__ = [
    "FunnelRecord",
    "FunnelRecordSet",
    "records_from_paths",
    "GAS_CONSTANT_HARTREE",
]
