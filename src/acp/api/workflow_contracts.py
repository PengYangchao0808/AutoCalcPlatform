"""Discovery projection: the submit contract an agent can read before writing.

The authoritative semantics live in :mod:`acp.api.submission_contract`; this
module only *publishes* them (plus examples and known constraints) through
``GET/POST /api/capabilities?workflow=``.  Coverage is deliberately staged:
only Confsearch and BatchOptimize claim ``complete`` today; every other active
workflow is marked ``not_provided`` so a caller never assumes a schema that
does not exist.

Keeping the projection derived from the same tables the validator uses is the
point: an example that this module publishes must pass the real submit check.
"""

from __future__ import annotations

import difflib
from typing import Any

from acp.api.guidance_pack import PACK_VERSION as GUIDANCE_PACK_VERSION
from acp.api.guidance_pack import workflow_guide_map
from acp.api.submission_contract import WORKFLOW_CONTRACTS
from acp.catalog import WORKFLOW_CATALOG

__all__ = [
    "ACTIVE_WORKFLOWS",
    "canonical_workflow",
    "describe_workflow",
    "workflow_suggestion",
]

#: Workflows the product advertises as active (catalog-driven, not hand-listed).
ACTIVE_WORKFLOWS: tuple[str, ...] = tuple(
    str(entry["id"])
    for entry in WORKFLOW_CATALOG
    if entry.get("status", "active") == "active" and entry.get("visible", True)
)

_COVERAGE: dict[str, str] = {
    "Confsearch": "complete",
    "BatchOptimize": "complete",
    "PESsearch": "partial_bond_length_scan",
    "scan": "partial",
}

_STATUS_VOCABULARY: dict[str, Any] = {
    "envelope": {"ok": "the call succeeded", "error": "the call failed"},
    "task_state": [
        "queued",
        "running",
        "paused",
        "waiting_review",
        "failed",
        "cancelled",
        "completed",
    ],
    "legacy_alias": "status mirrors state for older clients",
}

_CONFSEARCH_CONSTRAINTS = (
    "refinement_policy=screen selects zero refinement candidates; refined=false is expected",
    "pure xTB protocols (xtb-crest, xtb-md) never run DFT refinement",
    "censo-crest and xtbmd-censo may run CENSO screening followed by DFT refinement",
    "max_conformers is an NMR-only field and is refused for Confsearch",
)

_BATCH_CONSTRAINTS = (
    "when method.batch_roles is present, top-level flat settings must mirror it",
    "selected_ids belongs to input, not method",
    "one scheduler task carries one structure (single_flat layout)",
)

_CONFSEARCH_INPUT_PROPERTIES: dict[str, Any] = {
    "source_type": {
        "type": "string",
        "enum": ["smiles", "xyz_text", "structure_asset"],
        "description": "Input encoding; Workbench sends it with source.",
    },
    "source": {"type": "string", "description": "SMILES, XYZ text, or asset path."},
    "smiles": {"type": "string", "description": "Shorthand accepted by the CLI."},
    "input_artifact": {"type": "string", "description": "Upstream artifact reference."},
    "charge": {"type": "integer", "description": "Total charge (default 0)."},
    "multiplicity": {"type": "integer", "description": "Spin multiplicity (default 1)."},
    "input": {"type": "string", "description": "Inline structure/coordinate text."},
    "source_ref": {"type": "object", "description": "Structure-source provenance."},
    "asset_id": {"type": "string", "description": "Uploaded structure asset id."},
}

_BATCH_INPUT_PROPERTIES: dict[str, Any] = {
    "source_type": {"type": "string", "enum": ["batch_structures"]},
    "items": {
        "type": "array",
        "items": {"type": "object"},
        "description": "One entry per structure: {xyz, name?, charge?, multiplicity?}.",
    },
    "from_artifact": {"type": "string"},
    "items_file": {"type": "string"},
    "input": {"type": "string", "description": "Inline structure input (single-item batch)."},
    "source": {"type": "string", "description": "Alias of input for stage payloads."},
    "selected_ids": {"type": "array", "items": {"type": "string"}},
    "charge": {"type": "integer", "description": "Total charge (default 0)."},
    "multiplicity": {"type": "integer", "description": "Spin multiplicity (default 1)."},
}

