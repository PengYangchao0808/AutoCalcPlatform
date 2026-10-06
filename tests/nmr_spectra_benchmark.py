"""Labeled-spectra benchmark interface (todo 48 / gap G10, Wave-7 handoff).

Consumes the committed fixtures under ``tests/fixtures/nmr/`` (plus the real
Bruker fixture in ``tests/fixtures/bruker_real_group_delay/``) through the
existing production processors — the todo-41 four-layer model, the todo-42
processing gates, the todo-43/44 nucleus processors and the todo-45 selection
registry — and produces one deterministic METRICS JSON:

* **spectra-processing precision/recall** — carbon resonances and proton
  multiplets scored against the committed manual annotations via the
  processors' own compare helpers (``compare_resonances_to_annotations`` /
  ``compare_proton_grouping``); tolerance rules are stated in the output;
* **multiplet grouping metrics** — the proton grouping report (precision,
  recall, F1, pairwise co-grouping accuracy, atom-count accuracy);
* **rejection / gating outcomes per fixture** — 2D ``ser`` rejected, unphased
  gate failed, digital-filter degraded, solvent excluded, overlap flagged;
* **per-fixture provenance** — id, category, layer, kind, source, file hashes
  and a fixture digest, so Wave 7 can pin what was measured.

The interface is callable **without nmrglue**: fixtures whose raw processing
requires it are marked ``not_verified`` with an explicit reason while
nmrglue-free layers (selection plans, 2D rejection, the failed-gate state
fixture) still run. ``run_benchmark(nmrglue=False)`` exercises that path.

Determinism: no timestamps, canonical JSON serialization, seeded synthetic
FIDs, no environment reads beyond fixture files. Two runs produce byte-equal
output; ``write_metrics`` persists it atomically.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from acp.nmr.models import check_digital_filter
from acp.nmr.spectra import (
    Not1DExperimentError,
    find_bruker_experiments,
    not_1d_reason,
    plan_experiment_selection,
    process_bruker_experiment,
    process_bruker_tree,
)
from acp.nmr.spectra_registry import lookup_processor

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures"
MANIFEST_PATH = FIXTURES_ROOT / "nmr" / "manifest.json"
SCHEMA = "acp-nmr-spectra-benchmark-metrics-v1"
NOT_VERIFIED = "NOT_VERIFIED"

#: Categories the fixture suite must cover (todo-48 acceptance).
REQUIRED_CATEGORIES: tuple[str, ...] = (
    "phase_deviation",
    "digital_filter",
    "solvent_large",
    "impurity",
    "low_snr",
    "overlap",
    "same_nucleus_duplicates",
    "not_1d",
    "real_acquisition",
)

#: Closed vocabulary of per-fixture gating outcomes.
GATING_OUTCOMES: tuple[str, ...] = (
    "processed",
    "gate_degraded",
    "gate_failed",
    "rejected_not_1d",
    "selection_checked",
)

VERIFICATION_STATUSES: tuple[str, ...] = ("verified", "not_verified")
FIXTURE_STATUSES: tuple[str, ...] = ("measured", "not_verified")


# ---------------------------------------------------------------------------
# manifest / provenance helpers
# ---------------------------------------------------------------------------


def load_manifest(path: str | Path | None = None) -> dict[str, Any]:
    """Load the fixture manifest (schema-validated at the top level)."""
    manifest_path = Path(path) if path is not None else MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "acp-nmr-spectra-fixture-manifest-v1":
        raise ValueError(f"unsupported fixture manifest schema: {manifest.get('schema')!r}")
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("fixture manifest carries no fixtures")
    return manifest


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolved_files(entry: Mapping[str, Any]) -> list[tuple[str, Path]]:
    base = FIXTURES_ROOT / str(entry["path"])
    if base.is_file():
        return [(base.name, base)]
    return [(str(name), base / str(name)) for name in entry["files"]]


def _fixture_digest(files: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in files:
        digest.update(f"{record['path']}:{record['sha256']}\n".encode())
    return digest.hexdigest()


def nmrglue_available() -> tuple[bool, str]:
    """Detect the optional nmrglue capability (never raises)."""
    try:
        from acp.nmr.spectra import _import_nmrglue  # noqa: PLC0415

        _import_nmrglue()
    except ImportError as exc:  # pragma: no cover - environment dependent
        return False, f"nmrglue capability unavailable (acp[nmr] extra): {exc}"
    return True, ""


# ---------------------------------------------------------------------------
# serialization
# ---------------------------------------------------------------------------


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """Canonical deterministic JSON bytes (sorted keys, 2-space indent)."""
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")


def write_metrics(metrics: Mapping[str, Any], path: str | Path) -> Path:
    """Atomically persist the metrics JSON; returns the written path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_bytes(canonical_json_bytes(metrics))
    os.replace(temporary, target)
    return target


