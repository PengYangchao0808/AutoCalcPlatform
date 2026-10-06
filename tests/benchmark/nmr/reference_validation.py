"""Reference-validation comparison + reproducibility receipts (todo 54 / gap §10.3 trial A).

Gap §10.3 trial A asks whether the ACP migration reproduces the ORIGINAL
reference data: fixed structure, mapping, shieldings, weights, assets — no
sampling comparison.  This module wires the two committed reference sources
into one comparison table:

* **todo 31** (``acp.nmr.reference_validation``) — the pinned upstream settings
  (TMS reference shieldings from ``tms_references.txt``, the Goodman DP4
  sigmas, the DP5 folded-residual count) whose values are juxtaposed against
  the values the migrated runtime path actually produces
  (``acp.nmr.models.lookup_tms_shieldings`` /
  ``acp.nmr.error_model.GoodmanErrorModel.SIGMA`` /
  ``GoodmanDP5Model.folded_errors``);
* **todo 36** (``tests/baseline/nmr/fchl_golden.json``) — the nine layered
  FCHL golden values juxtaposed against a FRESH recomputation of
  ``tests.test_acp_nmr_fchl_golden.compute_layer_values`` with the frozen
  per-layer tolerances from ``fchl_golden_tolerances.json``.

Every row carries ``reference_value`` / ``acp_value`` / ``delta`` /
``tolerance`` and a three-state verdict (``match`` / ``mismatch`` /
``not_verified``).  Reproducibility is asserted from **hashes + measured
values**, never from a profile/revision label: the asset-hash manifest
records every consumed asset (path, sha256, size, NOTICE pin + golden/
tolerance pin match), and a present-but-changed asset is refused with the
typed :class:`ReferenceAssetHashMismatchError` before any row is produced.

Three-state discipline (never a formal calibration status):

* a missing reference asset/value makes the dependent rows ``not_verified``
  with an explicit reason (missing TMS asset, missing golden/tolerances,
  ``acp_values_not_supplied``, ``tolerance_entry_status_NOT_VERIFIED``);
* the table deliberately carries no ``calibration_status`` /
  ``validated`` / ``acp_calibrated`` field — the top-level ``claim`` states
  that this is an implementation-parity comparison, NOT a calibration or
  accuracy claim.

Determinism / reproducibility entry point:

* :func:`build_reference_validation_table` returns canonical JSON-safe data;
* :func:`write_reference_validation_table` persists it via the todo-48
  canonical atomic writer;
* ``python3.11 -m tests.benchmark.nmr.reference_validation --output T.json
  --now 2026-10-06T00:00:00+00:00`` regenerates the table byte-identically
  across processes (pinned clock, sorted keys, seeded nothing, no wall-clock
  reads inside the payload besides the recorded timestamp).

The harness never starts a QC subprocess: the ACP side is either read from
already-committed pickles/constants or recomputed from the NOTICE-pinned
FCHL assets (the same bounded slice conventions as
``tests/test_acp_nmr_fchl_golden.py`` — never the full 53 208-atom kernel).

Handoff: todo 55/56 (ShiftPredictor / DP5q isolation) consume
:func:`build_reference_validation_table` + the typed errors to prove that no
new predictor changes the reference comparison silently.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import math
import pickle
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from acp.nmr.error_model import GoodmanDP5Model, GoodmanErrorModel
from acp.nmr.models import lookup_tms_shieldings
from acp.nmr.reference_validation import ASSET_FILES, PINNED_GOODMAN_UPSTREAM
from tests.benchmark.nmr.harness import REPO_ROOT, _code_provenance
from tests.nmr_spectra_benchmark import canonical_json_bytes, write_metrics

REFERENCE_VALIDATION_SCHEMA: Final = "acp-nmr-reference-validation-comparison-v1"

#: Three-state verdict vocabulary (never a fourth "assumed" state).
VERDICTS: Final[tuple[str, ...]] = ("match", "mismatch", "not_verified")

#: The two reference sources the table must represent (no single-source shortcut).
SOURCES: Final[tuple[str, ...]] = ("todo31_pinned_upstream", "todo36_fchl_golden")

#: Exact tolerance for pinned values the migration reads from the same asset
#: (TMS shieldings, DP4 sigmas, folded-residual count): a measured drift is a
#: mismatch, not a rounding artifact to forgive.
EXACT_TOLERANCE: Final[dict[str, float]] = {"rtol": 0.0, "atol": 0.0}

GOLDEN_FILENAME: Final = "fchl_golden.json"
TOLERANCES_FILENAME: Final = "fchl_golden_tolerances.json"
NOTICE_FILENAME: Final = "NOTICE.md"

DEFAULT_MODELS_DIR: Final = REPO_ROOT / "src" / "acp" / "nmr" / "models"
DEFAULT_GOLDEN_PATH: Final = REPO_ROOT / "tests" / "baseline" / "nmr" / GOLDEN_FILENAME
DEFAULT_TOLERANCES_PATH: Final = REPO_ROOT / "tests" / "baseline" / "nmr" / TOLERANCES_FILENAME
DEFAULT_NOTICE_PATH: Final = DEFAULT_MODELS_DIR / NOTICE_FILENAME

_GENERATOR: Final = "tests/benchmark/nmr/reference_validation.py"
_CLAIM: Final = (
    "implementation-parity comparison only — NOT a calibration, validation, or accuracy claim"
)

#: NOTICE.md pin lines look like ``<backticked name> = `<64-hex digest>```.
_NOTICE_PIN_RE: Final = re.compile(r"`([^`]+?)`\s*=\s*`([0-9a-f]{64})`")


class ReferenceValidationError(ValueError):
    """Base class for typed reference-validation failures."""


class ReferenceAssetError(ReferenceValidationError):
    """A consumed reference artifact is unreadable or malformed."""


class ReferenceAssetHashMismatchError(ReferenceValidationError):
    """A present asset differs from a pinned sha256 — refuse to reuse it."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _display_path(path: Path) -> str:
    """Repo-relative POSIX path when possible (keeps receipts portable)."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


@functools.lru_cache(maxsize=32)
def _digest_cached(path_key: str, size: int, mtime_ns: int) -> str:
    del size, mtime_ns  # cache key only — content is re-read on any stat change
    digest = hashlib.sha256()
    with Path(path_key).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    stat = path.stat()
    return _digest_cached(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def _read_json_asset(path: Path, *, role: str) -> dict[str, Any] | None:
    """Parse a JSON asset; ``None`` when absent, typed error when malformed."""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReferenceAssetError(f"{role} asset at {path} is not readable JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReferenceAssetError(f"{role} asset at {path} must be a JSON object")
    return payload


def _resolve_timestamp(now: str | None) -> str:
    if now is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not isinstance(now, str) or not now.strip():
        raise ReferenceValidationError("now must be null or a non-empty ISO timestamp string")
    return now


def parse_notice_pins(notice_path: str | Path | None = None) -> dict[str, str]:
    """sha256 pins declared in ``NOTICE.md`` (``{}`` when the file is absent).

    The NOTICE document is the provenance anchor for the DP5 redistribution;
    a missing/unreadable file yields no pins (the manifest then records
    ``notice_pin: null`` / ``notice_pin_match: null`` — honest three-state,
    never a fabricated hash).
    """
    path = Path(notice_path) if notice_path is not None else DEFAULT_NOTICE_PATH
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReferenceAssetError(f"NOTICE asset at {path} is unreadable: {exc}") from exc
    return {name: digest for name, digest in _NOTICE_PIN_RE.findall(text)}


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _flatten_numbers(value: Any, *, where: str) -> list[float]:
    if isinstance(value, bool):
        raise ReferenceValidationError(f"{where} must be numeric, got bool")
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, (list, tuple)):
        flattened: list[float] = []
        for item in value:
            flattened.extend(_flatten_numbers(item, where=where))
        if not flattened:
            raise ReferenceValidationError(f"{where} must not be empty")
        return flattened
    raise ReferenceValidationError(
        f"{where} must be a number or nested list, got {type(value).__name__}"
    )


def _validated_tolerance(tolerance: Mapping[str, float]) -> tuple[float, float]:
    if not isinstance(tolerance, Mapping):
        raise ReferenceValidationError("tolerance must be a mapping with rtol/atol")
    values: list[float] = []
    for key in ("rtol", "atol"):
        raw = tolerance.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ReferenceValidationError(f"tolerance.{key} must be a finite non-negative number")
        number = float(raw)
        if not math.isfinite(number) or number < 0:
            raise ReferenceValidationError(f"tolerance.{key} must be a finite non-negative number")
        values.append(number)
    return values[0], values[1]


def compare_numeric_values(
    reference: Any,
    acp: Any,
    *,
    tolerance: Mapping[str, float],
) -> dict[str, Any]:
    """Elementwise comparison with the frozen tolerance rule.

    A row matches iff every entry satisfies ``|acp - reference| <=
    atol + rtol * |reference|`` (the exact rule the todo-36 layered golden
    uses, so a lower-layer breach cannot hide behind a passing final value).
    Scalars report the signed difference (``acp - reference``); vectors
    report the maximum absolute deviation plus the worst flat index.
    Non-finite input or a shape mismatch is a typed/honest outcome — never a
    silent match.
    """
    rtol, atol = _validated_tolerance(tolerance)
    ref = _flatten_numbers(reference, where="reference value")
    got = _flatten_numbers(acp, where="acp value")
    if len(ref) != len(got):
        raise ReferenceValidationError(
            f"reference and acp values have different length ({len(ref)} != {len(got)})"
        )
    if not all(math.isfinite(value) for value in (*ref, *got)):
        return {
            "verdict": "not_verified",
            "reasons": ["non_finite_value"],
            "delta": None,
            "delta_kind": None,
            "n_entries": len(ref),
            "n_exceeding": None,
            "worst_flat_index": None,
        }
    deviations = [abs(got[index] - ref[index]) for index in range(len(ref))]
    allowed = [atol + rtol * abs(ref[index]) for index in range(len(ref))]
    n_exceeding = sum(
        1 for deviation, bound in zip(deviations, allowed, strict=True) if deviation > bound
    )
    worst = deviations.index(max(deviations))
    verdict = "match" if n_exceeding == 0 else "mismatch"
    if _is_scalar(reference):
        return {
            "verdict": verdict,
            "reasons": [],
            "delta": got[0] - ref[0],
            "delta_kind": "signed_difference",
            "n_entries": 1,
            "n_exceeding": n_exceeding,
            "worst_flat_index": worst,
        }
    return {
        "verdict": verdict,
        "reasons": [],
        "delta": deviations[worst],
        "delta_kind": "max_abs_difference",
        "n_entries": len(ref),
        "n_exceeding": n_exceeding,
        "worst_flat_index": worst,
    }


# ---------------------------------------------------------------------------
# asset-hash manifest (every consumed asset: path, sha256, size, pin match)
# ---------------------------------------------------------------------------


def _asset_entry(
    *,
    name: str,
    role: str,
    path: Path,
    notice_pin: str | None,
    golden_pin: str | None,
    tolerance_pin: str | None,
) -> dict[str, Any]:
    exists = path.is_file()
    sha256 = _sha256_file(path) if exists else None
    size_bytes = path.stat().st_size if exists else None

    def _match(pin: str | None) -> bool | None:
        if pin is None or sha256 is None:
            return None
        return sha256 == str(pin).lower()

    notice_pin_match = _match(notice_pin)
    golden_pin_match = _match(golden_pin)
    tolerance_pin_match = _match(tolerance_pin)
    declared = [
        match
        for match in (notice_pin_match, golden_pin_match, tolerance_pin_match)
        if match is not None
    ]
    return {
        "name": name,
        "role": role,
        "path": _display_path(path),
        "exists": exists,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "notice_pin": notice_pin,
        "notice_pin_match": notice_pin_match,
        "golden_pin": golden_pin,
        "golden_pin_match": golden_pin_match,
        "tolerance_pin": tolerance_pin,
        "tolerance_pin_match": tolerance_pin_match,
        "pins_verified": all(declared) if declared else None,
    }


def collect_asset_manifest(
    *,
    models_dir: str | Path | None = None,
    golden_path: str | Path | None = None,
    tolerances_path: str | Path | None = None,
    notice_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Hash every consumed asset and verify its declared pins.

    Raises:
        ReferenceAssetHashMismatchError: a PRESENT asset differs from a
            declared pin (NOTICE.md sha256, the golden's ``asset_sha256``
            block, or the tolerance file's ``golden_sha256``).  A relabelled
            asset with changed bytes can therefore never be reused.
        ReferenceAssetError: a present JSON asset is malformed/unreadable.
    """
    models = Path(models_dir) if models_dir is not None else DEFAULT_MODELS_DIR
    golden = Path(golden_path) if golden_path is not None else DEFAULT_GOLDEN_PATH
    tolerances = Path(tolerances_path) if tolerances_path is not None else DEFAULT_TOLERANCES_PATH
    notice = Path(notice_path) if notice_path is not None else DEFAULT_NOTICE_PATH

    notice_pins = parse_notice_pins(notice)
    golden_doc = _read_json_asset(golden, role="fchl_golden")
    tolerances_doc = _read_json_asset(tolerances, role="fchl_golden_tolerances")
    golden_asset_pins = dict(golden_doc.get("asset_sha256") or {}) if golden_doc else {}
    tolerance_golden_pin: str | None = None
    if tolerances_doc is not None:
        raw_pin = tolerances_doc.get("golden_sha256")
        tolerance_golden_pin = str(raw_pin) if raw_pin else None

    entries: list[dict[str, Any]] = [
        _asset_entry(
            name=name,
            role="dp5_model_asset",
            path=models / name,
            notice_pin=notice_pins.get(name),
            golden_pin=golden_asset_pins.get(name),
            tolerance_pin=None,
        )
        for name in ASSET_FILES
    ]
    entries.append(
        _asset_entry(
            name=golden.name,
            role="fchl_golden",
            path=golden,
            notice_pin=None,
            golden_pin=None,
            tolerance_pin=tolerance_golden_pin,
        )
    )
    entries.append(
        _asset_entry(
            name=tolerances.name,
            role="fchl_golden_tolerances",
            path=tolerances,
            notice_pin=None,
            golden_pin=None,
            tolerance_pin=None,
        )
    )
    entries.append(
        _asset_entry(
            name=notice.name,
            role="notice",
            path=notice,
            notice_pin=None,
            golden_pin=None,
            tolerance_pin=None,
        )
    )

    mismatches: list[str] = []
    pin_sources = (
        ("NOTICE.md", "notice_pin"),
        ("fchl_golden.json#asset_sha256", "golden_pin"),
        ("fchl_golden_tolerances.json#golden_sha256", "tolerance_pin"),
    )
    for entry in entries:
        if not entry["exists"]:
            continue
        for source, pin_key in pin_sources:
            pin = entry[pin_key]
            if pin is not None and pin != entry["sha256"]:
                mismatches.append(
                    f"{entry['name']}: expected {pin} ({source}), actual {entry['sha256']}"
                )
    if mismatches:
        raise ReferenceAssetHashMismatchError(
            "refusing to reuse reference assets with mismatched pinned hashes: "
            + "; ".join(mismatches)
        )
    return entries


