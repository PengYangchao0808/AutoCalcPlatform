"""
xTB Path Search Interface
=========================

Interface for xTB metadynamics path searches (``xtb --path``).

Author: QCcalc Team
"""

# pyright: reportAny=false, reportExplicitAny=false, reportPrivateUsage=false, reportUnannotatedClassAttribute=false, reportUnusedCallResult=false

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cccp.qc.interfaces.xtb import _thread_env
from cccp.software import SoftwareNotFoundError, resolve_executable
from cccp.utils import ensure_dir
from cccp.utils.solvent_map import xtb_method_name, xtb_solvent_args

logger = logging.getLogger(__name__)

_PATH_TITLE_ENERGY_RE = re.compile(
    r"(?:energy|e)\s*[:=]\s*([-+]?\d+(?:\.\d+)?(?:[EeDd][+-]?\d+)?)",
    re.IGNORECASE,
)
_PATH_TITLE_FLOAT_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[EeDd][+-]?\d+)?")


@dataclass(frozen=True)
class PathSearchResult:
    """Result of an xTB ``--path`` run.

    Attributes:
        frame_paths: Materialized XYZ file for each path frame.
        energies_hartree: Per-frame energies parsed from the multi-frame
            trajectory title lines when available.
        success: Whether the xTB path search completed and yielded frames.
        trajectory_file: Original xTB multi-frame trajectory.
        stdout_file: Stored subprocess stdout path.
        stderr_file: Stored subprocess stderr path.
        error_message: Failure note when ``success`` is false.
    """

    frame_paths: list[Path]
    energies_hartree: list[float | None]
    success: bool
    trajectory_file: Path | None = None
    stdout_file: Path | None = None
    stderr_file: Path | None = None
    error_message: str | None = None

    def __len__(self) -> int:
        return len(self.frame_paths)


