"""Multi-replica xTB MD sampling (workflow-layer convention).

Single-trajectory responsibility lives in :meth:`MolclusBackend.run_md`;
this module implements the ``--md-seeds`` replica scheme on top of it: one
``run_md`` call per replica with strictly increasing seeds, each replica
starting from a distinct RDKit-embedded conformation of the original input
when ``md_seeds > 1`` (v1.3 multi-start), and the replica trajectories
merged into a single multi-frame XYZ for downstream batch optimization.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from acp.backends.base import QCResult
from acp.backends.registry import get_backend
from acp.chem.embedding import enumerate_embeddings

logger = logging.getLogger(__name__)


def run_md_replicas(
    input_source: str | Path,
    primary_xyz: Path,
    *,
    md_seed: int = 42,
    md_seeds: int = 1,
    embed_seed_base: int = 42,
    md_method: str = "gfnff",
    temperature: float = 400.0,
    time_ps: float = 100.0,
    dump_fs: float = 100.0,
    step_fs: float = 1.0,
    hmass: float = 1.0,
    shake: bool = True,
    nvt: bool = True,
    solvent: str | None = None,
    solvent_model: str = "none",
    charge: int = 0,
    multiplicity: int = 1,
    output_dir: str | Path = "./xtbmd_output",
    config: dict[str, Any] | None = None,
    timeout: int | None = None,
) -> QCResult:
    """Run ``md_seeds`` independent xTB MD trajectories and merge them.

    Replica *i* uses seed ``md_seed + i``.  With ``md_seeds > 1`` each
    replica starts from a different RDKit-embedded conformation of
    *input_source* (multi-start; the enumeration derives from the original
    input, not from the primary embedding), and the per-replica start
    conformation index is recorded in the result metadata.  Trajectories are
    merged into ``output_dir/traj.xyz`` preserving the original frame titles
    (which carry the GFN-FF potential energies used by the downstream
    equilibration analysis).

    Args:
        input_source: Original workflow input (SMILES or structure path) —
            the multi-start enumeration is derived from this.
        primary_xyz: The workflow's embedded single-frame XYZ (start
            structure for single-replica runs; replica 0 of multi-replica
            runs uses the enumeration, which re-derives it from the original
            input).
        md_seed: Base random seed; replica seeds increment from it.
        md_seeds: Number of replica trajectories (≥ 1).  Multi-start
            embedding only kicks in above 1.
        embed_seed_base: Base seed for the RDKit ETKDG multi-start
            enumeration.
        md_method: ``gfnff`` / ``gfn0`` / ``gfn1`` / ``gfn2``.
        temperature: Target temperature (K).
        time_ps: Simulation length (ps).
        dump_fs: Trajectory dump interval (fs).
        step_fs: Integration time step (fs).
        hmass: Hydrogen mass scaling.
        shake: Constrain X–H bonds via SHAKE.
        nvt: NVT ensemble (False selects NPT).
        solvent: Solvent name (e.g. ``water``); ``None`` for vacuum.
        solvent_model: ``alpb`` (default) or ``gbsa``.
        charge: Total charge.
        multiplicity: Spin multiplicity.
        output_dir: Output directory (merged trajectory lands in
            ``output_dir/traj.xyz``; per-replica working directories in
            ``output_dir/replica_%02d/``).  Callers pass the v2 WORK stage
            dir (``WORK/02_SEARCH/xTB``) so MD outputs stay in the task
            workspace.
        config: Backend config dict passed to ``get_backend("molclus")``.
        timeout: Per-trajectory subprocess timeout in seconds.  ``None`` or
            ``0`` falls back to the backend default (300 s) — too short for
            production MD runs (10s–100s of ps can take minutes to hours),
            so size this from ``time_ps`` / system size.

    Returns:
        QCResult whose metadata carries ``trajectory_file`` / ``n_frames`` /
        ``md_seed`` / ``md_seeds`` / ``replica_frames`` /
        ``start_conf_index`` (per-replica starting embedding index).
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_dir = out
    md_dir.mkdir(parents=True, exist_ok=True)

    if not isinstance(md_seeds, int) or isinstance(md_seeds, bool) or md_seeds < 1:
        raise ValueError(f"md_seeds must be an integer ≥ 1, got {md_seeds!r}")
    if not isinstance(md_seed, int) or isinstance(md_seed, bool) or md_seed < 0:
        raise ValueError(f"md_seed must be a non-negative integer, got {md_seed!r}")
    n_replicas = md_seeds

    starts: list[Path] = []
    start_indices: list[int] = []
    if n_replicas == 1:
        starts = [Path(primary_xyz)]
        start_indices = [0]
    else:
        embeddings = enumerate_embeddings(input_source, n=n_replicas, seed_base=embed_seed_base)
        for i, block in enumerate(embeddings):
            replica_dir = md_dir / f"replica_{i:02d}"
            replica_dir.mkdir(parents=True, exist_ok=True)
            start_xyz = replica_dir / "molecule.xyz"
            start_xyz.write_text(block, encoding="utf-8")
            starts.append(start_xyz)
            start_indices.append(i)

    backend_kwargs: dict[str, Any] = {}
    if timeout is not None and int(timeout) > 0:
        backend_kwargs["timeout"] = int(timeout)
    backend = get_backend("molclus")(config or {}, **backend_kwargs)

    replica_frames: list[int] = []
    replica_dirs: list[str] = []
    for i, (start_xyz, start_index) in enumerate(zip(starts, start_indices)):
        replica_dir = md_dir / f"replica_{i:02d}"
        replica_dirs.append(str(replica_dir))
        logger.info(
            "MD replica %d/%d seed=%d start_conf_index=%d method=%s",
            i + 1,
            n_replicas,
            md_seed + i,
            start_index,
            md_method,
        )
        result = backend.run_md(
            start_xyz,
            md_method=md_method,
            temperature=temperature,
            time_ps=time_ps,
            dump_fs=dump_fs,
            step_fs=step_fs,
            hmass=hmass,
            shake=shake,
            nvt=nvt,
            seed=md_seed + i,
            solvent=solvent,
            solvent_model=solvent_model,
            charge=charge,
            multiplicity=multiplicity,
            output_dir=replica_dir,
        )
        if not result.success:
            return QCResult(
                success=False,
                error_message=(
                    f"MD replica {i + 1}/{n_replicas} (seed={md_seed + i}) failed: "
                    f"{result.error_message}"
                ),
                log_file=result.log_file,
            )
        replica_frames.append(int(result.metadata.get("n_frames", 0)))

    merged_traj = md_dir / "traj.xyz"
    n_total = _merge_trajectories(
        [md_dir / f"replica_{i:02d}" / "traj.xyz" for i in range(n_replicas)],
        merged_traj,
    )

    return QCResult(
        success=True,
        converged=True,
        output_file=merged_traj,
        metadata={
            "trajectory_file": str(merged_traj),
            "n_frames": n_total,
            "md_seed": md_seed,
            "md_seeds": n_replicas,
            "replica_frames": replica_frames,
            "start_conf_index": start_indices,
            "replica_dirs": replica_dirs,
        },
    )


