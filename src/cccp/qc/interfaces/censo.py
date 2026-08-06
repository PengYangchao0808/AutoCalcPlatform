"""
CENSO Interface
===============

Interface for CENSO conformer ensemble generation and refinement.

Author: QCcalc Team
"""

import copy
import json
import logging
import math
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import numpy as np

from cccp.utils.file_io import read_xyz_multiframe

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CensoConformerRecord:
    """Single conformer record parsed from CENSO JSON output."""

    conf_id: str
    frame_index: int
    energy: float
    gsolv: float
    grrho: float
    gtot: float
    coordinates: np.ndarray
    symbols: List[str]

    def __post_init__(self) -> None:
        if not math.isfinite(self.energy):
            raise ValueError(f"Non-finite energy for {self.conf_id}: {self.energy}")
        if not math.isfinite(self.gsolv):
            raise ValueError(f"Non-finite gsolv for {self.conf_id}: {self.gsolv}")
        if not math.isfinite(self.grrho):
            raise ValueError(f"Non-finite grrho for {self.conf_id}: {self.grrho}")
        if not math.isfinite(self.gtot):
            raise ValueError(f"Non-finite gtot for {self.conf_id}: {self.gtot}")


@dataclass
class CensoRunResult:
    """Aggregated result from a CENSO run."""

    preset: str
    records: List[CensoConformerRecord] = field(default_factory=list)
    final_part: str = ""
    work_dir: Optional[Path] = None
    temperature: float = 298.15

    def boltzmann_weights(self) -> Dict[str, float]:
        k_b_hartree_per_kelvin = 3.166811563e-6
        if not self.records:
            return {}

        kt = k_b_hartree_per_kelvin * self.temperature
        gtot_min = min(r.gtot for r in self.records)

        raw: Dict[str, float] = {}
        for r in self.records:
            weight = math.exp(-(r.gtot - gtot_min) / kt)
            raw[r.conf_id] = weight

        total = sum(raw.values())
        if total <= 0:
            return {k: 0.0 for k in raw}

        return {k: v / total for k, v in raw.items()}

    def sort_by_gtot(self) -> None:
        self.records.sort(key=lambda r: r.gtot)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class CensoError(Exception):
    """Base CENSO-related error."""


class CensoExecutionError(CensoError):
    """CENSO subprocess exited with non-zero code."""


class CensoParseError(CensoError):
    """Failed to parse CENSO JSON/XYZ output."""


class CensoNotAvailableError(CensoError):
    """CENSO binary is not available on this system."""


# ---------------------------------------------------------------------------
# Preset definitions
# ---------------------------------------------------------------------------

CENSO_PRESETS: Dict[str, Dict[str, Any]] = {
    "censo-light": {
        "parts": ["prescreening", "screening"],
        "prescreening": {"func": "b97-3c", "threshold": 8.0},
        "screening": {"func": "b97-3c", "threshold": 6.0},
        "refinement": {"func": "wb97m-v", "basis": "def2-tzvpp", "threshold": 0.99},
        "parse_part": "screening",
    },
    "censo-default": {
        "parts": ["prescreening", "screening", "optimization", "refinement"],
        "prescreening": {"func": "pbe-d3", "basis": "def2-sv(p)", "threshold": 4.0},
        "screening": {"func": "r2scan-3c", "basis": "def2-mtzvpp", "threshold": 3.5},
        "optimization": {
            "func": "r2scan-3c",
            "optlevel": "normal",
            "threshold": 3.0,
            "optcycles": 8,
            "maxcyc": 200,
            "macrocycles": True,
            "xtb_opt": True,
        },
        "refinement": {"func": "wb97m-v", "basis": "def2-tzvpp", "threshold": 0.99},
        "parse_part": "refinement",
    },
    "censo-zero": {
        "parts": ["refinement"],
        "refinement": {"func": "wb97m-v", "basis": "def2-tzvpp", "threshold": 0.99},
        "parse_part": "refinement",
    },
}

