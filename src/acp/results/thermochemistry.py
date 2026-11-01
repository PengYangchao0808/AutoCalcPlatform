"""Thermochemistry projection — ZPE/H/G/S as a publishable RESULT product.

Three entry points:

* :func:`build_thermo_report` — the legacy viewer projection over the
  ``thermo.json`` producer format (design doc §11.3); unchanged.
* :func:`parse_orca_thermochemistry` /
  :func:`build_frequency_thermochemistry` /
  :func:`build_batch_thermochemistry` — the round-7 #4 projection that exposes
  the values the QC output actually printed (ORCA ``THERMOCHEMISTRY AT <T>K``
  block, or the Shermo ``*.sum`` used by the BatchOptimize thermochemistry
  step) as an ``acp_thermochemistry_v1`` payload.
* :func:`publish_if_available` — the shared "publish only what the output
  really printed" gate used by both the ``frequency`` workflow and the
  BatchOptimize item materializer (round-8 三.4).  A log without ORCA's
  thermochemistry block publishes nothing (never fabricated zeros) and a
  thermochemistry product already registered on the same path can be kept
  instead of overwritten.

The projection never invents a value: a field is present only when the source
text/metadata contains it, and derived fields are named as such.  Units are
Hartree unless a key says otherwise (``entropy_au`` is Eh/K,
``*_kcal_mol`` is kcal/mol).

:func:`publish_thermochemistry` writes the payload under ``RESULT/`` and
registers it as a ``ProductKind.THERMO_REPORT`` manifest product through the
single manifest writer, so ``acp_status.artifacts`` / ``acp_read_artifact`` can
reach it without grepping a QC log.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from cccp.utils.constants import HARTREE_TO_KCAL

logger = logging.getLogger(__name__)

__all__ = [
    "BATCH_PRODUCT_RELATIVE_DIR",
    "PRODUCT_RELATIVE_PATH",
    "SCHEMA_VERSION",
    "batch_thermochemistry_relative_path",
    "build_batch_thermochemistry",
    "build_frequency_thermochemistry",
    "build_thermo_report",
    "parse_orca_thermochemistry",
    "parse_shermo_zpe_au",
    "publish_if_available",
    "publish_thermochemistry",
    "thermochemistry_descriptor",
    "thermochemistry_product_metadata",
    "write_thermochemistry_json",
]

#: Wire schema of the published thermochemistry payload.
SCHEMA_VERSION = "acp_thermochemistry_v1"

#: Simple frequency workflow: one product at the RESULT root.
PRODUCT_RELATIVE_PATH = "thermochemistry.json"

#: BatchOptimize: one product per item under this RESULT-relative directory.
BATCH_PRODUCT_RELATIVE_DIR = "thermochemistry"

_UNIT_NOTE = "Hartree (Eh) unless a key says otherwise; entropy_au is Eh/K"

_FLOAT = r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"

#: ORCA ``THERMOCHEMISTRY AT <T>K`` block.  Every pattern matches one printed
#: line verbatim; a missing line leaves the field out (never defaulted).
_ORCA_PATTERNS: dict[str, re.Pattern[str]] = {
    "temperature_k": re.compile(r"^Temperature\s*\.\.\.\s*" + _FLOAT + r"\s*K", re.MULTILINE),
    "pressure_atm": re.compile(r"^Pressure\s*\.\.\.\s*" + _FLOAT + r"\s*atm", re.MULTILINE),
    "electronic_energy_hartree": re.compile(
        r"^Electronic energy\s*\.\.\.\s*" + _FLOAT + r"\s*Eh", re.MULTILINE
    ),
    "zpe_hartree": re.compile(r"^Zero point energy\s*\.\.\.\s*" + _FLOAT + r"\s*Eh", re.MULTILINE),
    "thermal_correction_hartree": re.compile(
        r"^Total thermal correction\s+" + _FLOAT + r"\s*Eh", re.MULTILINE
    ),
    "total_correction_hartree": re.compile(
        r"^Total correction\s+" + _FLOAT + r"\s*Eh", re.MULTILINE
    ),
    "total_thermal_energy_hartree": re.compile(
        r"^Total thermal energy\s+\.\.\.\s*" + _FLOAT + r"\s*Eh", re.MULTILINE
    ),
    "enthalpy_hartree": re.compile(
        r"^Total Enthalpy\s+\.\.\.\s*" + _FLOAT + r"\s*Eh", re.MULTILINE
    ),
    "entropy_term_hartree": re.compile(
        r"^Final entropy term\s+\.\.\.\s*" + _FLOAT + r"\s*Eh", re.MULTILINE
    ),
    "gibbs_hartree": re.compile(
        r"^Final Gibbs free energy\s+\.\.\.\s*" + _FLOAT + r"\s*Eh", re.MULTILINE
    ),
    "cutoff_frequency_cm1": re.compile(
        r"^Cut-Off Frequency\s*\.\.\.\s*" + _FLOAT + r"\s*cm\^-1", re.MULTILINE
    ),
}

_ORCA_QUASI_RRHO_RE = re.compile(r"^Quasi RRHO\s*\.\.\.\s*(\w+)", re.MULTILINE)
_ORCA_LINEAR_RE = re.compile(r"The molecule is recognized as being linear", re.IGNORECASE)
_SHERMO_ZPE_RE = re.compile(r"Zero point energy \(ZPE\):.*?([-+]?\d+\.\d+)\s*a\.u\.", re.IGNORECASE)


# ── legacy viewer projection (unchanged) ────────────────────────────────


def build_thermo_report(thermo_json: Path | dict) -> dict:
    """Project a ``thermo.json`` (path or parsed dict) onto the §11.3 viewer schema."""
    if isinstance(thermo_json, dict):
        data = thermo_json
    else:
        try:
            data = json.loads(Path(thermo_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("could not read thermo json %s: %s", thermo_json, exc)
            data = {}
    if not isinstance(data, dict):
        logger.debug("thermo json payload is not an object: %r", thermo_json)
        data = {}
    gibbs = data.get("total_gibbs_hartree", data.get("free_energy_hartree"))
    return {
        "scf_energy_hartree": data.get("sp_energy_hartree"),
        "zpe_included": data.get("thermal_correction_u_hartree") is not None,
        "enthalpy_hartree": data.get("total_enthalpy_hartree"),
        "gibbs_hartree": gibbs,
        "entropy": data.get("entropy"),
        "temperature_k": data.get("temperature_k"),
        "unit_note": "hartree unless noted",
    }


# ── ORCA thermochemistry block (simple frequency / optimize+freq) ───────


def parse_orca_thermochemistry(text: str) -> dict[str, Any] | None:
    """Parse ORCA's printed thermochemistry block from a frequency log.

    Returns the verbatim printed values (Hartree / K / atm) plus the model
    provenance ORCA states (quasi-RRHO flag, cut-off frequency, linearity).
    ``None`` when the log has no thermochemistry block or lacks the Gibbs
    energy — that is the "no frequency, no thermochemistry" case, and it is
    reported as absent rather than as zeros.
    """
    if not text:
        return None
    values: dict[str, Any] = {}
    for key, pattern in _ORCA_PATTERNS.items():
        match = pattern.search(text)
        if match is not None:
            values[key] = float(match.group(1))
    gibbs = values.get("gibbs_hartree")
    if gibbs is None:
        return None
    quasi = _ORCA_QUASI_RRHO_RE.search(text)
    if quasi is not None:
        values["quasi_rrho"] = quasi.group(1).strip().lower() in {"true", "yes", "1"}
    values["linear_molecule"] = bool(_ORCA_LINEAR_RE.search(text))
    temperature = values.get("temperature_k")
    entropy_term = values.get("entropy_term_hartree")
    if temperature and entropy_term is not None:
        # T*S is printed; the entropy itself is one division away, and the
        # derivation is labelled in ``provenance.derived``.
        values["entropy_au"] = entropy_term / temperature
    values["zpe_included"] = values.get("zpe_hartree") is not None
    return values


def build_frequency_thermochemistry(
    freq_log_path: Path | str,
    *,
    method: str | None = None,
    basis: str | None = None,
) -> dict[str, Any] | None:
    """Build the ``acp_thermochemistry_v1`` payload from one frequency log.

    ``None`` when the log cannot be read or carries no thermochemistry block;
    the caller must not register a product in that case.
    """
    path = Path(freq_log_path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.debug("thermochemistry: cannot read frequency log %s", path, exc_info=True)
        return None
    values = parse_orca_thermochemistry(text)
    if values is None:
        logger.debug("thermochemistry: no ORCA thermochemistry block in %s", path)
        return None
    return _payload(
        values,
        source_kind="orca_frequency_output",
        source_path=Path(path).name,
        method=method,
        basis=basis,
        parser="orca_thermochemistry_block_v1",
        derived=["entropy_au = Final entropy term / temperature"],
        phase_correction={
            "standard_state": "1atm",
            "qrrho": values.get("quasi_rrho"),
            "qrrho_mode": "ORCA quasi-RRHO" if values.get("quasi_rrho") else "ORCA harmonic",
            "cutoff_frequency_cm1": values.get("cutoff_frequency_cm1"),
            "linear_molecule": values.get("linear_molecule"),
        },
    )


def parse_shermo_zpe_au(text: str) -> float | None:
    """Return the last ``Zero point energy (ZPE): ... a.u.`` value in a Shermo sum."""
    matches = _SHERMO_ZPE_RE.findall(str(text or ""))
    if not matches:
        return None
    try:
        return float(matches[-1])
    except (TypeError, ValueError):
        return None


# ── BatchOptimize thermochemistry (Shermo metadata) ─────────────────────


def _first_float(metadata: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
    return None


def build_batch_thermochemistry(
    metadata: Mapping[str, Any],
    *,
    item_id: str,
    tag: str | None = None,
    method: str | None = None,
    basis: str | None = None,
    zpe_scale_factor: float | None = None,
    shermo_sum_path: Path | str | None = None,
) -> dict[str, Any] | None:
    """Project one BatchOptimize item's thermochemistry step metadata.

    The metadata is the shared ``build_metadata`` projection of the Shermo
    thermochemistry step.  The product is built only for a successful step
    with a Gibbs energy; ZPE is read from the Shermo sum file when reachable
    (the batch metadata does not carry it) and stays absent otherwise.
    """
    if metadata.get("status") not in (None, "completed"):
        return None
    if metadata.get("success") is False:
        return None
    gibbs = _first_float(
        metadata, "gibbs_hartree", "gibbs_free_energy_hartree", "total_gibbs_hartree"
    )
    if gibbs is None:
        return None
    enthalpy = _first_float(metadata, "enthalpy_hartree", "total_enthalpy_hartree")
    entropy = _first_float(metadata, "entropy_au", "s_total")
    electronic = _first_float(metadata, "sp_energy_hartree")
    correction = _first_float(metadata, "thermal_correction_u_hartree")
    zpe = None
    sum_path = shermo_sum_path if shermo_sum_path is not None else metadata.get("output_file")
    if isinstance(sum_path, (str, Path)) and str(sum_path):
        try:
            zpe = parse_shermo_zpe_au(Path(sum_path).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            logger.debug("thermochemistry: cannot read Shermo sum %s", sum_path, exc_info=True)
    values: dict[str, Any] = {
        "gibbs_hartree": gibbs,
        "zpe_included": zpe is not None,
    }
    if enthalpy is not None:
        values["enthalpy_hartree"] = enthalpy
    if entropy is not None:
        values["entropy_au"] = entropy
    if electronic is not None:
        values["electronic_energy_hartree"] = electronic
    if correction is not None:
        values["thermal_correction_hartree"] = correction
    if zpe is not None:
        values["zpe_hartree"] = zpe
    if zpe_scale_factor is not None:
        values["zpe_scale_factor"] = float(zpe_scale_factor)
    temperature = _first_float(metadata, "temperature_k", "temperature")
    pressure = _first_float(metadata, "pressure_atm", "pressure")
    if temperature is not None:
        values["temperature_k"] = temperature
    if pressure is not None:
        values["pressure_atm"] = pressure
    standard_state = metadata.get("standard_state")
    phase: dict[str, Any] = {
        "standard_state": str(standard_state) if standard_state is not None else None,
        "qrrho": metadata.get("qrrho") if isinstance(metadata.get("qrrho"), bool) else None,
        "qrrho_mode": (
            str(metadata.get("qrrho_mode")) if metadata.get("qrrho_mode") is not None else None
        ),
        "delta_g_hartree": _first_float(metadata, "standard_state_delta_g_hartree"),
        "delta_g_kcal_mol": _first_float(metadata, "standard_state_delta_g_kcal_mol"),
        "gibbs_source": (
            str(metadata.get("selected_gibbs_source"))
            if metadata.get("selected_gibbs_source") is not None
            else None
        ),
    }
    phase = {key: value for key, value in phase.items() if value is not None}
    return _payload(
        values,
        source_kind="shermo",
        source_path=(
            Path(sum_path).name
            if isinstance(sum_path, (str, Path)) and str(sum_path)
            else None
        ),
        method=method,
        basis=basis,
        parser="shermo_sum_v1",
        derived=[],
        phase_correction=phase,
        extra={"item_id": item_id, **({"tag": tag} if tag else {})},
    )


# ── shared payload builder ─────────────────────────────────────────────


def _payload(
    values: Mapping[str, Any],
    *,
    source_kind: str,
    source_path: str | None,
    method: str | None,
    basis: str | None,
    parser: str,
    derived: list[str],
    phase_correction: Mapping[str, Any],
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the ``acp_thermochemistry_v1`` payload from parsed values."""
    gibbs = values.get("gibbs_hartree")
    payload: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "unit_note": _UNIT_NOTE,
        "source": {
            "kind": source_kind,
            "parser": parser,
            **({"path": source_path} if source_path else {}),
            **({"method": method} if method else {}),
            **({"basis": basis} if basis else {}),
        },
        "phase_correction": dict(phase_correction),
        "derived": list(derived),
        "zpe_included": bool(values.get("zpe_included")),
    }
    for key in (
        "temperature_k",
        "pressure_atm",
        "electronic_energy_hartree",
        "zpe_hartree",
        "zpe_scale_factor",
        "thermal_correction_hartree",
        "total_correction_hartree",
        "total_thermal_energy_hartree",
        "enthalpy_hartree",
        "gibbs_hartree",
        "entropy_au",
        "entropy_term_hartree",
    ):
        value = values.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            payload[key] = float(value)
    if isinstance(gibbs, (int, float)) and not isinstance(gibbs, bool):
        # Unit conversion of a printed value — not a new scientific number.
        payload["gibbs_kcal_mol"] = float(gibbs) * HARTREE_TO_KCAL
    if extra:
        payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def thermochemistry_descriptor(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Bounded summary of a payload for manifest product metadata."""
    summary: dict[str, Any] = {"schema": SCHEMA_VERSION}
    source = payload.get("source")
    if isinstance(source, Mapping):
        for key in ("kind", "parser", "method", "basis"):
            value = source.get(key)
            if value is not None:
                summary[key] = value
    for key in (
        "temperature_k",
        "pressure_atm",
        "gibbs_hartree",
        "enthalpy_hartree",
        "zpe_hartree",
        "entropy_au",
        "zpe_included",
    ):
        value = payload.get(key)
        if value is not None:
            summary[key] = value
    phase = payload.get("phase_correction")
    if isinstance(phase, Mapping):
        summary["standard_state"] = phase.get("standard_state")
        summary["qrrho"] = phase.get("qrrho")
    return {key: value for key, value in summary.items() if value is not None}


# ── publication (RESULT file + manifest product) ────────────────────────


def batch_thermochemistry_relative_path(item_id: str) -> str:
    """RESULT-relative path of one BatchOptimize item's thermochemistry product."""
    return f"{BATCH_PRODUCT_RELATIVE_DIR}/{item_id}.json"


def write_thermochemistry_json(
    result_dir: Path | str,
    payload: Mapping[str, Any],
    *,
    relative_path: str,
) -> Path:
    """Atomically write one thermochemistry payload under ``RESULT/``."""
    from acp.results.frame_candidate_store import atomic_write_text

    root = Path(result_dir)
    target = root / relative_path
    atomic_write_text(
        target,
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
    )
    return target


def thermochemistry_product_metadata(
    payload: Mapping[str, Any],
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Manifest product metadata for a thermochemistry payload (bounded)."""
    product_metadata: dict[str, Any] = {"product_kind": "thermochemistry"}
    if extra:
        product_metadata.update(extra)
    product_metadata["thermochemistry"] = thermochemistry_descriptor(payload)
    return product_metadata


def publish_thermochemistry(
    result_dir: Path | str,
    payload: Mapping[str, Any],
    *,
    relative_path: str,
    product_id: str,
    label: str,
    metadata: Mapping[str, Any] | None = None,
    publisher: Callable[[Path, Any], Path] | None = None,
) -> Path:
    """Write the payload under *result_dir* and register its manifest product.

    Uses the single manifest writer (:func:`register_result_manifest`) and the
    Data atomic-write helper — never a second manifest/authority file.  The
    product is registered as ``ProductKind.THERMO_REPORT``.
    """
    from acp.calculations.result_publication import register_result_manifest
    from acp.storage.manifest import ProductKind, ResultManifest

    root = Path(result_dir)
    target = write_thermochemistry_json(root, payload, relative_path=relative_path)
    try:
        manifest = ResultManifest.read(root)
    except (OSError, ValueError):
        manifest = ResultManifest(task_id="", workflow="", status="completed")
    manifest.add_product(
        id=product_id,
        label=label,
        path=relative_path,
        kind=ProductKind.THERMO_REPORT,
        metadata=thermochemistry_product_metadata(payload, metadata),
    )
    register_result_manifest(root, manifest, publisher=publisher)
    return target


def _registered_thermochemistry_product(
    result_dir: Path,
    relative_path: str,
    manifest: Any | None,
) -> str | None:
    """Id of an already-registered thermochemistry product on *relative_path*."""
    from acp.storage.manifest import ProductKind, ResultManifest

    source = manifest
    if source is None:
        try:
            source = ResultManifest.read(result_dir)
        except (OSError, ValueError):
            return None
    products = getattr(source, "products", None)
    if not isinstance(products, list):
        return None
    for product in products:
        if getattr(product, "path", None) != relative_path:
            continue
        if getattr(product, "kind", None) != ProductKind.THERMO_REPORT:
            continue
        product_id = getattr(product, "id", None)
        return str(product_id) if product_id else None
    return None


def publish_if_available(
    result_dir: Path | str,
    *,
    relative_path: str,
    product_id: str,
    label: str,
    log_path: Path | str | None = None,
    payload: Mapping[str, Any] | None = None,
    method: str | None = None,
    basis: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    keep_existing: bool = False,
    manifest: Any | None = None,
    publisher: Callable[[Path, Any], Path] | None = None,
) -> str | None:
    """Publish thermochemistry when the source data really exists.

    Exactly one source is used: a ready *payload* (BatchOptimize's Shermo
    projection) wins over *log_path* (an ORCA frequency output parsed by
    :func:`build_frequency_thermochemistry`).  When neither yields values —
    including an ORCA log without its ``THERMOCHEMISTRY`` block — **nothing**
    is written or registered: the caller can never publish fabricated zeros.

    *keep_existing* keeps an already-registered ``THERMO_REPORT`` product on
    the same *relative_path* (the BatchOptimize "prefer the Shermo product,
    never overwrite it" rule) and returns its id.

    *manifest* lets an in-memory manifest owner (the batch engine, which
    writes one manifest for the whole run) register the product without a
    second disk write; when omitted the single manifest writer
    (:func:`publish_thermochemistry`) persists it through *publisher*.

    Returns the registered product id, or ``None`` when nothing was
    published.  Publication never raises: a failure is logged and reported as
    ``None`` (a thermochemistry product must never fail the workflow).
    """
    try:
        root = Path(result_dir)
        if keep_existing:
            existing = _registered_thermochemistry_product(root, relative_path, manifest)
            if existing is not None:
                return existing
        if payload is None:
            if log_path is None:
                return None
            payload = build_frequency_thermochemistry(log_path, method=method, basis=basis)
            if payload is None:
                return None
        if manifest is not None:
            from acp.storage.manifest import ProductKind

            _ = write_thermochemistry_json(root, payload, relative_path=relative_path)
            manifest.add_product(
                id=product_id,
                label=label,
                path=relative_path,
                kind=ProductKind.THERMO_REPORT,
                metadata=thermochemistry_product_metadata(payload, metadata),
            )
            return product_id
        _ = publish_thermochemistry(
            root,
            payload,
            relative_path=relative_path,
            product_id=product_id,
            label=label,
            metadata=metadata,
            publisher=publisher,
        )
        return product_id
    except (OSError, ValueError, TypeError, KeyError):
        logger.warning(
            "thermochemistry: could not publish %s; skipping",
            relative_path,
            exc_info=True,
        )
        return None
