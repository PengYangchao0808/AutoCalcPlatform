# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""NMR report serialization (DevDoc §7 / §5 stage 8).

Emits:

* ``nmr_report.json`` — the full machine-readable report (candidates,
  DP4/DP5, assignment tables, regression + split R², per-conformer weights);
* ``nmr_assignment.xlsx`` — per-candidate shift-comparison sheet;
* ``scatter_<nucleus>.png`` / ``error_hist.png`` — diagnostic plots; the
  error histogram carries one signed ``scaled - exp`` residual panel per
  nucleus (Goodman convention, todo 25).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TypedDict

from acp.nmr.models import (
    REPORT_SCHEMA_VERSION,
    Assignment,
    CandidateResult,
    NmrReport,
    ProcessedSpectrum,
    check_digital_filter,
)
from acp.nmr.scaling import prediction_r_squared

logger = logging.getLogger(__name__)

#: Additive top-level ``nmr_report.json`` key (todo 42): per-spectrum
#: processing gate verdicts + quality metrics. ``None`` when no raw spectra
#: were involved (hand-built reports / text input); existing keys untouched.
PROCESSING_QUALITY_KEY = "processing_quality"

#: Rendered for payloads written before schema v2 (no ``schema_version``):
#: historical reports carry no validation state and are never upgraded.
LEGACY_REPORT_NOTE = "历史报告：验证状态未知"

#: Residual axis label — Goodman convention (todo 25): the stored residual
#: is ``scaled - exp``; the pre-25 label claimed the opposite sign.
GOODMAN_RESIDUAL_LABEL = "residual (scaled - exp) / ppm"

#: Provenance descriptions of the two R² definitions (todo 25).
R2_DEFINITIONS: dict[str, str] = {
    "r2_regression": (
        "regression-correlation squared of the calc-on-exp OLS fit "
        "(coefficient of determination; equals the historical r_squared)"
    ),
    "r2_prediction": (
        "prediction-space goodness of fit: 1 - sum((scaled - exp)^2) / sum((exp - mean(exp))^2)"
    ),
    "residual_convention": "scaled - exp",
}


class ReportPaths(TypedDict):
    json: Path
    xlsx: Path | None
    plots: list[Path]


def report_validation_note(payload: Mapping[str, object]) -> str | None:
    """Validation-display note for an already-parsed report payload.

    Payloads with ``schema_version == 2`` return ``None`` (validation state
    known). Anything else — notably v1 payloads written before todo 24,
    identified by the *absence* of ``schema_version`` — returns
    :data:`LEGACY_REPORT_NOTE`: readers display it and must not rewrite or
    auto-upgrade the stored file.

    Pure read helper: the legacy read path (path probe order, file serving,
    remote unwrap) is untouched; this only classifies a payload for display.
    """
    if payload.get("schema_version") == REPORT_SCHEMA_VERSION:
        return None
    return LEGACY_REPORT_NOTE


def _prediction_r2_by_nucleus(candidate: CandidateResult) -> dict[str, float | None]:
    """Prediction-space r² per nucleus from one candidate's assignments.

    Nuclei with fewer than two matched rows get ``None`` (undefined — never
    reported as ``0``; the T23 null-display rule applies to report values).
    """
    rows_by_nucleus: dict[str, list[Assignment]] = {}
    for assignment in candidate.assignments:
        rows_by_nucleus.setdefault(_nucleus_of_element(assignment.element), []).append(assignment)
    out: dict[str, float | None] = {}
    for nucleus, rows in rows_by_nucleus.items():
        if len(rows) < 2:
            out[nucleus] = None
            continue
        out[nucleus] = round(
            prediction_r_squared(
                [row.exp_ppm for row in rows],
                [row.scaled_ppm for row in rows],
            ),
            6,
        )
    return out


def _augment_split_r2(report: NmrReport, payload: object) -> object:
    """Add the split R² keys to each fitted nucleus + provenance (todo 25).

    ``r2_regression`` is the documented alias of the historical
    ``r_squared`` key: :class:`RegressionResult.r_squared` already IS the
    regression-correlation squared after the scaling.py numerator/
    denominator correction; ``r2_prediction`` is computed from the
    assignment rows (missing rows → ``None``).
    """
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if isinstance(candidates, list) and len(candidates) == len(report.candidates):
        for candidate, cand_dict in zip(report.candidates, candidates):
            regression = cand_dict.get("regression") if isinstance(cand_dict, dict) else None
            if not isinstance(regression, dict):
                continue
            pred_by_nucleus = _prediction_r2_by_nucleus(candidate)
            for nucleus, reg in regression.items():
                if not isinstance(reg, dict):
                    continue
                reg["r2_regression"] = reg.get("r_squared")
                reg["r2_prediction"] = pred_by_nucleus.get(nucleus)
    provenance = payload.get("provenance") if isinstance(payload, dict) else None
    if isinstance(provenance, dict):
        provenance["r2_definitions"] = dict(R2_DEFINITIONS)
    return payload


