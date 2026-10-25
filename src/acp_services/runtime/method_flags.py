"""Method-levels → CLI flag conversion (single implementation root).

B05-7 (plan todo 77). The method-level parameter-conversion choke point used by
the remote CLI entry (``src/acp/scheduler/remote/script_gen.py``), the local
runner and the v1 API previously lived in the legacy ``catalog`` module. It is
a pure, dependency-free converter (stdlib + ``cccp`` only), so it moves here
**in place**: ``acp.catalog.method_levels_to_cli_flags`` is now a same-object
forwarder of :func:`method_levels_to_cli_flags` defined below.

This is the ANTI #25 case-folding choke point at CLI-flag emission — the
solvent-model / dispersion values are lower-cased here. The behavior is
byte-for-byte the legacy one: same flag map, same simple-workflow field
validation, same mutually-exclusive ``--calc-hess`` / ``--no-calc-hess``
emission. Equivalence is guarded by
``tests/test_services_migration_equivalence.py``.

The module imports **no** ``acp`` symbol (dependency direction is one-way):
only stdlib and the ``cccp`` hessian policy adapter.
"""

from __future__ import annotations

from typing import Any

from cccp.qc.hessian_policy import normalize_recalc_hess

__all__ = [
    "CASE_INSENSITIVE_FIELDS",
    "LEVEL_TO_CLI_FLAG_MAP",
    "SIMPLE_LEVEL_FIELDS",
    "method_levels_to_cli_flags",
]


LEVEL_TO_CLI_FLAG_MAP: dict[str, str] = {
    "functional": "method",
    "grid": "grid",
    "basis": "basis",
    "aux_j_basis": "aux-j-basis",
    "aux_c_basis": "aux-c-basis",
    "ri_approximation": "ri-approximation",
    "dispersion": "dispersion",
    "solvent_model": "solvent-model",
    "solvent": "solvent",
    "temperature": "temperature",
    "pressure": "pressure",
    "scale_factor": "scale-factor",
    "max_steps": "geom-maxiter",
    "opt_convergence": "opt-convergence",
    "opt_max_iter": "opt-max-iter",
    "opt_trust_radius": "opt-trust-radius",
    "opt_initial_hessian": "opt-initial-hessian",
    "minimum_opt_trust_radius": "minimum-opt-trust-radius",
    "minimum_opt_initial_hessian": "minimum-opt-initial-hessian",
    "transition_state_opt_trust_radius": "transition-state-opt-trust-radius",
    "transition_state_opt_initial_hessian": "transition-state-opt-initial-hessian",
    "minimum_opt_max_iter": "minimum-opt-max-iter",
    "minimum_opt_convergence": "minimum-opt-convergence",
    "minimum_scf_max_iter": "minimum-scf-max-iter",
    "minimum_scf_convergence": "minimum-scf-convergence",
    "minimum_scf_strategy": "minimum-scf-strategy",
    "minimum_opt_rescue_policy": "minimum-opt-rescue-policy",
    "minimum_opt_max_rescue": "minimum-opt-max-rescue",
    "transition_state_opt_max_iter": "transition-state-opt-max-iter",
    "transition_state_opt_convergence": "transition-state-opt-convergence",
    "transition_state_scf_max_iter": "transition-state-scf-max-iter",
    "transition_state_scf_convergence": "transition-state-scf-convergence",
    "transition_state_scf_strategy": "transition-state-scf-strategy",
    "transition_state_opt_rescue_policy": "transition-state-opt-rescue-policy",
    "transition_state_opt_max_rescue": "transition-state-opt-max-rescue",
    "opt_rescue_policy": "opt-rescue-policy",
    "opt_max_rescue": "opt-max-rescue",
    "scf_max_iter": "scf-max-iter",
    "scf_convergence": "scf-convergence",
    "scf_strategy": "scf-strategy",
    "scf_orbital_inherit": "scf-orbital-inherit",
    # NOTE: recalc_hess and opt_recalc_hess are handled inline in
    # method_levels_to_cli_flags() because the new CLI surface is
    # --calc-hess / --no-calc-hess rather than a single --recalc-hess flag.
}

SIMPLE_LEVEL_FIELDS = frozenset(
    {
        "functional",
        "basis",
        "aux_j_basis",
        "aux_c_basis",
        "ri_approximation",
        "dispersion",
        "solvent_model",
        "solvent",
        "grid",
        "scf_convergence",
    }
)

# ``solvent_model`` / ``dispersion`` legacy members plus the opt/SCF
# enum trio (2026-09-13): historical payloads and pre-2026-09 presets
# carry title-case values ("Tight"); membership here lowercases them at
# CLI-flag emission below.
CASE_INSENSITIVE_FIELDS = frozenset({"solvent_model", "dispersion"})


def method_levels_to_cli_flags(
    levels: dict[str, Any],
    prefix_map: dict[str, str] | None = None,
    *,
    workflow: str | None = None,
) -> list[str]:
    """Convert method levels dict to CLI flag list.

    Args:
        levels: Nested dict - e.g. {"optfreq": {...}, "single_point": {...}}.
        prefix_map: level_id -> CLI flag prefix (default: no prefix for all).

    Returns:
        CLI flag list - e.g. ["--method", "wB97M-V", "--basis", "def2-TZVPP"].
    """
    prefix_map = prefix_map or {}
    cmd: list[str] = []
    for level_id, level_config in levels.items():
        if not isinstance(level_config, dict) or level_config.get("_disabled"):
            continue
        prefix = prefix_map.get(level_id, "")
        for field, value in level_config.items():
            if field == "engine" or value is None or value == "":
                continue
            if workflow in {"singlepoint", "optimize", "frequency", "scan"}:
                supported = SIMPLE_LEVEL_FIELDS
                if workflow in {"optimize", "scan"}:
                    supported = supported | {"max_steps"}
                if workflow == "optimize":
                    supported = supported | {"opt_convergence", "recalc_hess"}
                if field in LEVEL_TO_CLI_FLAG_MAP or field == "recalc_hess":
                    if field not in supported:
                        raise ValueError(f"{workflow} does not support method field {field!r}")
            # recalc_hess maps to the mutually-exclusive --calc-hess /
            # --no-calc-hess CLI surface. It is only meaningful on the
            # unprefixed opt level; prefixed levels (e.g. sp-) are skipped
            # because single-point calculations do not optimise.
            if field == "recalc_hess":
                try:
                    normalized = normalize_recalc_hess(value)
                except ValueError:
                    continue
                if normalized is None:
                    continue
                if prefix:
                    continue
                if normalized == "auto":
                    cmd += ["--calc-hess", "auto"]
                elif normalized == 0:
                    cmd += ["--no-calc-hess"]
                else:
                    cmd += ["--calc-hess", str(normalized)]
                continue
            cli_flag = LEVEL_TO_CLI_FLAG_MAP.get(field)
            if cli_flag is None:
                continue
            str_value = str(value)
            if field in CASE_INSENSITIVE_FIELDS:
                str_value = str_value.lower()
            flag = f"--{prefix}{cli_flag}"
            cmd += [flag, str_value]
    return cmd
