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


def gaussian_pcm_keyword(solvent: Optional[str], model: str = 'smd') -> str:
    if not solvent:
        return ""
    key = _normalize(solvent)
    name = SOLVENT_ALIASES.get(key, solvent)
    
    model_lower = model.lower()
    if model_lower == 'pcm':
        return f"SCRF=(PCM,Solvent={name})"
    elif model_lower == 'cpcm':
        return f"SCRF=(CPCM,Solvent={name})"
    else:  # smd (default)
        return f"SCRF=(SMD,Solvent={name})"


def orca_smd_solvent(solvent: Optional[str]) -> str:
    if not solvent:
        return ""
    key = _normalize(solvent)
    return SOLVENT_ALIASES.get(key, solvent)
