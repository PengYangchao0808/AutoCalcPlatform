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
the hash only through ``_IDENTITY_ARTIFACT_KEYS`` as content sha256 —
an unreadable input raises :class:`IdentityInputMissing`.

``identity_fingerprint = "v2:" + sha256(canonical_json)[:32]``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Final

from acp.calculations.contracts import (
    CalculationPlan,
    CalculationStep,
    OptimizationMode,
    StepKind,
    StructureArtifact,
    StructureRole,
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

IDENTITY_SCHEMA: Final = 2
FINGERPRINT_PREFIX: Final = "v2:"

#: Resource/spec keys whose value is a file LOCATION → replaced by content sha256.
_IDENTITY_ARTIFACT_KEYS: Final[frozenset[str]] = frozenset(
    {"freq_log_path", "geometry_file", "hessian_file", "coordinates_file"}
)

#: Execution-domain keys never part of the science identity.
_EXECUTION_DOMAIN_KEYS: Final[frozenset[str]] = frozenset(
    {"output_dir", "config", "nproc", "mem", "maxcore", "timeout_s"}
)


class IdentityInputMissing(ValueError):  # noqa: N818 — name pinned by D06 plan contract
    """An identity input file (item structure or declared artifact) is unreadable."""


# ── content binding ──────────────────────────────────────────────────────


def content_sha256(path: Path | str, *, location_root: Path | str | None = None) -> str:
    """Return the sha256 hex digest of *path* content (no location binding)."""
    target = Path(path)
    if not target.is_absolute() and location_root is not None:
        target = Path(location_root) / target
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise IdentityInputMissing(f"identity input not readable: {target}") from exc
    return hashlib.sha256(data).hexdigest()


def _read_bytes(path: Path, *, location_root: Path | str | None = None) -> bytes:
    target = path if path.is_absolute() or location_root is None else Path(location_root) / path
    try:
        return target.read_bytes()
    except OSError as exc:
        raise IdentityInputMissing(f"identity input not readable: {target}") from exc


# ── canonicalisation (sorted keys, None omitted, no str()/repr) ──────────


def _canonical(value: Any, *, location_root: Path | str | None = None) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        raise IdentityInputMissing(
            f"path value {value!s} must be declared in _IDENTITY_ARTIFACT_KEYS "
            "or _EXECUTION_DOMAIN_KEYS — file locations never enter the hash"
        )
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, raw in sorted(((str(k), v) for k, v in value.items()), key=lambda kv: kv[0]):
            if raw is None or key in _EXECUTION_DOMAIN_KEYS:
                continue
            if key in _IDENTITY_ARTIFACT_KEYS and isinstance(raw, str):
                out[key] = {"content_sha256": content_sha256(raw, location_root=location_root)}
            else:
                out[key] = _canonical(raw, location_root=location_root)
        return out
    if isinstance(value, (list, tuple)):
        return [_canonical(entry, location_root=location_root) for entry in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(entry, location_root=location_root) for entry in value), key=str)
    if is_dataclass(value) and not isinstance(value, type):
        return _canonical(asdict(value), location_root=location_root)
    raise IdentityInputMissing(
        f"cannot canonicalise {type(value).__name__} for identity — repr/str hashing is forbidden"
    )


def identity_fingerprint(payload: Mapping[str, Any]) -> str:
    """``"v2:" + sha256(canonical_json)[:32]`` over *payload*."""
    blob = json.dumps(
        _canonical(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return FINGERPRINT_PREFIX + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


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


# ── identity object ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScientificIdentity:
    """Two-layer science identity for one calculation plan."""

    plan_identity: str
    step_identities: tuple[str, ...]
    items: tuple[dict[str, Any], ...]
    steps: tuple[dict[str, Any], ...]
    path_remaps: tuple[dict[str, Any], ...]

    def payload(self) -> dict[str, Any]:
        """Full JSON-safe identity record (checkpoint / step_result binding)."""
        return {
            "identity_schema": IDENTITY_SCHEMA,
            "plan_identity": self.plan_identity,
            "step_identities": list(self.step_identities),
            "items": [dict(item) for item in self.items],
            "steps": [dict(step) for step in self.steps],
            "path_remaps": [dict(remap) for remap in self.path_remaps],
        }


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


# ── attempt metadata (NOT part of the science hash) ──────────────────────


def config_digest(config: Mapping[str, Any] | None) -> str | None:
    """Digest of a resolved config — attempt metadata, never science hash."""
    if config is None:
        return None
    blob = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def current_config_digest() -> str | None:
    """Digest of the currently resolved platform config (``None`` if unreadable)."""
    from cccp.config import load_config

    try:
        return config_digest(load_config())
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        logger.warning("identity: config digest unavailable; treating as unknown", exc_info=True)
        return None


# ── location resolution / legacy reuse preconditions ─────────────────────


def resolve_stored_location(stored: str | None, fallback: str | None) -> str | None:
    """Prefer the checkpoint-stored location; else the current record fallback.

    The fallback is the location re-resolved from the current
    ``record.work_dir`` (batch) or the current item path (executor).
    """
    if isinstance(stored, str) and stored:
        return stored
    if isinstance(fallback, str) and fallback:
        return fallback
    return None


def inputs_not_newer_than(
    checkpoint_file: Path | str,
    input_locations: Sequence[Path | str],
) -> bool:
    """Legacy-reuse precondition: every input's mtime <= the checkpoint's.

    Missing inputs fail closed (not reusable).  Used together with
    ``load_checkpoint(..., allow_legacy_fingerprint=True)`` before adopting
    a schema=1 checkpoint whose completed artifacts are still present.
    """
    try:
        checkpoint_mtime = Path(checkpoint_file).stat().st_mtime
    except OSError:
        return False
    for location in input_locations:
        try:
            input_mtime = Path(location).stat().st_mtime
        except OSError:
            return False
        if input_mtime > checkpoint_mtime:
            return False
    return True
