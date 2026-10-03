"""ORCA backend wrapper."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from acp.backends.base import QCBackend, QCResult, to_qc_result
from acp.backends.registry import register_backend
from cccp.qc.interfaces.constraints import ReactionCoordinatePlan
from cccp.qc.interfaces.orca import ORCAInterface
from cccp.qc.interfaces.xtb_scan import RelaxedScanResult
from cccp.qc.keyword_registry import method_family
from cccp.software import detect_version

logger = logging.getLogger(__name__)

#: ORCA ``.engrad`` header states ``Eh/bohr``; CODATA-style value shared with
#: PES2TS ``gradient_evidence.BOHR_ANGSTROM`` for the derived Eh/Å view.
BOHR_ANGSTROM = 0.529177210903

#: Native unit of the ORCA-printed cartesian gradient (``.engrad`` header).
ORCA_GRADIENT_UNIT = "hartree/bohr"
#: Sign/semantics convention: energy gradient dE/dX as printed by ORCA —
#: NOT the force (force = -gradient). No sign flip is applied anywhere.
ORCA_GRADIENT_CONVENTION = "energy_gradient_dE_dX"


@dataclass
class SinglePointGradientResult:
    """Typed result of an ORCA single-point gradient (``EnGrad``) evaluation.

    Attributes:
        success: True only when the run succeeded AND a gradient was parsed.
        energy: Single-point energy in Hartree (``None`` when unavailable).
        gradient: Per-atom cartesian gradient ``(N, 3)`` in Hartree/bohr,
            exactly as printed by ORCA (energy gradient dE/dX; not force).
        gradient_unit: Native unit string of ``gradient``.
        gradient_convention: Sign/semantics convention string.
        symbols: Element symbols in input order.
        coordinates: Input geometry ``(N, 3)`` in Angstrom.
        gradient_source: Provenance tag — ``engrad_file:<name>`` or
            ``output_block:CARTESIAN GRADIENT``.
        output_file: ORCA input file path.
        log_file: ORCA output log path.
        error_message: Typed failure reason when ``success`` is False.
        metadata: Extra provenance (route extras, method/basis, charge,
            multiplicity, derived Eh/Å gradient, spin diagnostics).
    """

    success: bool = False
    energy: float | None = None
    gradient: NDArray[np.float64] | None = None
    gradient_unit: str = ORCA_GRADIENT_UNIT
    gradient_convention: str = ORCA_GRADIENT_CONVENTION
    symbols: list[str] | None = None
    coordinates: NDArray[np.float64] | None = None
    gradient_source: str | None = None
    output_file: Path | None = None
    log_file: Path | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _parse_engrad_file(path: Path, n_atoms: int) -> tuple[float, NDArray[np.float64]] | None:
    """Parse an ORCA ``.engrad`` companion file → ``(energy, (N,3) Eh/bohr)``.

    File layout (ORCA native): comment lines (``#``), atom count, total
    energy in Eh, ``3N`` gradient components (one per line, Eh/bohr), then
    ``N`` geometry rows ``Z x y z`` in Bohr. Returns ``None`` on any
    structural mismatch — never a partial or fabricated gradient.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    rows = [
        line.strip()
        for line in raw.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(rows) < 2 + 3 * n_atoms:
        return None
    try:
        natoms = int(rows[0])
        energy = float(rows[1])
        grad = np.array(
            [float(value) for value in rows[2 : 2 + 3 * natoms]], dtype=np.float64
        ).reshape(natoms, 3)
    except ValueError:
        return None
    if natoms != n_atoms:
        return None
    if not math.isfinite(energy) or not np.isfinite(grad).all():
        return None
    return energy, grad


def _parse_cartesian_gradient_block(
    log_text: str, symbols: list[str]
) -> NDArray[np.float64] | None:
    """Parse the LAST ``CARTESIAN GRADIENT`` block from ORCA stdout text.

    Block rows look like ``1   O   :   gx   gy   gz`` (the colon is an
    ORCA 6 spelling quirk; older builds omit it). Returns ``None`` when the
    block is missing, atom count mismatches, symbols disagree, or values are
    non-finite.
    """
    start = log_text.rfind("CARTESIAN GRADIENT")
    if start < 0:
        return None
    rows: list[list[float]] = []
    block_symbols: list[str] = []
    for line in log_text[start:].splitlines()[1:]:
        stripped = line.strip()
        if not stripped:
            if rows:
                break
            continue
        parts = stripped.replace(":", " ").split()
        if len(parts) >= 5:
            try:
                int(parts[0])
                gx, gy, gz = (float(value) for value in parts[2:5])
            except ValueError:
                if rows:
                    break
                continue
            rows.append([gx, gy, gz])
            block_symbols.append(parts[1])
        elif rows:
            break
    if len(rows) != len(symbols) or not rows:
        return None
    if [s.upper() for s in block_symbols] != [s.upper() for s in symbols]:
        return None
    grad = np.asarray(rows, dtype=np.float64)
    if not np.isfinite(grad).all():
        return None
    return grad


class ORCABackend(QCBackend):
    """Capability-based wrapper around :class:`ORCAInterface`."""

    name = "orca"

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(config, **kwargs)

        theory = config.get("theory", {})
        theory_opt = theory.get("optimization", {})
        theory_sp = theory.get("single_point", {})
        defaults = theory_opt if theory_opt.get("engine") == "orca" else theory_sp or theory_opt

        interface_kwargs = dict(kwargs)
        interface_kwargs.setdefault("method", defaults.get("method", "M062X"))
        # T10: GFN-family methods (GFN0/1/2-xTB, GFN-FF) carry their own
        # built-in Hamiltonian and consume no DFT basis. Never inject the
        # conventional-DFT default ``def2-TZVPP`` here — the cccp renderer
        # also strips a leaked basis, but the backend must not emit a value
        # that is misleading (and would be dropped) in the first place.
        if method_family(interface_kwargs["method"]) in {"gfn", "gfnff"}:
            interface_kwargs.setdefault("basis", "")
        else:
            interface_kwargs.setdefault("basis", defaults.get("basis", "def2-TZVPP"))
        interface_kwargs.setdefault("solvent", None)
        interface_kwargs.setdefault("solvent_model", "none")

        self._interface = ORCAInterface(config=config, **interface_kwargs)
        self._version: str | None = None
        self._version_checked = False

    def is_available(self) -> bool:
        return self._interface.is_available()

    def get_version(self) -> str | None:
        if self._version_checked:
            return self._version

        self._version = detect_version("orca", self._interface.executable)
        self._version_checked = True
        return self._version

    def optimize(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        target_dir = output_dir or Path.cwd()
        return to_qc_result(
            self._interface.optimize(
                coordinates,
                symbols,
                charge=charge,
                multiplicity=multiplicity,
                output_dir=target_dir,
                **kwargs,
            )
        )

    def single_point(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        target_dir = output_dir or Path.cwd()
        return to_qc_result(
            self._interface.single_point(
                coordinates,
                symbols,
                charge=charge,
                multiplicity=multiplicity,
                output_dir=target_dir,
                **kwargs,
            )
        )

    def frequency(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        target_dir = output_dir or Path.cwd()
        return to_qc_result(
            self._interface.frequency(
                coordinates,
                symbols,
                charge=charge,
                multiplicity=multiplicity,
                output_dir=target_dir,
                **kwargs,
            )
        )

    def casscf(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        """Run a CASSCF / NEVPT2 single point through ``ORCAInterface``.

        The ``metadata["casscf"]`` payload follows the electronic-state
        design doc §12.1 (active space, natural occupations, per-root
        NEVPT2 energies).
        """
        target_dir = output_dir or Path.cwd()
        return to_qc_result(
            self._interface.casscf(
                coordinates,
                symbols,
                charge=charge,
                multiplicity=multiplicity,
                output_dir=target_dir,
                **kwargs,
            )
        )

    def nmr_shielding(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        nuclei: list[str] | None = None,
        **kwargs: Any,
    ) -> QCResult:
        target_dir = output_dir or Path.cwd()
        return to_qc_result(
            self._interface.nmr_shielding(
                coordinates,
                symbols,
                charge=charge,
                multiplicity=multiplicity,
                output_dir=target_dir,
                nuclei=nuclei,
                **kwargs,
            )
        )

    def transition_state_opt(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> object:
        """Delegate an OptTS + frequency run to ``ORCAInterface``.

        Returns the interface's :class:`TsOptResult` (energies, imaginary
        frequencies and converged geometry).
        """
        target_dir = output_dir or Path.cwd()
        return self._interface.transition_state_opt(
            coordinates,
            symbols,
            charge=charge,
            multiplicity=multiplicity,
            output_dir=target_dir,
            **kwargs,
        )

    def irc(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> object:
        """Delegate an IRC run to ``ORCAInterface``.

        Returns the interface's :class:`IrcResult` (endpoints + step counts).
        """
        target_dir = output_dir or Path.cwd()
        return self._interface.irc(
            coordinates,
            symbols,
            charge=charge,
            multiplicity=multiplicity,
            output_dir=target_dir,
            **kwargs,
        )

    def relaxed_scan(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        output_dir: Path,
        plan: ReactionCoordinatePlan,
        charge: int = 0,
        multiplicity: int = 1,
        **kwargs: Any,
    ) -> RelaxedScanResult:
        """Delegate a relaxed scan to ``ORCAInterface``.

        A plan with multiple drive coordinates is kept synchronous by the
        interface: every frame constrains all coordinates at the same
        interpolation value.
        """
        drive_coordinates = plan.drive_coordinates()
        if not drive_coordinates:
            raise ValueError("ORCA relaxed_scan requires at least one drive coordinate")
        if len(drive_coordinates) > 1 or plan.fixed_endpoints or any(c.values for c in plan.coordinates):
            return self._interface.relaxed_scan(
                coordinates,
                symbols,
                plan=plan,
                points=plan.points,
                charge=charge,
                multiplicity=multiplicity,
                output_dir=output_dir,
                **kwargs,
            )
        return self._interface.relaxed_scan(
            coordinates,
            symbols,
            scan_coordinate=drive_coordinates[0],
            points=plan.points,
            charge=charge,
            multiplicity=multiplicity,
            output_dir=output_dir,
            **kwargs,
        )

    def single_point_gradient(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        output_name: str = "grad",
        method: str | None = None,
        basis: str | None = None,
        **kwargs: Any,
    ) -> SinglePointGradientResult:
        """Run an ORCA single-point gradient (``EnGrad``) via ``ORCAInterface``.

        Delegates to :meth:`ORCAInterface.single_point` with the ``EnGrad``
        route keyword prepended to ``route_extras`` (free-form route
        passthrough — no interface change required), then parses the physical
        gradient from the run output.

        Units/sign: ORCA prints the energy gradient dE/dX in Hartree/bohr
        (the ``.engrad`` header states ``Eh/bohr``); values are returned
        exactly as printed — NOT forces (force = -gradient). The gradient is
        bound to the run energy: a ``.engrad`` whose energy disagrees with
        the parsed single-point energy is rejected, and a missing gradient is
        a typed failure — never a fabricated value.
        """
        target_dir = output_dir or Path.cwd()
        extras = [str(x) for x in (kwargs.pop("route_extras", None) or []) if str(x).strip()]
        if not any(x.strip().lower() == "engrad" for x in extras):
            extras = ["EnGrad", *extras]

        qc = self._interface.single_point(
            coordinates,
            symbols,
            charge=charge,
            multiplicity=multiplicity,
            output_dir=target_dir,
            output_name=output_name,
            method=method,
            basis=basis,
            route_extras=extras,
            **kwargs,
        )

        log_file = Path(qc.log_file) if qc.log_file else target_dir / f"{output_name}.out"
        log_text = ""
        if log_file.is_file():
            log_text = log_file.read_text(encoding="utf-8", errors="replace")

        energy = qc.energy if qc.energy is not None and math.isfinite(qc.energy) else None
        gradient: NDArray[np.float64] | None = None
        source: str | None = None

        if qc.success and energy is not None:
            candidates = sorted(target_dir.glob(f"{output_name}*.engrad"))
            candidates.sort(key=lambda p: (p.name != f"{output_name}.engrad", p.name))
            for candidate in candidates:
                parsed = _parse_engrad_file(candidate, len(symbols))
                if parsed is None:
                    continue
                engrad_energy, engrad_gradient = parsed
                if abs(engrad_energy - energy) <= 1e-6:
                    gradient = engrad_gradient
                    source = f"engrad_file:{candidate.name}"
                    break
                logger.warning(
                    "ORCA .engrad energy %.12f disagrees with SP energy %.12f; refusing to bind %s",
                    engrad_energy,
                    energy,
                    candidate.name,
                )
            if gradient is None:
                block_gradient = _parse_cartesian_gradient_block(log_text, symbols)
                if block_gradient is not None:
                    gradient = block_gradient
                    source = "output_block:CARTESIAN GRADIENT"

        base = dict(
            energy=energy,
            symbols=list(symbols),
            coordinates=np.asarray(coordinates, dtype=np.float64),
            output_file=Path(qc.output_file) if qc.output_file else None,
            log_file=log_file,
            metadata={
                "route_extras": extras,
                "method": method if method is not None else self._interface.method,
                "basis": basis if basis is not None else self._interface.basis,
                "charge": charge,
                "multiplicity": multiplicity,
            },
        )
        if gradient is None or source is None:
            reason = qc.error_message or (
                "ORCA gradient missing: neither a bound .engrad file nor a "
                "'CARTESIAN GRADIENT' block was found in the run output"
            )
            if qc.success and energy is not None:
                reason = (
                    "ORCA gradient missing: run succeeded with an energy but "
                    "no bound gradient artifact was found"
                )
            return SinglePointGradientResult(
                success=False,
                error_message=reason,
                gradient_source=None,
                **base,
            )

        return SinglePointGradientResult(
            success=True,
            gradient=gradient,
            gradient_source=source,
            metadata={
                **base["metadata"],
                "gradient_hartree_per_angstrom": (gradient / BOHR_ANGSTROM).tolist(),
                "gradient_conversion": "hartree_per_bohr / 0.529177210903",
                "spin_metadata": dict(qc.metadata or {}),
            },
            **{k: v for k, v in base.items() if k != "metadata"},
        )


register_backend(ORCABackend)

__all__ = [
    "BOHR_ANGSTROM",
    "ORCABackend",
    "ORCAInterface",
    "ORCA_GRADIENT_CONVENTION",
    "ORCA_GRADIENT_UNIT",
    "SinglePointGradientResult",
]
