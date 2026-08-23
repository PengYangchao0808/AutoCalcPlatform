"""Thermochemistry viewer JSON from ``thermo.json`` (design doc §11.3).

Input keys mirror ``src/acp/workflows/simple.py::_write_thermo_json``;
projection is defensive — missing keys become None.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["build_thermo_report"]


def build_thermo_report(thermo_json: Path | dict) -> dict:
    """Project a ``thermo.json`` (path or parsed dict) onto the §11.3 viewer schema."""
    if isinstance(thermo_json, dict):
        data = thermo_json
    else:
        try:
            data = json.loads(Path(thermo_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("could not read thermo json %s: %s", thermo_json, exc)
            data = {}
    if not isinstance(data, dict):
        logger.debug("thermo json payload is not an object: %r", thermo_json)
        data = {}
    gibbs = data.get("total_gibbs_hartree", data.get("free_energy_hartree"))
    return {
        "scf_energy_hartree": data.get("sp_energy_hartree"),
        "zpe_included": data.get("thermal_correction_u_hartree") is not None,
        "enthalpy_hartree": data.get("total_enthalpy_hartree"),
        "gibbs_hartree": gibbs,
        "entropy": data.get("entropy"),
        "temperature_k": data.get("temperature_k"),
        "unit_note": "hartree unless noted",
    }
