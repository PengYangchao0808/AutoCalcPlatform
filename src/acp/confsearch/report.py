"""Final Confsearch report artifacts (C2).

Writes the consolidated final report for one Confsearch run::

    RESULT/confsearch/
    ├── final_report.json        ← schema ``confsearch_final_report_v1``
    └── final_conformers.xyz     ← one frame per entry, rank order

and merge-registers both into ``RESULT/result_manifest.json``
(read → ``add_product`` → ``write``; existing products and header fields are
preserved; re-registration is idempotent by product id). Write or
registration failures RAISE — the report is a required deliverable.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from acp.storage.manifest import ProductKind, ResultManifest

from .contracts import PURE_XTB_PROTOCOLS, ConformerEntry, ConfsearchRequest, ProtocolOutcome
from .shared.artifacts import write_json_atomic

logger = logging.getLogger(__name__)

FINAL_REPORT_SCHEMA_VERSION = "confsearch_final_report_v1"
REPORT_JSON_NAME = "final_report.json"
XYZ_NAME = "final_conformers.xyz"


def normalize_table_reference(raw: Any, mol_dir: Path) -> Any:
    """Normalize a ``boltzmann_table_json`` metadata value for provenance blocks.

    Paths (absolute or mol_dir-relative) that live under *mol_dir* become a
    task-relative POSIX reference (``"RESULT/..."``); anything else — other
    locations, non-string values, ``None`` — passes through unchanged.
    """
    if not isinstance(raw, str) or not raw:
        return raw
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = Path(mol_dir) / candidate
    try:
        return candidate.resolve().relative_to(Path(mol_dir).resolve()).as_posix()
    except ValueError:
        return raw


def write_final_report(
    confsearch_dir: Path,
    *,
    request: ConfsearchRequest,
    entries: list[ConformerEntry],
    outcome: ProtocolOutcome,
) -> tuple[Path, Path]:
    """Write ``final_report.json`` + ``final_conformers.xyz`` under *confsearch_dir*.

    Args:
        confsearch_dir: The ``RESULT/confsearch`` directory; geometry refs in
            the report are confsearch-relative (exactly ``entry.geometry``).
        request: The originating request (protocol/profile/refinement_policy).
        entries: Final conformer entries (already ranked/selected by the engine).
        outcome: Protocol outcome carrying temperature and weight provenance.

    Returns:
        ``(report_json_path, xyz_path)``.

    Raises:
        FileNotFoundError: When no entry has a readable geometry file.
    """
    confsearch_dir = Path(confsearch_dir)
    report_path = confsearch_dir / REPORT_JSON_NAME
    xyz_path = confsearch_dir / XYZ_NAME

    metadata = outcome.workflow_metadata or {}
    executed = dict((metadata.get("censo_execution") or {}))
    verified = _execution_verified(executed)
    payload: dict[str, Any] = {
        "schema_version": FINAL_REPORT_SCHEMA_VERSION,
        "workflow": "Confsearch",
        "protocol": request.protocol,
        "profile": request.profile,
        "refinement_policy": request.refinement_policy,
        "temperature_k": outcome.temperature_k,
        "weight_table": {
            "source": outcome.weight_source,
            "method": outcome.weight_method,
            "population_coverage": outcome.population_coverage,
            "reference": normalize_table_reference(
                metadata.get("boltzmann_table_json"),
                confsearch_dir.parent.parent,
            ),
        },
    }
    requested_levels = _requested_levels(request)
    if requested_levels or executed:
        payload["method_identity"] = {
            "requested": requested_levels,
            "executed": executed,
            "executed_status": "verified" if verified else "unverified",
            "final_part": executed.get("final_part"),
            "energy_kind": outcome.energy_kind,
        }
    payload["conformers"] = [
        _conformer_block(entry, outcome, request, executed=executed, verified=verified)
        for entry in entries
    ]
    for key in ("total_gibbs_hartree", "total_gibbs_kcal_mol"):
        if key in metadata:
            payload[key] = metadata[key]

    # Write the XYZ first so a geometry failure raises before any report JSON
    # exists; the JSON is written last and rolls the XYZ back on failure —
    # a failed write never leaves an orphan artifact pair.
    _write_frames_xyz(xyz_path, confsearch_dir, entries, outcome)
    try:
        write_json_atomic(report_path, payload)
    except Exception:
        try:
            xyz_path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "final report: could not remove orphan %s after failed JSON write",
                xyz_path,
            )
        raise
    return report_path, xyz_path


def register_final_report(mol_dir: Path, report_path: Path, xyz_path: Path) -> None:
    """Merge-register the final report artifacts into ``<mol_dir>/RESULT``.

    Reads the existing ``result_manifest.json`` when present (preserving its
    header fields and products) and replaces the two report products by id.
    Missing artifacts or write failures raise — never silently skipped.
    """
    mol_dir = Path(mol_dir)
    for artifact in (report_path, xyz_path):
        if not artifact.is_file():
            raise FileNotFoundError(f"cannot register final report — missing artifact: {artifact}")

    result_dir = mol_dir / "RESULT"
    try:
        manifest = ResultManifest.read(result_dir)
    except FileNotFoundError:
        manifest = ResultManifest(task_id="", workflow="Confsearch", status="completed")
    manifest.add_product(
        "confsearch_final_report",
        "Confsearch final report",
        f"confsearch/{REPORT_JSON_NAME}",
        ProductKind.REPORT,
    )
    manifest.add_product(
        "confsearch_final_conformers",
        "Refined conformers (XYZ)",
        f"confsearch/{XYZ_NAME}",
        ProductKind.STRUCTURE,
        metadata={"auto_reusable": False},
    )
    manifest.write(result_dir)


def _execution_verified(executed: Mapping[str, Any]) -> bool:
    """Require a successful run and both artifact digests, not just a dict."""
    if not isinstance(executed, Mapping) or not executed:
        return False
    if int(executed.get("returncode", -1)) != 0:
        return False
    if not executed.get("argv") or not str(executed.get("final_part") or ""):
        return False
    artifacts = executed.get("artifacts")
    if not isinstance(artifacts, Mapping):
        return False
    for name in ("json", "xyz"):
        entry = artifacts.get(name)
        if not isinstance(entry, Mapping):
            return False
        digest = str(entry.get("sha256") or "")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            return False
    return True


def _requested_levels(request: ConfsearchRequest) -> dict[str, Any]:
    """Level settings from the request — declared intent, never evidence."""
    requested: dict[str, Any] = {}
    levels = request.levels or {}
    if not isinstance(levels, dict):
        return requested
    for level_id, level in levels.items():
        if not isinstance(level, dict):
            continue
        picked = {
            key: level[key]
            for key in (
                "engine",
                "functional",
                "basis",
                "solvent_model",
                "solvent",
                "ewin",
                "refinement_threshold",
            )
            if level.get(key) not in (None, "")
        }
        if picked:
            requested[str(level_id)] = picked
    return requested


def _conformer_block(
    entry: ConformerEntry,
    outcome: ProtocolOutcome,
    request: ConfsearchRequest,
    *,
    executed: Mapping[str, Any],
    verified: bool,
) -> dict[str, Any]:
    role = "refinement_sp" if entry.refined else "screening"
    evidence = _role_evidence(entry, role, executed, verified)
    if entry.refined:
        energy_kind = "dft"
    else:
        default_kind = "xtb" if request.protocol in PURE_XTB_PROTOCOLS else "censo"
        energy_kind = outcome.energy_kind or default_kind
    return {
        "conf_id": entry.conf_id,
        "source_conf_id": entry.source_conf_id,
        "rank": entry.rank,
        "refined": entry.refined,
        "geometry": entry.geometry,
        "energy_hartree": entry.energy_hartree,
        "free_energy_hartree": entry.free_energy_hartree,
        "energy_kind": energy_kind,
        "method_role": role,
        "method_evidence": evidence,
        "energy_kind_verified": bool(evidence == "executed" and role == "screening"),
        "relative_energy_kcal": entry.relative_energy_kcal,
        "boltzmann_weight": entry.boltzmann_weight,
        "weight_source": entry.weight_source or outcome.weight_source,
    }


def _role_evidence(
    entry: ConformerEntry,
    role: str,
    executed: Mapping[str, Any],
    verified: bool,
) -> str:
    """Only the part that ran AND contains this conformer counts as executed."""
    if not verified:
        return "unverified"
    final_part = str(executed.get("final_part") or "")
    if role == "screening" and final_part != "screening":
        return "unverified"
    if role == "refinement_sp" and final_part != "refinement":
        return "unverified"
    tracked = executed.get("conformers")
    if isinstance(tracked, list) and tracked:
        conf_id = str(entry.source_conf_id or entry.conf_id)
        if conf_id not in {str(item) for item in tracked}:
            return "unverified"
    return "executed"


def _frame_title(entry: ConformerEntry, outcome: ProtocolOutcome) -> str:
    weight_source = entry.weight_source or outcome.weight_source
    title = (
        f"{entry.conf_id} G={entry.free_energy_hartree} "
        f"w={entry.boltzmann_weight} src={weight_source}"
    )
    if entry.refined:
        title += " refined"
    return title


def _write_frames_xyz(
    xyz_path: Path,
    confsearch_dir: Path,
    entries: list[ConformerEntry],
    outcome: ProtocolOutcome,
) -> None:
    """Write one XYZ frame per entry (rank order); skip missing geometries."""
    frames: list[str] = []
    for entry in sorted(entries, key=lambda item: item.rank):
        geometry_ref = (entry.geometry or "").strip()
        source = confsearch_dir / geometry_ref if geometry_ref else None
        if not geometry_ref or source is None or not source.is_file():
            logger.warning(
                "final report: skipping %s — geometry file missing: %s",
                entry.conf_id,
                entry.geometry,
            )
            continue
        lines = source.read_text(encoding="utf-8").splitlines()
        try:
            n_declared = int(lines[0].strip())
        except (ValueError, IndexError):
            logger.warning(
                "final report: skipping %s — unreadable XYZ header in %s",
                entry.conf_id,
                source,
            )
            continue
        atom_lines = lines[2 : 2 + n_declared]
        frame = f"{len(atom_lines)}\n{_frame_title(entry, outcome)}\n" + "\n".join(atom_lines)
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(
            f"final_conformers.xyz: no readable geometry files for "
            f"{len(entries)} conformer(s) under {confsearch_dir}"
        )
    xyz_path.write_text("\n".join(frames) + "\n", encoding="utf-8")


__all__ = [
    "FINAL_REPORT_SCHEMA_VERSION",
    "normalize_table_reference",
    "register_final_report",
    "write_final_report",
]