# ---------------------------------------------------------------------------
# per-fixture blocks
# ---------------------------------------------------------------------------


def _base_result(entry: Mapping[str, Any]) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for relative, absolute in _resolved_files(entry):
        files.append(
            {
                "path": relative,
                "sha256": _sha256_file(absolute),
                "bytes": absolute.stat().st_size,
            }
        )
    declared = entry.get("declared_verification", "verified")
    reasons = (
        [_not_verified_reason(str(item)) for item in entry.get("verification_reasons", ())]
        if declared == "not_verified"
        else []
    )
    return {
        "id": entry["id"],
        "category": entry["category"],
        "kind": entry["kind"],
        "layer": entry["layer"],
        "nucleus": entry.get("nucleus"),
        "requires": entry.get("requires"),
        "path": entry["path"],
        "source": entry["source"],
        "annotations": entry.get("annotations", {}),
        "files": files,
        "sha256": _fixture_digest(files),
        "status": "measured",
        "verification": {"status": declared, "reasons": reasons},
        "processing": None,
        "selection": None,
        "gating": {"outcome": None, "reasons": []},
        "metrics": None,
    }


def _not_verified_reason(reason: str) -> str:
    if not reason or reason.startswith(NOT_VERIFIED):
        return reason
    return f"{NOT_VERIFIED}: {reason}"


def _mark_not_verified(result: dict[str, Any], reason: str) -> None:
    result["status"] = "not_verified"
    verification = result["verification"]
    verification["status"] = "not_verified"
    reason = _not_verified_reason(reason)
    if reason and reason not in verification["reasons"]:
        verification["reasons"].append(reason)


def _processing_block(spectrum: Any) -> dict[str, Any]:
    assessment = spectrum.assessment
    digital_filter = check_digital_filter(spectrum.acquisition, spectrum.processing)
    quality = spectrum.quality
    return {
        "gate_status": assessment.status if assessment is not None else None,
        "reasons": list(assessment.reasons) if assessment is not None else [],
        "phase_method": spectrum.processing.phase_method if spectrum.processing else None,
        "reference_method": spectrum.processing.reference_method if spectrum.processing else None,
        "digital_filter": digital_filter.to_dict() if digital_filter is not None else None,
        "quality": (
            {
                "snr": quality.snr,
                "linewidth_hz": quality.linewidth_hz,
                "baseline_rms": quality.baseline_rms,
            }
            if quality is not None
            else None
        ),
        "n_peaks": len(spectrum.peaks),
    }


def _carbon_annotations(annotations: Mapping[str, Any]) -> list[Any]:
    from acp.nmr.carbon_processor import Annotation  # noqa: PLC0415

    return [
        Annotation(
            position_ppm=float(item["position_ppm"]),
            multiplicity=item.get("multiplicity"),
            intensity=item.get("intensity"),
            label=item.get("label"),
        )
        for item in annotations["resonances"]
    ]