_RESOURCE_PROPERTIES: dict[str, Any] = {
    "nproc": {"type": "integer", "minimum": 1, "description": "Cores (preferred key)."},
    "ncores": {"type": "integer", "minimum": 1, "description": "Alias of nproc."},
    "cores": {"type": "integer", "minimum": 1, "description": "Alias of nproc."},
    "mem": {"type": "string", "description": "Memory with unit, e.g. 8GB."},
    "memory_gb": {"type": "number", "description": "Legacy memory alias (GB)."},
    "walltime_s": {
        "type": "integer",
        "minimum": 1,
        "description": (
            "Request walltime in seconds. The admission ceiling caps it "
            "(default request 86400 s / 24 h); process termination and the "
            "queue deadline are separate enforced limits, not this field."
        ),
    },
    "walltime": {
        "type": "string",
        "description": "Alias accepted by admission: 'HH:MM' or seconds string.",
    },
}


_PES_CONSTRAINTS = (
    "coverage is the direct mode=bond_length_scan branch; the manifest path "
    "(from-job/from-artifact) is not published yet",
    "coordinate atoms are 0-based indices into the source structure; verify "
    "the atom mapping before submitting",
    "input.protocol levels follow the pes_bond_scan schema "
    "(scan_coordinate/scan_driver/scan_optimizer/single_point)",
    "method.levels is rejected for PESsearch: the scan protocol rides in "
    "input.protocol (or input.scan_request)",
    "TS/INT candidates picked from a scan are initial guesses only; publish "
    "and BatchOptimize before any stationary-point claim (see "
    "orchestration.pes_to_batch)",
)

_SCAN_CONSTRAINTS = (
    "bond-length tasks default to PESsearch; scan needs an explicit user "
    "choice or a recorded, confirmed limitation",
    "coordinates require a confirmable atom count: submit explicit geometry "
    "(xyz_text/inline input) so 0-based indices can be verified",
    "kind-aware ranges: distance > 0; angle 0-180; dihedral -360..360; "
    "start != end; integer scan_points >= 2",
    "ScanTS is opt-in (scan_use_scants/use_scants) and defaults to off; a "
    "plain scan never implies an internal TS optimization",
)

_COORDINATE_OBJECT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["distance", "angle", "dihedral"]},
        "atoms": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0},
            "minItems": 2,
            "maxItems": 4,
            "description": "0-based atom indices (2 distance / 3 angle / 4 dihedral).",
        },
        "unit": {"type": "string", "enum": ["angstrom", "degree"]},
        "start": {"type": "number"},
        "end": {"type": "number"},
        "n_points": {"type": "integer", "minimum": 3, "maximum": 101},
        "bond_type": {"type": "string"},
    },
    "required": ["atoms", "start", "end"],
}

