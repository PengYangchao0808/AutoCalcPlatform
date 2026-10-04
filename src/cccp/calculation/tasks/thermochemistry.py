"""Thermochemistry task core (plan todo 22 — task 7).

Thermochemistry is single-implementation and one-directional: this task calls
the shared Shermo adapter ``cccp.qc.shermo_adapter.execute_shermo`` (with the
pure normalization of ``cccp.qc.thermo_normalize``) directly and exactly once
per request.  It never routes through ``ExternalBackend``, never re-enters a
task entry, and adds no second runner call path — ``run_thermochemistry`` →
``execute_shermo`` → the low-level Shermo runner is the only path.  All three
entries (this task, the legacy ``ThermochemistryCalculator`` and
``ExternalBackend.thermochemistry``) share that one implementation.

Units / standard-state contract: ``enthalpy_hartree`` and ``gibbs_hartree``
are Hartree, ``entropy_au`` is atomic units, and ``standard_state`` is the
normalized ``"1atm"``/``"1M"`` token (``None`` in options = the contract
default ``"1atm"``, applied here at execution).  The shared ``build_metadata``
dict is the result metadata; the Shermo output file is the artifact.

Pre-launch input problems raise ``TaskInputError``; runtime failure is a
structured failed ``TaskResult``.  This module never imports ``acp``.

Author: QCcalc Team
"""

from __future__ import annotations

import logging

from cccp.calculation._common import classify_failure, error_text
from cccp.calculation.context import TaskContext, resolve_context
from cccp.calculation.contracts import ArtifactRef
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import (
    TaskKind,
    TaskRequest,
    ThermochemistryOptions,
    validate_request,
)
from cccp.calculation.results import TaskResult, ThermochemistryPayload
from cccp.qc.shermo_adapter import execute_shermo
from cccp.qc.thermo_normalize import ThermochemistryInputError

logger = logging.getLogger(__name__)

_DEFAULT_TEMPERATURE_K = 298.15
_DEFAULT_PRESSURE_ATM = 1.0
_DEFAULT_STANDARD_STATE = "1atm"
_DEFAULT_SP_ENERGY_HARTREE = 0.0


def run_thermochemistry(
    request: TaskRequest,
    *,
    context: TaskContext | None = None,
) -> TaskResult:
    """Run one Shermo thermochemistry calculation (frequency log input).

    ``request.options`` must be
    :class:`~cccp.calculation.requests.ThermochemistryOptions`; its fields map
    onto the shared T14 execution (``freq_log_path`` is the input shape;
    ``scl_zpe``/``ilowfreq``/``imagreal``/``conc`` become runner-option
    overrides).  Exactly one Shermo launch happens per request.
    """
    validate_request(request)
    if request.task is not TaskKind.THERMOCHEMISTRY:
        message = f"run_thermochemistry requires task 'thermochemistry', got {request.task.value!r}"
        raise TaskInputError(message)
    if not isinstance(request.options, ThermochemistryOptions):
        message = "thermochemistry requires ThermochemistryOptions"
        raise TaskInputError(message)
    ctx = resolve_context(request, context)
    options = request.options

    runner_options = {
        key: value
        for key, value in (
            ("scl_zpe", options.scl_zpe),
            ("ilowfreq", options.ilowfreq),
            ("imagreal", options.imagreal),
            ("conc", options.conc),
        )
        if value is not None
    }
    sp_energy = (
        options.sp_energy_hartree
        if options.sp_energy_hartree is not None
        else _DEFAULT_SP_ENERGY_HARTREE
    )
    try:
        run = execute_shermo(
            options.freq_log_path,
            sp_energy,
            temperature_k=(
                options.temperature_k
                if options.temperature_k is not None
                else _DEFAULT_TEMPERATURE_K
            ),
            pressure_atm=(
                options.pressure_atm
                if options.pressure_atm is not None
                else _DEFAULT_PRESSURE_ATM
            ),
            standard_state=options.standard_state or _DEFAULT_STANDARD_STATE,
            output_dir=request.output_dir,
            config=ctx.config,
            runner_options=runner_options,
        )
    except ThermochemistryInputError as error:
        raise TaskInputError(error_text(error)) from error

    # Shared metadata is the stable legacy projection (build_metadata).
    metadata = dict(run.metadata)
    artifacts: list[ArtifactRef] = []
    output_file = run.context.output_file
    if output_file.is_file():
        artifacts.append(ArtifactRef(path=output_file, type="thermochemistry", source="shermo"))

    values = run.outcome.values
    payload = ThermochemistryPayload(
        enthalpy_hartree=values.get("h_sum"),
        gibbs_hartree=run.outcome.gibbs,
        entropy_au=values.get("s_total"),
        gibbs_source=run.outcome.gibbs_source,
        standard_state=run.request.standard_state,
    )
    # ``energy_hartree`` echoes the input single-point energy (legacy
    # ``ThermochemistryCalculator`` envelope semantics); no provenance is
    # invented — Shermo runs outside the backend layer and the legacy result
    # carried none.
    if not run.success:
        message = run.error or "Shermo returned no thermochemistry data"
        return TaskResult(
            task=TaskKind.THERMOCHEMISTRY,
            status="failed",
            complete=False,
            error_kind=classify_failure(error_message=message),
            errors=(message,),
            energy_hartree=run.request.sp_energy_hartree,
            artifacts=tuple(artifacts),
            metadata=metadata,
        )
    return TaskResult(
        task=TaskKind.THERMOCHEMISTRY,
        status="completed",
        complete=True,
        errors=(),
        energy_hartree=run.request.sp_energy_hartree,
        artifacts=tuple(artifacts),
        payload=payload,
        metadata=metadata,
    )


__all__ = ["run_thermochemistry"]
