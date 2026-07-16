import re
from typing import Dict, Optional

SOLVENT_ALIASES: Dict[str, str] = {
    "acetone": "Acetone",
    "water": "Water",
    "dmso": "DimethylSulfoxide",
    "acetonitrile": "Acetonitrile",
    "meacn": "Acetonitrile",
    "me cn": "Acetonitrile",
    "dichloromethane": "Dichloromethane",
    "dcm": "Dichloromethane",
    "thf": "TetraHydroFuran",
    "methanol": "Methanol",
    "ethanol": "Ethanol",
    "toluene": "Toluene"
}


def _normalize(solvent: Optional[str]) -> str:
    if not solvent:
        return ""
    return re.sub(r"\s+", "", solvent).lower()


def xtb_solvent(solvent: Optional[str]) -> str:
    return _normalize(solvent)


def orca_smd_solvent(solvent: Optional[str]) -> str:
    if not solvent:
        return ""
    key = _normalize(solvent)
    return SOLVENT_ALIASES.get(key, solvent)
