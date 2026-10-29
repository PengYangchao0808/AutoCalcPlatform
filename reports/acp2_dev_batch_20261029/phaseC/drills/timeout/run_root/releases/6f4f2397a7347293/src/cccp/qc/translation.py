"""
Translation Layer — structured spec → ORCA route/block + CENSO template lines
=============================================================================

THE single-source render API for translating a structured level/spec into
ORCA ``!``-line control tokens, ``%geom`` block lines, and CENSO
advanced-field template lines.  Built entirely on the existing rendering
machinery — :mod:`cccp.qc.interfaces.route_render` (``render_route_line`` /
``RouteKeyword``) and :mod:`cccp.qc.keyword_registry` — and never
re-implements keyword tables or default vocabularies (those live in the
registry only).

Public surface (consumers: ACP batch effective-config summaries today;
``cccp.calculation`` task translation and Confsearch protocol wiring next):

* :class:`OrcaOptSpec` — structured optimization-control spec (one role).
* :func:`render_orca_opt` — spec → :class:`OrcaOptRender` in ONE resolution
  pass, so route tokens, ``%geom`` lines and the human-readable summary
  always describe the same normalized result (display == execution).
* :func:`render_opt_geom_lines` — the reusable ``%geom`` body renderer
  (Calc_Hess / Recalc_Hess / Trust / MaxIter / extras).  ``ORCAInterface``
  delegates its ``%geom`` block to this function, so the summary and the
  written input cannot drift.
* :func:`render_censo_template_lines` — CENSO advanced-field template lines
  (``!``-prefixed literal route extras) for the CENSO rcfile templates.

Boundary contract (F7): everything here is PURE input construction — no
I/O, no subprocess.  File writing and process calls stay in the interface
layer (``ORCAInterface._write_input`` / ``_write_nmr_input`` / ``_run_orca``).

Author: QCcalc Team
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from cccp.qc.interfaces.route_render import (
    orca_keyword_context,
    render_route_line,
)
from cccp.qc.keyword_registry import resolve

logger = logging.getLogger(__name__)

__all__ = [
    "OrcaOptRender",
    "OrcaOptSpec",
    "render_censo_template_lines",
    "render_opt_geom_lines",
    "render_orca_opt",
]

#: The bare ORCA ``Opt`` run-type keyword — what a normal-convergence
#: optimization receives when no level modifier applies.  Spelling matches
#: ``ORCAInterface._build_input_blocks``'s ``calc_type_map`` run-type token.
_RUN_TYPE_OPT = "Opt"


# ── Structured spec ────────────────────────────────────────────────────


@dataclass(frozen=True)
class OrcaOptSpec:
    """Structured optimization-control spec — the translation-layer input.

    Attributes:
        method: Effective method spelling; derives the keyword-registry
            context (family × implementation).  Unset falls back to the
            conventional-DFT context.
        opt_level: Optimization convergence level (``loose`` / ``normal`` /
            ``tight`` / ``verytight`` / …).  Unset or ``normal`` = ORCA's
            default convergence.
        scf_convergence: SCF convergence level (``loose`` / ``normal`` /
            ``tight`` / ``verytight``).
        scf_strategy: SCF convergence strategy (``normal`` / ``slowconv`` /
            ``soscf``).
        max_cycles: ``%geom`` MaxIter cap (emitted only when > 0 — the same
            gate the execution route uses).
        trust_radius: ``%geom`` Trust radius.
        initial_hessian: ``"calculate"`` renders ``Calc_Hess true``; any
            other value renders nothing (execution-identical rule).
        recalc_hess: Configured Hessian-recalc interval (positive int
            renders ``Recalc_Hess <n>``).  ``"auto"`` / ``None`` render
            nothing here — auto-resolution to a molecule-specific interval
            is the execution layer's policy
            (:mod:`cccp.qc.hessian_policy`) and the summary deliberately
            shows the configured value.
    """

    method: str | None = None
    opt_level: str | None = None
    scf_convergence: str | None = None
    scf_strategy: str | None = None
    max_cycles: int | None = None
    trust_radius: float | None = None
    initial_hessian: str | None = None
    recalc_hess: object = None


@dataclass(frozen=True)
class OrcaOptRender:
    """Rendered ORCA fragments for one :class:`OrcaOptSpec`.

    Attributes:
        route_tokens: Registry-resolved control tokens that join the ``!``
            line AFTER method / basis / run-type (level, SCF convergence,
            strategy) — e.g. ``("TightOpt", "TightSCF")``.  True no-ops
            (``normal``) and stripped values emit nothing.
        geom_lines: ``%geom`` body lines in execution order (Calc_Hess /
            Recalc_Hess / Trust / MaxIter / extras), each indented two
            spaces exactly as written to the input file.
        summary_tokens: Human-readable keyword list for display (the
            historical ``build_orca_summary`` order: level, SCF
            convergence, MaxIter, Trust, Calc_Hess, Recalc_Hess, strategy).
            Every token is derived from the same resolution pass as
            ``route_tokens`` / ``geom_lines`` — display == execution.
    """

    route_tokens: tuple[str, ...]
    geom_lines: tuple[str, ...]
    summary_tokens: tuple[str, ...]

    def route_line(self) -> str:
        """Render the bare control ``!`` line (``render_route_line`` join).

        Sites that need method / basis / run-type keywords build the full
        route via :func:`cccp.qc.interfaces.route_render.render_route_line`
        and pass these tokens as trailing segments.
        """
        return render_route_line(list(self.route_tokens))

    def geom_block(self) -> str:
        """Render the complete ``%geom … end`` block."""
        return "\n".join(["%geom", *self.geom_lines, "end"])


# ── %geom body renderer (shared with ORCAInterface) ────────────────────


def render_opt_geom_lines(
    *,
    initial_hessian: str | None = None,
    recalc_hess_interval: int = 0,
    trust_radius: float | None = None,
    max_cycles: int | None = None,
    extra_lines: Sequence[str] | None = None,
) -> list[str]:
    """Render the ``%geom`` body lines for one optimization.

    THE reusable %geom body renderer — consumed by both
    ``ORCAInterface._build_input_blocks`` (execution) and
    :func:`render_orca_opt` (summary/translation).  Emission rules mirror
    execution exactly:

    * ``initial_hessian == "calculate"`` → ``Calc_Hess true``;
    * ``recalc_hess_interval > 0`` → ``Recalc_Hess <interval>``;
    * ``trust_radius is not None`` → ``Trust <g-formatted>``;
    * ``max_cycles > 0`` → ``MaxIter <int>``;
    * *extra_lines* are appended last, falsy entries dropped.

    Args:
        initial_hessian: Hessian strategy spelling (only ``"calculate"``
            renders).
        recalc_hess_interval: Already-resolved recalc interval (0 = off).
        trust_radius: Trust radius (any float; formatted ``%g``).
        max_cycles: Geometry iteration cap.
        extra_lines: Raw extra lines appended inside the block.

    Returns:
        Body lines (two-space indented) in execution order — no ``%geom``
        / ``end`` wrappers.
    """
    lines: list[str] = []
    if initial_hessian == "calculate":
        lines.append("  Calc_Hess true")
    if recalc_hess_interval is not None and recalc_hess_interval > 0:
        lines.append(f"  Recalc_Hess {recalc_hess_interval}")
    if trust_radius is not None:
        lines.append(f"  Trust {float(trust_radius):g}")
    if max_cycles is not None and max_cycles > 0:
        lines.append(f"  MaxIter {int(max_cycles)}")
    if extra_lines:
        lines.extend(str(line) for line in extra_lines if line)
    return lines


# ── Spec → render (one resolution pass) ────────────────────────────────


def render_orca_opt(spec: OrcaOptSpec) -> OrcaOptRender:
    """Translate one :class:`OrcaOptSpec` into route / block / summary form.

    All three outputs derive from ONE keyword-registry resolution pass, so
    the human-readable summary and the executed route/block tokens always
    agree (display == execution).  Registry semantics apply verbatim:
    ``normal``/``none`` are true no-ops (no token), legacy aliases
    canonicalize, inapplicable values are stripped with a warning, and
    unknown enum values raise :class:`cccp.qc.keyword_registry.KeywordValueError`
    (fail-fast — never a silent passthrough).

    Display quirk preserved for byte-compatibility with the historical
    ``build_orca_summary``: when the level is a true no-op (unset /
    ``normal``) the summary shows the bare ``Opt`` run-type keyword — which
    is exactly what ORCA receives for a normal-convergence optimization.

    Args:
        spec: Structured optimization-control spec.

    Returns:
        The :class:`OrcaOptRender` bundle.

    Raises:
        KeywordValueError: Unknown enum value in *spec*.
    """
    family, implementation = orca_keyword_context(spec.method)

    def _token(domain: str, value: str | None) -> str | None:
        if _is_true_noop(value):
            # Unset / ``normal`` / ``none`` are engine defaults: emit
            # nothing and never trip the enum table (``none`` is a no-op
            # spelling, not a legal enum member).
            return None
        resolved, warning = resolve(
            domain, value, family=family, implementation=implementation
        )
        if warning:
            logger.warning("%s", warning)
        return resolved

    level_value = spec.opt_level
    level_token = _token("opt_level", level_value)
    if level_token is None and _is_true_noop(level_value):
        # Normal convergence = the bare run-type keyword (display only).
        level_display: str | None = _RUN_TYPE_OPT
    else:
        level_display = level_token
    scf_token = _token("scf_convergence", spec.scf_convergence)
    strategy_token = _token("scf_strategy", spec.scf_strategy)

    recalc_interval = 0
    if isinstance(spec.recalc_hess, int) and not isinstance(spec.recalc_hess, bool):
        recalc_interval = int(spec.recalc_hess)
    elif isinstance(spec.recalc_hess, bool) and spec.recalc_hess:
        recalc_interval = 1

    max_cycles = int(spec.max_cycles) if spec.max_cycles is not None else None

    geom_lines = render_opt_geom_lines(
        initial_hessian=spec.initial_hessian,
        recalc_hess_interval=recalc_interval,
        trust_radius=spec.trust_radius,
        max_cycles=max_cycles,
    )

    # Summary order is the historical display order (level, SCF
    # convergence, MaxIter, Trust, Calc_Hess, Recalc_Hess, strategy) —
    # same values as route_tokens/geom_lines, display projection only.
    summary: list[str] = []
    if level_display is not None:
        summary.append(level_display)
    if scf_token is not None:
        summary.append(scf_token)
    if max_cycles is not None and max_cycles > 0:
        summary.append(f"MaxIter {int(max_cycles)}")
    if spec.trust_radius is not None:
        summary.append(f"Trust {float(spec.trust_radius):g}")
    if spec.initial_hessian == "calculate":
        summary.append("Calc_Hess")
    if recalc_interval > 0:
        summary.append(f"Recalc_Hess {recalc_interval}")
    if strategy_token is not None:
        summary.append(strategy_token)

    route_tokens = tuple(
        token for token in (level_token, scf_token, strategy_token) if token is not None
    )
    return OrcaOptRender(
        route_tokens=route_tokens,
        geom_lines=tuple(geom_lines),
        summary_tokens=tuple(summary),
    )


def _is_true_noop(value: str | None) -> bool:
    """True when *value* is an unset/``normal``/``none`` engine default."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return True
    return str(value).strip().lower() in {"normal", "none"}


# ── CENSO template lines ──────────────────────────────────────────────


def render_censo_template_lines(extras: Sequence[str]) -> list[str]:
    """Render CENSO advanced-field template lines from route extras.

    The template is a single ``!``-prefixed simple-input line carrying the
    literal route extras (the historical ``["! " + " ".join(extras)]``
    assembly, now funneled through
    :func:`cccp.qc.interfaces.route_render.render_route_line`).

    Args:
        extras: Literal route-extra keywords (free-form; emitted verbatim).

    Returns:
        ``["! a b …"]`` for non-empty *extras*, else ``[]`` (no empty
        template line is ever emitted).
    """
    if not extras:
        return []
    return [render_route_line([str(item) for item in extras])]
