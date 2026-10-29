"""Scientific request identity v2 (D06) — content-bound plan fingerprints.

Two layers per plan: ``plan_identity`` (whole-plan checkpoint binding) and
per-step ``step_identities`` (prefix-cumulative single-step adoption).
Science identity covers item content digests (never paths / candidate ids /
profile names) plus the FULL effective parameter output of
``cccp.calculation.batch.resolve_effective_params`` together with the raw
normalized task request (task-specific options: scan coordinates, optimize
constraints, IRC directions, CASSCF active space …).

Execution-domain keys (``output_dir``, embedded ``config``, resource hints)
are dropped; config changes are tracked separately by ``config_digest``
(attempt metadata, never part of the science hash).  File locations enter
the hash only through the cccp ``_IDENTITY_ARTIFACT_KEYS`` as content sha256 —
an unreadable input raises :class:`IdentityInputMissing`.

**T53 split**: the pure, contract-free fingerprint algorithm now lives in
:mod:`cccp.analysis.identity` (canonicalisation, content digests,
``identity_fingerprint``, config digests).  This module keeps the
contract-coupled plan → identity orchestration (:func:`compute_identity`
over ``CalculationPlan`` / ``CalculationStep``) and re-exports every sunk
primitive **unchanged** (same objects, same output) so the historical
``acp.calculations.identity`` import path keeps working.

``identity_fingerprint = "v2:" + sha256(canonical_json)[:32]``.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from acp.calculations.contracts import (
    CalculationPlan,
    CalculationStep,
    OptimizationMode,
    StepKind,
    StructureArtifact,
    StructureRole,
)

# Pure fingerprint primitives sunk to cccp (T53).  ``_canonical`` / ``_read_bytes``
# are the shared normalisation core used by ``compute_identity`` below; importing
# them (instead of duplicating) keeps the single-sink guarantee.
from cccp.analysis.identity import (
    IDENTITY_SCHEMA,
    IdentityInputMissing,
    ScientificIdentity,
    _canonical,
    _read_bytes,
    config_digest,
    content_sha256,
    current_config_digest,
    identity_fingerprint,
    inputs_not_newer_than,
    resolve_stored_location,
)
from cccp.calculation._common import theory_run_config
from cccp.calculation.batch import resolve_effective_params
from cccp.qc.resolved_spec import RESOLVED_FIELDS

logger = logging.getLogger(__name__)

__all__ = [
    "IDENTITY_SCHEMA",
    "IdentityInputMissing",
    "ScientificIdentity",
    "compute_identity",
    "config_digest",
    "content_sha256",
    "current_config_digest",
    "identity_fingerprint",
    "inputs_not_newer_than",
    "resolve_stored_location",
]


# ── plan / step normalisation (mirrors executor dispatch sources) ────────


def _coerce_item(item: StructureArtifact | Mapping[str, Any]) -> tuple[str, list[str], Path]:
    if isinstance(item, StructureArtifact):
        return item.role.value, [str(e) for e in item.elements], Path(item.path)
    if isinstance(item, Mapping):
        raw_path = item.get("path")
        if not isinstance(raw_path, str):
            raw_path = item.get("geometry")
        path = Path(raw_path) if isinstance(raw_path, str) else Path(".")
        raw_role = item.get("role")
        try:
            role = StructureRole(raw_role) if isinstance(raw_role, str) else StructureRole.MINIMUM
        except ValueError:
            role = StructureRole.MINIMUM
        raw_elements = item.get("elements")
        elements = [str(e) for e in raw_elements] if isinstance(raw_elements, list) else []
        return role.value, elements, path
    raise IdentityInputMissing(f"unsupported plan item type: {type(item).__name__}")


def _normalise_step(step: CalculationStep | Mapping[str, Any]) -> CalculationStep:
    if isinstance(step, CalculationStep):
        return step
    raw_kind = step.get("kind")
    if not isinstance(raw_kind, str):
        raise IdentityInputMissing("calculation step kind must be a string")
    raw_mode = step.get("mode")
    mode = (
        OptimizationMode(raw_mode) if isinstance(raw_mode, str) else OptimizationMode.UNCONSTRAINED
    )
    raw_spec = step.get("spec")
    spec = dict(raw_spec) if isinstance(raw_spec, Mapping) else None
    return CalculationStep(kind=StepKind(raw_kind), mode=mode, spec=spec)


def _raw_spec(step: CalculationStep) -> dict[str, Any]:
    spec = step.spec
    if spec is None:
        return {}
    if isinstance(spec, Mapping):
        return dict(spec)
    if is_dataclass(spec) and not isinstance(spec, type):
        return asdict(spec)
    raise IdentityInputMissing(f"unsupported step spec type: {type(spec).__name__}")


def _extract_method(spec: Any, default: str) -> str:
    """Mirror ``executor._extract_method`` (identity must track dispatch)."""
    from acp.calculations.contracts import OptimizationSpec

    if isinstance(spec, OptimizationSpec):
        return spec.method or default
    if isinstance(spec, Mapping) and "method" in spec:
        return str(spec["method"])
    return default


def _optional_int(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _normalise_scan_task_options(spec: dict[str, Any]) -> dict[str, Any]:
    """Collapse the scan ScanTS aliases onto one canonical key.

    ``scan_use_scants`` / ``use_scants`` both name the effective ScanTS
    toggle; canonicalising the alias means the spelling never changes the
    identity.  An absent field is left absent — a legacy payload without the
    field must not be adopted as the new plain-scan semantics, so the
    conservative recompute path stays reachable.
    """
    alias = spec.get("scan_use_scants")
    if not isinstance(alias, bool):
        return spec
    normalised = dict(spec)
    normalised.pop("scan_use_scants", None)
    normalised.setdefault("use_scants", alias)
    return normalised


# ── identity object (contract-coupled: ACP plan/step dispatch) ───────────


def compute_identity(
    plan: CalculationPlan,
    *,
    run_config: Mapping[str, Any] | None = None,
    location_root: Path | str | None = None,
    previous_paths: Mapping[str, str] | None = None,
) -> ScientificIdentity:
    """Compute the v2 science identity of *plan*.

    Args:
        plan: The calculation plan (items + ordered steps).
        run_config: Optional run-config override; when ``None`` the theory
            layer of the plan's embedded ``config`` (the same source the
            executor dispatch resolves with) is used.
        location_root: Base for relative artifact locations.
        previous_paths: Checkpoint-stored item path mapping (``{"0": path}``)
            used to record ``identity.path_remapped`` — remaps never change
            the fingerprint.

    Returns:
        :class:`ScientificIdentity` with plan/step fingerprints.

    Raises:
        IdentityInputMissing: When an item structure or a declared
            ``_IDENTITY_ARTIFACT_KEYS`` location cannot be read.
    """
    items: list[dict[str, Any]] = []
    item_contents: dict[str, bytes] = {}
    current_paths: list[str] = []
    for index, raw_item in enumerate(plan.items):
        _role, elements, path = _coerce_item(raw_item)
        data = _read_bytes(path, location_root=location_root)
        items.append(
            {
                "role": _role,
                "elements": elements,
                "content_sha256": hashlib.sha256(data).hexdigest(),
            }
        )
        item_contents[f"item_{index}"] = data
        current_paths.append(str(path))

    steps: list[dict[str, Any]] = []
    for raw_step in plan.steps:
        step = _normalise_step(raw_step)
        spec_raw = _raw_spec(step)
        step_run_config = (
            run_config if run_config is not None else theory_run_config(spec_raw.get("config"))
        )
        spec = _canonical(spec_raw, location_root=location_root)
        if step.kind is StepKind.SCAN:
            spec = _normalise_scan_task_options(spec)
        method = _extract_method(step.spec, plan.profile or "r2SCAN-3c")
        basis = spec.get("basis")
        parameters = {key: spec[key] for key in RESOLVED_FIELDS if spec.get(key) is not None}
        if "basis" not in parameters:
            parameters["basis"] = basis if isinstance(basis, str) else ""
        request_payload: dict[str, Any] = {
            "task": step.kind.value,
            "backend": str(spec.get("backend") or spec.get("engine") or "unspecified"),
            "method": method,
            "parameters": parameters,
            "charge": _optional_int(spec.get("charge"), 0),
            "multiplicity": _optional_int(spec.get("multiplicity"), 1),
            "electronic_state": spec.get("electronic_state"),
            "inputs": item_contents,
            "version_constraint": (
                spec.get("version_constraint")
                if isinstance(spec.get("version_constraint"), str)
                else None
            ),
        }
        effective = resolve_effective_params(request_payload, run_config=step_run_config)
        steps.append(
            {
                "kind": step.kind.value,
                "mode": step.mode.value,
                "effective_spec": {
                    # full EffectiveTaskParams output incl. charge/multiplicity/inputs
                    "request": effective.identity_payload(),
                    # raw normalized task request: task-specific options ride here
                    "task_options": spec,
                },
            }
        )

    remaps: list[dict[str, Any]] = []
    if previous_paths:
        for index, current in enumerate(current_paths):
            previous = previous_paths.get(str(index))
            if isinstance(previous, str) and previous and previous != current:
                try:
                    previous_digest: str | None = content_sha256(
                        previous, location_root=location_root
                    )
                except IdentityInputMissing:
                    previous_digest = None
                remap = {
                    "event": "identity.path_remapped",
                    "index": index,
                    "from": previous,
                    "to": current,
                    "from_content_sha256": previous_digest,
                }
                remaps.append(remap)
                logger.info(
                    "identity.path_remapped: item %s %s -> %s (content-bound identity unchanged)",
                    index,
                    previous,
                    current,
                )

    item_entries = tuple(dict(entry) for entry in items)
    step_entries = tuple(dict(entry) for entry in steps)
    plan_identity = identity_fingerprint(
        {
            "scope": "plan",
            "workflow": plan.workflow,
            "items": list(item_entries),
            "steps": list(step_entries),
        }
    )
    step_identities = tuple(
        identity_fingerprint(
            {
                "scope": "step",
                "index": index,
                "workflow": plan.workflow,
                "items": list(item_entries),
                "steps": list(step_entries[: index + 1]),
            }
        )
        for index in range(len(step_entries))
    )
    return ScientificIdentity(
        plan_identity=plan_identity,
        step_identities=step_identities,
        items=item_entries,
        steps=step_entries,
        path_remaps=tuple(remaps),
    )