# ---------------------------------------------------------------------------
# ACP side (values the migration actually produces)
# ---------------------------------------------------------------------------


def _resolve_acp_layers(
    acp_layers: Mapping[str, Any] | None,
    recompute: bool,
) -> tuple[dict[str, Any] | None, str, str | None]:
    """(layer values, source label, reason) for the todo-36 ACP side."""
    if acp_layers is not None:
        return dict(acp_layers), "supplied", None
    if not recompute:
        return None, "unavailable", "acp_values_not_supplied"
    try:
        from tests.test_acp_nmr_fchl_golden import compute_layer_values
    except ImportError as exc:  # pragma: no cover - tests package always importable here
        return None, "unavailable", f"acp_layer_recomputation_failed: {exc}"
    try:
        with tempfile.TemporaryDirectory(prefix="acp-nmr-reference-validation-") as temporary:
            values = compute_layer_values(Path(temporary))
    except (OSError, EOFError) as exc:
        return None, "unavailable", f"acp_layer_recomputation_failed: {type(exc).__name__}: {exc}"
    return dict(values), "recomputed", None


@functools.lru_cache(maxsize=8)
def _folded_residual_count_cached(models_dir_key: str, size: int, mtime_ns: int) -> int:
    del size, mtime_ns  # cache key only
    model = GoodmanDP5Model(models_dir=Path(models_dir_key))
    return int(len(model.folded_errors))


