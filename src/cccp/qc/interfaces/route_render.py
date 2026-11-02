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
from typing import Any

from cccp.qc.keyword_registry import (
    IMPL_ORCA_DFT,
    KeywordValueError,
    is_applicable,
    method_family,
    resolve,
    resolve_implementation,
)
from cccp.qc.method_meta import (
    classify_basis_token,
    is_orbital_basis,
    orbital_basis_tokens,
)
from cccp.utils.solvent_map import orca_smd_solvent

logger = logging.getLogger(__name__)

__all__ = [
    "AuxiliaryBasisError",
    "RouteBasisConflictError",
    "RouteBasisResolution",
    "RouteKeyword",
    "classify_route_token",
    "detect_basis_conflicts",
    "orca_gfn_solvent_token",
    "orca_keyword_context",
    "render_route_line",
    "resolve_route_orbital_basis",
]

#: The single authority for route-token classification lives in
#: :mod:`cccp.qc.method_meta`; this alias is the renderer/preflight entry name.
classify_route_token = classify_basis_token


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
        RouteBasisConflictError: The rendered line would carry two DISTINCT
            orbital bases (ORCA would silently keep the last one).
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
    _assert_single_orbital_basis(tokens)
    return "! " + " ".join(tokens)


def _assert_single_orbital_basis(tokens: Sequence[str]) -> str | None:
    """Enforce the ONE-orbital-basis invariant on a rendered route line.

    Distinct orbital-basis names on one ``!`` line mean ORCA would silently
    override every token but the last.  Identical repeats are harmless (the
    basis renders twice with the same value) and are left to the call sites
    that collapse them.  Every assembly point funnels through
    :func:`render_route_line`, so this is the final guard for casscf / NMR /
    TS / IRC routes that bypass ``_build_input_blocks``.

    Returns:
        The single distinct orbital basis (or ``None``).
    """
    found: dict[str, str] = {}
    for token in tokens:
        for word in str(token).split():
            if is_orbital_basis(word):
                found.setdefault(word.lower(), word)
    if len(found) > 1:
        raise RouteBasisConflictError(list(found.values()))
    return next(iter(found.values()), None)


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


# ── single-orbital-basis invariant (round-7 #3) ─────────────────────────────
#
# ORCA accepts exactly one orbital basis on the ``!`` line.  When two appear
# (``! wB97X-D4 def2-TZVP ma-def2-SVP Opt``) it silently keeps the LAST one —
# a normal-terminated job with the wrong wavefunction.  The renderer therefore
# refuses any input that would place two *distinct* orbital bases on the route
# line; auxiliary bases must be ``/J``-``/C``-suffixed tokens rendered in the
# ``%basis`` block (or an ``AutoAux`` placeholder), never a bare second basis.


class RouteBasisConflictError(ValueError):
    """Two distinct orbital bases would be emitted on ONE ORCA ``!`` line.

    Typed ``rule="basis_conflict"`` refusal: the renderer never picks a winner
    and never silently rewrites the user's basis.  ``params`` is shaped like
    the application error envelope so the submit/preflight layer can forward
    it verbatim.
    """

    rule: str = "basis_conflict"

    def __init__(
        self,
        tokens: Sequence[str],
        *,
        sources: dict[str, str] | None = None,
        field_name: str = "basis",
    ) -> None:
        self.tokens: tuple[str, ...] = tuple(str(token) for token in tokens)
        self.field_name = field_name
        self.sources: dict[str, str] = dict(sources or {})
        self.params: dict[str, Any] = {
            "rule": self.rule,
            "field": self.field_name,
            "tokens": list(self.tokens),
            # ORCA's own precedence: the last token on the line wins.
            "effective_if_emitted": self.tokens[-1] if self.tokens else "",
        }
        if self.sources:
            self.params["sources"] = dict(self.sources)
        described = ", ".join(
            f"{token!r} ({self.sources.get(token, 'route')})" for token in self.tokens
        )
        super().__init__(
            f"ORCA accepts exactly one orbital basis on the '!' line, but this input "
            f"would emit {len(self.tokens)}: {described}. ORCA silently keeps the LAST "
            "token, so every earlier basis would be overridden without a warning. "
            "Keep exactly one orbital basis (drop the extra method-string token or set "
            "the basis field to it) and express auxiliary bases as '/J' or '/C' suffixed "
            "tokens in aux_j_basis/aux_c_basis."
        )


class AuxiliaryBasisError(ValueError):
    """An orbital basis name was supplied as an auxiliary/fitting basis."""

    rule: str = "aux_basis_invalid"

    def __init__(self, *, field_name: str, token: str) -> None:
        self.field_name = field_name
        self.token = str(token)
        self.params: dict[str, Any] = {
            "rule": self.rule,
            "field": self.field_name,
            "token": self.token,
            "allowed": ["AutoAux", "def2/J", "def2-<basis>/C"],
        }
        super().__init__(
            f"{field_name}={token!r} is an ORBITAL basis, not an auxiliary/fitting "
            "basis; ORCA would silently consume it as a fitting basis. Use a "
            "'/J' or '/C' suffixed token (e.g. 'def2/J', 'def2-TZVP/C') or 'AutoAux'."
        )