CENSO_PART_FLAGS = {
    "prescreening": "--prescreening",
    "screening": "--screening",
    "optimization": "--optimization",
    "refinement": "--refinement",
}

CENSO_PARSE_PRIORITY = ["refinement", "optimization", "screening", "prescreening"]

_GTOT_TOLERANCE = 1e-6

# Minimum OMP threads per CENSO child calculation.  CENSO subdivides the
# total core budget (--maxcores) across parallel children
# (n_parallel = maxcores / omp-min), and EACH child inherits the OMP/MKL/
# OPENBLAS env of the parent CENSO process.  Therefore the env must carry
# the PER-CHILD thread share (= omp-min), NOT the full nproc — otherwise
# every parallel child spawns ``nproc`` threads and oversubscribes the node
# by the parallelism factor (e.g. nproc=16, omp-min=4 → 4 children × 16
# threads = 64 threads on a 16-core budget).
_DEFAULT_OMP_MIN_THREADS = 4


# ---------------------------------------------------------------------------
# CensoInterface
# ---------------------------------------------------------------------------


class CensoInterface:
    """Interface wrapping the CENSO CLI for conformer ensemble generation
    and refinement.

    CENSO is invoked as a subprocess. Input/output follows the CENSO v3.x
    file contract (``N_PART.json`` / ``N_PART.xyz`` / ``N_PART.out``).
    """

    def __init__(self, config: Dict[str, Any], **kwargs):
        """
        Initialize CENSO interface.

        Args:
            config: Configuration dictionary
            **kwargs: Additional parameters
        """
        self.config = config

        censo_cfg = config.get("executables", {}).get("censo", {})
        self._censo_path = censo_cfg.get("path", "censo")
        self._orca_path = config.get("executables", {}).get("orca", {}).get("path", "orca")
        self._xtb_path = config.get("executables", {}).get("xtb", {}).get("path", "xtb")
        self._default_preset = config.get("censo", {}).get("preset", "censo-light")
        self._default_solvent = config.get("censo", {}).get("solvent")
        self._temperature = config.get("censo", {}).get("temperature", 298.15)
        self._keep_all = bool(config.get("censo", {}).get("keep_all", False))
        self._solvent_model = config.get("censo", {}).get("solvent_model", "none").lower()
        raw_nproc = config.get("resources", {}).get("nproc", 16)
        try:
            self._nproc = max(1, int(raw_nproc))
        except (TypeError, ValueError):
            self._nproc = 16

    # ----- QCBackend ABC ---------------------------------------------------

    def is_available(self) -> bool:
        """Return True when the CENSO binary is on PATH."""
        return shutil.which(self._censo_path) is not None

    def get_version(self) -> Optional[str]:
        """Return the CENSO version string when available."""
        try:
            result = subprocess.run(
                [self._censo_path, "-v"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None
        return None

    # ----- Preset helpers --------------------------------------------------

    def resolve_preset(self, preset: Optional[str]) -> Dict[str, Any]:
        name = preset or self._default_preset
        if not name:
            name = "censo-light"

        name_lower = name.lower()

        if name_lower in _PRESETS:
            return {"name": name_lower, **copy.deepcopy(_PRESETS[name_lower])}

        allowed = ", ".join(sorted(_PRESETS))
        raise ValueError(f"Unknown CENSO preset '{preset}'. Allowed: {allowed}")

    # ----- rcfile generation -----------------------------------------------

    def generate_rcfile(
        self,
        preset_cfg: Dict[str, Any],
        output_dir: Path,
        charge: int,
        multiplicity: int,
        solvent: Optional[str],
        templated_parts: Optional[Set[str]] = None,
        solvent_model: Optional[str] = None,
    ) -> Path:
        rcfile = output_dir / "censo2rc"
        lines: List[str] = []
        templated_parts = templated_parts or set()

        uhf = multiplicity - 1 if multiplicity > 0 else 0

        # [general]
        if solvent is not None:
            solvent_val = solvent
        else:
            solvent_val = self._default_solvent
        lines.append("[general]")
        lines.append(f"temperature = {self._temperature}")
        lines.append("evaluate_rrho = True")
        lines.append("sm_rrho = alpb")
        lines.append("imagthr = -100.0")
        lines.append("sthr = 50.0")
        if solvent_val:
            lines.append(f"solvent = {solvent_val}")
            lines.append("gas_phase = False")
        else:
            lines.append("gas_phase = True")
        lines.append("balance = True")
        lines.append("ignore_failed = True")
        lines.append(f"charge = {charge}")
        lines.append(f"uhf = {uhf}")
        lines.append("")

        active_parts = preset_cfg.get("parts", [])
        for part_name in ["prescreening", "screening", "optimization", "refinement"]:
            part_cfg = preset_cfg.get(part_name, {})
            if not part_cfg:
                continue
            # CENSO 3.0.8 validates every section present in the rcfile
            # (even for parts that are not enabled), so only active parts
            # may be written — e.g. an ACP-side DLPNO refinement functional
            # would otherwise fail CENSO's functional-key validation.
            if part_name not in active_parts:
                continue

            lines.append(f"[{part_name}]")
            lines.append("prog = orca")

            for key, val in part_cfg.items():
                if isinstance(val, bool):
                    lines.append(f"{key} = {'True' if val else 'False'}")
                else:
                    lines.append(f"{key} = {val}")

            # CENSO v3.0.8 defaults sm=COSMORS for screening & refinement
            # parts. When solvent is set (gas_phase=False) this triggers a
            # cosmotherm path requirement that ACP cannot satisfy (the project
            # uses ORCA SMD/CPCM, not COSMO-RS). Explicitly set sm to an
            # ORCA-compatible solvation model to avoid the validation trap.
            if part_name in ("screening", "refinement") and solvent_val:
                if "sm" not in part_cfg:
                    sm_value = (
                        str(solvent_model).lower()
                        if solvent_model and str(solvent_model).lower() != "none"
                        else self._solvent_model
                    )
                    lines.append(f"sm = {sm_value}")

            lines.append("gfnv = gfn2")
            lines.append(f"template = {'True' if part_name in templated_parts else 'False'}")
            lines.append("")

        # [paths]
        lines.append("[paths]")
        lines.append(f"orca = {self._orca_path}")
        lines.append(f"xtb = {self._xtb_path}")
        lines.append("")

        rcfile.write_text("\n".join(lines), encoding="utf-8")
        logger.debug("Wrote CENSO rcfile to %s", rcfile)
        return rcfile

    # ----- Advanced-field template injection (per-run HOME isolation) -------

    def write_part_templates(
        self,
        output_dir: Path,
        part_templates: Dict[str, List[str]],
    ) -> Path:
        """Write ``{part}.orca.template`` files under a per-run HOME.

        CENSO only reads templates from ``$HOME/.censo2_assets/``; to keep
        concurrent jobs isolated the subprocess HOME is redirected to
        ``{output_dir}/home``. Template bodies use the official
        ``{main}``/``{geom}`` placeholders; extra lines are generated from a
        whitelisted field set only (no free-text injection).

        Returns:
            The per-run HOME directory to inject into the subprocess env.
        """
        home_dir = output_dir / "home"
        assets_dir = home_dir / ".censo2_assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        for part_name, extra_lines in part_templates.items():
            body_lines = ["{main}"]
            body_lines.extend(str(line) for line in extra_lines if line)
            body_lines.append("")
            body_lines.append("{geom}")
            body_lines.append("")
            template_path = assets_dir / f"{part_name}.orca.template"
            template_path.write_text("\n".join(body_lines), encoding="utf-8")
            logger.debug("Wrote CENSO template %s", template_path)

        return home_dir

    # ----- CLI construction ------------------------------------------------

    def build_cli(
        self,
        input_xyz: Path,
        rcfile: Path,
        preset_cfg: Dict[str, Any],
        nproc: int,
        temperature: float,
        solvent: Optional[str],
        nconf: Optional[int] = None,
        keep_all: bool = False,
        charge: int = 0,
        multiplicity: int = 1,
    ) -> List[str]:
        cmd = [self._censo_path, "-i", str(input_xyz)]

        unpaired = multiplicity - 1 if multiplicity > 0 else 0
        cmd.extend(["-c", str(charge), "-u", str(unpaired)])

        part_list = preset_cfg.get("parts", [])
        for part in CENSO_PART_FLAGS:
            if part in part_list:
                cmd.append(_PART_FLAGS[part])

        if nconf is not None and nconf > 0:
            cmd.extend(["-n", str(nconf)])

        cmd.extend(["--inprc", str(rcfile)])
        cmd.extend(["--maxcores", str(nproc)])
        # --omp-min must never exceed --maxcores (CENSO rejects omp-min >
        # maxcores), so cap it at nproc for small jobs (e.g. nproc=2).
        cmd.extend(["--omp-min", str(min(_DEFAULT_OMP_MIN_THREADS, max(1, nproc)))])

        cmd.extend(["-T", str(temperature)])

        if solvent is not None:
            solvent_val = solvent
        else:
            solvent_val = self._default_solvent
        if solvent_val:
            cmd.extend(["--solvent", str(solvent_val)])
        else:
            cmd.append("--gas-phase")

        cmd.append("--evaluate-rrho")
        cmd.append("--sm-rrho")
        cmd.append("alpb")
        if keep_all:
            cmd.append("--keep-all")
        cmd.append("--ignore-failed")

        return cmd

    # ----- Output parsing --------------------------------------------------

    def _resolve_final_part(self, output_dir: Path) -> Optional[str]:
        for part_name in CENSO_PARSE_PRIORITY:
            json_path = output_dir / f"{_part_index(part_name)}_{part_name.upper()}.json"
            if json_path.exists():
                return part_name
        return None

    def parse_censo_json(
        self,
        json_path: Path,
        xyz_path: Path,
    ) -> List[CensoConformerRecord]:
        if not json_path.exists():
            raise CensoParseError(f"CENSO JSON not found: {json_path}")
        if not xyz_path.exists():
            raise CensoParseError(f"CENSO XYZ not found: {xyz_path}")

        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        records_data = data.get("data", {})
        if not records_data:
            logger.warning("No conformer data found in %s", json_path)
            return []

        all_coords, symbols = read_xyz_multiframe(xyz_path)
        n_atoms = len(symbols)

        if n_atoms == 0:
            raise CensoParseError(f"No atoms found in XYZ: {xyz_path}")

        n_frames_xyz = len(all_coords) // n_atoms if n_atoms > 0 else 0
        title_map = self._read_xyz_frame_titles(xyz_path)

        records: List[CensoConformerRecord] = []
        for conf_id, entry in records_data.items():
            if not isinstance(entry, dict):
                logger.warning("Invalid entry for %s (not a dict): %s", conf_id, type(entry))
                continue

            raw_energy = entry.get("energy")
            raw_gsolv = entry.get("gsolv")
            raw_grrho = entry.get("grrho")
            raw_gtot = entry.get("gtot")
            if raw_energy is None:
                logger.warning("Missing 'energy' for %s, defaulting to 0.0", conf_id)
            if raw_gtot is None:
                logger.warning("Missing 'gtot' for %s, defaulting to 0.0", conf_id)

            energy = float(raw_energy) if raw_energy is not None else 0.0
            gsolv = float(raw_gsolv) if raw_gsolv is not None else 0.0
            grrho = float(raw_grrho) if raw_grrho is not None else 0.0
            gtot = float(raw_gtot) if raw_gtot is not None else 0.0

            computed_gtot = energy + gsolv + grrho
            if abs(computed_gtot - gtot) > _GTOT_TOLERANCE:
                logger.warning(
                    "gtot mismatch for %s: parsed=%.10f computed=%.10f (diff=%.2e)",
                    conf_id,
                    gtot,
                    computed_gtot,
                    abs(computed_gtot - gtot),
                )

            frame_index = title_map.get(conf_id)
            if frame_index is not None and frame_index < n_frames_xyz:
                start = frame_index * n_atoms
                end = start + n_atoms
                coord = np.array(all_coords[start:end], dtype=float)
                syms = list(symbols)
            else:
                logger.warning(
                    "Frame for %s not found in XYZ (title_map=%s, n_frames=%d)",
                    conf_id,
                    title_map,
                    n_frames_xyz,
                )
                coord = np.zeros((0, 3), dtype=float)
                syms = []

            record = CensoConformerRecord(
                conf_id=conf_id,
                frame_index=frame_index if frame_index is not None else -1,
                energy=energy,
                gsolv=gsolv,
                grrho=grrho,
                gtot=gtot,
                coordinates=coord,
                symbols=syms,
            )
            records.append(record)

        return records

    @staticmethod
    def _read_xyz_frame_titles(xyz_path: Path) -> Dict[str, int]:
        """Parse XYZ frame comment lines to build {conf_id -> frame_index} map."""
        titles: Dict[str, int] = {}
        with open(xyz_path, encoding="utf-8") as f:
            lines = f.readlines()
        i = 0
        frame_idx = 0
        while i < len(lines):
            try:
                natoms = int(lines[i].strip())
            except (ValueError, IndexError):
                i += 1
                continue
            if natoms == 0:
                break
            if i + 1 < len(lines):
                title = lines[i + 1].strip()
                titles[title] = frame_idx
            i += natoms + 2
            frame_idx += 1
        return titles

    def _validate_xyz_json_consistency(
        self,
        json_path: Path,
        xyz_path: Path,
    ) -> None:
        """Validate that JSON keys match XYZ frame names."""
        if not json_path.exists() or not xyz_path.exists():
            return

        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        json_keys = set(data.get("data", {}).keys())

        xyz_keys = set(self._read_xyz_frame_titles(xyz_path).keys())
        if not json_keys.issubset(xyz_keys):
            missing = json_keys - xyz_keys
            raise CensoParseError(f"JSON keys missing in XYZ: {missing}")

    # ----- Boltzmann weight comparison -------------------------------------

    def _compare_boltzmann_weights(
        self,
        result: CensoRunResult,
        out_path: Path,
        tolerance: float = 0.005,
    ) -> None:
        if not out_path.exists():
            logger.debug("No .out file for Boltzmann cross-check: %s", out_path)
            return

        computed = result.boltzmann_weights()

        table_weights: Dict[str, float] = {}
        with open(out_path, encoding="utf-8", errors="replace") as f:
            content = f.read()

        header_found = False
        for line in content.splitlines():
            if "Boltzmann weight" in line:
                header_found = True
                continue
            if not header_found:
                continue
            parts = line.split()
            if len(parts) >= 3:
                try:
                    conf_name = parts[0]
                    bw = float(parts[-1])
                    table_weights[conf_name] = bw
                except (ValueError, IndexError):
                    continue

        table_sum = sum(table_weights.values())
        if table_sum > 0:
            table_weights = {k: v / table_sum for k, v in table_weights.items()}

        for conf_id, comp_w in computed.items():
            table_w = table_weights.get(conf_id)
            if table_w is not None and abs(comp_w - table_w) > tolerance:
                logger.warning(
                    "Boltzmann weight mismatch for %s: computed=%.6f table=%.6f",
                    conf_id,
                    comp_w,
                    table_w,
                )

    # ----- Main refinement entry point -------------------------------------

    def refine_ensemble(
        self,
        ensemble_xyz: Path,
        output_dir: Path,
        *,
        preset: Optional[str] = None,
        charge: int = 0,
        multiplicity: int = 1,
        temperature: Optional[float] = None,
        solvent: Optional[str] = None,
        nproc: Optional[int] = None,
        include_refinement: bool = False,
        nconf: Optional[int] = None,
        part_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
        keep_all: Optional[bool] = None,
        part_templates: Optional[Dict[str, List[str]]] = None,
        solvent_model: Optional[str] = None,
    ) -> CensoRunResult:
        """Run CENSO on an ensemble XYZ and return parsed results.

        Args:
            ensemble_xyz: Path to input multi-frame XYZ.
            output_dir: Working directory for CENSO (cwd during execution).
            preset: Preset name (censo-light/censo-default/censo-zero).
            charge: Molecular charge.
            multiplicity: Spin multiplicity.
            temperature: Temperature in Kelvin.
            solvent: Solvent name or None for gas phase.
            nproc: Number of processors (also pins the BLAS/OpenMP thread
                count of the subprocess env).
            include_refinement: Append the refinement part to the preset's
                part list (energy workflow, ``--no-opt`` cheap path).
            nconf: Limit CENSO to the first N input frames (``-n`` flag);
                ``-n 1`` selects the xTB rank1 conformer (censo-zero semantics).
            part_overrides: Per-part rcfile key overrides, e.g.
                ``{"refinement": {"func": "dlpno-ccsd(t)"}}``. Merged on top
                of the resolved preset configuration.
            keep_all: Pass ``--keep-all`` so CENSO does not truncate the
                ensemble at part thresholds. Defaults to the ``censo.keep_all``
                config value (False — literature truncation semantics).
            part_templates: Advanced-field template lines per part, e.g.
                ``{"refinement": ["! RIJCOSX def2-TZVPP/C VeryTightSCF"]}``.
                Written to a per-run ``$HOME/.censo2_assets/`` and activated
                via ``template = True`` in the rcfile (§6.4 HOME isolation).
            solvent_model: Solvation model for ORCA-compatible sm override.

        Returns:
            CensoRunResult with parsed conformer records.
        """
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        ensemble_xyz = Path(ensemble_xyz).resolve()
        if not ensemble_xyz.exists():
            raise FileNotFoundError(f"Input ensemble XYZ not found: {ensemble_xyz}")

        # CENSO chdirs into the input file's parent directory and writes all
        # outputs there (§4.2). Copy the ensemble into the isolated per-run
        # censo/ directory (§5.2) so outputs land in output_dir.
        local_input = output_dir / "crest_conformers.xyz"
        if ensemble_xyz != local_input:
            shutil.copy2(ensemble_xyz, local_input)
        ensemble_xyz = local_input

        if not self.is_available():
            raise CensoNotAvailableError(f"CENSO binary '{self._censo_path}' not found on PATH")

        preset_cfg = self._resolve_preset(preset)

        if include_refinement and "refinement" not in preset_cfg.get("parts", []):
            preset_cfg["parts"] = [*preset_cfg.get("parts", []), "refinement"]
            preset_cfg["parse_part"] = "refinement"

        if part_overrides:
            for part_name, overrides in part_overrides.items():
                if not isinstance(overrides, dict):
                    continue
                merged = dict(preset_cfg.get(part_name, {}))
                merged.update(overrides)
                preset_cfg[part_name] = merged

        temp = temperature if temperature is not None else self._temperature
        nproc_val = nproc if nproc is not None else self._nproc
        keep_all_val = self._keep_all if keep_all is None else bool(keep_all)

        effective_templates = {
            part: lines for part, lines in (part_templates or {}).items() if lines
        }

        rcfile = self._generate_rcfile(
            preset_cfg,
            output_dir,
            charge,
            multiplicity,
            solvent,
            templated_parts=set(effective_templates),
            solvent_model=solvent_model,
        )

        env: Optional[Dict[str, str]] = None

        orca_cfg = self.config.get("executables", {}).get("orca", {})
        ld_path = orca_cfg.get("ld_library_path")
        if ld_path:
            env = dict(os.environ)
            env["LD_LIBRARY_PATH"] = ld_path

        if effective_templates:
            if env is None:
                env = dict(os.environ)
            home_dir = self._write_part_templates(output_dir, effective_templates)
            env["HOME"] = str(home_dir)
            logger.info("CENSO template injection active (HOME=%s)", home_dir)

        # Pin BLAS/OpenMP threads for the xTB/ORCA children CENSO spawns.
        #
        # Why the value is omp-min and NOT nproc (verified against CENSO
        # 2.0.1 source, censo/parallel.py + processing/processor.py):
        #
        # CENSO parallelises via a Dask LocalCluster with a HARD concurrency
        # cap of ``threads_per_worker = ncores // omp-min`` (= 4 for our
        # default omp-min=4).  set_omp() assigns each job a per-job omp
        # (= omp-min for the bulk of conformers, higher for the few trailing
        # stragglers), and submits it with ``resources={"CPU": job.omp}`` --
        # but that is only a Dask SCHEDULING hint.  The actual subprocess
        # threads come from ``ENVIRON = os.environ.copy()`` (processor.py
        # ``env=env or ENVIRON``): CENSO does NOT set OMP_NUM_THREADS per
        # child, and xTB honours the env over its own ``--parallel`` flag.
        #
        # So whatever we put here is the de-facto thread count of EVERY
        # child, and the peak load is ``max_concurrency * env_omp``.  The
        # largest value that never exceeds ncores is therefore omp-min:
        #   (ncores // omp-min) * omp-min == ncores   (saturated, never over)
        # Setting it to nproc makes every parallel child spawn nproc threads
        # -> oversubscription by the concurrency factor (observed on
        # compute-01: 4 children x 16 threads = 64 on a 40-core node).  The
        # only downside of capping at omp-min is that trailing single
        # stragglers (which CENSO would otherwise give >omp-min cores) run
        # under-utilised -- unavoidable with a static env snapshot.
        # Assign keys individually (env.update() would clobber the
        # HOME/LD_LIBRARY_PATH overrides set above).
        if env is None:
            env = dict(os.environ)
        pinned_nproc = max(1, int(nproc_val))
        omp_per_child = min(_DEFAULT_OMP_MIN_THREADS, pinned_nproc)
        env["OMP_NUM_THREADS"] = str(omp_per_child)
        env["MKL_NUM_THREADS"] = str(omp_per_child)
        env["OPENBLAS_NUM_THREADS"] = str(omp_per_child)

        cmd = self._build_cli(
            ensemble_xyz,
            rcfile,
            preset_cfg,
            nproc_val,
            temp,
            solvent,
            nconf=nconf,
            keep_all=keep_all_val,
            charge=charge,
            multiplicity=multiplicity,
        )

        logger.info("Running CENSO: %s", " ".join(cmd))
        stdout_path = output_dir / "censo_stdout.log"
        stderr_path = output_dir / "censo_stderr.log"

        timeout_seconds = self.config.get("censo", {}).get("timeout_seconds")

        try:
            proc = subprocess.run(
                cmd,
                cwd=output_dir,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=env,
            )
        except FileNotFoundError as exc:
            raise CensoExecutionError(f"CENSO binary not found: {self._censo_path}") from exc
        except subprocess.TimeoutExpired as exc:
            raise CensoExecutionError(
                f"CENSO timed out after {timeout_seconds}s: {self._censo_path}"
            ) from exc

        stdout_path.write_text(proc.stdout or "")
        stderr_path.write_text(proc.stderr or "")

        if proc.stderr:
            logger.debug("CENSO stderr (see %s): %s", stderr_path, proc.stderr.strip()[-500:])

        if proc.returncode != 0:
            crash_dump = output_dir / "CRASH_DUMP.json"
            tail = (proc.stderr or "")[-2000:] + (proc.stdout or "")[-2000:]
            error_msg = (
                f"CENSO exited with code {proc.returncode}. "
                f"See {stdout_path} and {stderr_path} for details.\n"
                f"Tail output:\n{tail}"
            )

            if crash_dump.exists():
                try:
                    crash_data = json.loads(crash_dump.read_text(encoding="utf-8"))
                    error_msg += f"\nCRASH_DUMP: {json.dumps(crash_data, indent=2)[:500]}"
                except Exception:
                    pass

            raise CensoExecutionError(error_msg)

        final_part = self._resolve_final_part(output_dir)
        if final_part is None:
            raise CensoParseError(
                f"No CENSO output JSON found in {output_dir}. Checked: {_PARSE_PRIORITY}"
            )

        part_idx = _part_index(final_part)
        json_path = output_dir / f"{part_idx}_{final_part.upper()}.json"
        xyz_path = output_dir / f"{part_idx}_{final_part.upper()}.xyz"

        self._validate_xyz_json_consistency(json_path, xyz_path)

        records = self._parse_censo_json(json_path, xyz_path)

        for rec in records:
            if rec.grrho == 0.0:
                logger.debug(
                    "grrho=0 for %s — evaluate_rrho may not be active",
                    rec.conf_id,
                )

        result = CensoRunResult(
            preset=preset_cfg["name"],
            records=records,
            final_part=final_part,
            work_dir=output_dir,
            temperature=temp,
        )
        result.sort_by_gtot()

        out_path = output_dir / f"{part_idx}_{final_part.upper()}.out"
        self._compare_boltzmann_weights(result, out_path)

        return result

    # ----- ConformerSearcher Protocol --------------------------------------

    def search(
        self,
        initial_xyz: Path,
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Optional[Path] = None,
        **kwargs,
    ) -> Path:
        """Run CENSO ensemble generation and return the final ensemble XYZ path.

        Implements the :class:`ConformerSearcher` protocol.
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        output_dir.mkdir(parents=True, exist_ok=True)

        preset = kwargs.pop("preset", self._default_preset)
        solvent = kwargs.pop("solvent", self._default_solvent)
        temperature = kwargs.pop("temperature", self._temperature)
        nproc = kwargs.pop("nproc", self._nproc)
        keep_all = kwargs.pop("keep_all", None)

        result = self.refine_ensemble(
            initial_xyz,
            output_dir,
            preset=preset,
            charge=charge,
            multiplicity=multiplicity,
            temperature=temperature,
            solvent=solvent,
            nproc=nproc,
            keep_all=keep_all,
        )

        if not result.records:
            raise CensoError("CENSO search produced no conformer records")

        part_idx = _part_index(result.final_part)
        final_xyz = output_dir / f"{part_idx}_{result.final_part.upper()}.xyz"
        return final_xyz

    # ----- Legacy private aliases (kept for backward compatibility) -----

    _resolve_preset = resolve_preset

    _generate_rcfile = generate_rcfile

    _write_part_templates = write_part_templates

    _build_cli = build_cli

    _parse_censo_json = parse_censo_json



# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

CENSO_PART_INDEX_MAP: Dict[str, str] = {
    "prescreening": "0",
    "screening": "1",
    "optimization": "2",
    "refinement": "3",
}


def part_index(part_name: str) -> str:
    return CENSO_PART_INDEX_MAP.get(part_name, "0")


# Legacy private aliases (kept for backward compatibility).
_PRESETS = CENSO_PRESETS
_PART_FLAGS = CENSO_PART_FLAGS
_PARSE_PRIORITY = CENSO_PARSE_PRIORITY
_PART_INDEX_MAP = CENSO_PART_INDEX_MAP
_part_index = part_index