class XTBPathInterface:
    """Interface for xTB metadynamics path searches."""

    def __init__(
        self,
        config: dict[str, Any],
        gfn_level: int = 2,
        solvent: str | None = None,
        solvent_model: str = "none",
        **kwargs: Any,
    ) -> None:
        self.config = config
        executables = config.get("executables", {})

        xtb_config = executables.get("xtb", {})
        self.exe_path = Path(xtb_config.get("path", "xtb"))
        self.executable = resolve_executable(
            "xtb",
            configured_path=xtb_config.get("path", "xtb"),
        )

        self.gfn_level = gfn_level
        self.solvent = solvent
        self.solvent_model = (solvent_model or "none").lower()

        resources = config.get("resources", {})
        raw_nproc = kwargs.get("nproc", resources.get("nproc", 16))
        try:
            self.nproc = max(1, int(raw_nproc))
        except (TypeError, ValueError):
            self.nproc = 1

    def _require_executable(self) -> str:
        if self.executable is None:
            raise SoftwareNotFoundError(
                "xTB executable not found. Add 'xtb' to PATH or configure executables.xtb.path."
            )
        return str(self.executable)

    def is_available(self) -> bool:
        return self.executable is not None

    def _solvent_args(self, solvent: str | None = None, gfn_level: int | None = None) -> list[str]:
        """Return xTB solvation flags for the EFFECTIVE solvent/method/model."""
        sol = solvent if solvent is not None else self.solvent
        level = self.gfn_level if gfn_level is None else gfn_level
        return xtb_solvent_args(
            sol,
            method=xtb_method_name(level),
            solvent_model=self.solvent_model,
        )

    def path_search(
        self,
        start_xyz: Path,
        end_xyz: Path,
        output_dir: Path,
        *,
        nrun: int = 1,
        npoint: int = 25,
        anopt: int = 10,
        kpush: float = 0.003,
        kpull: float = -0.015,
        ppull: float = 0.05,
        alp: float = 1.2,
        charge: int = 0,
        multiplicity: int = 1,
        uhf: int = 0,
        gfn_level: int | None = None,
        solvent: str | None = None,
        etemp: float | None = None,
        timeout: int | None = None,
        path_inp_text: str | None = None,
        extra_args: Sequence[str] = (),
        seed: int | None = None,
    ) -> PathSearchResult:
        """Run ``xtb --path`` between *start_xyz* and *end_xyz*.

        Parameter semantics match the RPH ``run_path()`` contract: path bias
        settings are written to ``path.inp`` and the subprocess is launched as
        ``xtb <start> --path <end> --input path.inp``.

        Args:
            start_xyz: Reactant XYZ path.
            end_xyz: Product XYZ path.
            output_dir: Working directory for the path search.
            nrun: Number of metadynamics runs (used when *path_inp_text* is None).
            npoint: Number of path points (used when *path_inp_text* is None).
            anopt: Optimization steps between points (used when *path_inp_text* is None).
            kpush: Bias push force constant (used when *path_inp_text* is None).
            kpull: Bias pull force constant (used when *path_inp_text* is None).
            ppull: Pull offset (used when *path_inp_text* is None).
            alp: Gaussian width (used when *path_inp_text* is None).
            charge: Molecular charge passed to ``--chrg``.
            multiplicity: Spin multiplicity; maps to xTB ``--uhf`` as
                ``max(0, multiplicity - 1)`` when *uhf* is 0.
            uhf: Explicit xTB ``--uhf`` value from the frozen recipe. When
                greater than 0 it overrides the *multiplicity* mapping; when 0
                the mapping is used. ``--uhf`` is always passed on the command
                line (even 0).
            gfn_level: GFN method level; ``--gfn <gfn_level>`` is always
                passed (even 2).
            solvent: Optional solvent name for ALPB/GBSA solvation flags.
            etemp: Optional electronic temperature (``--etemp``).
            timeout: Subprocess timeout in seconds.
            path_inp_text: Externally authored ``path.inp`` body. When
                provided it is written verbatim; when None the bias block is
                generated from the keyword arguments above.
            extra_args: Extra CLI flags appended after the standard flags.
            seed: Optional random seed passed as ``--seed`` when not None.

        Returns:
            PathSearchResult with materialized frames and parsed energies.
        """
        output_dir = Path(output_dir)
        ensure_dir(output_dir)

        start_xyz = Path(start_xyz)
        end_xyz = Path(end_xyz)
        if not start_xyz.exists():
            return PathSearchResult(
                frame_paths=[],
                energies_hartree=[],
                success=False,
                error_message=f"start_xyz not found: {start_xyz}",
            )
        if not end_xyz.exists():
            return PathSearchResult(
                frame_paths=[],
                energies_hartree=[],
                success=False,
                error_message=f"end_xyz not found: {end_xyz}",
            )

        start_copy = output_dir / start_xyz.name
        end_copy = output_dir / end_xyz.name
        if start_copy.resolve() != start_xyz.resolve():
            start_copy.write_text(start_xyz.read_text(encoding="utf-8"), encoding="utf-8")
        if end_copy.resolve() != end_xyz.resolve():
            end_copy.write_text(end_xyz.read_text(encoding="utf-8"), encoding="utf-8")

        input_file = output_dir / "path.inp"
        if path_inp_text is not None:
            input_file.write_text(path_inp_text, encoding="utf-8")
        else:
            input_file.write_text(
                "\n".join(
                    [
                        "$path",
                        f"   nrun={int(nrun)}",
                        f"   npoint={int(npoint)}",
                        f"   anopt={int(anopt)}",
                        f"   kpush={float(kpush)}",
                        f"   kpull={float(kpull)}",
                        f"   ppull={float(ppull)}",
                        f"   alp={float(alp)}",
                        "$end",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

        stdout_file = output_dir / "xtb_path.stdout.log"
        stderr_file = output_dir / "xtb_path.stderr.log"

        try:
            executable = self._require_executable()
            resolved_gfn_level = int(self.gfn_level if gfn_level is None else gfn_level)
            resolved_solvent = solvent if solvent is not None else self.solvent
            cmd = [
                executable,
                start_copy.name,
                "--path",
                end_copy.name,
                "--input",
                input_file.name,
                "-P",
                str(self.nproc),
                "--chrg",
                str(charge),
            ]
            resolved_uhf = int(uhf) if uhf else max(0, int(multiplicity) - 1)
            cmd.extend(["--uhf", str(resolved_uhf)])
            cmd.extend(["--gfn", str(resolved_gfn_level)])
            if resolved_solvent:
                cmd.extend(self._solvent_args(resolved_solvent, gfn_level=resolved_gfn_level))
            if etemp is not None:
                cmd.extend(["--etemp", str(float(etemp))])
            if seed is not None:
                cmd.extend(["--seed", str(int(seed))])
            if extra_args:
                cmd.extend(str(arg) for arg in extra_args)

            result = subprocess.run(
                cmd,
                cwd=output_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_thread_env(self.nproc),
            )

            stdout_file.write_text(result.stdout or "", encoding="utf-8")
            stderr_file.write_text(result.stderr or "", encoding="utf-8")

            trajectory_file = _locate_path_trajectory(output_dir)
            frame_paths: list[Path] = []
            energies_hartree: list[float | None] = []
            if trajectory_file is not None:
                frame_paths, energies_hartree = _split_multiframe_xyz(
                    trajectory_file,
                    output_dir / "path_frames",
                )

            success = result.returncode == 0 and bool(frame_paths)
            error_message = None
            if not success:
                if result.returncode != 0:
                    error_message = f"xTB path search failed with return code {result.returncode}"
                elif trajectory_file is None:
                    error_message = "xTB path trajectory (xtbpath.txt/xtbpath.xyz) not found"
                else:
                    error_message = f"No path frames could be parsed from {trajectory_file.name}"

            return PathSearchResult(
                frame_paths=frame_paths,
                energies_hartree=energies_hartree,
                success=success,
                trajectory_file=trajectory_file,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
                error_message=error_message,
            )
        except Exception as exc:
            logger.error("xTB path search failed: %s", exc)
            return PathSearchResult(
                frame_paths=[],
                energies_hartree=[],
                success=False,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
                error_message=str(exc),
            )


def path_search(
    config: dict[str, Any],
    start_xyz: Path,
    end_xyz: Path,
    output_dir: Path,
    **kwargs: Any,
) -> PathSearchResult:
    """Convenience wrapper around :class:`XTBPathInterface`."""
    interface_keys = {"gfn_level", "solvent", "solvent_model", "nproc"}
    interface_kwargs = {key: kwargs.pop(key) for key in list(kwargs) if key in interface_keys}
    return XTBPathInterface(config=config, **interface_kwargs).path_search(
        start_xyz=start_xyz,
        end_xyz=end_xyz,
        output_dir=output_dir,
        **kwargs,
    )


def _locate_path_trajectory(output_dir: Path) -> Path | None:
    for candidate in ("xtbpath.txt", "xtbpath.xyz", "xtbpath_0.xyz"):
        path = output_dir / candidate
        if path.exists():
            return path
    return None


def _split_multiframe_xyz(
    trajectory_file: Path,
    frame_dir: Path,
) -> tuple[list[Path], list[float | None]]:
    ensure_dir(frame_dir)
    lines = trajectory_file.read_text(encoding="utf-8", errors="replace").splitlines()
    frame_paths: list[Path] = []
    energies_hartree: list[float | None] = []

    cursor = 0
    while cursor < len(lines):
        raw_header = lines[cursor].strip()
        if not raw_header:
            cursor += 1
            continue
        try:
            atom_count = int(raw_header)
        except ValueError:
            cursor += 1
            continue
        if atom_count <= 0:
            break
        end = cursor + atom_count + 2
        if end > len(lines):
            break
        title = lines[cursor + 1] if cursor + 1 < len(lines) else ""
        frame_path = frame_dir / f"path_frame_{len(frame_paths):03d}.xyz"
        frame_path.write_text("\n".join(lines[cursor:end]) + "\n", encoding="utf-8")
        frame_paths.append(frame_path)
        energies_hartree.append(_energy_from_title(title))
        cursor = end

    return frame_paths, energies_hartree


def _energy_from_title(title: str) -> float | None:
    match = _PATH_TITLE_ENERGY_RE.search(title)
    if match is not None:
        try:
            return float(match.group(1).replace("D", "E").replace("d", "e"))
        except ValueError:
            return None
    numeric_tokens = _PATH_TITLE_FLOAT_RE.findall(title.replace("D", "E").replace("d", "e"))
    if len(numeric_tokens) == 1:
        try:
            return float(numeric_tokens[0])
        except ValueError:
            return None
    return None


__all__ = ["PathSearchResult", "XTBPathInterface", "path_search"]