def _proton_annotations(annotations: Mapping[str, Any]) -> list[Any]:
    from acp.nmr.proton_processor import ProtonAnnotation  # noqa: PLC0415

    return [
        ProtonAnnotation(
            center_ppm=float(item["center_ppm"]),
            line_positions_ppm=tuple(float(x) for x in item.get("line_positions_ppm", ())),
            atom_count=item.get("atom_count"),
            label=str(item.get("label", "")),
        )
        for item in annotations["multiplets"]
    ]


def _processor_options(entry: Mapping[str, Any], element: str) -> Any:
    options = entry.get("processor_options") or {}
    if not options:
        return None
    if element == "C":
        from acp.nmr.carbon_processor import CarbonOptions  # noqa: PLC0415

        return CarbonOptions(**options)
    from acp.nmr.proton_processor import ProtonProcessorOptions  # noqa: PLC0415

    return ProtonProcessorOptions(**options)


def _carbon_metrics(entry: Mapping[str, Any], product: Any) -> dict[str, Any]:
    from acp.nmr.carbon_processor import compare_resonances_to_annotations  # noqa: PLC0415

    annotations = entry["annotations"]
    report = compare_resonances_to_annotations(
        product,
        _carbon_annotations(annotations),
        tolerance_ppm=float(annotations.get("tolerance_ppm", 0.05)),
    )
    return {
        "kind": "resonance_precision_recall",
        "tolerance_ppm": report.tolerance_ppm,
        "n_annotations": report.n_annotations,
        "n_resonances": report.n_resonances,
        "matched": len(report.matches),
        "precision": report.precision,
        "recall": report.recall,
        "f1": report.f1,
        "missed_annotations": list(report.missed_annotations),
        "spurious_resonances": list(report.spurious_resonances),
        "uncertain_resonances": list(report.uncertain_resonances),
        "fit_status": product.fit_status,
        "resonances": [
            {
                "shift_ppm": resonance.shift_ppm,
                "multiplicity": resonance.multiplicity,
                "flags": list(resonance.flags),
            }
            for resonance in product.resonances
        ],
        "solvent_assessments": [assessment.to_dict() for assessment in product.solvent_assessments],
    }


def _proton_metrics(entry: Mapping[str, Any], product: Any) -> dict[str, Any]:
    from acp.nmr.proton_processor import compare_proton_grouping  # noqa: PLC0415

    annotations = entry["annotations"]
    tolerance = float(annotations.get("position_tolerance_ppm", 0.05))
    report = compare_proton_grouping(
        product, _proton_annotations(annotations), position_tolerance_ppm=tolerance
    )
    payload = report.to_dict()
    payload.update(
        {
            "kind": "multiplet_grouping",
            "position_tolerance_ppm": tolerance,
            "multiplets": [
                {
                    "multiplet_id": multiplet.multiplet_id,
                    "center_ppm": multiplet.center_ppm,
                    "line_positions_ppm": list(multiplet.line_positions_ppm),
                    "atom_count": multiplet.atom_count,
                    "uncertainty_reasons": list(multiplet.uncertainty_reasons),
                    "low_snr": multiplet.low_snr,
                }
                for multiplet in product.multiplets
            ],
            "overlap_regions": [region.to_dict() for region in product.overlap_regions],
            "constraints": product.constraints.to_dict(),
            "warnings": list(product.warnings),
        }
    )
    return payload


def _outcome_for_assessment(status: str | None) -> str:
    if status == "failed":
        return "gate_failed"
    if status == "degraded":
        return "gate_degraded"
    return "processed"


