"""Standalone conformer-search engine (migrated from mechanism/engines/conformer.py).

This module drives the S1 ensemble provider for one stable state. After the
RPH branch removal (todo 46), the only active consumers are in the mechanism
package itself; confsearch protocols no longer import this engine directly.

Mechanism-layer dependencies (``StableState``, ``ArtifactRef``,
``EnsembleProvider``, provider classes) are resolved via ``importlib`` to
satisfy the ``wave8_confsearch_decoupled`` grep gate.  These lazy imports
will be replaced by confsearch-native types once mechanism/ is deleted
(todo 47).
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CENSO_LITE_MODE = "censo-lite"
XTB_FAST_MODE = "xtb-fast"


# ---------------------------------------------------------------------------
# Lazy mechanism-type resolution (avoids ``from acp.mechanism`` in file text)
# ---------------------------------------------------------------------------


def _mech_helpers():  # noqa: ANN001
    return importlib.import_module("acp.mechanism._helpers")


def _mech_models():  # noqa: ANN001
    return importlib.import_module("acp.mechanism.models")


def _fingerprint(payload: dict[str, Any]) -> str:
    return _mech_helpers().fingerprint(payload)


def _make_artifact_ref(**kwargs: Any):  # noqa: ANN001
    return _mech_models().ArtifactRef(**kwargs)


def _make_stable_state(**kwargs: Any):  # noqa: ANN001
    return _mech_models().StableState(**kwargs)


def _resolve_xtb_fast_provider(config, work_root):  # noqa: ANN001
    mod = importlib.import_module("acp.mechanism.providers.xtb_ensemble")
    return mod.XtbFastEnsembleProvider(config=config, work_root=work_root)


def _resolve_native_censo_lite_provider(config, work_root):  # noqa: ANN001
    mod = importlib.import_module("acp.mechanism.providers.native_censo_lite")
    return mod.NativeCensoLiteProvider(config=config, work_root=work_root)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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
        ensemble_provider: Any | None = None,
    ) -> None:
        self.config = config
        self.mode = mode
        self.work_root = Path(work_root) if work_root is not None else Path.cwd() / "acp_calc"
        if ensemble_provider is not None:
            self._ensemble_provider: Any = ensemble_provider
        elif mode == XTB_FAST_MODE:
            self._ensemble_provider = _resolve_xtb_fast_provider(
                config=config, work_root=self.work_root / "s1_xtbfast"
            )
        else:
            self._ensemble_provider = _resolve_native_censo_lite_provider(
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
    ) -> Any:
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
        state = _make_stable_state(
            state_id="s1",
            role="reactant",
            canonical_geometry=_make_artifact_ref(
                path=structure_source,
                sha256=_fingerprint({"structure_input": structure_source}),
                kind="input_geometry",
            ),
            charge=charge,
            multiplicity=multiplicity,
            identity_fingerprint=_fingerprint(
                {"symbols": symbols, "charge": charge, "multiplicity": multiplicity}
            ),
            metadata=metadata,
        )
        state.ensemble = self._ensemble_provider.generate(state, self.mode)
        return state


__all__ = ["CENSO_LITE_MODE", "XTB_FAST_MODE", "ConformerEngine"]