def _merge_trajectories(trajectories: list[Path], output: Path) -> int:
    """Concatenate replica trajectory files, preserving frame titles.

    All replica trajectories must share the same atom count (enforced by
    their common molecular graph) and contain only well-formed frames;
    violations raise before anything is written, so a failed merge never
    leaves a partial ``output`` behind (which a resume-based workflow could
    mistake for a complete checkpoint).  Returns the merged frame count.
    """
    texts: list[str] = []
    expected_atoms: int | None = None
    total_frames = 0
    for traj in trajectories:
        if not traj.exists():
            raise FileNotFoundError(f"Trajectory missing: {traj}")
        text = traj.read_text(encoding="utf-8")
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            try:
                n_atoms = int(line)
            except ValueError:
                raise ValueError(
                    f"Malformed trajectory frame in {traj} at line {i + 1}: "
                    f"{line!r} (expected an atom count)"
                ) from None
            if n_atoms <= 0 or i + 2 + n_atoms > len(lines):
                raise ValueError(
                    f"Truncated trajectory frame in {traj} at line {i + 1} "
                    f"(declared {n_atoms} atoms, {len(lines) - i - 2} lines remain)"
                )
            if expected_atoms is None:
                expected_atoms = n_atoms
            elif n_atoms != expected_atoms:
                raise ValueError(
                    f"Atom count mismatch in {traj}: frame at line {i + 1} "
                    f"has {n_atoms} atoms, expected {expected_atoms}"
                )
            total_frames += 1
            i += 2 + n_atoms
        texts.append(text)

    with open(output, "w", encoding="utf-8") as out:
        for text in texts:
            out.write(text)
            if text and not text.endswith("\n"):
                out.write("\n")
    return total_frames


__all__ = ["run_md_replicas"]