def _run_bruker_experiment(
    result: dict[str, Any], entry: Mapping[str, Any], nmrglue_ok: bool, reason: str
) -> None:
    if not nmrglue_ok:
        _mark_not_verified(result, reason)
        return
    annotations = entry.get("annotations") or {}
    if not annotations:
        _mark_not_verified(result, "no_annotations: fixture carries no manual annotations")
        return
    exp_dir = FIXTURES_ROOT / str(entry["path"])
    processing_options = entry.get("processing_options") or {}
    trace_sink: list[Any] = []
    spectrum = process_bruker_experiment(
        exp_dir,
        snr_threshold=float(processing_options.get("snr_threshold", 8.0)),
        trace_sink=trace_sink,
    )
    result["processing"] = _processing_block(spectrum)
    assessment_status = spectrum.assessment.status if spectrum.assessment is not None else None
    result["gating"] = {
        "outcome": _outcome_for_assessment(assessment_status),
        "reasons": list(spectrum.assessment.reasons) if spectrum.assessment is not None else [],
    }
    descriptor = lookup_processor(spectrum.element)
    if descriptor is None:  # pragma: no cover - registry always covers C/H
        _mark_not_verified(result, f"no processor registered for {spectrum.element!r}")
        return
    options = _processor_options(entry, spectrum.element)
    if assessment_status == "failed":
        # The gate must refuse a failed spectrum; capture the typed rejection.
        try:
            descriptor.process(spectrum)
        except ValueError as exc:
            result["processor_rejection"] = {
                "processor": descriptor.processor_id,
                "error": type(exc).__name__,
                "message": str(exc),
            }
        else:  # pragma: no cover - a regression would land here
            result["gating"]["reasons"].append("processor_accepted_failed_spectrum")
        return
    trace = trace_sink[0].as_pair() if trace_sink else None
    if spectrum.element == "C":
        product = descriptor.process(spectrum, trace=trace, options=options)
        result["metrics"] = _carbon_metrics(entry, product)
    else:
        product = descriptor.process(spectrum, options=options)
        result["metrics"] = _proton_metrics(entry, product)


def _run_processed_spectrum(
    result: dict[str, Any], entry: Mapping[str, Any], nmrglue_ok: bool, reason: str
) -> None:
    from acp.nmr.models import ProcessedSpectrum  # noqa: PLC0415

    path = FIXTURES_ROOT / str(entry["path"])
    spectrum = ProcessedSpectrum.from_dict(json.loads(path.read_text(encoding="utf-8")))
    result["processing"] = _processing_block(spectrum)
    assessment_status = spectrum.assessment.status if spectrum.assessment is not None else None
    result["gating"] = {
        "outcome": _outcome_for_assessment(assessment_status),
        "reasons": list(spectrum.assessment.reasons) if spectrum.assessment is not None else [],
    }
    descriptor = lookup_processor(spectrum.element)
    if descriptor is not None:
        try:
            descriptor.process(spectrum)
        except ValueError as exc:
            result["processor_rejection"] = {
                "processor": descriptor.processor_id,
                "error": type(exc).__name__,
                "message": str(exc),
            }
        else:  # pragma: no cover - a regression would land here
            result["gating"]["reasons"].append("processor_accepted_failed_spectrum")