_PES_INPUT_PROPERTIES: dict[str, Any] = {
    **_CONFSEARCH_INPUT_PROPERTIES,
    "source": {
        "type": "object",
        "description": (
            "BondLengthScanSource for mode=bond_length_scan: source_type "
            "(task_artifact|structure_asset|xyz_text), xyz_text/asset_path/"
            "artifact_path, source_job_id, structure_selector, charge, multiplicity."
        ),
    },
    "coordinate": {**_COORDINATE_OBJECT, "description": "Primary scan coordinate (0-based atoms)."},
    "coordinates": {
        "type": "array",
        "items": _COORDINATE_OBJECT,
        "description": "Synchronous coordinates (max 64); keeps topology, never flattened.",
    },
    "selection": {
        "type": "object",
        "description": "Functional selection metadata (kind/atom_indices/groups).",
    },
    "protocol": {
        "type": "object",
        "description": (
            "Scan protocol: scan_driver (software/mode/reuse_previous_geometry/"
            "use_scants/...), scan_optimizer (method GFN2-xTB, ...), single_point "
            "(enabled/software/method/B97-3c/...). Levels follow pes_bond_scan."
        ),
    },
    "source_job_id": {"type": "string", "description": "Upstream job id (manifest path)."},
    "from_artifact": {"type": "string", "description": "Upstream artifact (manifest path)."},
    "from": {"type": "string", "description": "Resolved manifest path (post-resolution mirror)."},
    "coordinate_plan": {"type": "object", "description": "Manifest-path --plan JSON."},
    "product": {"type": "string"},
    "ts_guess": {"type": "string"},
}

_SCAN_INPUT_PROPERTIES: dict[str, Any] = {
    **_CONFSEARCH_INPUT_PROPERTIES,
    "scan_coordinates": {
        "type": "array",
        "items": _COORDINATE_OBJECT,
        "description": "Coordinates may ride in input or method; the shared validator reads input FIRST (input.scan_coordinates/input.coordinate before method) — do not override an input coordinate via method.",
    },
    "coordinate": {
        "oneOf": [
            {
                "type": "string",
                "description": "Compact form 'atom_i,atom_j[,atom_k[,atom_l]],start,end'.",
            },
            _COORDINATE_OBJECT,
        ]
    },
}


def canonical_workflow(value: str) -> str | None:
    """Return the catalog spelling of *value* (case-insensitive), else ``None``."""
    wanted = str(value).strip().lower()
    for name in ACTIVE_WORKFLOWS:
        if name.lower() == wanted:
            return name
    return None


def workflow_suggestion(value: str) -> list[str]:
    """Closest active workflow ids for a typo (case-insensitive, canonical output)."""
    wanted = str(value).strip()
    by_fold = {name.casefold(): name for name in ACTIVE_WORKFLOWS}
    folded = [name.casefold() for name in ACTIVE_WORKFLOWS]
    matches = difflib.get_close_matches(wanted.casefold(), folded, n=1, cutoff=0.6)
    return [by_fold[match] for match in matches]


def _method_properties(workflow: str) -> tuple[dict[str, Any], dict[str, list[str]]]:
    contract = WORKFLOW_CONTRACTS[workflow]
    enums = {
        key: sorted(values) for key, values in contract.method_enums.items()
    }
    properties: dict[str, Any] = {}
    for key in sorted(contract.method_keys):
        if key in enums:
            properties[key] = {"type": "string", "enum": enums[key]}
        elif key in {"levels", "batch_roles"}:
            properties[key] = {"type": "object"}
        elif key in {"select", "selected_ids"}:
            properties[key] = {"type": ["string", "array"]}
        elif key == "confsearch_profile":
            properties[key] = {"type": "string", "description": "Workbench UI selection (no scheduler consumer)."}
        else:
            properties[key] = {"type": ["string", "number", "boolean", "object", "array"]}
    return properties, enums


def _level_table(workflow: str) -> dict[str, list[str]]:
    contract = WORKFLOW_CONTRACTS[workflow]
    return {
        level_id: sorted(fields | {"engine"})
        for level_id, fields in contract.levels.items()
    }