def processing_quality_records(spectra: Iterable[ProcessedSpectrum]) -> list[dict[str, object]]:
    """JSON-safe per-spectrum processing gate + quality records (todo 42 / G10).

    One record per processed spectrum for the additive
    :data:`PROCESSING_QUALITY_KEY` report block: gate verdict (``unknown``
    when the spectrum carries no assessment), reasons, phase/reference
    provenance, measured quality metrics (``None`` when not measurable —
    never zeroed) and the digital-filter check result. No existing report
    key is touched or renamed.
    """
    records: list[dict[str, object]] = []
    for spectrum in spectra:
        assessment = spectrum.assessment
        processing = spectrum.processing
        check = check_digital_filter(spectrum.acquisition, processing)
        records.append(
            {
                "nucleus": spectrum.nucleus,
                "element": spectrum.element,
                "source_dir": spectrum.source_dir,
                "status": assessment.status if assessment is not None else "unknown",
                "reasons": list(assessment.reasons) if assessment is not None else [],
                "formal_usable": spectrum.formal_usable,
                "phase_method": processing.phase_method if processing is not None else None,
                "reference_method": (
                    processing.reference_method if processing is not None else None
                ),
                "reference_ppm": processing.reference_ppm if processing is not None else None,
                "applied_shift_ppm": (
                    processing.applied_shift_ppm if processing is not None else None
                ),
                "quality": spectrum.quality.to_dict() if spectrum.quality is not None else None,
                "digital_filter": check.to_dict() if check is not None else None,
            }
        )
    return records