def _acp_folded_residual_count(models_dir: Path) -> int | None:
    folded = models_dir / "folded_scaled_errors.p"
    if not folded.is_file():
        return None
    try:
        stat = folded.stat()
        return _folded_residual_count_cached(
            str(models_dir.resolve()), stat.st_size, stat.st_mtime_ns
        )
    except (OSError, EOFError, pickle.UnpicklingError, ValueError):
        return None


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------


def _entry_count(reference: Any) -> int:
    if reference is None:
        return 0
    try:
        return len(_flatten_numbers(reference, where="reference value"))
    except ReferenceValidationError:
        return 0


def _value_kind(reference: Any) -> str:
    if reference is None:
        return "unmeasured"
    return "scalar" if _is_scalar(reference) else "vector"


def _comparison_row(
    *,
    source: str,
    row_id: str,
    reference: Any | None,
    acp: Any | None,
    tolerance: Mapping[str, float] | None,
    reference_provenance: str,
    acp_provenance: str,
    units: str | None = None,
    reasons: Sequence[str] = (),
) -> dict[str, Any]:
    reason_list = [str(reason) for reason in reasons]
    base: dict[str, Any] = {
        "source": source,
        "row_id": row_id,
        "kind": _value_kind(reference),
        "units": units,
        "reference_value": reference,
        "acp_value": acp,
        "tolerance": dict(tolerance) if tolerance is not None else None,
        "reference_provenance": reference_provenance,
        "acp_provenance": acp_provenance,
    }
    if reason_list or reference is None or acp is None or tolerance is None:
        if not reason_list:
            reason_list = ["reference_or_acp_value_missing"]
        return {
            **base,
            "delta": None,
            "delta_kind": None,
            "n_entries": _entry_count(reference),
            "n_exceeding": None,
            "worst_flat_index": None,
            "verdict": "not_verified",
            "reasons": reason_list,
        }
    return {**base, **compare_numeric_values(reference, acp, tolerance=tolerance)}


