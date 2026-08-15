"""
ORCA Interface
=============

Interface for ORCA quantum chemistry software.

Author: QCcalc Team (adapted from RPH)
"""

# pyright: reportArgumentType=false, reportAny=false, reportConstantRedefinition=false, reportDeprecated=false, reportExplicitAny=false, reportImplicitOverride=false, reportImplicitStringConcatenation=false, reportMissingParameterType=false, reportMissingTypeArgument=false, reportPrivateUsage=false, reportUnannotatedClassAttribute=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnnecessaryComparison=false, reportUnusedCallResult=false, reportUnusedImport=false, reportUnusedParameter=false

import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cccp.qc.interfaces.base import QCInterfaceBase, QCResult
from cccp.qc.interfaces.orca_ts import (
    IrcResult,
    TsOptResult,
    freq_block_for_ts,
    irc_block,
    irc_route,
    parse_irc_endpoints,
    parse_ts_frequency_map,
    parse_ts_mode_vectors,
    ts_geom_block,
    ts_opt_route,
)
from cccp.software import SoftwareNotFoundError, resolve_executable
from cccp.utils import ensure_dir
from cccp.utils.file_io import read_xyz
from cccp.utils.geometry_tools import LogParser
from cccp.utils.resource_utils import calc_orca_maxcore, mem_to_mb
from cccp.utils.solvent_map import orca_smd_solvent

logger = logging.getLogger(__name__)


def _resolve_method_meta(method: str | None) -> dict[str, Any] | None:
    """Look up ``METHOD_META`` for *method* (case-insensitive).

    Returns ``None`` if ``acp.catalog`` is unavailable or *method* is not
    declared. Imported lazily so that ``cccp`` has no
    import-time dependency on the ``acp`` package.
    """
    if not method:
        return None
    try:
        from acp.catalog import METHOD_META, _case_insensitive_get
    except ImportError:
        return None
    return _case_insensitive_get(METHOD_META, method)


# --- Hessian resolver (lazy import + module-level cache) -------------------
# ``cccp`` must not import ``acp.chem`` at module load time
# (reverse-dependency). The resolver is pulled in on first use and cached
# so conformer-batch invocations do not re-import per frame. Mirrors the
# existing ``_resolve_method_meta`` pattern.
_RESOLVER = None


def _get_resolver():
    """Return the cached ``resolve_recalc_hess`` callable."""
    global _RESOLVER
    if _RESOLVER is None:
        from acp.chem.composition import resolve_recalc_hess as _resolver

        _RESOLVER = _resolver
    return _RESOLVER


def _resolve_recalc_hess_lazy(
    explicit: object,
    configured: object,
    symbols: list[str] | None,
):
    """Thin wrapper around the ACP resolver; preserves lazy semantics."""
    return _get_resolver()(
        explicit=explicit,
        configured=configured,
        symbols=symbols,
    )


