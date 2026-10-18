"""Pure input classifiers (retired-engine closure, T24).

Verbatim relocation of ``acp.workflows.ensemble`` input-shape classifiers into
the CCCP analysis domain.  The historical ACP path re-exports the same objects.
"""

from __future__ import annotations

from pathlib import Path

from cccp.utils.file_io import read_xyz_multiframe


def _is_multiframe_xyz(path: Path) -> bool:
    if not path.exists() or path.suffix.lower() not in (".xyz",):
        return False
    try:
        coords, _ = read_xyz_multiframe(path)
        n_atom_lines = coords.shape[0]
        if n_atom_lines == 0:
            return False
        with open(path) as f:
            first_line = f.readline().strip()
        try:
            atoms_per_frame = int(first_line)
        except ValueError:
            return False
        if atoms_per_frame <= 0:
            return False
        return n_atom_lines > atoms_per_frame
    except (OSError, ValueError, RuntimeError):
        return False


def _is_file_input(input_source: str) -> bool:
    try:
        path = Path(input_source)
        return path.exists() and path.is_file()
    except OSError:
        return False

__all__ = [
    "_is_file_input",
    "_is_multiframe_xyz",
]
