"""ACP-side legacy adapters: legacy contracts <-> cccp task envelopes.

DATA TRANSFORM ONLY.  The adapter performs in-memory conversion between
the ACP compatibility contracts (``CalculationRequest`` /
``CalculationResult``) and the cccp task envelopes
(``TaskRequest`` / ``TaskResult``).  It never writes files, never
publishes platform artifacts (persistence belongs to the ACP result
publication layer), and never fills defaults — empty typed fields stay
empty and absent legacy keys stay absent.

Platform identity (workflow / profile / candidate id / trajectory item
id) and the verbatim legacy residue needed for exact reconstruction
travel through :class:`LegacyBinding`, which lives only on the ACP side:
the caller passes it in and gets it back with the result.

Authoritative mapping tables: ``docs/ACP_CCCP_Task_API_DevDoc.md``.

Author: QCcalc Team
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from acp.calculations.contracts import (
    CalculationRequest,
    CalculationResult,
    Provenance,
    StructureArtifact,
    electronic_state_config_from_dict,
)
from acp.calculations.contracts import (
    JsonValue as LegacyJsonValue,
)
from cccp.calculation.contracts import (
    ArtifactRef,
    ElectronicStateSpec,
    JsonValue,
    OptimizationMode,
    StructureRole,
    casscf_spec_from_dict,
)
from cccp.calculation.contracts import (
    Provenance as TaskProvenance,
)
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import (
    CasscfOptions,
    FrequencyOptions,
    IrcDirection,
    IrcOptions,
    MethodSpec,
    OptimizeOptions,
    RescueSpec,
    ScanCoordinateSpec,
    ScanOptions,
    SinglePointOptions,
    StructureInput,
    TaskKind,
    TaskOptions,
    TaskRequest,
    TaskResources,
    ThermochemistryOptions,
    TsSpec,
)
from cccp.calculation.results import (
    CasscfPayload,
    FrequencyPayload,
    OptimizePayload,
    SinglePointPayload,
    TaskPayload,
    TaskResult,
    ThermochemistryPayload,
)

# ── committed legacy keys: canonical name -> accepted legacy names ──────
# The first legacy name is the canonical emission name; alias spellings are
# preserved through ``LegacyBinding.resources_key_names``.

_ALIAS_RESOURCE_KEYS: dict[str, tuple[str, ...]] = {
    "backend": ("backend", "engine"),
    "scl_zpe": ("scl_zpe", "scale_factor"),
    "initial_hessian": ("initial_hessian", "opt_initial_hessian"),
    "trust_radius": ("trust_radius", "opt_trust_radius"),
    "recalc_hess": ("recalc_hess", "opt_recalc_hess"),
}

# Envelope-level scalar keys with typed homes on TaskRequest/TaskResources.
_ENVELOPE_RESOURCE_KEYS: tuple[str, ...] = (
    "backend",
    "basis",
    "charge",
    "multiplicity",
    "nproc",
    "mem",
    "maxcore",
    "timeout_s",
    "output_dir",
)

# Committed legacy result-metadata keys -> typed payload homes.
_METADATA_ALIASES: dict[str, tuple[str, ...]] = {
    "optimization_status": ("optimization_status",),
    "rescue_attempts": ("rescue_attempts",),
    "rescue_actions": ("rescue_actions",),
    "failure_type": ("failure_type",),
    "electronic_state": ("electronic_state",),
    "n_imaginary": ("n_imaginary",),
    "enthalpy_hartree": ("enthalpy_hartree",),
    "gibbs_hartree": ("gibbs_hartree",),
    "entropy_au": ("entropy_au",),
    "gibbs_source": ("gibbs_source", "selected_gibbs_source"),
    "standard_state": ("standard_state",),
}
_RAW_METADATA_KEYS: frozenset[str] = frozenset({"multireference", "casscf"})


@dataclass(frozen=True, slots=True)
class LegacyBinding:
    """ACP-side binding info carried across the adapter (never enters cccp).

    Holds platform identity (workflow / profile / candidate id / trajectory
    item id), the artifact root used to map artifact paths, and the verbatim
    legacy residue (unmapped keys, alias key names, raw dict-valued forms)
    that makes ``to_legacy_request`` / ``to_legacy_result`` exact.
    """

    workflow: str = ""
    profile: str | None = None
    candidate_id: str | None = None
    trajectory_item_id: str | None = None
    artifact_root: Path | None = None
    config: Mapping[str, JsonValue] | None = None
    legacy_method: str = ""
    resources_extra: dict[str, JsonValue] = field(default_factory=dict)
    resources_raw: dict[str, JsonValue] = field(default_factory=dict)
    resources_key_names: dict[str, str] = field(default_factory=dict)
    metadata_extra: dict[str, JsonValue] = field(default_factory=dict)
    metadata_raw: dict[str, JsonValue] = field(default_factory=dict)
    metadata_key_names: dict[str, str] = field(default_factory=dict)


# ── coercion helpers (documented in the mapping table) ──────────────────


def _pop_alias(
    resources: dict[str, JsonValue],
    names: Sequence[str],
    key_names: dict[str, str],
    canonical: str,
) -> object | None:
    """Pop the first present legacy alias and record its original name."""
    for name in names:
        if name in resources:
            value = resources.pop(name)
            key_names[canonical] = name
            return value
    return None


def _pop_named(
    resources: dict[str, JsonValue],
    name: str,
    key_names: dict[str, str],
) -> object | None:
    """Pop one legacy key and record its original name."""
    if name not in resources:
        return None
    value = resources.pop(name)
    key_names[name] = name
    return value


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and float(value).is_integer():
        return int(value)
    return None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_path_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _is_geometry_matrix(value: object) -> bool:
    """Return whether a JSON value has the geometry shape ``list[list[number]]``."""
    if not isinstance(value, list) or not value:
        return False
    return all(
        isinstance(row, list)
        and len(row) == 3
        and all(
            isinstance(component, (int, float)) and not isinstance(component, bool)
            for component in row
        )
        for row in value
    )


def _parse_legacy_coordinate(value: object, index: int) -> ScanCoordinateSpec:
    """Parse one legacy coordinate entry (``atom1,atom2,start,end`` form)."""
    if isinstance(value, Mapping):
        atoms_raw = value.get("atoms")
        atoms: list[int] = []
        if isinstance(atoms_raw, (list, tuple)):
            for atom in atoms_raw:
                if isinstance(atom, bool) or not isinstance(atom, int):
                    message = f"scan coordinate {index + 1} atoms must be integers"
                    raise TaskInputError(message)
                atoms.append(atom)
        return ScanCoordinateSpec(
            atoms=tuple(atoms),
            start=_as_float(value.get("start")),
            end=_as_float(value.get("end")),
            kind=_as_str(value.get("kind")) or "distance",
        )
    if isinstance(value, str):
        parts = [text.strip() for text in value.split(",")]
        if len(parts) not in (4, 5, 6):
            message = f"scan coordinate {index + 1} must be atom1,atom2,start,end"
            raise TaskInputError(message)
        *atom_texts, start_text, end_text = parts
        atoms = []
        for atom_text in atom_texts:
            try:
                atoms.append(int(atom_text))
            except ValueError as exc:
                message = f"scan coordinate {index + 1} atoms must be integers"
                raise TaskInputError(message) from exc
        try:
            start = float(start_text)
            end = float(end_text)
        except ValueError as exc:
            message = f"scan coordinate {index + 1} start/end must be numbers"
            raise TaskInputError(message) from exc
        kind = {2: "distance", 3: "angle", 4: "dihedral"}.get(len(atoms), "distance")
        return ScanCoordinateSpec(atoms=tuple(atoms), start=start, end=end, kind=kind)
    message = f"scan coordinate {index + 1} must be a string or mapping"
    raise TaskInputError(message)


# ── request conversion ──────────────────────────────────────────────────


def to_task_request(
    request: CalculationRequest,
    task: TaskKind | str,
    *,
    directions: Sequence[str] | None = None,
) -> tuple[TaskRequest, LegacyBinding]:
    """Convert a legacy request into a typed :class:`TaskRequest` + binding.

    Pure data transform: no defaults are filled (empty typed fields stay
    empty) and every legacy field lands in a typed home or in the binding
    residue.  ``directions`` supplies IRC directions when the legacy
    request carried them out-of-band (``run_irc(..., directions=...)``).

    Raises:
        TaskInputError: On an unknown task, a state-sweep
            ``electronic_state`` (the batch engine must pre-expand sweeps
            before conversion) or malformed scan coordinates.
    """
    try:
        task_kind = TaskKind(task)
    except ValueError as exc:
        allowed = ", ".join(kind.value for kind in TaskKind)
        message = f"task must be one of: {allowed}"
        raise TaskInputError(message) from exc

    resources: dict[str, JsonValue] = dict(request.resources)
    key_names: dict[str, str] = {}
    raw: dict[str, JsonValue] = {}

    candidate_id = request.input_artifact.candidate_id
    artifact = request.input_artifact

    # platform identity key (banned from requests; kept on the binding)
    trajectory_item_id = _as_str(resources.pop("trajectory_item_id", None))

    config_raw = resources.pop("config", None)
    config: Mapping[str, JsonValue] | None = None
    if config_raw is not None:
        raw["config"] = config_raw
        if isinstance(config_raw, Mapping):
            config = config_raw

    # method: the top-level field wins; a resources-level method is only
    # promoted when the top-level one is empty (then it keeps its own key
    # name); when both are present the resources entry stays verbatim.
    method_value = request.method
    resources_method = resources.pop("method", None)
    if request.method:
        if resources_method is not None:
            resources["method"] = resources_method
    else:
        method_value = _as_str(resources_method) or ""
        if resources_method is not None:
            key_names["method"] = "method"

    envelope_values: dict[str, object] = {}
    for canonical in _ENVELOPE_RESOURCE_KEYS:
        names = _ALIAS_RESOURCE_KEYS.get(canonical, (canonical,))
        value = _pop_alias(resources, names, key_names, canonical)
        if value is not None:
            envelope_values[canonical] = value

    structure_kind = resources.pop("structure_kind", None)
    if structure_kind is not None:
        raw["structure_kind"] = structure_kind

    # electronic state: single-state projection; raw form preserved verbatim
    electronic_state_raw = resources.pop("electronic_state", None)
    electronic_state: ElectronicStateSpec | None = None
    if electronic_state_raw is not None:
        raw["electronic_state"] = electronic_state_raw
        if isinstance(electronic_state_raw, Mapping):
            state_config = electronic_state_config_from_dict(electronic_state_raw)
            if state_config.states:
                if state_config.execution_mode.value == "state_sweep":
                    message = (
                        "state_sweep electronic_state must be expanded before "
                        "conversion to a task request"
                    )
                    raise TaskInputError(message)
                electronic_state = state_config.selected_state()

    coordinates_value = None
    symbols_value = None
    if "coordinates" in resources and _is_geometry_matrix(resources.get("coordinates")):
        coordinates_value = resources.pop("coordinates")
        raw["coordinates"] = coordinates_value
    if "symbols" in resources:
        symbols_value = resources.pop("symbols")
        raw["symbols"] = symbols_value

    options = _options_from_resources(
        task_kind, resources, raw, key_names, structure_kind, directions
    )

    # leftover envelope keys without a consumed value stay verbatim
    for canonical in _ENVELOPE_RESOURCE_KEYS:
        if canonical in resources:
            value = resources.pop(canonical)
            key_names.setdefault(canonical, canonical)
            envelope_values.setdefault(canonical, value)

    def _env(canonical: str) -> object | None:
        return envelope_values.get(canonical)

    mem_value = _env("mem")
    task_resources = TaskResources(
        nproc=_as_int(_env("nproc")),
        mem=(
            mem_value
            if isinstance(mem_value, (str, int)) and not isinstance(mem_value, bool)
            else None
        ),
        maxcore=_as_int(_env("maxcore")),
        timeout_s=_as_float(_env("timeout_s")),
    )

    output_dir_str = _as_path_str(_env("output_dir"))
    result_dir_str = _as_path_str(raw.get("result_dir"))
    artifact_root: Path | None = None
    if output_dir_str is not None:
        artifact_root = Path(output_dir_str)
    elif result_dir_str is not None:
        artifact_root = Path(result_dir_str)

    structure = StructureInput(
        path=artifact.path,
        coordinates=(
            tuple(tuple(float(c) for c in row) for row in coordinates_value)
            if isinstance(coordinates_value, list)
            else None
        ),
        symbols=tuple(str(s) for s in symbols_value) if isinstance(symbols_value, list) else None,
        elements=tuple(artifact.elements),
        role=artifact.role,
        source=artifact.source,
    )

    binding = LegacyBinding(
        workflow=request.workflow,
        profile=request.profile,
        candidate_id=candidate_id,
        trajectory_item_id=trajectory_item_id,
        artifact_root=artifact_root,
        config=config,
        legacy_method=request.method,
        resources_extra=dict(resources),
        resources_raw=raw,
        resources_key_names=key_names,
    )

    task_request = TaskRequest(
        task=task_kind,
        structure=structure,
        charge=_as_int(_env("charge")) or 0,
        multiplicity=_as_int(_env("multiplicity")) or 1,
        level=MethodSpec(method=method_value, basis=_as_str(_env("basis")) or ""),
        backend=_as_str(_env("backend")),
        electronic_state=electronic_state,
        options=options,
        resources=task_resources,
        output_dir=Path(output_dir_str) if output_dir_str is not None else None,
    )
    return task_request, binding


def _options_from_resources(
    task_kind: TaskKind,
    resources: dict[str, JsonValue],
    raw: dict[str, JsonValue],
    key_names: dict[str, str],
    structure_kind: object,
    directions: Sequence[str] | None,
) -> TaskOptions | None:
    """Project legacy resources into the typed options for ``task_kind``.

    Task-specific keys are consumed only for their own task; for any other
    task they remain in ``resources`` and land on the binding residue.
    """
    if task_kind is TaskKind.SINGLEPOINT:
        stability = _as_bool(_pop_named(resources, "stability_check", key_names))
        if stability is None:
            return SinglePointOptions()
        return SinglePointOptions(stability_check=stability)

    if task_kind is TaskKind.OPTIMIZE:
        ts_raw = resources.pop("ts_mode", None)
        if ts_raw is not None:
            raw["ts_mode"] = ts_raw
        failure_type = _as_str(_pop_named(resources, "failure_type", key_names))
        policy = _as_str(_pop_named(resources, "opt_rescue_policy", key_names))
        max_rescue = _pop_named(resources, "opt_max_rescue", key_names)
        rescue: RescueSpec | None = None
        if policy is not None or max_rescue is not None or failure_type is not None:
            rescue = RescueSpec(
                policy=policy or "adaptive",
                max_rescue=_as_int(max_rescue),
                failure_type=failure_type,
            )
        return OptimizeOptions(
            mode=(
                OptimizationMode.TRANSITION_STATE
                if _as_str(structure_kind) == "ts"
                else OptimizationMode.UNCONSTRAINED
            ),
            initial_hessian=_as_str(
                _pop_alias(
                    resources, _ALIAS_RESOURCE_KEYS["initial_hessian"], key_names, "initial_hessian"
                )
            ),
            recalc_hess=_as_int(
                _pop_alias(resources, _ALIAS_RESOURCE_KEYS["recalc_hess"], key_names, "recalc_hess")
            ),
            trust_radius=_as_float(
                _pop_alias(
                    resources, _ALIAS_RESOURCE_KEYS["trust_radius"], key_names, "trust_radius"
                )
            ),
            max_cycles=_as_int(_pop_named(resources, "max_cycles", key_names)),
            geom_maxiter=_as_int(_pop_named(resources, "geom_maxiter", key_names)),
            ts=_ts_spec_from_legacy(ts_raw),
            rescue=rescue,
        )

    if task_kind is TaskKind.FREQUENCY:
        return FrequencyOptions()

    if task_kind is TaskKind.SCAN:
        return _scan_options_from_resources(resources, raw, key_names)

    if task_kind is TaskKind.IRC:
        parsed: list[IrcDirection] = []
        raw_directions = _pop_named(resources, "directions", key_names)
        if raw_directions is not None:
            entries = raw_directions if isinstance(raw_directions, list) else [raw_directions]
            for entry in entries:
                text = _as_str(entry)
                if text == "both":
                    parsed.extend((IrcDirection.FORWARD, IrcDirection.REVERSE))
                elif text in ("forward", "reverse"):
                    parsed.append(IrcDirection(text))
        if not parsed and directions:
            for entry in directions:
                text = str(entry)
                if text == "both":
                    parsed.extend((IrcDirection.FORWARD, IrcDirection.REVERSE))
                elif text in ("forward", "reverse"):
                    parsed.append(IrcDirection(text))
        kwargs: dict[str, object] = {}
        maxpoints = _pop_named(resources, "maxpoints", key_names)
        if maxpoints is not None:
            kwargs["maxpoints"] = _as_int(maxpoints)
        step = _pop_named(resources, "step", key_names)
        if step is not None:
            kwargs["step"] = _as_float(step)
        initial_hessian = _as_str(
            _pop_alias(
                resources, _ALIAS_RESOURCE_KEYS["initial_hessian"], key_names, "initial_hessian"
            )
        )
        if initial_hessian is not None:
            kwargs["initial_hessian"] = initial_hessian
        if parsed:
            kwargs["directions"] = tuple(parsed)
        return IrcOptions(**kwargs)  # type: ignore[arg-type]

    if task_kind is TaskKind.CASSCF:
        casscf_raw = resources.pop("casscf", None)
        if casscf_raw is None:
            return None
        raw["casscf"] = casscf_raw
        if isinstance(casscf_raw, Mapping):
            try:
                return CasscfOptions(spec=casscf_spec_from_dict(casscf_raw))
            except ValueError as exc:
                raise TaskInputError(str(exc)) from exc
        return None

    if task_kind is TaskKind.THERMOCHEMISTRY:
        values: dict[str, object] = {}
        freq_log = _as_path_str(_pop_named(resources, "freq_log_path", key_names))
        if freq_log is not None:
            values["freq_log_path"] = Path(freq_log)
        sp_energy = _pop_named(resources, "sp_energy_hartree", key_names)
        if sp_energy is not None:
            values["sp_energy_hartree"] = _as_float(sp_energy)
        temperature = _pop_named(resources, "temperature", key_names)
        if temperature is not None:
            values["temperature_k"] = _as_float(temperature)
        pressure = _pop_named(resources, "pressure", key_names)
        if pressure is not None:
            values["pressure_atm"] = _as_float(pressure)
        standard_state = _as_str(_pop_named(resources, "standard_state", key_names))
        if standard_state is not None:
            values["standard_state"] = standard_state
        for canonical, names in (
            ("scl_zpe", _ALIAS_RESOURCE_KEYS["scl_zpe"]),
            ("ilowfreq", ("ilowfreq",)),
            ("imagreal", ("imagreal",)),
            ("conc", ("conc",)),
        ):
            entry = _pop_alias(resources, names, key_names, canonical)
            if entry is not None:
                values[canonical] = _as_float(entry)
        return ThermochemistryOptions(**values)  # type: ignore[arg-type]

    message = f"no options mapping for task {task_kind.value!r}"
    raise TaskInputError(message)


def _ts_spec_from_legacy(value: object) -> TsSpec | None:
    if isinstance(value, bool):
        return TsSpec(enabled=value)
    index = _as_int(value)
    if index is not None:
        return TsSpec(enabled=True, mode_index=index)
    return None


def _scan_options_from_resources(
    resources: dict[str, JsonValue],
    raw: dict[str, JsonValue],
    key_names: dict[str, str],
) -> ScanOptions | None:
    """Project legacy scan keys into :class:`ScanOptions` (raw forms kept)."""
    plan_raw = resources.pop("scan_plan", None)
    if plan_raw is not None:
        raw["scan_plan"] = plan_raw
    values_raw = None
    for name in ("scan_coordinates", "coordinate"):
        if name in resources:
            values_raw = resources.pop(name)
            raw[name] = values_raw
            key_names.setdefault("scan_coordinates", name)
            break
    coordinates_raw = resources.pop("coordinates", None)
    if coordinates_raw is not None:
        raw["coordinates"] = coordinates_raw

    entries: list[object] = []
    points: int | None = None
    if isinstance(plan_raw, Mapping):
        plan_coordinates = plan_raw.get("coordinates")
        if isinstance(plan_coordinates, list):
            entries = list(plan_coordinates)
        points = _as_int(plan_raw.get("points"))
    if not entries and values_raw is not None:
        entries = list(values_raw) if isinstance(values_raw, list) else [values_raw]
    if not entries and coordinates_raw is not None and not _is_geometry_matrix(coordinates_raw):
        entries = list(coordinates_raw) if isinstance(coordinates_raw, list) else [coordinates_raw]

    points_value = _pop_named(resources, "scan_points", key_names)
    if points_value is not None:
        points = _as_int(points_value)

    if not entries and points is None:
        return None
    coordinates = tuple(
        _parse_legacy_coordinate(entry, index) for index, entry in enumerate(entries)
    )
    return ScanOptions(coordinates=coordinates, points=points)


def to_legacy_request(
    task_request: TaskRequest,
    binding: LegacyBinding | None = None,
) -> CalculationRequest:
    """Rebuild the legacy request from a typed request + binding.

    Exact for round-trips of canonical legacy values: absent keys stay
    absent (emission is presence-gated by ``resources_key_names``), alias
    key names are restored, and raw dict-valued forms win over their typed
    projections.
    """
    binding = binding if binding is not None else LegacyBinding()
    resources: dict[str, LegacyJsonValue] = dict(binding.resources_extra)
    resources.update(binding.resources_raw)

    def _emit(canonical: str, value: object) -> None:
        name = binding.resources_key_names.get(canonical)
        if name is None or name in resources or value is None:
            return
        resources[name] = value  # type: ignore[assignment]

    _emit("backend", task_request.backend)
    _emit("basis", task_request.level.basis or None)
    _emit("charge", task_request.charge)
    _emit("multiplicity", task_request.multiplicity)
    _emit("nproc", task_request.resources.nproc)
    _emit("mem", task_request.resources.mem)
    _emit("maxcore", task_request.resources.maxcore)
    _emit("timeout_s", task_request.resources.timeout_s)
    _emit("output_dir", str(task_request.output_dir) if task_request.output_dir else None)
    _emit("method", task_request.level.method or None)

    options = task_request.options
    if isinstance(options, SinglePointOptions):
        _emit("stability_check", options.stability_check)
    elif isinstance(options, OptimizeOptions):
        _emit("initial_hessian", options.initial_hessian)
        _emit("recalc_hess", options.recalc_hess)
        _emit("trust_radius", options.trust_radius)
        _emit("geom_maxiter", options.geom_maxiter)
        _emit("max_cycles", options.max_cycles)
        if options.rescue is not None:
            _emit("opt_rescue_policy", options.rescue.policy)
            _emit("opt_max_rescue", options.rescue.max_rescue)
            _emit("failure_type", options.rescue.failure_type)
    elif isinstance(options, ScanOptions):
        _emit("scan_points", options.points)
    elif isinstance(options, IrcOptions):
        if "directions" in binding.resources_key_names:
            _emit("directions", [direction.value for direction in options.directions])
    elif isinstance(options, ThermochemistryOptions):
        _emit("freq_log_path", str(options.freq_log_path) if options.freq_log_path else None)
        _emit("sp_energy_hartree", options.sp_energy_hartree)
        _emit("temperature", options.temperature_k)
        _emit("pressure", options.pressure_atm)
        _emit("standard_state", options.standard_state)
        _emit("scl_zpe", options.scl_zpe)
        _emit("ilowfreq", options.ilowfreq)
        _emit("imagreal", options.imagreal)
        _emit("conc", options.conc)

    if binding.trajectory_item_id is not None:
        resources.setdefault("trajectory_item_id", binding.trajectory_item_id)

    structure = task_request.structure
    artifact = StructureArtifact(
        path=structure.path if structure is not None and structure.path is not None else Path("."),
        elements=list(structure.elements) if structure is not None else [],
        role=structure.role if structure is not None else StructureRole.MINIMUM,
        source=structure.source if structure is not None else "",
        candidate_id=binding.candidate_id,
    )
    return CalculationRequest(
        input_artifact=artifact,
        method=binding.legacy_method,
        resources=dict(resources),
        workflow=binding.workflow,
        profile=binding.profile,
    )


# ── result conversion ───────────────────────────────────────────────────


def to_task_result(
    result: CalculationResult,
    task: TaskKind | str,
    *,
    binding: LegacyBinding | None = None,
) -> tuple[TaskResult, LegacyBinding]:
    """Convert a legacy result into a typed :class:`TaskResult` + binding.

    Artifact paths under ``binding.artifact_root`` become root-relative
    (the task-layer path rule); profile moves from ``Provenance.profile``
    onto the binding.  Committed metadata keys project into the typed
    payload; everything else stays in the binding residue.
    """
    try:
        task_kind = TaskKind(task)
    except ValueError as exc:
        allowed = ", ".join(kind.value for kind in TaskKind)
        message = f"task must be one of: {allowed}"
        raise TaskInputError(message) from exc

    binding = binding if binding is not None else LegacyBinding()
    root = binding.artifact_root

    metadata: dict[str, JsonValue] = dict(result.metadata)
    key_names: dict[str, str] = {}
    raw: dict[str, JsonValue] = {}
    values: dict[str, object] = {}
    for canonical, names in _METADATA_ALIASES.items():
        for name in names:
            if name in metadata:
                values[canonical] = metadata.pop(name)
                key_names[canonical] = name
                break
    for name in list(metadata):
        if name in _RAW_METADATA_KEYS:
            raw[name] = metadata.pop(name)

    profile = result.provenance.profile if result.provenance is not None else binding.profile
    provenance = (
        TaskProvenance(
            backend=result.provenance.backend,
            method=result.provenance.method,
            version=result.provenance.version,
            input_signature=result.provenance.input_signature,
        )
        if result.provenance is not None
        else None
    )

    artifacts = tuple(
        ArtifactRef(
            path=_relativize(artifact.path, root),
            type=artifact.type,
            checksum=artifact.checksum,
            source=artifact.source,
        )
        for artifact in result.artifacts
    )

    binding = replace(
        binding,
        profile=profile,
        metadata_extra=dict(metadata),
        metadata_raw=raw,
        metadata_key_names=key_names,
    )

    task_result = TaskResult(
        task=task_kind,
        status=result.status,
        complete=True,
        error_kind=None,
        errors=tuple(result.errors),
        energy_hartree=result.energy,
        coordinates=(
            tuple(tuple(float(c) for c in row) for row in result.coords)
            if result.coords is not None
            else None
        ),
        symbols=None,
        frequencies=tuple(float(f) for f in result.frequencies),
        converged=None,
        artifacts=artifacts,
        provenance=provenance,
        payload=_payload_from_values(task_kind, values, raw),
        metadata={},
    )
    return task_result, binding


def _payload_from_values(
    task_kind: TaskKind,
    values: dict[str, object],
    raw: dict[str, JsonValue],
) -> TaskPayload | None:
    if task_kind is TaskKind.SINGLEPOINT:
        state = values.get("electronic_state")
        return SinglePointPayload(
            electronic_state=dict(state) if isinstance(state, Mapping) else None
        )
    if task_kind is TaskKind.OPTIMIZE:
        state = values.get("electronic_state")
        actions = values.get("rescue_actions")
        return OptimizePayload(
            optimization_status=_as_str(values.get("optimization_status")),
            rescue_failure_type=_as_str(values.get("failure_type")),
            rescue_actions=tuple(str(a) for a in actions) if isinstance(actions, list) else (),
            rescue_attempts=_as_int(values.get("rescue_attempts")),
            electronic_state=dict(state) if isinstance(state, Mapping) else None,
        )
    if task_kind is TaskKind.FREQUENCY:
        state = values.get("electronic_state")
        return FrequencyPayload(
            n_imaginary=_as_int(values.get("n_imaginary")),
            electronic_state=dict(state) if isinstance(state, Mapping) else None,
        )
    if task_kind is TaskKind.CASSCF:
        multiref = raw.get("multireference")
        if isinstance(multiref, Mapping):
            roots = multiref.get("root_energies")
            occupations = multiref.get("natural_occupations")
            nevpt2 = multiref.get("nevpt2_energies")
            active_space = multiref.get("active_space")
            return CasscfPayload(
                root_energies=tuple(float(v) for v in roots) if isinstance(roots, list) else (),
                natural_occupations=(
                    tuple(float(v) for v in occupations) if isinstance(occupations, list) else ()
                ),
                nevpt2_energies=tuple(float(v) for v in nevpt2) if isinstance(nevpt2, list) else (),
                active_space=active_space if isinstance(active_space, str) else "",
            )
        return None
    if task_kind is TaskKind.THERMOCHEMISTRY:
        return ThermochemistryPayload(
            enthalpy_hartree=_as_float(values.get("enthalpy_hartree")),
            gibbs_hartree=_as_float(values.get("gibbs_hartree")),
            entropy_au=_as_float(values.get("entropy_au")),
            gibbs_source=_as_str(values.get("gibbs_source")),
            standard_state=_as_str(values.get("standard_state")),
        )
    return None


def to_legacy_result(
    result: TaskResult,
    binding: LegacyBinding | None = None,
) -> CalculationResult:
    """Rebuild the legacy result from a typed result + binding.

    Restores platform identity onto the legacy shapes (``profile`` on
    ``Provenance``) and re-roots artifact paths.  Task-only fields without a
    legacy home (``symbols``/``complete``/``converged``/``error_kind``) are
    not representable in the legacy result and are dropped (see the mapping
    table).  Payload fields project into legacy metadata under their
    original key names (alias-respecting) and are only written when set —
    the adapter never invents keys or values.
    """
    binding = binding if binding is not None else LegacyBinding()
    root = binding.artifact_root

    metadata: dict[str, LegacyJsonValue] = dict(binding.metadata_extra)
    metadata.update(binding.metadata_raw)

    def _emit(canonical: str, value: object) -> None:
        if value is None:
            return
        name = binding.metadata_key_names.get(canonical, canonical)
        if name in metadata:
            return
        metadata[name] = value  # type: ignore[assignment]

    payload = result.payload
    if isinstance(payload, SinglePointPayload):
        _emit("electronic_state", payload.electronic_state)
    elif isinstance(payload, OptimizePayload):
        _emit("optimization_status", payload.optimization_status)
        _emit("failure_type", payload.rescue_failure_type)
        _emit("rescue_actions", list(payload.rescue_actions) or None)
        _emit("rescue_attempts", payload.rescue_attempts)
        _emit("electronic_state", payload.electronic_state)
    elif isinstance(payload, FrequencyPayload):
        _emit("n_imaginary", payload.n_imaginary)
        _emit("electronic_state", payload.electronic_state)
    elif isinstance(payload, CasscfPayload):
        if "multireference" not in metadata and (
            payload.root_energies
            or payload.natural_occupations
            or payload.nevpt2_energies
            or payload.active_space
        ):
            metadata["multireference"] = {
                "root_energies": list(payload.root_energies),
                "natural_occupations": list(payload.natural_occupations),
                "nevpt2_energies": list(payload.nevpt2_energies),
                "active_space": payload.active_space,
            }
    elif isinstance(payload, ThermochemistryPayload):
        _emit("enthalpy_hartree", payload.enthalpy_hartree)
        _emit("gibbs_hartree", payload.gibbs_hartree)
        _emit("entropy_au", payload.entropy_au)
        _emit("gibbs_source", payload.gibbs_source)
        _emit("standard_state", payload.standard_state)

    provenance = (
        Provenance(
            backend=result.provenance.backend,
            method=result.provenance.method,
            profile=binding.profile if binding.profile is not None else "",
            version=result.provenance.version,
            input_signature=result.provenance.input_signature,
        )
        if result.provenance is not None
        else None
    )

    return CalculationResult(
        energy=result.energy_hartree,
        coords=(
            [[float(c) for c in row] for row in result.coordinates]
            if result.coordinates is not None
            else None
        ),
        frequencies=[float(f) for f in result.frequencies],
        artifacts=[
            ArtifactRef(
                path=_reroot(artifact.path, root),
                type=artifact.type,
                checksum=artifact.checksum,
                source=artifact.source,
            )
            for artifact in result.artifacts
        ],
        status=result.status,
        errors=list(result.errors),
        provenance=provenance,
        metadata=metadata,
    )


# ── artifact-root path mapping ─────────────────────────────────────────
# Task-layer ArtifactRef paths are artifact-root-relative (doc §"Path
# rules"); legacy results may carry absolute paths.  Paths under the root
# round-trip exactly; an already-relative legacy path is treated as
# root-relative (documented normalisation).


def _relativize(path: Path, root: Path | None) -> Path:
    candidate = Path(path)
    if root is None or not candidate.is_absolute():
        return candidate
    root_path = Path(root)
    try:
        return candidate.relative_to(root_path)
    except ValueError:
        try:
            return candidate.resolve().relative_to(root_path.resolve())
        except (ValueError, OSError):
            return candidate


def _reroot(path: Path, root: Path | None) -> Path:
    candidate = Path(path)
    if root is not None and not candidate.is_absolute():
        return Path(root) / candidate
    return candidate


__all__ = [
    "LegacyBinding",
    "to_legacy_request",
    "to_legacy_result",
    "to_task_request",
    "to_task_result",
]