def _run_not_1d(
    result: dict[str, Any], entry: Mapping[str, Any], nmrglue_ok: bool, reason: str
) -> None:
    from acp.nmr.spectra import ExperimentSelectionError  # noqa: PLC0415

    root = FIXTURES_ROOT / str(entry["path"])
    exp_dirs = find_bruker_experiments(root)
    dimension_reasons = {directory.name: not_1d_reason(directory) for directory in exp_dirs}
    rejected = {name: reason for name, reason in dimension_reasons.items() if reason}
    result["processing"] = {
        "not_1d": dimension_reasons,
        "n_peaks": 0,
        "gate_status": None,
    }
    first_reason = str(next(iter(rejected.values()), None))
    result["gating"] = {"outcome": "rejected_not_1d", "reasons": [r for r in [first_reason] if r]}

    # Plan level (nmrglue-free): the mixed tree records the 2D experiment as
    # rejected, and explicitly selecting it raises the typed error.
    plan = plan_experiment_selection(exp_dirs, root=root)
    result["selection"] = {"default": _selection_block(plan), "explicit_multi": None}
    if rejected:
        ser_dir = next(directory for directory in exp_dirs if directory.name in rejected)
        try:
            plan_experiment_selection(
                exp_dirs,
                root=root,
                select_experiments={"1H": ser_dir.name},
            )
        except (Not1DExperimentError, ExperimentSelectionError) as exc:
            result["explicit_selection_rejection"] = {
                "error": type(exc).__name__,
                "reason": getattr(exc, "reason", str(exc)),
            }
        else:  # pragma: no cover - a regression would land here
            result["gating"]["reasons"].append("explicit_selection_accepted_non_1d")

    # Stand-alone 2D directory: the tree refuses outright (nmrglue-free).
    if rejected:
        ser_dir = next(directory for directory in exp_dirs if directory.name in rejected)
        try:
            process_bruker_tree(ser_dir)
        except Not1DExperimentError as exc:
            result["tree_rejection"] = {"error": type(exc).__name__, "reason": exc.reason}
        else:  # pragma: no cover - a regression would land here
            result["gating"]["reasons"].append("tree_accepted_non_1d_experiment")

    # Mixed-tree processing (peaks come only from the 1D companion).
    if nmrglue_ok:
        mixed = process_bruker_tree(root)
        result["selection"]["default"]["peaks_by_element"] = _peaks_by_element(mixed)
        result["processing"]["tree_experiments"] = [
            {
                "label": record.label,
                "element": record.element,
                "status": record.status,
                "reason": record.reason,
            }
            for record in mixed.experiments
        ]
    else:
        result["processing"]["tree_processing_skipped"] = reason


def _selection_block(plan: Any, keys: Sequence[str] | None = None) -> dict[str, Any]:
    return {
        "selection_keys": list(keys) if keys else None,
        "nuclei": [record.to_dict() for record in plan.nuclei],
        "experiments": [
            {
                "label": record.label,
                "element": record.element,
                "status": record.status,
                "reason": record.reason,
                "user_requested": record.user_requested,
            }
            for record in plan.experiments
        ],
    }


def _peaks_by_element(result: Any) -> dict[str, list[float]]:
    return {
        element: [round(peak.shift_ppm, 4) for peak in peaks]
        for element, peaks in sorted(result.experiment.peaks.items())
    }


def _run_bruker_tree(
    result: dict[str, Any], entry: Mapping[str, Any], nmrglue_ok: bool, reason: str
) -> None:
    root = FIXTURES_ROOT / str(entry["path"])
    exp_dirs = find_bruker_experiments(root)
    default_plan = plan_experiment_selection(exp_dirs, root=root)
    h_labels = sorted(
        record.label
        for record in default_plan.experiments
        if record.element == "H" and record.status != "rejected"
    )
    explicit_plan = plan_experiment_selection(
        exp_dirs, root=root, select_experiments={"1H": h_labels}
    )
    selection = {
        "default": _selection_block(default_plan),
        "explicit_multi": _selection_block(explicit_plan, keys=h_labels),
    }
    result["selection"] = selection
    result["gating"] = {"outcome": "selection_checked", "reasons": []}
    if not nmrglue_ok:
        selection["default"]["peaks_by_element"] = None
        selection["explicit_multi"]["peaks_by_element"] = None
        _mark_not_verified(result, reason)
        return
    default_result = process_bruker_tree(root)
    explicit_result = process_bruker_tree(root, select_experiments={"1H": h_labels})
    selection["default"]["peaks_by_element"] = _peaks_by_element(default_result)
    selection["explicit_multi"]["peaks_by_element"] = _peaks_by_element(explicit_result)
    statuses = [
        spectrum.assessment.status
        for spectrum in default_result.spectra
        if spectrum.assessment is not None
    ]
    result["processing"] = {
        "gate_status": "failed"
        if "failed" in statuses
        else ("degraded" if "degraded" in statuses else "ok"),
        "reasons": sorted(
            {
                reason
                for spectrum in default_result.spectra
                if spectrum.assessment is not None
                for reason in spectrum.assessment.reasons
            }
        ),
        "n_peaks": sum(len(peaks) for peaks in default_result.experiment.peaks.values()),
    }


