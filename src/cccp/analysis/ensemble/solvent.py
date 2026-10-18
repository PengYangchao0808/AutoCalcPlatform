"""Solvent / CREST-ewin resolution (retired-engine closure, T24).

Verbatim relocation of the pure ``acp.workflows.energy_shared`` solvent science
into the CCCP analysis domain.  The historical ACP path re-exports the same
objects.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from cccp.utils.solvent_map import LEGAL_SOLVENT_MODELS, SolventValueError

logger = logging.getLogger(__name__)


def resolve_solvent_config(
    cfg: dict[str, Any],
    user_solvent: str | None,
) -> tuple[str | None, str]:
    censo_solvent = (
        user_solvent if user_solvent is not None else cfg.get("censo", {}).get("solvent")
    )
    preopt_model = (
        cfg.get("theory", {}).get("preoptimization", {}).get("solvent_model") or ""
    ).lower()
    censo_model = (cfg.get("censo", {}).get("solvent_model") or "").lower()

    if preopt_model and preopt_model != "none":
        solvent_model = preopt_model
    elif censo_model and censo_model != "none":
        solvent_model = censo_model
    else:
        solvent_model = "none"

    return censo_solvent, solvent_model


def resolve_crest_ewin(
    cfg: dict[str, Any],
    ewin: float | None,
) -> float:
    """Resolve the CREST energy window: explicit arg > censo.ewin > 6.0."""
    if ewin is not None and ewin > 0:
        return float(ewin)
    raw = cfg.get("censo", {}).get("ewin", 6.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 6.0
    return value if value > 0 else 6.0


SAMPLING_SOLVENT_CONFIG_KEY: Final = "sampling_solvent_model"


SAMPLING_DEFAULT_SOLVENT_MODEL: Final = "alpb"


@dataclass(frozen=True)
class SamplingSolventReceipt:
    """Effective-parameters receipt for the sampling (CREST/xTB) segment.

    ``defaulted`` is ``True`` ONLY when the dedicated key was absent and a
    solvent name was present, so ACP chose ALPB rather than the user. An
    explicit ``none``/``alpb``/``gbsa`` is never ``defaulted`` — a recorded
    default must never masquerade as a user statement.
    """

    solvent: str | None
    solvent_model: str
    defaulted: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "solvent": self.solvent,
            "solvent_model": self.solvent_model,
            "defaulted": self.defaulted,
        }


def resolve_sampling_solvent(
    cfg: Mapping[str, Any],
    solvent: str | None,
) -> SamplingSolventReceipt:
    """Resolve the sampling (CREST/xTB) solvent model for one candidate run.

    Reads ONLY the dedicated :data:`SAMPLING_SOLVENT_CONFIG_KEY` key — the
    legacy ``theory.preoptimization.solvent_model`` / ``censo.solvent_model``
    keys keep their CENSO/DFT semantics and are deliberately not consulted
    here (todo 8). The model vocabulary is validated against the single source
    :data:`cccp.utils.solvent_map.LEGAL_SOLVENT_MODELS`, so a CREST/xTB
    sampling stage can only ever receive a legal model.

    Three-state semantics:

    * key ABSENT + solvent name present → ``alpb`` (``defaulted=True``);
    * key ABSENT + no solvent → ``none`` (gas phase, no flags emitted);
    * key explicitly ``none`` → gas phase (``defaulted=False``);
    * key explicitly ``alpb``/``gbsa`` → used as given.

    Moved from ``workflows/nmr.py`` (T08b) so the retired Confsearch engines
    (ensemble/energy/xtbmd_censo_energy) and the NMR workflow share ONE rule;
    ``nmr.py`` re-exports it under its historical private names.

    Raises:
        SolventValueError: the key carries a value outside
            :data:`LEGAL_SOLVENT_MODELS`.
    """
    nmr_section = cfg.get("nmr")
    raw = nmr_section.get(SAMPLING_SOLVENT_CONFIG_KEY) if isinstance(nmr_section, Mapping) else None
    name = solvent if solvent else None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        # Absence (or an empty/whitespace value) is the signal — never read as
        # an explicit statement. A named solvent defaults to ALPB; otherwise
        # the sampling segment runs gas phase.
        if name is None:
            return SamplingSolventReceipt(solvent=None, solvent_model="none", defaulted=False)
        return SamplingSolventReceipt(
            solvent=name, solvent_model=SAMPLING_DEFAULT_SOLVENT_MODEL, defaulted=True
        )
    model = str(raw).strip().lower()
    if model not in LEGAL_SOLVENT_MODELS:
        raise SolventValueError(
            f"Unknown xTB solvent model {raw!r} for nmr.{SAMPLING_SOLVENT_CONFIG_KEY}; "
            f"legal solvent models: {', '.join(sorted(LEGAL_SOLVENT_MODELS))}"
        )
    return SamplingSolventReceipt(solvent=name, solvent_model=model, defaulted=False)


@dataclass(frozen=True)
class StageSolventModels:
    """Effective solvent models of one run, split by science stage.

    ``sampling`` is the CREST/xTB segment (legal models only: ``alpb`` /
    ``gbsa`` / ``none``); ``dft_model`` is the CENSO/ORCA DFT segment where
    the historical ``none → smd`` fallback is legal. The solvent *name*
    (``solvent``) is shared — only the model is stage-split.
    """

    solvent: str | None
    dft_model: str
    sampling: SamplingSolventReceipt


def resolve_stage_solvent_models(
    cfg: Mapping[str, Any],
    solvent: str | None,
    *,
    dft_model_override: str | None = None,
) -> StageSolventModels:
    """Split one run's solvent into sampling (CREST/xTB) and DFT models.

    The DFT side keeps the legacy contract (``resolve_solvent_config``
    precedence, then *dft_model_override* from UI wizard levels, then the
    historical ``none → smd`` fallback when a solvent name is present — SMD
    is legal for CENSO/ORCA). The sampling side resolves through the
    dedicated-key rule of :func:`resolve_sampling_solvent`, so a CREST/xTB
    stage NEVER receives SMD/CPCM: absence defaults to ALPB (receipt
    ``defaulted=True``) and an unknown dedicated-key value raises
    ``SolventValueError`` (strict failure).
    """
    name, legacy_model = resolve_solvent_config(dict(cfg), solvent)
    if name and dft_model_override:
        legacy_model = dft_model_override
    dft_model = legacy_model or "none"
    if name and dft_model == "none":
        logger.info(
            "Solvent %r has no configured DFT model — defaulting to SMD for the "
            "CENSO/DFT stage (the CREST/xTB sampling stage resolves separately).",
            name,
        )
        dft_model = "smd"
    return StageSolventModels(
        solvent=name,
        dft_model=dft_model,
        sampling=resolve_sampling_solvent(cfg, solvent),
    )

__all__ = [
    "SAMPLING_DEFAULT_SOLVENT_MODEL",
    "SAMPLING_SOLVENT_CONFIG_KEY",
    "SamplingSolventReceipt",
    "StageSolventModels",
    "resolve_crest_ewin",
    "resolve_sampling_solvent",
    "resolve_solvent_config",
    "resolve_stage_solvent_models",
]
