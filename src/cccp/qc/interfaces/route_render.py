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
* :class:`RouteKeyword` — one registry-governed enumerated parameter
  (``opt_level`` / ``scf_convergence`` / ``scf_strategy`` / ``grid`` /
  ``dispersion``). Resolved through
  :func:`cccp.qc.keyword_registry.resolve` (case-insensitive, fail-fast on
  unknown enum values), so ``normal``/``none`` no-ops are skipped, legacy
  grid aliases (``SG1``/``Fine``/``UltraFine``/``SuperFine``) canonicalize
  to ``DefGrid1/2/3`` with a migration warning, and the family ×
  implementation applicability rules (via ``resolve`` + the explicit
  :func:`cccp.qc.keyword_registry.is_applicable` emission gate) strip
  values that never apply (e.g. GFN x grid/dispersion). The raw token is
  never written to the ``!`` line.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from cccp.qc.keyword_registry import (
    IMPL_ORCA_DFT,
    is_applicable,
    method_family,
    resolve,
    resolve_implementation,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RouteKeyword",
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
    """One registry-governed enumerated route parameter.

    Attributes:
        domain: Enum domain name (``opt_level`` / ``scf_convergence`` /
            ``scf_strategy`` / ``grid`` / ``dispersion``).
        value: User input spelling (any case); ``None``/empty is an unset
            parameter and renders nothing.
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
    """

    domain: str
    value: str | None
    emit: bool = True
    suppress_tokens: frozenset[str] = field(default_factory=frozenset)
    register: bool = True


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
        # single token-emission point (T6 stripping hook).
        return []
    key = token.upper()
    if key in {str(item).upper() for item in keyword.suppress_tokens}:
        return []
    if key in seen:
        return []
    if keyword.register:
        seen.add(key)
    return [token]