def _t31_rows(
    models_dir: Path,
    manifest_by_name: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pinned upstream settings vs the migrated runtime path (todo 31)."""
    pinned = PINNED_GOODMAN_UPSTREAM
    method = str(pinned.value("nmr_method"))
    basis = str(pinned.value("nmr_basis"))
    solvent = str(pinned.value("solvent"))
    tms_entry = manifest_by_name.get("tms_references.txt", {})
    tms_available = bool(tms_entry.get("exists"))
    folded_entry = manifest_by_name.get("folded_scaled_errors.p", {})
    folded_available = bool(folded_entry.get("exists"))
    rows: list[dict[str, Any]] = []

    tms_specs = (
        ("tms_reference_13c_chloroform", "13C", solvent, 0),
        ("tms_reference_1h_chloroform", "1H", solvent, 1),
        ("tms_reference_13c_gas", "13C", "none", 0),
        ("tms_reference_1h_gas", "1H", "none", 1),
    )
    for row_id, nucleus, query_solvent, index in tms_specs:
        setting = pinned.get(row_id)
        reference = float(setting.value)
        reasons: list[str] = []
        acp: float | None = None
        if not tms_available:
            reasons.append("reference_asset_missing: tms_references.txt")
        else:
            sigma_c, sigma_h = lookup_tms_shieldings(method, basis, query_solvent)
            acp = sigma_c if index == 0 else sigma_h
            if acp is None:
                reasons.append(
                    "acp_value_unavailable: lookup_tms_shieldings("
                    f"{method!r}, {basis!r}, {query_solvent!r})"
                )
        rows.append(
            _comparison_row(
                source="todo31_pinned_upstream",
                row_id=row_id,
                reference=reference,
                acp=acp,
                tolerance=EXACT_TOLERANCE,
                units="ppm",
                reference_provenance=setting.source,
                acp_provenance=(
                    f"acp.nmr.models.lookup_tms_shieldings({method!r}, {basis!r}, "
                    f"{query_solvent!r})[{index}]"
                ),
                reasons=reasons,
            )
        )

    sigma_specs = (("dp4_sigma_13c", "13C"), ("dp4_sigma_1h", "1H"))
    for row_id, nucleus in sigma_specs:
        setting = pinned.get(row_id)
        reference = float(setting.value)
        acp = GoodmanErrorModel.SIGMA.get(nucleus)
        reasons = (
            []
            if acp is not None
            else [f"acp_value_unavailable: GoodmanErrorModel.SIGMA[{nucleus!r}]"]
        )
        rows.append(
            _comparison_row(
                source="todo31_pinned_upstream",
                row_id=row_id,
                reference=reference,
                acp=acp,
                tolerance=EXACT_TOLERANCE,
                units="ppm",
                reference_provenance=setting.source,
                acp_provenance=f"acp.nmr.error_model.GoodmanErrorModel.SIGMA[{nucleus!r}]",
                reasons=reasons,
            )
        )

    count_setting = pinned.get("dp5_folded_scaled_residuals")
    count_reference = float(count_setting.value)
    count_reasons: list[str] = []
    count_acp: int | None = None
    if not folded_available:
        count_reasons.append("reference_asset_missing: folded_scaled_errors.p")
    else:
        count_acp = _acp_folded_residual_count(models_dir)
        if count_acp is None:
            count_reasons.append(
                "acp_value_unavailable: acp.nmr.error_model.GoodmanDP5Model.folded_errors"
            )
    rows.append(
        _comparison_row(
            source="todo31_pinned_upstream",
            row_id="dp5_folded_scaled_residuals",
            reference=count_reference,
            acp=count_acp,
            tolerance=EXACT_TOLERANCE,
            units="count",
            reference_provenance=count_setting.source,
            acp_provenance=(
                "len(acp.nmr.error_model.GoodmanDP5Model("
                f"models_dir={_display_path(models_dir)!r}).folded_errors)"
            ),
            reasons=count_reasons,
        )
    )
    return rows


def _tolerance_entry(entry: Mapping[str, Any]) -> dict[str, float] | None:
    if "rtol" not in entry or "atol" not in entry:
        return None
    return {"rtol": float(entry["rtol"]), "atol": float(entry["atol"])}


def _t36_rows(
    *,
    golden_doc: Mapping[str, Any] | None,
    tolerances_doc: Mapping[str, Any] | None,
    layers: Mapping[str, Any] | None,
    layers_reason: str | None,
    golden_path: Path,
    tolerances_path: Path,
) -> list[dict[str, Any]]:
    """Layered FCHL golden vs a fresh ACP recomputation (todo 36)."""
    golden_layers = dict(golden_doc.get("golden") or {}) if golden_doc else {}
    tolerance_layers = dict(tolerances_doc.get("layers") or {}) if tolerances_doc else {}
    golden_name = golden_path.name
    tolerances_name = tolerances_path.name
    rows: list[dict[str, Any]] = []
    for key in sorted(set(golden_layers) | set(tolerance_layers)):
        entry = tolerance_layers.get(key) or {}
        tolerance = _tolerance_entry(entry)
        status = entry.get("status")
        reference = golden_layers.get(key)
        note = str(entry.get("note", "")).strip()
        row_reasons: list[str] = []
        if status is not None and str(status).lower() not in ("measured", "verified"):
            row_reasons.append(f"tolerance_entry_status_{status}: {note or 'no note recorded'}")
        elif reference is None:
            if golden_doc is None:
                row_reasons.append(f"reference_asset_missing: {golden_name}")
            else:
                row_reasons.append(f"golden_layer_missing: {key}")
        elif tolerance is None:
            if tolerances_doc is None:
                row_reasons.append(f"reference_asset_missing: {tolerances_name}")
            else:
                row_reasons.append(f"tolerance_missing: {key}")
        elif layers is None:
            row_reasons.append(layers_reason or f"acp_value_missing: {key}")
        elif key not in layers:
            row_reasons.append(f"acp_value_missing: {key}")

        acp = layers.get(key) if layers is not None else None
        if reference is None:
            reference_provenance = (
                f"{_display_path(tolerances_path)}#layers.{key}"
                if key in tolerance_layers
                else f"{_display_path(golden_path)}#golden.{key}"
            )
        else:
            reference_provenance = f"{_display_path(golden_path)}#golden.{key}"
        rows.append(
            _comparison_row(
                source="todo36_fchl_golden",
                row_id=key,
                reference=reference,
                acp=acp,
                tolerance=tolerance,
                reference_provenance=reference_provenance,
                acp_provenance=(
                    "tests.test_acp_nmr_fchl_golden.compute_layer_values"
                    if layers is None
                    else "supplied acp_layers"
                ),
                reasons=row_reasons,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# table assembly
# ---------------------------------------------------------------------------


def build_reference_validation_table(
    *,
    models_dir: str | Path | None = None,
    golden_path: str | Path | None = None,
    tolerances_path: str | Path | None = None,
    notice_path: str | Path | None = None,
    acp_layers: Mapping[str, Any] | None = None,
    recompute: bool = True,
    now: str | None = None,
) -> dict[str, Any]:
    """Build the reference-vs-ACP comparison table + asset-hash manifest.

    Args:
        models_dir: Directory holding the NOTICE-listed DP5 assets (default:
            the committed ``src/acp/nmr/models/``).
        golden_path: todo-36 layered golden (default: committed baseline).
        tolerances_path: todo-36 frozen tolerances (default: committed).
        notice_path: NOTICE.md pin source (default: models dir).
        acp_layers: Precomputed ACP layer values; when omitted and
            ``recompute`` is true the layers are recomputed with
            :func:`tests.test_acp_nmr_fchl_golden.compute_layer_values`.
        recompute: Disable to record ``acp_values_not_supplied`` rows instead
            of running the bounded recomputation.
        now: Pinned ISO timestamp for deterministic replay.

    Raises:
        ReferenceAssetHashMismatchError: a present asset differs from a
            declared pin (refusal to reuse, before any row is produced).
        ReferenceAssetError: a present JSON asset is malformed.
    """
    models = Path(models_dir) if models_dir is not None else DEFAULT_MODELS_DIR
    golden = Path(golden_path) if golden_path is not None else DEFAULT_GOLDEN_PATH
    tolerances = Path(tolerances_path) if tolerances_path is not None else DEFAULT_TOLERANCES_PATH
    notice = Path(notice_path) if notice_path is not None else DEFAULT_NOTICE_PATH

    manifest = collect_asset_manifest(
        models_dir=models,
        golden_path=golden,
        tolerances_path=tolerances,
        notice_path=notice,
    )
    manifest_by_name = {entry["name"]: entry for entry in manifest}
    golden_doc = _read_json_asset(golden, role="fchl_golden")
    tolerances_doc = _read_json_asset(tolerances, role="fchl_golden_tolerances")
    layers, layers_source, layers_reason = _resolve_acp_layers(acp_layers, recompute)
    timestamp = _resolve_timestamp(now)

    rows = _t31_rows(models, manifest_by_name) + _t36_rows(
        golden_doc=golden_doc,
        tolerances_doc=tolerances_doc,
        layers=layers,
        layers_reason=layers_reason,
        golden_path=golden,
        tolerances_path=tolerances,
    )

    verdict_counts = Counter(row["verdict"] for row in rows)
    by_source: dict[str, dict[str, int]] = {}
    for source in SOURCES:
        source_rows = [row for row in rows if row["source"] == source]
        source_counts = Counter(row["verdict"] for row in source_rows)
        by_source[source] = {
            "n_rows": len(source_rows),
            **{verdict: source_counts.get(verdict, 0) for verdict in VERDICTS},
        }

    notice_pins = parse_notice_pins(notice)
    provenance: dict[str, Any] = {
        "generator": _GENERATOR,
        "timestamp": timestamp,
        "reference_revision": {
            "value": str(PINNED_GOODMAN_UPSTREAM.value("dp5_source_revision")),
            "source": PINNED_GOODMAN_UPSTREAM.get("dp5_source_revision").source,
        },
        "golden_revision": str(golden_doc.get("dp5_revision")) if golden_doc else None,
        "notice": {
            "path": _display_path(notice),
            "status": "verified" if notice_pins else "not_verified",
            "pins": notice_pins,
        },
        "paths": {
            "models_dir": _display_path(models),
            "golden": _display_path(golden),
            "tolerances": _display_path(tolerances),
        },
        "code": _code_provenance(),
    }

    return {
        "schema": REFERENCE_VALIDATION_SCHEMA,
        "claim": _CLAIM,
        "provenance": provenance,
        "acp_side": {"layers_source": layers_source, "layers_reason": layers_reason},
        "asset_manifest": manifest,
        "rows": rows,
        "summary": {
            "n_rows": len(rows),
            "verdicts": {verdict: verdict_counts.get(verdict, 0) for verdict in VERDICTS},
            "by_source": by_source,
            "n_assets": len(manifest),
            "n_assets_missing": sum(1 for entry in manifest if not entry["exists"]),
            "n_asset_pins": sum(
                1
                for entry in manifest
                for pin_key in ("notice_pin", "golden_pin", "tolerance_pin")
                if entry[pin_key] is not None
            ),
            "n_asset_pin_mismatches": 0,
        },
    }


def write_reference_validation_table(table: Mapping[str, Any], path: str | Path) -> Path:
    """Canonically and atomically persist the table; returns the written path."""
    return write_metrics(table, path)


# ---------------------------------------------------------------------------
# CLI entry point (reproducible verification script)
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Regenerate the comparison table (canonical JSON) and report verdicts.

    Exit code 1 when any row is a ``mismatch`` (a verification failure), 0
    otherwise — ``not_verified`` never counts as either.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", help="write the canonical table JSON here (stdout when omitted)"
    )
    parser.add_argument(
        "--now", help="pin the recorded timestamp (ISO 8601) for deterministic replay"
    )
    parser.add_argument("--models-dir", help="DP5 asset directory (default: src/acp/nmr/models)")
    parser.add_argument("--golden", help="todo-36 layered golden JSON")
    parser.add_argument("--tolerances", help="todo-36 frozen tolerances JSON")
    parser.add_argument("--notice", help="NOTICE.md pin source")
    parser.add_argument(
        "--no-recompute",
        action="store_true",
        help="do not recompute the ACP FCHL layers (records not_verified rows)",
    )
    args = parser.parse_args(argv)

    table = build_reference_validation_table(
        models_dir=args.models_dir,
        golden_path=args.golden,
        tolerances_path=args.tolerances,
        notice_path=args.notice,
        acp_layers=None,
        recompute=not args.no_recompute,
        now=args.now,
    )
    mismatches = table["summary"]["verdicts"]["mismatch"]
    if args.output:
        target = write_reference_validation_table(table, args.output)
        receipt = {
            "output": str(target),
            "n_rows": table["summary"]["n_rows"],
            "verdicts": table["summary"]["verdicts"],
            "acp_side": table["acp_side"],
        }
        json.dump(receipt, sys.stdout, indent=2, ensure_ascii=True)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(canonical_json_bytes(table).decode("utf-8"))
    return 1 if mismatches else 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())


__all__ = [
    "DEFAULT_GOLDEN_PATH",
    "DEFAULT_MODELS_DIR",
    "DEFAULT_NOTICE_PATH",
    "DEFAULT_TOLERANCES_PATH",
    "EXACT_TOLERANCE",
    "REFERENCE_VALIDATION_SCHEMA",
    "SOURCES",
    "VERDICTS",
    "ReferenceAssetError",
    "ReferenceAssetHashMismatchError",
    "ReferenceValidationError",
    "build_reference_validation_table",
    "collect_asset_manifest",
    "compare_numeric_values",
    "main",
    "parse_notice_pins",
    "write_reference_validation_table",
]