def _record_hessian_resolution(
    out_dir: Path,
    inp_file: Path,
    resolution,
    input_value: object,
    config_value: object,
) -> None:
    """Write a ``<stem>.hessian.json`` sidecar next to the ORCA input.

    Captures the *resolved* Hessian policy so the actual ORCA ``%geom``
    block can be reproduced bit-for-bit. ``auto`` resolution depends on
    the concrete molecule, so recording only the input ``"auto"`` value
    is insufficient for replay (plan §7.5).

    Failures are logged and swallowed: provenance must never break the
    actual ORCA run.
    """
    try:
        sidecar = inp_file.with_suffix(".hessian.json")
        payload = {
            "interval": int(resolution.interval),
            "enabled": bool(resolution.enabled),
            "source": resolution.source,
            "reason": resolution.reason,
            "heavy_elements": list(resolution.heavy_elements),
            "triggering_elements": list(resolution.triggering_elements),
            "input_value": input_value,
            "config_value": config_value,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
        ensure_dir(out_dir)
        with sidecar.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
    except Exception as exc:  # pragma: no cover - best-effort provenance
        logger.warning("Failed to write Hessian resolution sidecar: %s", exc)


_NMR_NUCLEUS_TENSOR_RE = re.compile(
    r"^\s*Nucleus\s+(\d+)\s*([A-Za-z]{1,2})\s*:\s*isotropic\s*=\s*"
    r"([-+]?\d+\.\d+)\s*anisotropy\s*=\s*([-+]?\d+\.\d+)"
)
_NMR_TENSOR_COMP_RE = re.compile(r"([XYZ]{2})\s*=\s*([-+]?\d+\.\d+)")
# ORCA 5.x summary table fallback:
#   Nucleus   Element   Isotropic(ppm)
#      0         6 C       45.230
_NMR_SUMMARY_ROW_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s*([A-Za-z]{1,2})\s+([-+]?\d+\.\d+)\s*$")
_NMR_TENSOR_HEADER = "NMR SHIELDING TENSOR"
_NMR_SUMMARY_HEADER = "CHEMICAL SHIELDING SUMMARY"
_NMR_SHIELDING_HEADERS = (_NMR_TENSOR_HEADER, _NMR_SUMMARY_HEADER)


class NmrShieldingParser:
    """Parse ORCA GIAO NMR shielding output.

    Handles both formats emitted by ORCA 5.x:

    * the full ``NMR SHIELDING TENSOR (PPM)`` block (per-nucleus tensor
      components, written by ``%eprnmr`` or the simple ``NMR`` keyword);
    * the compact ``CHEMICAL SHIELDING SUMMARY (ppm)`` table.

    Returns a mapping of 0-based atom index → shielding descriptor. The
    ORCA ``Nucleus NH:`` line numbers atoms from 1; the parser converts
    to 0-based to match :class:`Structure` indexing.
    """

    @staticmethod
    def parse(
        log_file: Path,
        expected_symbols: list[str] | tuple[str, ...] | None = None,
    ) -> dict[int, dict[str, Any]]:
        """Parse the last NMR shielding section from an ORCA log.

        Args:
            log_file: ORCA ``.out`` log path.
            expected_symbols: When provided, validate that the parsed
                element sequence matches (raises ``ValueError`` on mismatch).

        Returns:
            Dict ``{atom_index(0-based): {"symbol", "isotropic",
            "anisotropy", "tensor_components"}}``.
        """
        try:
            text = Path(log_file).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Could not read NMR shielding from %s: %s", log_file, exc)
            return {}

        lines = text.splitlines()
        shieldings = NmrShieldingParser._parse_tensor_block(lines)
        if not shieldings:
            shieldings = NmrShieldingParser._parse_summary_block(lines)

        if not shieldings:
            logger.warning("No NMR shielding section found in %s", log_file)
            return {}

        if expected_symbols is not None:
            NmrShieldingParser._validate_symbols(shieldings, expected_symbols)
        return shieldings

    @staticmethod
    def _parse_tensor_block(lines: list[str]) -> dict[int, dict[str, Any]]:
        """Parse the ``NMR SHIELDING TENSOR (PPM)`` block."""
        # find the last occurrence of the tensor header
        start = None
        for idx in range(len(lines) - 1, -1, -1):
            if _NMR_TENSOR_HEADER in lines[idx]:
                start = idx + 1
                break
        if start is None:
            return {}

        result: dict[int, dict[str, Any]] = {}
        current: dict[str, Any] | None = None
        for line in lines[start:]:
            if not line.strip():
                if current is not None:
                    result[int(current["atom_index"])] = current
                    current = None
                # blank lines inside a tensor block are normal; only stop
                # when we hit a completely new section / end of relevant data
                continue
            m = _NMR_NUCLEUS_TENSOR_RE.match(line)
            if m:
                if current is not None:
                    result[int(current["atom_index"])] = current
                current = {
                    "atom_index": int(m.group(1)) - 1,  # → 0-based
                    "symbol": _normalize_nmr_symbol(m.group(2)),
                    "isotropic": float(m.group(3)),
                    "anisotropy": float(m.group(4)),
                    "tensor_components": {},
                }
                continue
            if current is not None:
                for comp in _NMR_TENSOR_COMP_RE.finditer(line):
                    current["tensor_components"][comp.group(1)] = float(comp.group(2))
                # stop scanning once we clearly leave the tensor block
                if "JOB DONE" in line or line.startswith("----"):
                    if current is not None:
                        result[int(current["atom_index"])] = current
                        current = None
                    break
        if current is not None:
            result[int(current["atom_index"])] = current
        return result

    @staticmethod
    def _parse_summary_block(lines: list[str]) -> dict[int, dict[str, Any]]:
        """Parse the ``CHEMICAL SHIELDING SUMMARY (ppm)`` table fallback."""
        start = None
        for idx in range(len(lines) - 1, -1, -1):
            if _NMR_SUMMARY_HEADER in lines[idx]:
                start = idx + 1
                break
        if start is None:
            return {}

        result: dict[int, dict[str, Any]] = {}
        for line in lines[start:]:
            m = _NMR_SUMMARY_ROW_RE.match(line)
            if not m:
                stripped = line.strip()
                if not stripped or stripped.startswith("-"):
                    continue
                # skip the column-header row ("Nucleus Element Isotropic(ppm)")
                lowered = stripped.lower()
                if lowered.startswith("nucleus") and "isotropic" in lowered:
                    continue
                if result:
                    break  # already collected rows → left the table
                continue  # haven't seen data yet → keep scanning
            # group layout: <nucleus#> <element_num> <element_sym> <iso>
            # ORCA 5.x summary table Nucleus column is 0-based (starts at 0),
            # unlike the TENSOR block's "Nucleus N El:" which is 1-based.
            # Real ORCA 5.x output example (confirmed by ORCA manual §9.10):
            #   Nucleus   Element   Isotropic(ppm)
            #      0         6 C       45.230
            atom_idx = int(m.group(1))  # 0-based, no -1
            result[atom_idx] = {
                "atom_index": atom_idx,
                "symbol": _normalize_nmr_symbol(m.group(3)),
                "isotropic": float(m.group(4)),
                "anisotropy": None,
                "tensor_components": {},
            }
        return result

    @staticmethod
    def _validate_symbols(
        shieldings: dict[int, dict[str, Any]],
        expected_symbols: list[str] | tuple[str, ...],
    ) -> None:
        expected = [_normalize_nmr_symbol(s) for s in expected_symbols]
        indices = sorted(shieldings)
        if indices != list(range(len(expected))):
            raise ValueError(
                f"Parsed shielding atom indices do not form a contiguous 0..N-1 sequence: {indices}"
            )
        parsed = [shieldings[i]["symbol"] for i in indices]
        if parsed != expected:
            raise ValueError(f"Parsed shielding symbols {parsed} do not match expected {expected}")


def _normalize_nmr_symbol(symbol: str) -> str:
    """Return a normalized element symbol (Title-case, stripped)."""
    s = symbol.strip()
    if not s:
        return s
    return s[:1].upper() + s[1:].lower()


_FREQ_SECTION_HEADER = "VIBRATIONAL FREQUENCIES"
_FREQ_LINE_RE = re.compile(r"^\s*\d+:\s+([-+]?\d+\.\d+)\s+cm\*\*-1", re.MULTILINE)


def _parse_frequencies(output_file: Path) -> list[float]:
    """Parse the final frequency section from an ORCA log.

    ORCA 5.x prints frequency lines as ``   N:     value cm**-1`` (imaginary
    modes carry a ``***imaginary mode***`` suffix).  ``Opt Freq`` jobs
    contain one section per Hessian recalculation during optimization; only
    the last section corresponds to the final geometry.  The six
    translational/rotational zero modes are excluded.

    Args:
        output_file: ORCA ``.out`` log path

    Returns:
        Vibrational frequencies (cm**-1); empty list on parse failure.
    """
    try:
        with open(output_file, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        logger.warning("Could not read frequency data from %s: %s", output_file, e)
        return []
    sections = content.split(_FREQ_SECTION_HEADER)
    if len(sections) < 2:
        logger.warning("No VIBRATIONAL FREQUENCIES section found in %s", output_file)
        return []
    frequencies = [float(m.group(1)) for m in _FREQ_LINE_RE.finditer(sections[-1])]
    return [f for f in frequencies if f != 0.0]


def _apply_mode_displacement(
    coordinates: np.ndarray,
    mode_vector: np.ndarray,
    step_size: float,
    sign: str = "plus",
) -> np.ndarray:
    """Displace coordinates along a normalized normal-mode vector.

    Args:
        coordinates: Cartesian coordinates (N, 3).
        mode_vector: Cartesian displacement vector (N, 3).
        step_size: Displacement amplitude in Å.
        sign: ``"plus"`` or ``"minus"``.

    Returns:
        Displaced coordinates.
    """
    coords = np.asarray(coordinates, dtype=float).reshape((-1, 3))
    vector = np.asarray(mode_vector, dtype=float).reshape((-1, 3))
    if coords.shape != vector.shape:
        raise ValueError(
            f"mode_vector shape must match coordinates shape: {vector.shape} vs {coords.shape}"
        )
    norm = float(np.linalg.norm(vector))
    if norm <= 0.0:
        raise ValueError("mode_vector must be non-zero to apply a mode displacement")
    sign_key = str(sign).strip().lower()
    if sign_key not in {"plus", "minus"}:
        raise ValueError(f"mode_displacement_sign must be 'plus' or 'minus', got {sign!r}")
    direction = 1.0 if sign_key == "plus" else -1.0
    return coords + direction * float(step_size) * (vector / norm)


def _copy_irc_hessian(
    hessian_source: Path,
    target_dir: Path,
    input_stem: str,
) -> Path:
    """Stage a Hessian file under the ORCA basename expected by IRC."""
    source = Path(hessian_source)
    if not source.exists():
        raise FileNotFoundError(f"IRC Hessian file not found: {source}")
    staged = target_dir / f"{input_stem}.hess"
    if source.resolve() != staged.resolve():
        shutil.copy2(source, staged)
    return staged


def _discover_sibling_hessian(source_path: Path | None) -> Path | None:
    """Look for a sibling ``.hess`` file next to a geometry source path."""
    if source_path is None:
        return None
    source = Path(source_path)
    parent = source.parent
    exact = parent / f"{source.stem}.hess"
    if exact.exists():
        return exact
    matches = sorted(parent.glob("*.hess"))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logger.warning(
            "Multiple sibling Hessian files found next to %s; "
            "skipping automatic IRC Hessian handoff",
            source,
        )
    return None


def _read_endpoint_geometry(
    endpoint_file: Path,
    expected_symbols: list[str],
) -> NDArray[np.float64] | None:
    """Read an IRC endpoint XYZ and validate its atom ordering."""
    try:
        coordinates, parsed_symbols = read_xyz(endpoint_file)
    except OSError as e:
        logger.warning("Failed to read IRC endpoint %s: %s", endpoint_file, e)
        return None

    if len(parsed_symbols) != len(expected_symbols):
        logger.warning(
            "IRC endpoint %s atom count mismatch: %d vs expected %d",
            endpoint_file,
            len(parsed_symbols),
            len(expected_symbols),
        )
        return None
    normalized_expected = [_normalize_nmr_symbol(symbol) for symbol in expected_symbols]
    normalized_parsed = [_normalize_nmr_symbol(symbol) for symbol in parsed_symbols]
    if normalized_parsed != normalized_expected:
        logger.warning(
            "IRC endpoint %s symbols %s do not match expected ordering %s",
            endpoint_file,
            normalized_parsed,
            normalized_expected,
        )
        return None
    return np.asarray(coordinates, dtype=float).reshape((-1, 3))


class ORCAInterface(QCInterfaceBase):
    """
    Interface for ORCA calculations.
    """

    def __init__(
        self,
        config: dict[str, Any],
        method: str = "M062X",
        basis: str = "def2-TZVPP",
        solvent: str = None,
        solvent_model: str = "none",
        **kwargs,
    ):
        """
        Initialize ORCA interface.

        Args:
            config: Configuration dictionary
            method: DFT method
            basis: Basis set
            solvent: Solvent model
            solvent_model: Solvent model type - none, smd, cpcm (default none)
            **kwargs: Additional parameters
        """
        super().__init__(config, **kwargs)

        self.method = method
        self.basis = basis
        self.solvent = solvent
        self.solvent_model = solvent_model

        orca_config = self.executables.get("orca", {})
        self.exe_path = Path(orca_config.get("path", "orca"))
        self.executable = resolve_executable(
            "orca",
            configured_path=orca_config.get("path", "orca"),
        )
        self._orca_ld_library_path = orca_config.get("ld_library_path")

        resources = self.resources
        orca_nproc_config = orca_config.get("nproc")
        self.nproc = kwargs.get(
            "nprocs",
            orca_nproc_config if orca_nproc_config is not None else resources.get("nproc", 16),
        )

        self.mem_str = resources.get("mem", "32GB")
        self.mem_mb = mem_to_mb(self.mem_str)

        orca_maxcore_config = orca_config.get("maxcore")
        if orca_maxcore_config is not None:
            self.maxcore = orca_maxcore_config
        else:
            self.maxcore = calc_orca_maxcore(
                self.mem_mb, self.nproc, resources.get("orca_maxcore_safety", 0.8)
            )

        self.charge = kwargs.get("charge", 0)
        self.multiplicity = kwargs.get("multiplicity", 1)

    def _require_executable(self) -> str:
        if self.executable is None:
            raise SoftwareNotFoundError(
                "ORCA executable not found. Add 'orca' to PATH or configure executables.orca.path."
            )
        return str(self.executable)

    def is_available(self) -> bool:
        return self.executable is not None

    def _build_input_blocks(
        self,
        calc_type: str = "opt",
        method: str = None,
        basis: str = None,
        route_extras: list = None,
        geom_maxiter: int = None,
        extra_blocks: list = None,
        recalc_hess: object = None,
        solvent: str = None,
        solvent_model: str = None,
        aux_basis: str = None,
        aux_j_basis: str = None,
        aux_c_basis: str = None,
        symbols: list[str] | None = None,
    ) -> tuple[str, Any]:
        """Build ORCA input blocks.

        Args:
            calc_type: Calculation type
            method: Override method (uses self.method if None)
            basis: Override basis (uses self.basis if None)
            route_extras: Extra route-line keywords appended to the ``!`` line
                (e.g. ``["RIJCOSX", "VeryTightSCF"]``)
            geom_maxiter: Optional MaxIter for the %geom block (opt only)
            extra_blocks: Extra raw input blocks appended after the route
            recalc_hess: Hessian policy for the %geom block (opt only);
                accepts ``"auto"``, ``0``, positive ``N``, or ``None``
                (follow config). See plan §5.1 for full semantics.
            solvent: Override solvent (uses self.solvent if None)
            solvent_model: Override solvent model (uses self.solvent_model if None)
            aux_basis: Legacy auxiliary basis (backward compat, migrated to aux_c_basis)
            aux_j_basis: Auxiliary /J basis for RI-J fitting
            aux_c_basis: Auxiliary /C basis for RI-MP2 correlation
            symbols: Atomic symbols of the molecule. Required when
                ``recalc_hess`` resolves to ``"auto"`` and no explicit
                numeric value is available; ignored otherwise.

        Returns:
            A 2-tuple ``(input_str, resolution)`` where ``input_str`` is
            the rendered ORCA input and ``resolution`` is the resolved
            :class:`HessianResolution` for opt-family calcs (``None`` for
            non-opt routes). The caller is expected to persist the
            resolution alongside the input file.
        """
        _method = method if method is not None else self.method
        _basis = basis if basis is not None else self.basis
        _route_extras = [str(x) for x in route_extras if x] if route_extras else []
        _solvent = solvent if solvent is not None else self.solvent
        _solvent_model = (
            solvent_model if solvent_model is not None else self.solvent_model
        ) or "none"

        blocks = []

        calc_type_map = {
            "opt": "Opt",
            "freq": "Freq",
            "sp": "SP",
            "optfreq": "Opt Freq",
            "nmr": "NMR",
        }
        route = calc_type_map.get(calc_type, calc_type)

        if (
            route in ("Freq", "Opt Freq")
            and _solvent
            and _solvent_model.lower() != "cpcm"
            and _solvent_model.lower() != "none"
        ):
            route = route.replace("Freq", "NumFreq")

        meta = _resolve_method_meta(_method)
        basis_inline = True if meta is None else bool(meta.get("basis_inline", True))
        ri_support = (meta or {}).get("ri_support", "user")

        _aux_j = aux_j_basis
        _aux_c = aux_c_basis
        _filtered_extras: list[str] = []

        _aux_basis_pattern = re.compile(r"/([JC])(?=$|[^A-Za-z])")

        if not aux_j_basis and not aux_c_basis and not aux_basis:
            for x in _route_extras:
                xs = str(x)
                m = _aux_basis_pattern.search(xs)
                if m:
                    kind = m.group(1)
                    if kind == "J" and not _aux_j:
                        _aux_j = xs
                    elif kind == "C" and not _aux_c:
                        _aux_c = xs
                    else:
                        _filtered_extras.append(x)
                else:
                    _filtered_extras.append(x)
        else:
            _filtered_extras = list(_route_extras)

        if aux_basis and not _aux_c:
            _aux_c = aux_basis

        if ri_support in ("composite", "automatic"):
            _ri_keywords = {"RI", "RIJCOSX", "RIJK", "NONE"}
            _filtered_extras = [
                x
                for x in _filtered_extras
                if str(x).upper() not in _ri_keywords and not _aux_basis_pattern.search(str(x))
            ]
            _aux_j = None
            if ri_support == "composite":
                _aux_c = None

        builtin = (meta or {}).get("builtin_dispersion")
        if builtin:
            _filtered_extras = [x for x in _filtered_extras if str(x).upper() != builtin.upper()]

        extras_str = (" " + " ".join(_filtered_extras)) if _filtered_extras else ""

        if not basis_inline:
            method_name = _method
            if _method.lower() == "dlpno-ccsd(t)":
                method_name = "DLPNO-CCSD(T)"

            route_prefix = ""
            if method_name == "DLPNO-CCSD(T)":
                route_prefix = " TightSCF"
            blocks.append(f"! {method_name}{route_prefix} {route}{extras_str}")
        else:
            blocks.append(f"! {_method} {_basis} {route}{extras_str}")

        needs_basis_block = (
            _aux_j
            or _aux_c
            or (
                not basis_inline
                and meta is not None
                and (meta.get("default_aux_j") or meta.get("default_aux_c"))
            )
        )
        if needs_basis_block:
            blocks.append("%basis")
            if not basis_inline:
                effective_basis = _basis or (meta or {}).get("default_basis", "")
                if effective_basis:
                    blocks.append(f'  basis "{effective_basis}"')
            final_aux_j = _aux_j or (meta or {}).get("default_aux_j")
            final_aux_c = _aux_c or (meta or {}).get("default_aux_c")
            for blk in extra_blocks or []:
                if isinstance(blk, dict):
                    if "auxJ" in blk:
                        final_aux_j = blk["auxJ"]
                    if "auxC" in blk:
                        final_aux_c = blk["auxC"]
            if final_aux_j:
                blocks.append(f'  auxJ  "{final_aux_j}"')
            if final_aux_c:
                blocks.append(f'  auxC  "{final_aux_c}"')
            blocks.append("end")

        blocks.append(f"%maxcore {self.maxcore}")
        blocks.append(f"%pal nprocs {self.nproc} end")

        # Hessian policy resolution (plan §7.2).
        # ``recalc_hess`` accepts "auto" / 0 / N / None and is resolved
        # through the shared ACP resolver. ``configured`` defaults to
        # "auto" so an unset config still triggers element inference.
        to_cfg = self.config.get("optimization_control") or {}
        configured_recalc = to_cfg.get("recalc_hess", "auto")

        resolution = None
        is_opt_route = route.split()[0] == "Opt"
        if is_opt_route:
            resolution = _resolve_recalc_hess_lazy(
                explicit=recalc_hess,
                configured=configured_recalc,
                symbols=symbols,
            )
            blocks.append("%geom")
            # Recalc_Hess N: exact Hessian at step 1 and recalculated
            # after N, 2N, ... steps. interval == 0 suppresses the
            # directive entirely: ORCA then NEVER computes an exact
            # Hessian — it uses its default approximate (model) initial
            # Hessian with BFGS updates throughout.
            if resolution.interval > 0:
                blocks.append(f"  Recalc_Hess {resolution.interval}")
            if geom_maxiter is not None and geom_maxiter > 0:
                blocks.append(f"  MaxIter {int(geom_maxiter)}")
            blocks.append("end")

            if resolution.reason == "auto" and resolution.enabled:
                logger.info(
                    "Auto Hessian recalc enabled: interval=%d, non-light elements=%s",
                    resolution.interval,
                    ",".join(resolution.heavy_elements) or "(none)",
                )

        if extra_blocks:
            for blk in extra_blocks:
                # Skip dict entries — they are structured overrides consumed
                # earlier (e.g. DLPNO %basis block auxJ/auxC overrides via
                # R19). Only stringifiable blocks render as raw input.
                if isinstance(blk, dict):
                    continue
                if blk:
                    blocks.append(str(blk))

        if _solvent and _solvent_model.lower() != "none":
            blocks.append("%cpcm")
            if _solvent_model.lower() == "cpcm":
                blocks.append(f'  SMDsolvent "{orca_smd_solvent(_solvent)}"')
            else:  # smd (default)
                blocks.append("  smd true")
                blocks.append(f'  SMDsolvent "{orca_smd_solvent(_solvent)}"')
            blocks.append("end")

        return "\n".join(blocks), resolution

    def _write_input(
        self,
        input_file: Path,
        coordinates: np.ndarray,
        symbols: list[str],
        calc_type: str = "opt",
        charge: int | None = None,
        multiplicity: int | None = None,
        method: str = None,
        basis: str = None,
        route_extras: list = None,
        geom_maxiter: int = None,
        extra_blocks: list = None,
        recalc_hess: object = None,
        solvent: str = None,
        solvent_model: str = None,
        aux_basis: str = None,
        aux_j_basis: str = None,
        aux_c_basis: str = None,
    ):
        """Write ORCA input file.

        Args:
            input_file: Output input file path
            coordinates: Molecular coordinates
            symbols: Element symbols
            calc_type: Calculation type
            charge: Molecular charge
            multiplicity: Spin multiplicity
            method: Override method (uses self.method if None)
            basis: Override basis (uses self.basis if None)
            route_extras: Extra route-line keywords (see _build_input_blocks)
            geom_maxiter: Optional MaxIter for the %geom block
            extra_blocks: Extra raw input blocks
            recalc_hess: Hessian policy ("auto"/0/N/None) for the %geom block
            solvent: Override solvent (uses self.solvent if None)
            solvent_model: Override solvent model (uses self.solvent_model if None)
            aux_basis: Legacy auxiliary basis (backward compat)
            aux_j_basis: Auxiliary /J basis for RI-J fitting
            aux_c_basis: Auxiliary /C basis for RI-MP2 correlation
        """
        charge = charge if charge is not None else self.charge
        multiplicity = multiplicity if multiplicity is not None else self.multiplicity

        blocks, resolution = self._build_input_blocks(
            calc_type,
            method=method,
            basis=basis,
            route_extras=route_extras,
            geom_maxiter=geom_maxiter,
            extra_blocks=extra_blocks,
            recalc_hess=recalc_hess,
            solvent=solvent,
            solvent_model=solvent_model,
            aux_basis=aux_basis,
            aux_j_basis=aux_j_basis,
            aux_c_basis=aux_c_basis,
            symbols=symbols,
        )

        ensure_dir(input_file.parent)

        with open(input_file, "w", encoding="utf-8") as f:
            f.write(blocks + "\n")
            f.write(f"\n* xyz {charge} {multiplicity}\n")

            for symbol, coord in zip(symbols, coordinates):
                f.write(f"{symbol:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}\n")

            f.write("*\n")

        # Persist the resolved Hessian policy for reproducibility (plan §7.5).
        # ``auto`` resolves to a molecule-specific interval; recording only
        # the input "auto" would make the actual %geom block unreproducible.
        if resolution is not None:
            configured_recalc = (self.config.get("optimization_control") or {}).get(
                "recalc_hess", "auto"
            )
            _record_hessian_resolution(
                input_file.parent,
                input_file,
                resolution,
                input_value=recalc_hess,
                config_value=configured_recalc,
            )

    def _run_orca(self, input_file: Path, output_file: Path) -> bool:
        """
        Run ORCA calculation.

        Args:
            input_file: Input file
            output_file: Output file

        Returns:
            True if calculation completed successfully
        """
        ensure_dir(output_file.parent)

        to_cfg = self.config.get("optimization_control") or {}
        to_val = to_cfg.get("timeout") or {}
        timeout = to_val.get("default_seconds", 864000) if isinstance(to_val, dict) else 864000
        executable = self._require_executable()

        try:
            env = None
            if self._orca_ld_library_path:
                env = dict(os.environ)
                env["LD_LIBRARY_PATH"] = self._orca_ld_library_path

            result = subprocess.run(
                [executable, str(input_file)],
                cwd=input_file.parent,
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout,
            )

            with open(output_file, "w", encoding="utf-8") as f:
                f.write(result.stdout)
                if result.stderr:
                    f.write("\nSTDERR:\n")
                    f.write(result.stderr)

            return result.returncode == 0

        except subprocess.TimeoutExpired:
            logger.error(f"ORCA calculation timed out: {input_file}")
            return False
        except Exception as e:
            logger.error(f"ORCA calculation failed: {e}")
            return False

    def optimize(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path = None,
        output_name: str = "optimize",
        method: str = None,
        basis: str = None,
        **kwargs,
    ) -> QCResult:
        """
        Perform geometry optimization.

        Args:
            coordinates: Initial coordinates (N, 3)
            symbols: Element symbols
            charge: Molecular charge
            multiplicity: Spin multiplicity
            output_dir: Output directory
            output_name: Base name for output files
            method: Override method (uses self.method if None)
            basis: Override basis (uses self.basis if None)
            **kwargs: Additional parameters

        Returns:
            QCResult with optimization results
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        ensure_dir(output_dir)

        input_file = output_dir / f"{output_name}.inp"
        output_file = output_dir / f"{output_name}.out"

        _solvent = kwargs.pop("solvent", None)
        _solvent_model = kwargs.pop("solvent_model", None)

        self._write_input(
            input_file,
            coordinates,
            symbols,
            "opt",
            charge,
            multiplicity,
            method=method,
            basis=basis,
            route_extras=kwargs.get("route_extras"),
            geom_maxiter=kwargs.get("geom_maxiter"),
            extra_blocks=kwargs.get("extra_blocks"),
            recalc_hess=kwargs.get("recalc_hess"),
            solvent=_solvent,
            solvent_model=_solvent_model,
            aux_basis=kwargs.get("aux_basis"),
            aux_j_basis=kwargs.get("aux_j_basis"),
            aux_c_basis=kwargs.get("aux_c_basis"),
        )

        success = self._run_orca(input_file, output_file)

        if not success:
            return QCResult(
                success=False,
                error_message="ORCA optimization failed",
                output_file=input_file,
                log_file=output_file,
            )

        coords, syms, error = LogParser.extract_last_converged_coords(output_file, "orca")
        energy = LogParser.extract_energy(output_file, "orca")

        if coords is None:
            return QCResult(
                success=False,
                error_message=error or "Could not extract coordinates",
                output_file=input_file,
                log_file=output_file,
            )

        return QCResult(
            success=True,
            energy=energy,
            coordinates=coords,
            symbols=syms or symbols,
            converged=True,
            output_file=input_file,
            log_file=output_file,
        )

    def single_point(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path = None,
        output_name: str = "sp",
        method: str = None,
        basis: str = None,
        **kwargs,
    ) -> QCResult:
        """
        Perform single-point energy calculation.

        Args:
            coordinates: Molecular coordinates (N, 3)
            symbols: Element symbols
            charge: Molecular charge
            multiplicity: Spin multiplicity
            output_dir: Output directory
            output_name: Base name for output files
            method: Override method (uses self.method if None)
            basis: Override basis (uses self.basis if None)
            **kwargs: Additional parameters

        Returns:
            QCResult with single-point energy
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        ensure_dir(output_dir)

        input_file = output_dir / f"{output_name}.inp"
        output_file = output_dir / f"{output_name}.out"

        _solvent = kwargs.pop("solvent", None)
        _solvent_model = kwargs.pop("solvent_model", None)

        self._write_input(
            input_file,
            coordinates,
            symbols,
            "sp",
            charge,
            multiplicity,
            method=method,
            basis=basis,
            route_extras=kwargs.get("route_extras"),
            extra_blocks=kwargs.get("extra_blocks"),
            solvent=_solvent,
            solvent_model=_solvent_model,
            aux_basis=kwargs.get("aux_basis"),
            aux_j_basis=kwargs.get("aux_j_basis"),
            aux_c_basis=kwargs.get("aux_c_basis"),
        )

        success = self._run_orca(input_file, output_file)

        if not success:
            return QCResult(
                success=False,
                error_message="ORCA SP calculation failed",
                output_file=input_file,
                log_file=output_file,
            )

        energy = LogParser.extract_energy(output_file, "orca")

        if energy is None:
            return QCResult(
                success=False,
                error_message="Could not extract energy",
                output_file=input_file,
                log_file=output_file,
            )

        return QCResult(
            success=True,
            energy=energy,
            coordinates=coordinates,
            symbols=symbols,
            converged=True,
            output_file=input_file,
            log_file=output_file,
        )

    def frequency(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path = None,
        output_name: str = "freq",
        method: str = None,
        basis: str = None,
        **kwargs,
    ) -> QCResult:
        """
        Perform frequency calculation.

        Args:
            coordinates: Molecular coordinates (N, 3)
            symbols: Element symbols
            charge: Molecular charge
            multiplicity: Spin multiplicity
            output_dir: Output directory
            output_name: Base name for output files
            method: Override method (uses self.method if None)
            basis: Override basis (uses self.basis if None)
            **kwargs: Additional parameters

        Returns:
            QCResult with frequency results
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        ensure_dir(output_dir)

        input_file = output_dir / f"{output_name}.inp"
        output_file = output_dir / f"{output_name}.out"

        _solvent = kwargs.pop("solvent", None)
        _solvent_model = kwargs.pop("solvent_model", None)

        self._write_input(
            input_file,
            coordinates,
            symbols,
            "freq",
            charge,
            multiplicity,
            method=method,
            basis=basis,
            route_extras=kwargs.get("route_extras"),
            extra_blocks=kwargs.get("extra_blocks"),
            solvent=_solvent,
            solvent_model=_solvent_model,
            aux_basis=kwargs.get("aux_basis"),
            aux_j_basis=kwargs.get("aux_j_basis"),
            aux_c_basis=kwargs.get("aux_c_basis"),
        )

        success = self._run_orca(input_file, output_file)

        if not success:
            return QCResult(
                success=False,
                error_message="ORCA frequency calculation failed",
                output_file=input_file,
                log_file=output_file,
            )

        energy = LogParser.extract_energy(output_file, "orca")
        coords, syms, _ = LogParser.extract_last_converged_coords(output_file, "orca")

        frequencies = _parse_frequencies(output_file)

        return QCResult(
            success=True,
            energy=energy,
            coordinates=coords if coords is not None else coordinates,
            symbols=syms or symbols,
            converged=True,
            output_file=input_file,
            log_file=output_file,
            frequencies=frequencies if frequencies else None,
            has_frequencies=len(frequencies) > 0,
        )

    def opt_freq(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path = None,
        output_name: str = "orca_optfreq",
        method: str = None,
        basis: str = None,
        route_extras: list = None,
        geom_maxiter: int = None,
        extra_blocks: list = None,
        recalc_hess: object = None,
        aux_basis: str = None,
        aux_j_basis: str = None,
        aux_c_basis: str = None,
        **kwargs,
    ) -> QCResult:
        """Run combined optimization + frequency as single ORCA job.

        Uses calc_type='optfreq' -> generates '! ... Opt Freq ...' route line.
        NumFreq fallback is handled automatically by _build_input_blocks.

        Args:
            coordinates: Initial coordinates (N, 3)
            symbols: Element symbols
            charge: Molecular charge
            multiplicity: Spin multiplicity
            output_dir: Output directory
            output_name: Base name for output files
            method: Override method (uses self.method if None)
            basis: Override basis (uses self.basis if None)
            route_extras: Extra route-line keywords
            geom_maxiter: Max geometry iterations
            extra_blocks: Extra raw input blocks
            recalc_hess: Hessian policy ("auto"/0/N/None) for Recalc_Hess
            aux_basis: Legacy auxiliary basis (backward compat)
            aux_j_basis: Auxiliary /J basis for RI-J fitting
            aux_c_basis: Auxiliary /C basis for RI-MP2 correlation
            **kwargs: Additional parameters

        Returns:
            QCResult with opt+freq results
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        ensure_dir(output_dir)

        input_file = output_dir / f"{output_name}.inp"
        output_file = output_dir / f"{output_name}.out"

        _solvent = kwargs.pop("solvent", None)
        _solvent_model = kwargs.pop("solvent_model", None)

        self._write_input(
            input_file,
            coordinates,
            symbols,
            "optfreq",
            charge,
            multiplicity,
            method=method,
            basis=basis,
            route_extras=route_extras,
            geom_maxiter=geom_maxiter,
            extra_blocks=extra_blocks,
            recalc_hess=recalc_hess,
            solvent=_solvent,
            solvent_model=_solvent_model,
            aux_basis=aux_basis,
            aux_j_basis=aux_j_basis,
            aux_c_basis=aux_c_basis,
        )

        success = self._run_orca(input_file, output_file)

        if not success:
            return QCResult(
                success=False,
                error_message="ORCA opt+freq calculation failed",
                output_file=input_file,
                log_file=output_file,
            )

        coords, syms, error = LogParser.extract_last_converged_coords(output_file, "orca")
        energy = LogParser.extract_energy(output_file, "orca")

        frequencies = _parse_frequencies(output_file)

        if coords is None:
            return QCResult(
                success=False,
                error_message=error or "Could not extract coordinates from optfreq output",
                output_file=input_file,
                log_file=output_file,
            )

        return QCResult(
            success=True,
            energy=energy,
            coordinates=coords,
            symbols=syms or symbols,
            converged=True,
            output_file=input_file,
            log_file=output_file,
            frequencies=frequencies if frequencies else None,
            has_frequencies=len(frequencies) > 0,
        )

    def nmr_shielding(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path = None,
        output_name: str = "nmr",
        method: str = None,
        basis: str = None,
        nuclei: list[str] | None = None,
        **kwargs,
    ) -> QCResult:
        """Perform a GIAO NMR shielding calculation via ORCA ``%eprnmr``.

        Uses a dedicated input writer (``_write_nmr_input``) that emits the
        ``%eprnmr`` block for explicit nucleus selection rather than the
        simple ``NMR`` route keyword, giving finer control over which nuclei
        are computed. The default method/basis is ``mPW1PW91/6-311G(d)`` to
        match the Goodman DP4/DP5 error model (DevDoc §8.0); callers that
        override the level must keep the error model consistent.

        Args:
            coordinates: Molecular coordinates (N, 3).
            symbols: Element symbols.
            charge: Molecular charge.
            multiplicity: Spin multiplicity.
            output_dir: Output directory.
            output_name: Base name for output files.
            method: DFT method override (default mPW1PW91 if neither this
                nor ``self.method`` is set).
            basis: Basis set override (default 6-311G(d)).
            nuclei: Target nuclei as element symbols (e.g. ``["C", "H"]``).
                When ``None``, defaults to all distinct elements present in
                the molecule that are NMR-active (1H, 13C, 19F, 31P, 15N).
            **kwargs: ``solvent`` / ``solvent_model`` and other overrides.

        Returns:
            :class:`QCResult` with ``metadata["shieldings"]`` holding the
            parsed shielding dict (0-based atom index → descriptor).
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        ensure_dir(output_dir)

        input_file = output_dir / f"{output_name}.inp"
        output_file = output_dir / f"{output_name}.out"

        solvent = kwargs.pop("solvent", None)
        solvent_model = kwargs.pop("solvent_model", None)

        self._write_nmr_input(
            input_file,
            coordinates,
            symbols,
            charge=charge,
            multiplicity=multiplicity,
            method=method,
            basis=basis,
            solvent=solvent,
            solvent_model=solvent_model,
            nuclei=nuclei,
        )

        success = self._run_orca(input_file, output_file)

        if not success:
            return QCResult(
                success=False,
                error_message="ORCA NMR calculation failed",
                output_file=input_file,
                log_file=output_file,
            )

        energy = LogParser.extract_energy(output_file, "orca")
        shieldings = NmrShieldingParser.parse(output_file, expected_symbols=symbols)

        return QCResult(
            success=True,
            energy=energy,
            coordinates=coordinates,
            symbols=symbols,
            converged=True,
            output_file=input_file,
            log_file=output_file,
            metadata={"shieldings": shieldings},
        )

    def transition_state_opt(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path = None,
        output_name: str = "ts_opt",
        method: str = None,
        basis: str = None,
        initial_hessian: str = "calculate",
        recalc_hess: int = 5,
        trust_radius: float = 0.15,
        **kwargs,
    ) -> TsOptResult:
        """Run an ORCA OptTS + independent-frequency transition-state search.

        The input carries an ``! OptTS`` route (built via
        :func:`cccp.qc.interfaces.orca_ts.ts_opt_route`), a ``%geom`` block
        requesting a calculated Hessian / RecalcHess / TrustRadius, and a
        ``%freq`` block for the independent frequency analysis used to verify
        the transition state (exactly one imaginary mode).

        Args:
            coordinates: Initial TS-guess coordinates (N, 3).
            symbols: Element symbols.
            charge: Molecular charge.
            multiplicity: Spin multiplicity.
            output_dir: Output directory.
            output_name: Base name for output files.
            method: Override method (uses ``self.method`` if None).
            basis: Override basis (uses ``self.basis`` if None).
            initial_hessian: ``"calculate"`` (default) / ``"model"`` / ``"read"``.
            recalc_hess: Recalculate Hessian every N steps (0 disables).
            trust_radius: Initial TrustRadius for the TS optimizer.
            **kwargs: ``solvent`` / ``solvent_model`` / ``grid`` / ``scf`` /
                ``nproc`` / ``ts_mode`` / ``opt_level`` /
                ``mode_displacement`` / ``mode_vector`` /
                ``mode_displacement_sign`` overrides.

        Returns:
            :class:`TsOptResult` with the converged TS geometry, energies and
            imaginary frequencies.
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        ensure_dir(output_dir)

        input_file = output_dir / f"{output_name}.inp"
        output_file = output_dir / f"{output_name}.out"

        _solvent = kwargs.pop("solvent", None)
        _solvent_model = kwargs.pop("solvent_model", None)
        _grid = kwargs.pop("grid", None)
        _scf = kwargs.pop("scf", None)
        _nproc = kwargs.pop("nproc", None)
        _ts_mode = kwargs.pop("ts_mode", False)
        _opt_level = kwargs.pop("opt_level", None)
        _mode_displacement = kwargs.pop("mode_displacement", None)
        _mode_vector = kwargs.pop("mode_vector", None)
        _mode_displacement_sign = kwargs.pop("mode_displacement_sign", "plus")
        if kwargs:
            logger.warning(
                "Unused ORCA transition_state_opt kwargs for %s: %s",
                output_name,
                sorted(kwargs),
            )

        eff_method = method or self.method
        eff_basis = basis or self.basis

        input_coordinates = np.asarray(coordinates, dtype=float).reshape((-1, 3))
        if _mode_displacement is not None:
            if _mode_vector is None:
                raise ValueError(
                    "mode_displacement requires a mode_vector kwarg containing "
                    "an (N, 3) normal-mode array"
                )
            input_coordinates = _apply_mode_displacement(
                input_coordinates,
                np.asarray(_mode_vector, dtype=float),
                float(_mode_displacement),
                str(_mode_displacement_sign),
            )

        route = ts_opt_route(
            eff_method,
            eff_basis or "",
            grid=_grid,
            scf=_scf,
            solvent=_solvent,
            solvent_model=_solvent_model,
            nproc=_nproc or self.nproc,
            opt_level=_opt_level,
        )
        blocks = (
            route
            + "\n"
            + ts_geom_block(
                initial_hessian,
                recalc_hess,
                trust_radius,
                ts_mode=_ts_mode,
            )
            + "\n"
            + freq_block_for_ts()
        )

        with open(input_file, "w", encoding="utf-8") as f:
            f.write(blocks + "\n")
            f.write(f"\n* xyz {charge} {multiplicity}\n")
            for symbol, coord in zip(symbols, input_coordinates):
                f.write(f"{symbol:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}\n")
            f.write("*\n")

        success = self._run_orca(input_file, output_file)

        if not success:
            return TsOptResult(
                success=False,
                error_message="ORCA transition-state optimization failed",
                output_file=input_file,
                log_file=output_file,
            )

        output_text = output_file.read_text(encoding="utf-8", errors="replace")
        coords, syms, _ = LogParser.extract_last_converged_coords(output_file, "orca")
        energy = LogParser.extract_energy(output_file, "orca")
        frequency_map = parse_ts_frequency_map(output_text)
        frequencies = list(frequency_map.values())
        imaginary = [f for f in frequencies if f < 0.0]
        mode_vectors = parse_ts_mode_vectors(output_text)
        most_negative_mode_index = None
        mode_vector = None
        negative_pairs = [
            (mode_index, freq) for mode_index, freq in frequency_map.items() if freq < 0.0
        ]
        if negative_pairs:
            most_negative_mode_index, _ = min(negative_pairs, key=lambda item: item[1])
            mode_vector = mode_vectors.get(most_negative_mode_index)

        return TsOptResult(
            success=coords is not None,
            energy_hartree=energy,
            coordinates=coords,
            symbols=syms or symbols,
            converged=coords is not None,
            imaginary_frequencies=imaginary,
            all_frequencies=frequencies,
            output_file=input_file,
            log_file=output_file,
            mode_vector=mode_vector,
        )

    def irc(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path = None,
        output_name: str = "irc",
        method: str = None,
        basis: str = None,
        direction: str = "both",
        max_iter: int = 100,
        hess_file: Path | None = None,
        **kwargs,
    ) -> IrcResult:
        """Run an ORCA IRC from a converged transition state.

        Args:
            coordinates: Converged TS coordinates (N, 3).
            symbols: Element symbols.
            charge: Molecular charge.
            multiplicity: Spin multiplicity.
            output_dir: Output directory.
            output_name: Base name for output files.
            method: Override method (uses ``self.method`` if None).
            basis: Override basis (uses ``self.basis`` if None).
            direction: ``"forward"`` / ``"reverse"`` / ``"both"`` (default).
            max_iter: Maximum IRC steps.
            hess_file: Optional Hessian file to stage as ``<output_name>.hess``.
            **kwargs: ``solvent`` / ``solvent_model`` /
                ``irc_midpoint_reseed`` / ``geometry_source`` overrides.

        Returns:
            :class:`IrcResult` with endpoint paths and step counts.
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        ensure_dir(output_dir)

        input_file = output_dir / f"{output_name}.inp"
        output_file = output_dir / f"{output_name}.out"

        _solvent = kwargs.pop("solvent", None)
        _solvent_model = kwargs.pop("solvent_model", None)
        _irc_midpoint_reseed = bool(kwargs.pop("irc_midpoint_reseed", False))
        _geometry_source = kwargs.pop("geometry_source", None)
        if kwargs:
            logger.warning("Unused ORCA irc kwargs for %s: %s", output_name, sorted(kwargs))

        eff_method = method or self.method
        eff_basis = basis or self.basis

        staged_hessian = None
        if hess_file is not None:
            staged_hessian = _copy_irc_hessian(Path(hess_file), output_dir, output_name)
        elif not _irc_midpoint_reseed:
            sibling_hessian = _discover_sibling_hessian(
                Path(_geometry_source) if _geometry_source else None
            )
            if sibling_hessian is not None:
                staged_hessian = _copy_irc_hessian(sibling_hessian, output_dir, output_name)
            else:
                logger.warning(
                    "No IRC Hessian supplied for %s; omitting InitHess Read "
                    "from the ORCA %%irc block",
                    output_name,
                )

        route = irc_route(
            eff_method,
            eff_basis or "",
            solvent=_solvent,
            solvent_model=_solvent_model,
        )
        blocks = (
            route
            + "\n"
            + irc_block(
                direction,
                max_iter,
                hess_file_name=staged_hessian.name if staged_hessian is not None else None,
                irc_midpoint_reseed=_irc_midpoint_reseed,
            )
        )

        with open(input_file, "w", encoding="utf-8") as f:
            f.write(blocks + "\n")
            f.write(f"\n* xyz {charge} {multiplicity}\n")
            for symbol, coord in zip(symbols, coordinates):
                f.write(f"{symbol:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}\n")
            f.write("*\n")

        success = self._run_orca(input_file, output_file)

        if not success:
            return IrcResult(
                success=False,
                error_message="ORCA IRC calculation failed",
                output_file=input_file,
                log_file=output_file,
            )

        output_text = output_file.read_text(encoding="utf-8", errors="replace")
        endpoints = parse_irc_endpoints(output_text, output_dir)
        forward_points = output_text.count("IRC forward direction")
        reverse_points = output_text.count("IRC reverse direction")
        final_geometries: dict[str, np.ndarray] = {}
        for endpoint_direction, endpoint_file in endpoints.items():
            endpoint_coords = _read_endpoint_geometry(endpoint_file, symbols)
            if endpoint_coords is not None:
                final_geometries[endpoint_direction] = endpoint_coords

        return IrcResult(
            success=True,
            endpoints=endpoints or None,
            forward_points=forward_points,
            reverse_points=reverse_points,
            output_file=input_file,
            log_file=output_file,
            final_geometries=final_geometries,
        )

    def _write_nmr_input(
        self,
        input_file: Path,
        coordinates: np.ndarray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        method: str = None,
        basis: str = None,
        solvent: str = None,
        solvent_model: str = None,
        nuclei: list[str] | None = None,
    ) -> None:
        """Write an ORCA GIAO NMR input with a ``%eprnmr`` block.

        Defaults to ``mPW1PW91/6-311G(d)`` (Goodman DP4/DP5 reference level)
        when neither the override nor the instance default is set to an NMR
        level. Solvent is emitted as the standalone ``CPCM(<name>)`` /
        ``SMD(<name>)`` route keyword per the DevDoc §9.2 convention.
        """
        _method = method if method is not None else self.method
        if not _method:
            _method = "mPW1PW91"
        _basis = basis if basis is not None else self.basis
        if not _basis:
            _basis = "6-311G(d)"
        _solvent = solvent if solvent is not None else self.solvent
        _solvent_model = (
            solvent_model if solvent_model is not None else self.solvent_model
        ) or "cpcm"

        target_elements = self._resolve_nmr_nuclei(nuclei, symbols)

        lines: list[str] = [f"! {_method} {_basis} TightSCF"]
        if _solvent and _solvent_model.lower() != "none":
            solv_name = orca_smd_solvent(_solvent)
            model = _solvent_model.lower()
            if model == "smd":
                lines.append(f"! SMD({solv_name})")
            else:  # cpcm (default for NMR)
                lines.append(f"! CPCM({solv_name})")

        if target_elements:
            lines.append("%eprnmr")
            for element in target_elements:
                lines.append(f"  nuclei = all {element} {{shift}}")
            lines.append("end")

        lines.append(f"%maxcore {self.maxcore}")
        lines.append(f"%pal nprocs {self.nproc} end")

        body = "\n".join(lines) + "\n"
        body += f"\n* xyz {charge} {multiplicity}\n"
        for symbol, coord in zip(symbols, coordinates):
            body += f"{symbol:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}\n"
        body += "*\n"

        ensure_dir(input_file.parent)
        input_file.write_text(body, encoding="utf-8")

    @staticmethod
    def _resolve_nmr_nuclei(
        nuclei: list[str] | None,
        symbols: list[str],
    ) -> list[str]:
        """Resolve the target NMR-active elements.

        Args:
            nuclei: Explicit element list (e.g. ``["C", "H"]``); when given,
                used as-is (order preserved, de-duplicated).
            symbols: Molecular element symbols (fallback to derive from).

        Returns:
            Distinct NMR-active elements present in the molecule, preserving
            user-given order. Falls back to the molecule's NMR-active
            elements (with a warning) when *nuclei* names elements outside
            the supported set — so ``--nuclei Si`` does not silently produce
            a plain single-point run with no shielding output.
        """
        active = {"H", "C", "N", "F", "P"}
        if nuclei:
            seen: set[str] = set()
            out: list[str] = []
            for n in nuclei:
                sym = _normalize_nmr_symbol(str(n))
                if sym in active and sym not in seen:
                    seen.add(sym)
                    out.append(sym)
            if not out:
                # All requested nuclei are outside the supported set —
                # fall back to the molecule's NMR-active elements rather
                # than emitting a GIAO-less plain SP job (silent data loss).
                logger.warning(
                    "Requested nuclei %s contain no supported NMR-active "
                    "element (H/C/N/F/P); falling back to molecule elements",
                    nuclei,
                )
                return ORCAInterface._resolve_nmr_nuclei(None, symbols)
            return out
        present = []
        for sym in symbols:
            norm = _normalize_nmr_symbol(sym)
            if norm in active and norm not in present:
                present.append(norm)
        return present