@dataclass(frozen=True)
class RouteBasisResolution:
    """Outcome of the route-line orbital-basis resolution.

    Attributes:
        method_basis: Orbital basis found in the method string (or ``None``).
        field_basis: Orbital basis found in the ``basis`` field (or ``None``).
        orbital_basis: The single distinct orbital basis that will be emitted.
        emit_field_basis: Whether the ``basis`` field value still needs its
            own route segment (``False`` when the method string already emits
            the identical basis).
        extras: ``route_extras`` entries with duplicated orbital-basis tokens
            removed (identical duplicates only — distinct values raise).
    """

    method_basis: str | None
    field_basis: str | None
    orbital_basis: str | None
    emit_field_basis: bool
    extras: tuple[str, ...] = ()


def resolve_route_orbital_basis(
    method: str | None,
    basis: str | None,
    extras: Sequence[str] = (),
) -> RouteBasisResolution:
    """Resolve the ONE orbital basis a ``!`` line may carry.

    Rules (never silently rewrite the requested basis):

    * no orbital basis anywhere → no token (composite/GFN-shaped routes);
    * one source → that token (unchanged historical rendering);
    * method + basis field carry the *identical* token → emitted once;
    * duplicated *identical* token inside ``route_extras`` → deduplicated;
    * two *distinct* tokens → :class:`RouteBasisConflictError`.

    Args:
        method: Effective method string (may inline a basis).
        basis: Effective ``basis`` field (may be empty).
        extras: Free-form ``route_extras`` literals.

    Returns:
        A :class:`RouteBasisResolution`.

    Raises:
        RouteBasisConflictError: More than one distinct orbital basis.
    """
    method_tokens = orbital_basis_tokens(method)
    field_tokens = orbital_basis_tokens(basis)

    def _raise(tokens: Sequence[str], sources: dict[str, str]) -> None:
        ordered: list[str] = []
        for token in tokens:
            if token not in ordered:
                ordered.append(token)
        raise RouteBasisConflictError(ordered, sources=sources)

    if len({token.lower() for token in method_tokens}) > 1:
        _raise(method_tokens, {token: "method" for token in method_tokens})
    if len({token.lower() for token in field_tokens}) > 1:
        _raise(field_tokens, {token: "basis" for token in field_tokens})

    method_basis = method_tokens[0] if method_tokens else None
    field_basis = field_tokens[0] if field_tokens else None

    registered: dict[str, str] = {}  # lower-case token -> spelling
    sources: dict[str, str] = {}
    if method_basis is not None:
        registered[method_basis.lower()] = method_basis
        sources[method_basis] = "method"
    if field_basis is not None:
        key = field_basis.lower()
        if registered and key not in registered:
            # A distinct orbital basis in the basis field would override the
            # method-string basis (ORCA keeps the last token) — refuse.
            sources.setdefault(field_basis, "basis")
            _raise([*registered.values(), field_basis], sources)
        registered.setdefault(key, field_basis)
        sources.setdefault(field_basis, "basis")

    cleaned_extras: list[str] = []
    for entry in extras:
        words = str(entry).split()
        if not words:
            cleaned_extras.append(str(entry))
            continue
        kept: list[str] = []
        for word in words:
            if not is_orbital_basis(word):
                kept.append(word)
                continue
            key = word.lower()
            if key in registered:
                # Identical duplicate: drop it (the basis is emitted once).
                continue
            if registered:
                _raise([*registered.values(), word], {**sources, word: "route_extras"})
            registered[key] = word
            sources[word] = "route_extras"
            kept.append(word)
        if kept:
            cleaned_extras.append(" ".join(kept))

    orbital_basis = next(iter(registered.values()), None)
    emit_field_basis = bool(str(basis or "").strip()) and not (
        field_basis is not None and method_basis is not None
    )
    return RouteBasisResolution(
        method_basis=method_basis,
        field_basis=field_basis,
        orbital_basis=orbital_basis,
        emit_field_basis=emit_field_basis,
        extras=tuple(cleaned_extras),
    )


def detect_basis_conflicts(
    *,
    method: str | None,
    basis: str | None = None,
    route_extras: Sequence[str] = (),
    aux_j_basis: str | None = None,
    aux_c_basis: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Pure, submit/preflight-shaped detection of basis conflicts.

    Zero I/O and zero side effects: the caller (preflight/submit validation)
    forwards each returned dict as an error ``reason``.  An empty tuple means
    the requested method/basis/aux combination renders a legal route line.

    Returns:
        One dict per violation, each with ``rule``/``field``/``message`` plus
        rule-specific details (``tokens``/``effective_if_emitted`` for
        ``basis_conflict``, ``token``/``allowed`` for ``aux_basis_invalid``).
    """
    reasons: list[dict[str, Any]] = []
    try:
        resolve_route_orbital_basis(method, basis, route_extras)
    except RouteBasisConflictError as exc:
        reasons.append({**exc.params, "message": str(exc)})

    for field_name, value in (("aux_j_basis", aux_j_basis), ("aux_c_basis", aux_c_basis)):
        if not value:
            continue
        if is_orbital_basis(str(value)):
            error = AuxiliaryBasisError(field_name=field_name, token=str(value))
            reasons.append({**error.params, "message": str(error)})
    return tuple(reasons)