def _confsearch_block() -> dict[str, Any]:
    contract = WORKFLOW_CONTRACTS["Confsearch"]
    properties, enums = _method_properties("Confsearch")
    return {
        "workflow": "Confsearch",
        "coverage": "partial",
        "gaps": [
            "value-level mirror equivalence is not enforced: the engine's "
            "role fallbacks (TS→INT, recalc auto→numeric) are not reproduced here",
        ],
        "input": {
            "required_any": list(contract.required_input_any),
            "properties": _CONFSEARCH_INPUT_PROPERTIES,
            "additional_properties": False,
        },
        "method": {
            "properties": properties,
            "enums": enums,
            "levels": _level_table("Confsearch"),
            "additional_properties": False,
        },
        "resources": {"properties": _RESOURCE_PROPERTIES},
        "examples": [
            {
                "workflow": "Confsearch",
                "input": {"source_type": "smiles", "source": "CCO", "charge": 0, "multiplicity": 1},
                "method": {
                    "protocol": "xtb-crest",
                    "profile": "default",
                    "refinement_policy": "screen",
                },
                "resources": {"nproc": 4, "mem": "8GB"},
            },
            {
                "workflow": "Confsearch",
                "input": {"smiles": "CCO"},
                "method": {
                    "protocol": "censo-crest",
                    "refinement_policy": "rank1",
                    "levels": {
                        "dft_opt": {"engine": "orca", "functional": "wB97X-D4"},
                        "refinement_sp": {"engine": "orca", "functional": "wB97X-D4"},
                    },
                },
                "resources": {"nproc": 4, "mem": "8GB"},
            },
        ],
        "constraints": list(_CONFSEARCH_CONSTRAINTS),
        "status_vocabulary": _STATUS_VOCABULARY,
    }


def _batch_block() -> dict[str, Any]:
    contract = WORKFLOW_CONTRACTS["BatchOptimize"]
    properties, enums = _method_properties("BatchOptimize")
    return {
        "workflow": "BatchOptimize",
        "coverage": "partial",
        "gaps": [
            "value-level batch_roles mirror equivalence is enforced only when the "
            "role field is explicitly present; engine fallbacks stay unverified",
        ],
        "input": {
            "required_any": list(contract.required_input_any),
            "properties": _BATCH_INPUT_PROPERTIES,
            "additional_properties": False,
        },
        "method": {
            "properties": properties,
            "enums": enums,
            "levels": _level_table("BatchOptimize"),
            "additional_properties": False,
        },
        "resources": {"properties": _RESOURCE_PROPERTIES},
        "examples": [
            {
                "workflow": "BatchOptimize",
                "input": {
                    "source_type": "batch_structures",
                    "items": [{"xyz": "O 0 0 0\nH 1 0 0\nH 0 1 0"}],
                    "charge": 0,
                    "multiplicity": 1,
                },
                "method": {
                    "profile": "opt_freq",
                    "batch_roles": {"int": {"role": "int", "method": "r2SCAN-3c"}},
                    "optimization_method": "r2SCAN-3c",
                },
                "resources": {"nproc": 4, "mem": "8GB"},
            },
            {
                "workflow": "BatchOptimize",
                "input": {"items_file": "inputs/items.json"},
                "method": {"profile": "opt_only"},
                "resources": {"nproc": 4, "mem": "8GB"},
            },
        ],
        "constraints": list(_BATCH_CONSTRAINTS),
        "status_vocabulary": _STATUS_VOCABULARY,
    }


