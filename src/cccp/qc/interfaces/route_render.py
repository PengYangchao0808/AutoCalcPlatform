"""
ORCA Route Renderer
===================

Single renderer for ORCA ``!``-simple-input route lines. Every assembly
point (``ORCAInterface._build_input_blocks``, ``casscf``,
``_write_nmr_input``, ``orca_ts.ts_opt_route``, ``orca_ts.irc_route``)
funnels through :func:`render_route_line`; sites declare ordered segments
and the renderer owns all registry-governed token handling.

Segment kinds:

* ``str`` — free-form literal (method/basis/run-type keywords/``route_extras``
  passthrough). Emitted verbatim and registered in the dedup set, but never
  deduplicated or case-folded themselves (free-form passthrough semantics
  are unchanged).
* :class:`RouteKeyword` — one registry-governed parameter (enum domains
  ``opt_level`` / ``scf_convergence`` / ``scf_strategy`` / ``grid`` /
  ``dispersion``; free-form domains ``basis`` / ``ri`` / ``aux``). Enum
  values resolve through :func:`cccp.qc.keyword_registry.resolve`
  (case-insensitive, fail-fast on unknown enum values), so ``normal``/
  ``none`` no-ops are skipped and legacy grid aliases (``SG1``/``Fine``/
  ``UltraFine``/``SuperFine``) canonicalize to ``DefGrid1/2/3`` with a
  migration warning. Free-form values pass through verbatim (never
  case-folded). Either way, the family × implementation applicability
  rules (via ``resolve`` + the explicit
  :func:`cccp.qc.keyword_registry.is_applicable` emission gate) strip
  values that never apply with a warning — for the GFN family that is the
  DFT-only parameter set ``basis`` / ``dispersion`` / ``grid`` / ``ri`` /
  ``aux`` (T6: stripped at EVERY ``!``-line entry point, including
  ``ts_opt_route`` / ``irc_route`` which bypass ``_build_input_blocks``).
  The raw token is never written to the ``!`` line.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from cccp.qc.keyword_registry import (
    IMPL_ORCA_DFT,
    KeywordValueError,
    is_applicable,
    method_family,
    resolve,
    resolve_implementation,
)
from cccp.utils.solvent_map import orca_smd_solvent

logger = logging.getLogger(__name__)

__all__ = [
    "RouteKeyword",
    "orca_gfn_solvent_token",
    "orca_keyword_context",
    "render_route_line",
]


def orca_keyword_context(method: str | None) -> tuple[str, str]:
    """Return the registry ``(family, implementation)`` context for ORCA input builds.

    Derived per input build from the effective method via
    :func:`cccp.qc.keyword_registry.method_family` +
    :func:`cccp.qc.keyword_registry.resolve_implementation` (``engine="orca"``).

    Arbitrary/user-supplied DFT functionals are NOT in the registry's finite
    method table (family ``"unknown"``) and MUST keep working — they run as
    conventional ORCA DFT, so fall back to
    ``("conventional_dft", IMPL_ORCA_DFT)`` instead of letting
    ``resolve_implementation`` reject them. Only recognized GFN/3c methods
    get family-specific behavior.

    Args:
        method: Effective method spelling (case/whitespace-insensitive).

    Returns:
        ``(family, implementation)`` suitable for
        :func:`cccp.qc.keyword_registry.resolve`.
    """
    if not method:
        return "conventional_dft", IMPL_ORCA_DFT
    family = method_family(method)
    if family == "unknown":
        return "conventional_dft", IMPL_ORCA_DFT
    return family, resolve_implementation(method, engine="orca")


@dataclass(frozen=True)
class RouteKeyword:
    """One registry-governed route parameter.

    Attributes:
        domain: Domain name — enum domains (``opt_level`` /
            ``scf_convergence`` / ``scf_strategy`` / ``grid`` /
            ``dispersion``) or free-form domains (``basis`` / ``ri`` /
            ``aux``).
        value: User input spelling (any case for enums, verbatim for
            free-form); ``None``/empty is an unset parameter and renders
            nothing.
        emit: Emission gate evaluated AFTER resolution — validation always
            runs first so a bogus value cannot hide behind a suppressed
            emission (``False`` reproduces e.g. the ``ri_support == "user"``
            dispersion gate).
        suppress_tokens: Resolved canonical tokens that must not be emitted
            for this run (e.g. the DLPNO-CCSD(T) built-in ``TightSCF``).
            Suppressed tokens are neither emitted nor registered for dedup.
        register: Whether the emitted token joins the dedup set. Default
            ``True``; ``False`` reproduces the historical ``scf_strategy``
            non-registration quirk.
        prefix: Optional free-form keyword word emitted immediately before
            the resolved token — ORCA spells some parameters as
            ``<keyword> <value>`` (e.g. ``aux def2/J``). The word rides with
            the governed value, so an applicability strip (GFN x aux)
            removes keyword and value together and never leaves a dangling
            token on the route line.
    """

    domain: str
    value: str | None
    emit: bool = True
    suppress_tokens: frozenset[str] = field(default_factory=frozenset)
    register: bool = True
    prefix: str | None = None


def render_route_line(
    segments: Sequence[str | RouteKeyword],
    *,
    method: str | None = None,
    context: tuple[str, str] | None = None,
    seen: set[str] | None = None,
) -> str:
    """Assemble one ``!``-simple-input route line from ordered segments.

    The ``!`` prefix and single-space join reproduce the historical
    hand-built f-strings byte-for-byte for legal values — including empty
    literal segments, which keep their spacing slot (e.g. an empty basis in
    the basis-inline build renders ``! B3LYP  Opt``).

    Governed tokens are appended in segment order and case-insensitively
    deduplicated against *seen* (the ``_extras_upper`` behavior). Literal
    segments register their upper-case form in *seen* but are themselves
    emitted verbatim — free-form ``route_extras`` passthrough semantics are
    unchanged.

    Args:
        segments: Ordered route segments (literals and/or governed
            parameters).
        method: Effective method; used to derive the registry context when
            *context* is not given.
        context: Explicit ``(family, implementation)`` override (sites that
            already derived it once pass it in).
        seen: Shared dedup set (upper-case tokens). Mutated in place so a
            caller can pre-seed it or share it across calls.

    Returns:
        The complete ``! ...`` route line (no trailing newline).

    Raises:
        KeywordValueError: Unknown enum value in a :class:`RouteKeyword`
            (fail-fast — nothing is silently passed through).
    """
    if context is None:
        context = orca_keyword_context(method)
    family, implementation = context
    tokens: list[str] = []
    if seen is None:
        seen = set()
    for segment in segments:
        if isinstance(segment, RouteKeyword):
            tokens.extend(
                _render_governed(
                    segment,
                    family=family,
                    implementation=implementation,
                    seen=seen,
                )
            )
            continue
        literal = str(segment)
        tokens.append(literal)
        seen.add(literal.upper())
    return "! " + " ".join(tokens)


def _render_governed(
    keyword: RouteKeyword,
    *,
    family: str,
    implementation: str,
    seen: set[str],
) -> list[str]:
    """Resolve + emit one governed parameter through the keyword registry."""
    token, warning = resolve(
        keyword.domain,
        keyword.value,
        family=family,
        implementation=implementation,
    )
    if warning:
        logger.warning("%s", warning)
    if token is None:
        # True no-op (``normal`` / ``none``) or stripped by applicability.
        return []
    if not keyword.emit:
        return []
    if not is_applicable(keyword.domain, family=family, implementation=implementation):
        # ``resolve`` already strips inapplicable values; this explicit gate
        # keeps the family x implementation emission policy visible at the
        # single token-emission point (T6 stripping hook: GFN-family basis /
        # ri / aux / grid / dispersion never reach the route line here).
        return []
    key = token.upper()
    if key in {str(item).upper() for item in keyword.suppress_tokens}:
        return []
    if key in seen:
        return []
    if keyword.register:
        seen.add(key)
    if keyword.prefix:
        return [keyword.prefix, token]
    return [token]


# ── GFN solvent rule (T7: ALPB-only under ORCA) ─────────────────────────────


def orca_gfn_solvent_token(
    method: str | None,
    solvent: str | None,
    solvent_model: str | None,
) -> str | None:
    """Return the ORCA GFN-family solvent route token (``ALPB(<name>)``) or None.

    THE single GFN solvent rule under the ORCA engine (external xTB and
    native alike), used by every ``!``-line site (``_build_input_blocks``,
    ``_orca_scan_route_settings``, ``_write_nmr_input``, ``ts_opt_route``,
    ``irc_route``):

    * model ``ALPB`` (any case) + a solvent -> ``ALPB(<ORCA solvent name>)``
      on the ``!`` line (the name is mapped via
      :func:`cccp.utils.solvent_map.orca_smd_solvent`);
    * model ``none`` or unset -> emit NOTHING (a true no-op — no token, no
      warning, and never a silent default to ALPB);
    * any other model (``GBSA`` / ``CPCM`` / ``SMD`` / unknown) ->
      :class:`cccp.qc.keyword_registry.KeywordValueError` raised by the
      registry ``resolve`` policy gate. The user model is NEVER rewritten.

    **Capability vs dependency vs policy (three-way distinction — recorded
    per T22; probe results update capability/dependency records only and
    NEVER auto-rewrite policy):**

    * *capability* — ORCA 6.1's external-xTB interface implements ALPB
      (T22 case 3: ``! GFN2-xTB ALPB(water)`` = valid completion, otool
      ``--alpb WATER``, Gsolv present). GBSA is not an ORCA keyword at all
      (T22 case 4: ``! GFN2-xTB GBSA(water)`` = ``UNRECOGNIZED OR
      DUPLICATED KEYWORD(S) IN SIMPLE INPUT LINE: GBSA(WATER)``, rc=4).
    * *dependency* — none; no optional package gates this rule.
    * *policy* — PLATFORM POLICY (decision Q1): ORCA GFN solvent models are
      restricted to ``{none, ALPB}``. This is a platform choice, not a claim
      about software capability — it would hold even if ORCA gained GBSA
      support tomorrow. The standalone xTB binary keeps
      ``{none, ALPB, GBSA}`` (``resolve_xtb_solvent``, T9).

    Args:
        method: Effective method spelling (GFN family only).
        solvent: Effective solvent spelling (any case/alias; mapped to the
            ORCA name at emission). ``None``/empty suppresses the token.
        solvent_model: Effective solvent model; ``None``/empty is treated as
            ``none`` (emit nothing) — an unset model must never silently
            gain solvation.

    Returns:
        The ``ALPB(...)`` route token, or ``None`` when nothing is emitted.

    Raises:
        ValueError: ``method`` is not GFN-family (misuse — this helper is
            GFN-only; DFT solvent emission is site-owned and unchanged).
        cccp.qc.keyword_registry.KeywordValueError: Policy violation via the
            registry ``resolve`` (e.g. ``GBSA`` under ORCA), with the
            ``PLATFORM POLICY`` message naming the legal models.
    """
    family, implementation = orca_keyword_context(method)
    if family not in ("gfn", "gfnff"):
        raise ValueError(
            f"orca_gfn_solvent_token is GFN-family only (method={method!r}, "
            f"family={family!r}); DFT solvent emission is site-owned"
        )
    canonical, warning = resolve(
        "solvent_model",
        solvent_model,
        family=family,
        implementation=implementation,
    )
    if warning:
        logger.warning("%s", warning)
    if canonical is None:
        return None
    folded = str(canonical).strip().lower()
    if folded == "none":
        return None
    if folded != "alpb":
        # Unreachable while the policy set is exactly {none, ALPB}; guards
        # against silently rewriting a future allowed model to ALPB.
        raise KeywordValueError(
            f"solvent_model {canonical!r} has no ORCA GFN route token "
            f"(family={family!r}, implementation={implementation!r}); "
            "policy allows exactly {none, ALPB} — refusing to rewrite the model"
        )
    if not solvent:
        return None
    return f"ALPB({orca_smd_solvent(solvent)})"
