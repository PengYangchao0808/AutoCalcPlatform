"""Standalone conformer-search engine for the ``mech-conf`` module (M1)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .._helpers import fingerprint
from ..models import ArtifactRef, StableState
from ..providers.contracts import EnsembleProvider

logger = logging.getLogger(__name__)

CENSO_LITE_MODE = "censo-lite"
XTB_FAST_MODE = "xtb-fast"


def _read_input_symbols(structure_source: str) -> tuple[list[str], list[list[float]] | None]:
    """Best-effort symbol/coordinate extraction for XYZ inputs; SMILES skip."""
    path = Path(structure_source).expanduser()
    if not path.exists() or path.suffix.lower() != ".xyz":
        return [], None
    from cccp.utils.file_io import read_xyz

    coordinates, symbols = read_xyz(path)
    return [str(symbol) for symbol in symbols], coordinates.tolist()


class ConformerEngine:
    """Drive the S1 ensemble provider for one stable state."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        work_root: Path | None = None,
        mode: str = CENSO_LITE_MODE,
        ensemble_provider: EnsembleProvider | None = None,
    ) -> None:
        self.config = config
        self.mode = mode
        self.work_root = Path(work_root) if work_root is not None else Path.cwd() / "acp_calc"
        if ensemble_provider is not None:
            self._ensemble_provider: EnsembleProvider = ensemble_provider
        elif mode == XTB_FAST_MODE:
            from ..providers.xtb_ensemble import XtbFastEnsembleProvider

            self._ensemble_provider = XtbFastEnsembleProvider(
                config=config, work_root=self.work_root / "s1_xtbfast"
            )
        else:
            from ..providers.native_censo_lite import NativeCensoLiteProvider

            self._ensemble_provider = NativeCensoLiteProvider(
                config=config, work_root=self.work_root / "s1"
            )

    @property
    def provider_name(self) -> str:
        if self.mode == XTB_FAST_MODE:
            return "acp-native-xtb-fast"
        return "acp-native-censo-lite"

    def run(
        self,
        structure_source: str,
        *,
        charge: int = 0,
        multiplicity: int = 1,
        name: str | None = None,
    ) -> StableState:
        """Build a minimal source StableState and generate its ensemble."""
        symbols, coordinates = _read_input_symbols(structure_source)
        metadata: dict[str, Any] = {
            "smiles": structure_source,
            "input_smiles": structure_source,
            "input": structure_source,
            "source_input": structure_source,
            "structure_input": structure_source,
            "charge": charge,
            "multiplicity": multiplicity,
        }
        if symbols:
            metadata["symbols"] = symbols
        if coordinates is not None:
            metadata["coordinates"] = coordinates
        if name:
            metadata["name"] = name
        state = StableState(
            state_id="s1",
            role="reactant",
            canonical_geometry=ArtifactRef(
                path=structure_source,
                sha256=fingerprint({"structure_input": structure_source}),
                kind="input_geometry",
            ),
            charge=charge,
            multiplicity=multiplicity,
            identity_fingerprint=fingerprint(
                {"symbols": symbols, "charge": charge, "multiplicity": multiplicity}
            ),
            metadata=metadata,
        )
        state.ensemble = self._ensemble_provider.generate(state, self.mode)
        return state


__all__ = ["CENSO_LITE_MODE", "XTB_FAST_MODE", "ConformerEngine"]