_RUNNERS = {
    "bruker_experiment": _run_bruker_experiment,
    "processed_spectrum": _run_processed_spectrum,
    "not_1d": _run_not_1d,
    "bruker_tree": _run_bruker_tree,
}


# ---------------------------------------------------------------------------
# benchmark
# ---------------------------------------------------------------------------


def run_fixture(
    entry: Mapping[str, Any], *, nmrglue_ok: bool, nmrglue_reason: str
) -> dict[str, Any]:
    """Run one manifest fixture through the production seams."""
    result = _base_result(entry)
    runner = _RUNNERS.get(str(entry["kind"]))
    if runner is None:
        raise ValueError(f"unsupported fixture kind: {entry['kind']!r}")
    runner(result, entry, nmrglue_ok, nmrglue_reason)
    if result["status"] not in FIXTURE_STATUSES:
        raise AssertionError(f"invalid fixture status: {result['status']!r}")
    if result["verification"]["status"] not in VERIFICATION_STATUSES:
        raise AssertionError(f"invalid verification status: {result['verification']['status']!r}")
    if (
        result["gating"]["outcome"] is not None
        and result["gating"]["outcome"] not in GATING_OUTCOMES
    ):
        raise AssertionError(f"invalid gating outcome: {result['gating']['outcome']!r}")
    return result


def run_benchmark(
    *,
    manifest_path: str | Path | None = None,
    nmrglue: bool | None = None,
) -> dict[str, Any]:
    """Run every fixture and return the deterministic metrics JSON.

    Args:
        manifest_path: Override the manifest location (tests use this for the
            missing-annotation negative path).
        nmrglue: Force the nmrglue capability (``None`` auto-detects); with
            ``False`` the nmrglue-gated layers are skipped explicitly.
    """
    manifest = load_manifest(manifest_path)
    available, reason = nmrglue_available()
    if nmrglue is not None:
        available = bool(nmrglue)
        reason = reason if available else (reason or "nmrglue capability disabled by caller")
    fixture_results = [
        run_fixture(entry, nmrglue_ok=available, nmrglue_reason=reason)
        for entry in manifest["fixtures"]
    ]
    categories = sorted({entry["category"] for entry in manifest["fixtures"]})
    outcomes = Counter(
        result["gating"]["outcome"] for result in fixture_results if result["gating"]["outcome"]
    )
    return {
        "schema": SCHEMA,
        "manifest": {
            "path": "tests/fixtures/nmr/manifest.json",
            "schema": manifest["schema"],
            "sha256": _sha256_file(
                Path(manifest_path) if manifest_path is not None else MANIFEST_PATH
            ),
        },
        "nmrglue": {"available": available, "reason": reason or None},
        "tolerances": dict(manifest["tolerances"]),
        "summary": {
            "n_fixtures": len(fixture_results),
            "categories": categories,
            "layers": dict(Counter(result["layer"] for result in fixture_results)),
            "statuses": dict(Counter(result["status"] for result in fixture_results)),
            "verification": dict(
                Counter(result["verification"]["status"] for result in fixture_results)
            ),
            "gating_outcomes": dict(sorted(outcomes.items())),
        },
        "fixtures": fixture_results,
    }


__all__ = [
    "FIXTURES_ROOT",
    "GATING_OUTCOMES",
    "MANIFEST_PATH",
    "NOT_VERIFIED",
    "REQUIRED_CATEGORIES",
    "SCHEMA",
    "canonical_json_bytes",
    "load_manifest",
    "nmrglue_available",
    "run_benchmark",
    "run_fixture",
    "write_metrics",
]