def _pes_block() -> dict[str, Any]:
    contract = WORKFLOW_CONTRACTS["PESsearch"]
    properties, enums = _method_properties("PESsearch")
    return {
        "workflow": "PESsearch",
        "coverage": "partial",
        "partial_scope": (
            "mode=bond_length_scan direct branch only; the manifest path is not published"
        ),
        "gaps": [
            "input.protocol level values are type-checked only; per-level value "
            "semantics stay with the pes_bond_scan schema and the pydantic bond "
            "scan model downstream",
        ],
        "input": {
            "required_any": list(contract.required_input_any),
            "properties": _PES_INPUT_PROPERTIES,
            "additional_properties": False,
        },
        "method": {
            "properties": properties,
            "enums": enums,
            "additional_properties": False,
        },
        "resources": {"properties": _RESOURCE_PROPERTIES},
        "examples": [
            {
                "workflow": "PESsearch",
                "input": {
                    "source": {
                        "source_type": "xyz_text",
                        "xyz_text": "3\n\nO 0.0 0.0 0.0\nH 0.957 0.0 0.0\nH -0.24 0.927 0.0",
                        "charge": 0,
                        "multiplicity": 1,
                    },
                    "coordinate": {
                        "kind": "distance",
                        "atoms": [0, 1],
                        "unit": "angstrom",
                        "start": 0.957,
                        "end": 2.4,
                        "n_points": 15,
                    },
                    "protocol": {
                        "scan_driver": {
                            "software": "orca",
                            "mode": "relaxed_scan",
                            "reuse_previous_geometry": True,
                        },
                        "scan_optimizer": {"method": "GFN2-xTB"},
                        "single_point": {
                            "enabled": True,
                            "software": "orca",
                            "method": "B97-3c",
                        },
                    },
                },
                "method": {"mode": "bond_length_scan"},
                "resources": {"nproc": 4, "mem": "8GB"},
            },
        ],
        "constraints": list(_PES_CONSTRAINTS),
        "status_vocabulary": _STATUS_VOCABULARY,
    }


def _scan_block() -> dict[str, Any]:
    contract = WORKFLOW_CONTRACTS["scan"]
    properties, enums = _method_properties("scan")
    return {
        "workflow": "scan",
        "coverage": "partial",
        "partial_scope": "relaxed internal-coordinate scan (ORCA path); ScanTS opt-in",
        "gaps": [
            "multi-coordinate coupling semantics beyond the shared validator are "
            "not projected here; the backend contract remains authoritative",
        ],
        "input": {
            "required_any": list(contract.required_input_any),
            "properties": _SCAN_INPUT_PROPERTIES,
            "additional_properties": False,
        },
        "method": {
            "properties": properties,
            "enums": enums,
            "levels": _level_table("scan"),
            "additional_properties": False,
        },
        "resources": {"properties": _RESOURCE_PROPERTIES},
        "examples": [
            {
                "workflow": "scan",
                "input": {
                    "source_type": "xyz_text",
                    "source": "3\n\nO 0.0 0.0 0.0\nH 0.957 0.0 0.0\nH -0.24 0.927 0.0",
                    "charge": 0,
                    "multiplicity": 1,
                },
                "method": {
                    "coordinate": "0,1,0.957,2.4",
                    "scan_points": 12,
                },
                "resources": {"nproc": 4, "mem": "8GB"},
            },
        ],
        "constraints": list(_SCAN_CONSTRAINTS),
        "status_vocabulary": _STATUS_VOCABULARY,
    }


def describe_workflow(workflow: str) -> dict[str, Any]:
    """Return the published contract block for *workflow*.

    A workflow outside the staged coverage returns an explicit
    ``not_provided`` block: the caller must not infer a schema that does not
    exist, and the rest of the capabilities payload still answers.  Every
    block (including ``not_provided``) carries the workflow's ``guide_ref``:
    the guidance content pack covers all active workflows, and a guide is
    guidance — never a parameter authority or an execution permission.
    """
    canonical = canonical_workflow(workflow) or workflow
    if canonical == "Confsearch":
        block = _confsearch_block()
    elif canonical == "BatchOptimize":
        block = _batch_block()
    elif canonical == "PESsearch":
        block = _pes_block()
    elif canonical == "scan":
        block = _scan_block()
    else:
        block = {
            "workflow": canonical,
            "coverage": "not_provided",
            "reason": (
                "the submit schema for this workflow is not published yet; "
                "use the CLI help or the Workbench form, and treat unknown method "
                "keys as unsafe"
            ),
            "status_vocabulary": _STATUS_VOCABULARY,
        }
    guide_id = workflow_guide_map().get(canonical)
    if guide_id is not None:
        block["guide_ref"] = {
            "guide_id": guide_id,
            "pack_version": GUIDANCE_PACK_VERSION,
            "tool": "acp_get_guidance",
        }
    return block
