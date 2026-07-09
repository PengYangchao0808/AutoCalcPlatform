import logging

logger = logging.getLogger(__name__)


class KeywordTranslator:
    """
    Translates generic computational chemistry terms into software-specific keywords.
    Current support: Gaussian 16.
    """

    @staticmethod
    def to_gaussian_basis(basis_raw: str) -> str:
        """
        Gaussian requires 'Def2SVP' (no hyphen) for def2 family.
        Generic 'def2-SVP' -> 'Def2SVP'.
        """
        if not basis_raw: return "Def2SVP"
        if "def2" in basis_raw.lower():
            return basis_raw.replace("-", "").replace("_", "")
        return basis_raw

    @staticmethod
    def to_gaussian_dispersion(dispersion_raw: str) -> str:
        """
        Maps generic 'GD3BJ' -> Gaussian 'em=GD3BJ' (Concise format).
        """
        if not dispersion_raw: return ""
        d = dispersion_raw.upper()
        if d in ["GD3BJ", "D3BJ"]: return "em=GD3BJ"
        if d in ["GD3", "D3"]: return "em=GD3"
        return ""

    @staticmethod
    def to_gaussian_solvent(solvent_raw: str, model: str = 'smd') -> str:
        from conformer_search.utils.solvent_map import gaussian_pcm_keyword
        return gaussian_pcm_keyword(solvent_raw, model)
