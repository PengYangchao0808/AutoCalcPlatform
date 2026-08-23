"""CREST ensemble XYZ parsing for design doc §11.

``cccp.utils.file_io.read_xyz_multiframe`` drops frame title lines, and CREST
stores the conformer energy in the title (bare-number convention), so this
parser walks the frames manually.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["CrestEnsemble", "parse_crest_ensemble"]

_TITLE_ENERGY_RE = re.compile(r"[-+]?\d+\.?\d*(?:[eE][+-]?\d+)?")


@dataclass(frozen=True)
class CrestEnsemble:
    """Energies + titles of a CREST multi-frame conformer ensemble XYZ."""

    n_conformers: int = 0
    energies: list[float] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)


def parse_crest_ensemble(path: Path) -> CrestEnsemble:
    """Parse a multi-frame XYZ whose titles carry energies (CREST convention).

    The first float token in each title line is taken as the conformer energy
    (hartree); titles without a parseable float contribute ``0.0``.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        logger.debug("could not read CREST ensemble %s: %s", path, exc)
        return CrestEnsemble()
    titles: list[str] = []
    energies: list[float] = []
    offset = 0
    while offset < len(lines):
        try:
            n_atoms = int(lines[offset].strip())
        except (ValueError, IndexError):
            offset += 1
            continue
        if n_atoms <= 0:
            break
        if offset + 2 + n_atoms > len(lines):
            logger.debug("trailing incomplete ensemble frame at line %d in %s", offset, path)
            break
        title = lines[offset + 1].strip()
        match = _TITLE_ENERGY_RE.search(title)
        if match:
            try:
                energies.append(float(match.group(0)))
            except ValueError:
                energies.append(0.0)
        else:
            logger.debug("no energy token in ensemble frame title %r (%s)", title, path)
            energies.append(0.0)
        titles.append(title)
        offset += 2 + n_atoms
    return CrestEnsemble(n_conformers=len(titles), energies=energies, titles=titles)
