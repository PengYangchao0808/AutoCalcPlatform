"""Project execution parameters into the editor and apply edited level fields.

Historical jobs contain both form levels and execution payloads. The draft
must display the latter; an edit must update every execution alias before
validation, diffing, fingerprinting, or submission.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from acp.scheduler.jobs import JobSpec, scan_method_flags

__all__ = [
    "apply_edited_parameters",
    "method_projection_for_edit",
    "merge_edit_section",
    "merge_edit_input",
]

_PES_COORDINATE_FIELDS = {
    "scan_coordinate_kind": "kind",
    "scan_coordinate_start": "start",
    "scan_coordinate_end": "end",
    "scan_coordinate_points": "n_points",
    "scan_bond_type": "bond_type",
}
_PES_PROTOCOL_FIELDS = {
    "scan_driver": {
        "engine": "software",
        "scan_mode": "mode",
        "scan_reuse_previous_geometry": "reuse_previous_geometry",
        "scan_full_scan": "full_scan",
        "scan_failure_policy": "failure_policy",
        "scan_retry_count": "retry_count",
        "scan_use_scants": "use_scants",
        "scan_max_iterations": "max_iterations",
    },
    "scan_optimizer": {
        "scan_optimizer_method": "method",
        "scan_optimizer_basis": "basis",
        "scan_optimizer_dispersion": "dispersion",
        "scan_optimizer_solvent_model": "solvent_model",
        "scan_optimizer_solvent": "solvent",
        "scan_optimizer_grid": "grid",
        "scan_optimizer_scf_convergence": "scf_convergence",
        "scan_optimizer_scf_max_iterations": "scf_max_iterations",
        "scan_optimizer_ri_approximation": "ri_approximation",
        "scan_optimizer_max_iterations": "max_iterations",
        "scan_optimizer_convergence": "convergence",
        "scan_optimizer_retries": "retry_count",
        "scan_optimizer_retry_strategy": "retry_strategy",
    },
    "single_point": {
        "engine": "software",
        "functional": "method",
        "basis": "basis",
        "dispersion": "dispersion",
        "ri_approximation": "ri_approximation",
        "aux_j_basis": "aux_j_basis",
        "aux_c_basis": "aux_c_basis",
        "solvent_model": "solvent_model",
        "solvent": "solvent",
        "grid": "grid",
        "scf_convergence": "scf_convergence",
        "single_point_resume": "resume",
    },
}
_NMR_GIAO_ALIASES = {
    "functional": ("functional", "nmr_method"),
    "basis": ("basis", "nmr_basis"),
    "solvent_model": ("solvent_model",),
    "solvent": ("solvent",),
    "nuclei": ("nuclei",),
    "boltzmann_temp": ("boltzmann_temp",),
    "tms_shielding_h": ("tms_shielding_h",),
    "tms_shielding_c": ("tms_shielding_c",),
}
_UNSUPPORTED_SCAN_FIELDS = frozenset(
    field
    for mapping in _PES_PROTOCOL_FIELDS.values()
    for field in mapping
    if field.startswith("scan_") and field != "scan_use_scants"
)


def merge_edit_section(original: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge supplied object fields; an explicit empty object clears the section."""
    if not patch:
        return {}
    merged = deepcopy(original)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_edit_section(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def merge_edit_input(original: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge input edits, discarding superseded geometry/source descriptors."""
    merged = merge_edit_section(original, patch)
    source, old_source = patch.get("source"), original.get("source")
    replacement = isinstance(source, str) and source != old_source
    if isinstance(source, dict):
        previous = old_source if isinstance(old_source, dict) else {}
        replacement = any(
            key in source and source[key] != previous.get(key)
            for key in (
                "source_type",
                "xyz_text",
                "xyz",
                "source",
                "smiles",
                "asset_path",
                "artifact_path",
            )
        )
        if source.get("source_type") and source["source_type"] != previous.get("source_type"):
            merged["source"] = {
                **{key: previous[key] for key in ("charge", "multiplicity") if key in previous},
                **deepcopy(source),
            }
    if replacement:
        for key in (
            "source_ref",
            "edit_input_origin",
            "source_job_id",
            "from",
            "from_artifact",
            "asset_id",
            "input_artifact",
            "smiles",
            "xyz",
            "xyz_text",
            "geometry",
            "scan_request",
        ):
            if key not in patch:
                merged.pop(key, None)
    return merged


def _scan_coordinates(method: dict[str, Any], inp: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalized coordinates for the edit projection (shared parser, no flags).

    The projection must keep working while a stale level mirror disagrees with
    an explicit method value (the edit form shows the effective selection); the
    strict conflict refusal belongs to submission, not to hydration.
    """
    from acp.calculations.pes.normalization import (
        CoordinateError as _CoordinateError,
    )
    from acp.calculations.pes.normalization import normalize_scan_entries

    try:
        entries = normalize_scan_entries(inp, method)
    except _CoordinateError:
        return []
    return [
        {
            "kind": str(entry.get("kind") or "distance"),
            "atoms": [int(atom) for atom in entry.get("atoms") or ()],
            "start": float(entry.get("start") or 0.0),
            "end": float(entry.get("end") or 0.0),
        }
        for entry in entries
    ]


def method_projection_for_edit(spec: JobSpec) -> dict[str, Any]:
    """Return display levels with execution payloads overriding stale mirrors."""
    from acp.catalog import get_method_schema, get_workflow_by_id

    method = deepcopy(spec.method)
    workflow = get_workflow_by_id(spec.workflow) or {}
    schema = get_method_schema(workflow.get("method_schema_id", "")) or {}
    profile_id = method.get("profile_id") or method.get("profile")
    profiles = schema.get("profiles") or []
    profile = next((p for p in profiles if p["profile_id"] == profile_id), {})
    levels = deepcopy(profile.get("levels") or {})
    for key, value in (method.get("levels") or {}).items():
        if isinstance(value, dict):
            levels.setdefault(key, {}).update(deepcopy(value))
    method["levels"] = levels
    inp = spec.input
    if spec.workflow == "PESsearch" and method.get("mode") == "bond_length_scan":
        payload = inp.get("scan_request") or {}
        coordinate = inp.get("coordinate") or payload.get("coordinate") or {}
        protocol = inp.get("protocol") or payload.get("protocol") or {}
        level = levels.setdefault("scan_coordinate", {"engine": "orca"})
        for field, key in _PES_COORDINATE_FIELDS.items():
            if key in coordinate:
                level[field] = deepcopy(coordinate[key])
        for level_id, mapping in _PES_PROTOCOL_FIELDS.items():
            target = levels.setdefault(level_id, {"engine": "orca"})
            source = protocol.get(level_id) or {}
            for field, key in mapping.items():
                if key in source:
                    target[field] = deepcopy(source[key])
            if level_id == "single_point" and "enabled" in source:
                target["_disabled"] = not source["enabled"]
    elif spec.workflow == "scan":
        level = levels.setdefault("scan", {"engine": "orca"})
        try:
            coordinates = _scan_coordinates(method, inp)
        except (ValueError, TypeError, KeyError):
            coordinates = []
        try:
            # a stale mirror/point-count conflict must not blank the coordinates
            flags = scan_method_flags(method, inp)
        except (ValueError, TypeError, KeyError):
            flags = []
        if coordinates:
            coordinate = coordinates[0]
            level.update(
                {
                    "scan_coordinate_kind": coordinate["kind"],
                    "scan_coordinate_atoms": [atom + 1 for atom in coordinate["atoms"]],
                    "scan_coordinate_start": coordinate["start"],
                    "scan_coordinate_end": coordinate["end"],
                }
            )
        # The projection shows the effective value: an explicit method.scan_points
        # overrides the stale level mirror (the edit form must not re-apply it).
        explicit_points = method.get("scan_points")
        if explicit_points is None:
            explicit_points = (levels.get("scan") or {}).get("scan_coordinate_points")
        if explicit_points is not None:
            level["scan_coordinate_points"] = int(explicit_points)
        elif "--scan-points" in flags:
            level["scan_coordinate_points"] = int(flags[flags.index("--scan-points") + 1])
        level["scan_use_scants"] = "--scants" in flags
    elif spec.workflow == "irc":
        level = levels.setdefault("irc", {"engine": "orca"})
        for field in ("maxpoints", "step"):
            if method.get(field) is not None:
                level[field] = deepcopy(method[field])
    elif spec.workflow == "nmr":
        from acp.nmr.method_config import resolve_nmr_method
        from acp.scheduler.jobs import nmr_flag_config

        giaoa = levels.setdefault("giaoa", {"engine": "orca"})
        for field, aliases in _NMR_GIAO_ALIASES.items():
            for alias in reversed(aliases):
                if method.get(alias) not in (None, ""):
                    giaoa[field] = deepcopy(method[alias])
                    break
        try:
            effective = resolve_nmr_method(spec.method, nmr_flag_config(spec.config_path))
        except (ValueError, OSError):
            pass  # The draft reports the resolver error separately.
        else:
            giaoa.update(
                {
                    "functional": effective.nmr_method,
                    "basis": effective.nmr_basis,
                    "solvent_model": effective.solvent_model,
                    "solvent": effective.solvent,
                    "nuclei": list(effective.nuclei),
                    "boltzmann_temp": effective.boltzmann_temp,
                    "tms_shielding_h": effective.tms_1h,
                    "tms_shielding_c": effective.tms_13c,
                }
            )
            levels.setdefault("conformer", {"engine": "censo"})["ewin"] = effective.ewin
    elif spec.workflow == "Confsearch":
        level = levels.setdefault("confsearch", {})
        for field, alias in (
            ("protocol", "protocol"),
            ("confsearch_profile", "profile"),
            ("refinement_policy", "refinement_policy"),
            ("ewin", "ewin"),
        ):
            if alias in method:
                level[field] = deepcopy(method[alias])
    return method


def apply_edited_parameters(
    original: JobSpec,
    method: dict[str, Any],
    inp: dict[str, Any],
    *,
    edited_levels: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply changed display fields to the payload the workflow consumes."""
    method, inp = deepcopy(method), deepcopy(inp)
    method_changed = method != original.method or edited_levels is not None
    baseline = method_projection_for_edit(original).get("levels") or {}
    levels = method.get("levels") or {}

    def changes(level_id: str) -> dict[str, Any]:
        if not method_changed:
            return {}
        before = baseline.get(level_id) or {}
        after = levels.get(level_id) or {}
        supplied_fields = after if edited_levels is None else (edited_levels.get(level_id) or {})
        return {
            key: value
            for key, value in after.items()
            if key in supplied_fields and (key not in before or value != before[key])
        }

    wf = original.workflow
    if wf == "PESsearch" and method.get("mode") == "bond_length_scan":
        payload = inp.get("scan_request") or {}
        for key in ("source", "coordinate", "coordinates", "selection", "protocol"):
            if key not in inp and key in payload:
                inp[key] = deepcopy(payload[key])
        if isinstance(inp.get("source"), str) and inp.get("source_type") == "xyz_text":
            inp["source"] = {
                "source_type": "xyz_text",
                "xyz_text": inp["source"],
                **{key: inp[key] for key in ("charge", "multiplicity") if key in inp},
            }
        coordinate = inp.setdefault("coordinate", {})
        changed = changes("scan_coordinate")
        for field, key in _PES_COORDINATE_FIELDS.items():
            if field in changed:
                coordinate[key] = deepcopy(changed[field])
                for coupled in inp.get("coordinates") or []:
                    coupled[key] = deepcopy(changed[field])
        protocol = inp.setdefault("protocol", {})
        for level_id, mapping in _PES_PROTOCOL_FIELDS.items():
            changed = changes(level_id)
            for field, key in mapping.items():
                if field in changed:
                    protocol.setdefault(level_id, {})[key] = deepcopy(changed[field])
        # cleanLevels omits disabled stages; the original spec stays intact
        # on a no-op, while an actual toggle updates the execution flag.
        if (
            method_changed
            and "single_point" in levels
            and (edited_levels is None or "single_point" in edited_levels)
        ):
            enabled = "single_point" in levels and not levels["single_point"].get("_disabled")
            was_enabled = not (baseline.get("single_point") or {}).get("_disabled", False)
            if enabled != was_enabled:
                protocol.setdefault("single_point", {})["enabled"] = enabled
        source = inp.get("source")
        if isinstance(source, dict):
            old_source = (
                original.input.get("source")
                or (original.input.get("scan_request") or {}).get("source")
                or {}
            )
            for key in ("charge", "multiplicity"):
                top_changed = inp.get(key) != original.input.get(key)
                chemistry_changed = top_changed or source.get(key) != (
                    old_source.get(key) if isinstance(old_source, dict) else None
                )
                if top_changed and key in inp:
                    source[key] = inp[key]
                if chemistry_changed and key in source:
                    protocol.setdefault("single_point", {})[key] = source[key]
    elif wf == "scan":
        changed = changes("scan")
        unsupported = _UNSUPPORTED_SCAN_FIELDS.intersection(changed)
        if unsupported:
            raise ValueError("普通 scan 不支持这些 PES 驱动参数：" + ", ".join(sorted(unsupported)))
        coordinate_fields = {
            "scan_coordinate_atoms",
            "scan_coordinate_kind",
            "scan_coordinate_start",
            "scan_coordinate_end",
        }
        if coordinate_fields.intersection(changed):
            coordinates = _scan_coordinates(original.method, inp)
            if len(coordinates) != 1:
                raise ValueError("耦合 scan 坐标请通过 input.scan_coordinates 完整编辑")
            coordinate = coordinates[0]
            for field, key in (
                ("scan_coordinate_kind", "kind"),
                ("scan_coordinate_start", "start"),
                ("scan_coordinate_end", "end"),
            ):
                if field in changed:
                    coordinate[key] = changed[field]
            if "scan_coordinate_atoms" in changed:
                coordinate["atoms"] = [int(atom) - 1 for atom in changed["scan_coordinate_atoms"]]
            inp["scan_coordinates"] = [coordinate]
        if "scan_coordinate_points" in changed:
            method["scan_points"] = changed["scan_coordinate_points"]
        if "scan_use_scants" in changed:
            method["scan_use_scants"] = changed["scan_use_scants"]
        # A partial edit must not fail on (or re-apply) a stale level mirror:
        # the explicit execution value wins and the mirror follows it.
        explicit_points = method.get("scan_points")
        level_mirror = (method.get("levels") or {}).get("scan")
        if explicit_points is not None and isinstance(level_mirror, dict):
            level_mirror["scan_coordinate_points"] = explicit_points
    elif wf == "irc":
        for field, value in changes("irc").items():
            if field in ("maxpoints", "step"):
                method[field] = value
    elif wf == "nmr":
        for field, value in changes("giaoa").items():
            for alias in _NMR_GIAO_ALIASES.get(field, ()):
                method[alias] = value
        if "ewin" in changes("conformer"):
            method["ewin"] = changes("conformer")["ewin"]
        if method_changed and (levels.get("giaoa") or {}).get("solvent_model") == "none":
            method["solvent"] = ""
    elif wf == "Confsearch":
        for field, alias in (
            ("protocol", "protocol"),
            ("confsearch_profile", "profile"),
            ("refinement_policy", "refinement_policy"),
            ("ewin", "ewin"),
        ):
            if field in changes("confsearch"):
                method[alias] = changes("confsearch")[field]
        if method_changed:
            for level_id in ("refinement_sp", "dft_opt"):
                changed = changes(level_id)
                if {"solvent", "solvent_model"}.intersection(changed):
                    level = levels[level_id]
                    method["solvent"] = (
                        level.get("solvent") if level.get("solvent_model") != "none" else ""
                    )
                    break
    return method, inp