def write_json_report(report: NmrReport, output_path: Path) -> Path:
    """Write ``nmr_report.json`` (schema v2 payload from ``NmrReport.as_dict``).

    The payload additionally carries the split R² keys
    (``r2_regression``/``r2_prediction`` per fitted nucleus) and the
    ``provenance.r2_definitions`` block (todo 25), plus the additive
    :data:`PROCESSING_QUALITY_KEY` block (todo 42).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _augment_split_r2(report, report.as_dict())
    if isinstance(payload, dict):
        payload[PROCESSING_QUALITY_KEY] = report.metadata.get(PROCESSING_QUALITY_KEY)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def write_xlsx_report(report: NmrReport, output_path: Path) -> Path | None:
    """Write ``nmr_assignment.xlsx`` (one sheet per candidate).

    Every number mirrors the JSON serialization of the same raw field
    (``scaled_ppm`` raw; DP4/DP5/regression at round-6) so both artifacts
    carry identical values — nothing is recomputed from display output.

    Returns ``None`` (and logs) when openpyxl is unavailable.
    """
    try:
        from openpyxl import Workbook
    except ImportError:  # pragma: no cover - openpyxl is in deps
        logger.warning("openpyxl not available; skipping XLSX report")
        return None

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    # remove the default sheet — we add one per candidate
    default_ws = wb.active

    for candidate in report.candidates:
        sheet_name = f"cand_{candidate.index}"[:31]
        ws = wb.create_sheet(title=sheet_name)
        ws.append(["atom", "element", "exp_ppm", "calc_ppm", "scaled_ppm", "residual"])
        for assignment in candidate.assignments:
            ws.append(
                [
                    assignment.atom_label,
                    assignment.element,
                    round(assignment.exp_ppm, 4),
                    round(assignment.calc_ppm, 4),
                    assignment.scaled_ppm,
                    round(assignment.residual, 4),
                ]
            )
        ws.append([])
        dp4 = candidate.dp4_probability
        dp5 = candidate.dp5_probability
        ws.append(["DP4", round(dp4, 6) if dp4 is not None else None])
        ws.append(["DP5", round(dp5, 6) if dp5 is not None else None])
        pred_by_nucleus = _prediction_r2_by_nucleus(candidate)
        for nucleus, regression in candidate.regressions.items():
            ws.append([])
            ws.append([f"regression[{nucleus}]", "slope", round(regression.slope, 6)])
            ws.append(["", "intercept", round(regression.intercept, 6)])
            ws.append(["", "r_squared", round(regression.r_squared, 6)])
            ws.append(["", "mae", round(regression.mae, 6)])
            # split R² (todo 25) — same numbers as the JSON record
            ws.append(["", "r2_regression", round(regression.r_squared, 6)])
            ws.append(["", "r2_prediction", pred_by_nucleus.get(nucleus)])

    if default_ws is not None and len(wb.sheetnames) > 1:
        wb.remove(default_ws)
    wb.save(output_path)
    return output_path


def write_plots(report: NmrReport, output_dir: Path) -> list[Path]:
    """Write scatter + error-histogram PNGs.

    Returns the list of paths actually written (empty if matplotlib is
    unavailable or the report has no residuals).
    """
    try:
        import matplotlib

        matplotlib.use("Agg")  # headless backend
        import matplotlib.pyplot as plt
    except ImportError:  # pragma: no cover - matplotlib is in deps
        logger.warning("matplotlib not available; skipping plots")
        return []

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    # collect per-nucleus (calc, exp) pairs across candidates
    by_nucleus: dict[str, list[tuple[float, float, int]]] = {}
    for candidate in report.candidates:
        for assignment in candidate.assignments:
            nucleus = _nucleus_of_element(assignment.element)
            by_nucleus.setdefault(nucleus, []).append(
                (assignment.calc_ppm, assignment.exp_ppm, candidate.index)
            )

    for nucleus, triples in by_nucleus.items():
        fig, ax = plt.subplots(figsize=(5, 5))
        calc = [t[0] for t in triples]
        exp = [t[1] for t in triples]
        cand = [t[2] for t in triples]
        scatter = ax.scatter(calc, exp, c=cand, cmap="tab10", alpha=0.7)
        if calc and exp:
            lo = min(min(calc), min(exp))
            hi = max(max(calc), max(exp))
            ax.plot([lo, hi], [lo, hi], "k--", lw=0.8, alpha=0.5)
        ax.set_xlabel(f"calc δ ({nucleus}) / ppm")
        ax.set_ylabel(f"exp δ ({nucleus}) / ppm")
        ax.set_title(f"{nucleus}: calc vs exp")
        fig.colorbar(scatter, ax=ax, label="candidate #")
        fig.tight_layout()
        path = output_dir / f"scatter_{nucleus}.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        written.append(path)

    # Residual histogram — one panel PER NUCLEUS with SIGNED residuals in
    # the Goodman convention (scaled - exp, never absolute). The pre-25
    # version combined all nuclei under a δ_exp − δ_scaled label — the
    # opposite sign of the stored values. The file name stays
    # ``error_hist.png`` (T22/T23 manifest consumers pin the path).
    by_nucleus_residuals: dict[str, list[float]] = {}
    for candidate in report.candidates:
        for assignment in candidate.assignments:
            nucleus = _nucleus_of_element(assignment.element)
            by_nucleus_residuals.setdefault(nucleus, []).append(assignment.residual)
    if by_nucleus_residuals:
        nuclei = sorted(by_nucleus_residuals)
        fig, axes = plt.subplots(1, len(nuclei), figsize=(4.5 * len(nuclei), 3.6), squeeze=False)
        for ax, nucleus in zip(axes[0], nuclei):
            ax.hist(by_nucleus_residuals[nucleus], bins=20, alpha=0.75, edgecolor="black")
            ax.set_xlabel(GOODMAN_RESIDUAL_LABEL)
            ax.set_ylabel("count")
            ax.set_title(f"{nucleus}: scaled - exp")
        fig.suptitle("Residual distribution (Goodman: scaled - exp)", fontsize=10)
        fig.tight_layout(rect=(0, 0, 1, 0.94))
        path = output_dir / "error_hist.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        written.append(path)

    return written


def write_all_reports(report: NmrReport, output_dir: Path) -> ReportPaths:
    """Write JSON + XLSX + plots into *output_dir*.

    Returns ``{"json": path, "xlsx": path|None, "plots": [paths...]}``.
    """
    output_dir = Path(output_dir)
    json_path = write_json_report(report, output_dir / "nmr_report.json")
    xlsx_path = write_xlsx_report(report, output_dir / "nmr_assignment.xlsx")
    plots = write_plots(report, output_dir / "plots")
    return {"json": json_path, "xlsx": xlsx_path, "plots": plots}


def _nucleus_of_element(element: str) -> str:
    sym = (element or "").strip()
    if not sym:
        return "?"
    sym = sym[:1].upper() + sym[1:].lower()
    defaults = {"H": "1H", "C": "13C", "N": "15N", "F": "19F", "P": "31P"}
    return defaults.get(sym, f"1{sym}")


__all__ = [
    "write_json_report",
    "write_xlsx_report",
    "write_plots",
    "write_all_reports",
    "report_validation_note",
    "GOODMAN_RESIDUAL_LABEL",
    "LEGACY_REPORT_NOTE",
    "PROCESSING_QUALITY_KEY",
    "R2_DEFINITIONS",
    "ReportPaths",
    "processing_quality_records",
]
