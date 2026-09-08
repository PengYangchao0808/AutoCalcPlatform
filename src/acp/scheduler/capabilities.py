"""
Submission-Time Capability Model
================================

Software-requirement derivation and node-capability matching for
submission-time node selection (design doc
``.omo/drafts/node-selection-design.md`` §1.3/§2.1, plan todo W2-T3).

This module is the single authority for:

* :func:`derive_required_software` — pure derivation of the QC software a
  :class:`~acp.scheduler.jobs.JobSpec` needs, from the workflow/protocol/
  profile fixed table (:data:`WORKFLOW_SOFTWARE_REQUIREMENTS`) plus the
  method-schema ``engine`` mapping.
* :func:`matches_capabilities` — pure three-state capability match
  (declared / probe-inferred / unknown) implementing decisions D8/D13.
* :func:`local_satisfies` — server-local binary resolution via
  :func:`cccp.software.resolve_executable` seeded with the configured
  ``executables.<name>.path`` (no SSH).
* :func:`is_degraded` — single shared load/disk degradation predicate
  (consumed by node selection, the matching preview, and frontend badges).

Pure functions plus one read-only cccp config load (:func:`local_satisfies`):
no subprocess, no SSH, no node_manager import
(its ``(declared, probed_software)`` inputs are passed in by callers,
avoiding an import cycle with the remote layer).

Author: QCcalc Team
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cccp.config import load_config
from cccp.software import resolve_executable

if TYPE_CHECKING:
    from acp.scheduler.jobs import JobSpec
    from acp.scheduler.remote.config import NodeCapabilities

__all__ = [
    "ENGINE_TO_SOFTWARE",
    "WORKFLOW_SOFTWARE_REQUIREMENTS",
    "MatchResult",
    "NoCapableNodeError",
    "derive_required_software",
    "is_degraded",
    "local_satisfies",
    "matches_capabilities",
]

logger = logging.getLogger(__name__)

#: Disk-usage percentage at or above which a node counts as degraded.
DISK_DEGRADED_THRESHOLD_PCT = 90

#: method-schema ``engine`` value → declared software name (identity map;
#: kept explicit so unknown engines can be detected and logged).
ENGINE_TO_SOFTWARE: dict[str, str] = {
    "orca": "orca",
    "xtb": "xtb",
    "crest": "crest",
    "censo": "censo",
    "molclus": "molclus",
    "isostat": "isostat",
    "shermo": "shermo",
}

# --------------------------------------------------------------------- #
# Fixed per-workflow software requirements
# --------------------------------------------------------------------- #

#: Confsearch protocol → base software set (refinement policy ``screen``
#: semantics).  Every line is code-verified; ``tests/test_capabilities.py``
#: carries the file:line evidence per row.
#
# * ``xtb-crest`` — CREST search + xTB passthrough: ensemble.py:303
#   ``get_backend("crest")``; the ``censo-zero`` preset bypasses CENSO
#   entirely (ensemble.py:335-347) and CREST itself runs on the xTB
#   binary (cccp crest.py:56-57 resolves ``executables.xtb.path``).
#   Pure xTB — no ORCA/CENSO (confsearch/protocols/xtb_crest.py:3);
#   non-screen refinement policies are coerced to ``screen`` for pure-xTB
#   protocols (confsearch/contracts.py:154-160), so no policy delta
#   exists (enforced in :func:`_confsearch_fixed_software` via
#   ``PURE_XTB_PROTOCOLS``).
# * ``xtb-md`` — MD sampling via molclus (workflows/xtbmd_md.py:127
#   ``get_backend("molclus")``), GFN1 batch opt via xtb
#   (xtbmd_censo_energy.py:703 ``get_backend("xtb")``, imported at
#   confsearch/protocols/xtb_md.py:37), dedup via isostat
#   (confsearch/protocols/xtb_md.py:122 ``get_backend("isostat")``).
#   Pure xTB terminus — no CENSO/ORCA tail (xtb_md.py:3-6).
# * ``censo-crest`` — CREST (ensemble.py:303) + CENSO (ensemble.py:354
#   ``CensoBackend``).  ORCA is required even at the ``screen`` policy:
#   CENSO's rcfile pins ``prog = orca`` for every active part
#   (cccp censo.py:295) and the ``censo-light`` preset runs its
#   prescreening/screening parts at the B97-3c DFT functional
#   (cccp censo.py:115-122); xTB backs CREST (crest.py:56-57) and is
#   pinned in the CENSO rcfile paths (censo.py:323-324).
# * ``xtbmd-censo`` — the ``xtb-md`` chain (molclus/xtb/isostat) plus
#   CENSO (xtbmd_censo_energy.py:1743 ``CensoBackend``) with the same
#   ``prog = orca`` rcfile requirement (censo.py:295).
_CONFSEARCH_PROTOCOL_SOFTWARE: dict[str, frozenset[str]] = {
    "xtb-crest": frozenset({"xtb", "crest"}),
    "xtb-md": frozenset({"xtb", "molclus", "isostat"}),
    "censo-crest": frozenset({"xtb", "crest", "censo", "orca"}),
    "xtbmd-censo": frozenset({"xtb", "molclus", "isostat", "censo", "orca"}),
}

#: Software added on top of the protocol base when the refinement policy
#: engages the fine-DFT handoff (``rank1`` / ``cumulative-99`` / ``all``):
#: the handoff runs ORCA opt→freq→SP plus a Shermo correction
#: (energy_shared.py:382/427/459 ``get_backend("orca")``,
#: energy_shared.py:489 ``run_shermo``).  ORCA itself is already in the
#: CENSO-protocol bases (``prog = orca``), so the delta is Shermo only.
_CONFSEARCH_REFINEMENT_SOFTWARE: frozenset[str] = frozenset({"shermo"})

#: Refinement policies that engage the fine-DFT handoff.  ``screen`` is the
#: only policy that skips it (confsearch/protocols/censo_crest.py:33 branches
#: on ``policy == "screen"``; xtbmd_censo.py:44 maps it to ``no_opt``).
_REFINEMENT_POLICIES_WITH_DFT: frozenset[str] = frozenset({"rank1", "cumulative-99", "all"})

#: BatchOptimize profile → software.  Opt/freq/SP steps default to the ORCA
#: backend (calculations/primitives/_common.py:238-245
#: ``backend_name`` default ``"orca"``; batch/engine.py:675 hardcodes
#: ``backend="orca"`` for electronic-state validation; the
#: ``batch_optimize`` schema profiles carry no per-level engines).
#: Only ``opt_freq_sp_thermo`` adds the THERMOCHEMISTRY step
#: (calculations/batch/profiles.py:17-22) which runs Shermo
#: (batch/engine.py:967 → primitives/thermochemistry.py:10,73
#: ``run_shermo``).
_BATCHOPTIMIZE_PROFILE_SOFTWARE: dict[str, frozenset[str]] = {
    "opt_only": frozenset({"orca"}),
    "opt_freq": frozenset({"orca"}),
    "opt_freq_sp": frozenset({"orca"}),
    "opt_freq_sp_thermo": frozenset({"orca", "shermo"}),
}

#: Workflow-level fixed requirements, keyed by workflow id.  Confsearch is
#: keyed by protocol/policy (see :func:`_confsearch_fixed_software`) and
#: BatchOptimize by profile (see :func:`_batchoptimize_fixed_software`);
#: both are surfaced through this mapping's accessor semantics — the dict
#: below holds the rows that need no sub-key.  PESsearch/irc/scan/simple
#: have **no** workflow-level fixed entries: their engines come from the
#: method-schema mapping (irc's ORCA via the ``irc`` schema default
#: profile; PESsearch's orca/xtb via the ``pes_scan`` default profile).
WORKFLOW_SOFTWARE_REQUIREMENTS: dict[str, frozenset[str]] = {
    # nmr.py:6-16 — conformer generation reuses run_ensemble_generation
    # with the censo-light preset (CREST + CENSO), GIAO shieldings via the
    # ORCA backend (nmr.py:437-441 get_backend("orca")); xTB backs both
    # CREST (crest.py:56-57) and the CENSO rcfile paths (censo.py:324).
    "nmr": frozenset({"xtb", "crest", "censo", "orca"}),
}


def _confsearch_fixed_software(method: dict[str, Any]) -> frozenset[str]:
    """Fixed Confsearch software for the spec's protocol/policy.

    Protocol resolution mirrors ``confsearch_method_flags``
    (scheduler/jobs.py:405-407): explicit ``method.protocol`` wins, then
    ``method.profile_id`` when it doubles as a protocol id; the CLI
    default is ``censo-crest`` (cli.py:419-422).  Policy defaults to
    ``screen`` (``ConfsearchRequest.refinement_policy``,
    confsearch/contracts.py:64).
    """
    from acp.confsearch.contracts import PROTOCOLS, PURE_XTB_PROTOCOLS  # lazy: avoid import cycle

    protocol = str(method.get("protocol") or "").strip()
    if not protocol and str(method.get("profile_id") or "") in PROTOCOLS:
        protocol = str(method["profile_id"])
    if not protocol:
        protocol = "censo-crest"  # CLI default (cli.py:419-422)
    base = _CONFSEARCH_PROTOCOL_SOFTWARE.get(protocol)
    if base is None:
        logger.warning(
            "Confsearch protocol %r not in requirement table; skipping fixed set", protocol
        )
        return frozenset()
    policy = str(method.get("refinement_policy") or "screen")
    if policy in _REFINEMENT_POLICIES_WITH_DFT and protocol not in PURE_XTB_PROTOCOLS:
        # Pure-xTB protocols coerce non-screen policies to "screen" at
        # runtime (confsearch/contracts.py:154-160) — no DFT handoff, so
        # no Shermo delta may be derived for them.
        return base | _CONFSEARCH_REFINEMENT_SOFTWARE
    return base


def _batchoptimize_fixed_software(method: dict[str, Any]) -> frozenset[str]:
    """Fixed BatchOptimize software for the spec's profile.

    Profile resolution mirrors ``batchoptimize_method_flags``
    (scheduler/jobs.py:464): ``method.profile`` then ``method.profile_id``;
    the CLI default is ``opt_freq`` (cli.py:628-630).
    """
    profile = str(method.get("profile") or method.get("profile_id") or "opt_freq")
    fixed = _BATCHOPTIMIZE_PROFILE_SOFTWARE.get(profile)
    if fixed is None:
        logger.warning(
            "BatchOptimize profile %r not in requirement table; skipping fixed set",
            profile,
        )
        return frozenset()
    return fixed


def _engines_from_levels(levels: Any) -> set[str]:
    """Collect ``engine`` values from a ``{level_id: {...}}`` mapping."""
    engines: set[str] = set()
    if not isinstance(levels, dict):
        return engines
    for level in levels.values():
        if isinstance(level, dict):
            engine = level.get("engine")
            if isinstance(engine, str) and engine:
                engines.add(engine)
    return engines


def _resolve_schema_id(workflow: str, method: dict[str, Any]) -> str | None:
    """Resolve the method schema id: explicit ``schema_id`` first, then the
    workflow catalog's ``method_schema_id``."""
    from acp.catalog import METHOD_SCHEMAS, get_workflow_by_id

    schema_id = method.get("schema_id")
    if isinstance(schema_id, str) and schema_id in METHOD_SCHEMAS:
        return schema_id
    entry = get_workflow_by_id(workflow)
    catalog_id = entry.get("method_schema_id") if entry else None
    if isinstance(catalog_id, str) and catalog_id in METHOD_SCHEMAS:
        return catalog_id
    return None


def _method_schema_software(workflow: str, method: dict[str, Any]) -> frozenset[str]:
    """Map the spec's method schema onto required software names.

    Resolution chain (design §2.1 item 2 — the ``engine`` field name is
    verified against ``METHOD_SCHEMAS`` profiles):

    1. explicit ``method.levels`` — user-configured levels with an
       ``engine`` field;
    2. explicit profile (``method.profile_id`` / ``method.profile``)
       → that profile's level engines;
    3. schemas with exactly one profile → that profile's engines
       (``dft_singlepoint``/``dft_optimize``/``dft_scan``/``irc``/
       ``pes_scan`` all expose a single ``default`` profile);
    4. when the above yield nothing → the union of ``allowed_engines``
       across ``method_levels`` (covers profile-less schemas such as
       ``dft_frequency`` → {orca} and ``xtb_optimize`` → {xtb}, and
       engine-less level payloads).

    Unknown engine values are ignored with a warning.
    """
    from acp.catalog import METHOD_SCHEMAS, get_method_profiles

    schema_id = _resolve_schema_id(workflow, method)
    if schema_id is None:
        return frozenset()
    schema = METHOD_SCHEMAS[schema_id]

    engines: set[str] = set()
    explicit_levels = method.get("levels")
    profiles = get_method_profiles(schema_id)
    if isinstance(explicit_levels, dict) and explicit_levels:
        engines |= _engines_from_levels(explicit_levels)
    else:
        profile_id = method.get("profile_id") or method.get("profile")
        selected: dict[str, Any] | None = None
        if isinstance(profile_id, str) and profile_id:
            selected = next(
                (p["levels"] for p in profiles if p.get("profile_id") == profile_id),
                None,
            )
            if selected is None and profiles:
                # Only warn when the schema actually offers profiles —
                # profile-less schemas (dft_frequency, xtb_optimize)
                # legitimately fall through to allowed_engines below.
                logger.warning(
                    "method profile %r not found in schema %r; ignoring",
                    profile_id,
                    schema_id,
                )
        elif len(profiles) == 1:
            selected = profiles[0].get("levels")
        if selected is not None:
            engines |= _engines_from_levels(selected)
    if not engines:
        for level_def in schema.get("method_levels", []):
            for engine in level_def.get("allowed_engines", []):
                if isinstance(engine, str) and engine:
                    engines.add(engine)

    software: set[str] = set()
    for engine in sorted(engines):
        name = ENGINE_TO_SOFTWARE.get(engine)
        if name is None:
            logger.warning("Unknown engine %r in schema %r; ignoring", engine, schema_id)
            continue
        software.add(name)
    return frozenset(software)


def derive_required_software(spec: JobSpec) -> frozenset[str]:
    """Derive the QC software a job spec requires (design §2.1).

    Two contributions, both read-only over the spec and the catalog:

    1. workflow/protocol/profile fixed table
       (:data:`WORKFLOW_SOFTWARE_REQUIREMENTS` + the Confsearch/BatchOptimize
       sub-tables);
    2. method-schema ``engine`` mapping over the spec's schema/profile/
       levels.

    Confsearch deliberately contributes **only** via its fixed table: the
    ``confsearch_unified`` profile levels are configuration knobs (e.g. an
    optional ``thermo`` level present in every profile), while actual
    execution is policy-driven and fully encoded in the fixed rows —
    expanding profile levels there would over-derive (e.g. Shermo for the
    pure-xTB ``xtb-crest`` protocol, which has no DFT stage at all per
    confsearch/contracts.py:154-160).

    Args:
        spec: The job specification. Only ``workflow`` and ``method`` are
            consulted — no IO, no subprocess, no SSH.

    Returns:
        The required software names (subset of
        ``DECLARED_SOFTWARE_NAMES``); empty when nothing derivable (the
        generic auto-selection fallback, D14).
    """
    method = spec.method if isinstance(spec.method, dict) else {}
    workflow = str(spec.workflow)

    if workflow == "Confsearch":
        return _confsearch_fixed_software(method)
    if workflow == "BatchOptimize":
        return _batchoptimize_fixed_software(method)
    fixed = WORKFLOW_SOFTWARE_REQUIREMENTS.get(workflow, frozenset())
    return fixed | _method_schema_software(workflow, method)


# --------------------------------------------------------------------- #
# Capability matching (D8 / D13)
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class MatchResult:
    """Outcome of one node's capability match.

    Attributes:
        satisfies: True when the node satisfies both the software and the
            tag requirements under the D8 three-state model.
        missing_software: Required software the node cannot provide
            (sorted).
        missing_tags: Required tags the node cannot provide (sorted);
            non-empty for ANY tag requirement on an undeclared node (D13).
        reasons: English human-readable reason strings (empty when
            ``satisfies`` is True).
    """

    satisfies: bool
    missing_software: tuple[str, ...] = ()
    missing_tags: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


def matches_capabilities(
    required_software: Iterable[str],
    required_tags: Iterable[str],
    *,
    declared: NodeCapabilities | None,
    probed_software: Iterable[str] | None,
) -> MatchResult:
    """Match a job's requirements against one node's capabilities (D8/D13).

    Three-state semantics (design §1.1 D8 + §1.3 D13):

    * ``declared`` is not None — the declaration is authoritative for BOTH
      software and tags (probe results are display-only for declared
      nodes).
    * ``declared`` is None and ``probed_software`` is non-empty — software
      is judged against the probe-inferred set; tags can NEVER be
      satisfied (they are not probeable).
    * ``declared`` is None and the probe is empty/None — unknown state:
      software requirements fall back to "generic" (treated as
      satisfied); tags still never satisfied.

    Args:
        required_software: Software names from :func:`derive_required_software`.
        required_tags: Tag names from ``JobSpec.node_tags``.
        declared: The node's static declaration, or ``None``.
        probed_software: Probed software names (any iterable), ``None``
            or empty when no probe has ever succeeded.

    Returns:
        The frozen :class:`MatchResult`.
    """
    required_sw = frozenset(required_software)
    required_tag_set = frozenset(required_tags)

    if declared is not None:
        missing_software = required_sw - frozenset(declared.software)
        missing_tags = required_tag_set - frozenset(declared.tags)
    else:
        probed = frozenset(probed_software or ())
        if probed:
            missing_software = required_sw - probed
        else:
            # Unknown capability state — generic fallback (D8).
            missing_software = frozenset()
        # Tags are never satisfied on undeclared nodes (D13).
        missing_tags = required_tag_set

    reasons: list[str] = []
    if missing_software:
        reasons.append(f"missing software: {', '.join(sorted(missing_software))}")
    if missing_tags:
        reasons.append(f"missing tags: {', '.join(sorted(missing_tags))}")
    return MatchResult(
        satisfies=not missing_software and not missing_tags,
        missing_software=tuple(sorted(missing_software)),
        missing_tags=tuple(sorted(missing_tags)),
        reasons=tuple(reasons),
    )


# --------------------------------------------------------------------- #
# Local satisfaction + degradation
# --------------------------------------------------------------------- #


def local_satisfies(required_software: Iterable[str]) -> bool:
    """Whether the server-local machine provides every required binary.

    Resolves each name via :func:`cccp.software.resolve_executable`, seeded
    with the ``executables.<name>.path`` from the cccp YAML config
    (``~/.cccp.yaml``) so config-only installs count as present — mirrors
    the CLI preflight (cli.py ``_preflight_workflow``).  Per-name order:
    configured path → env → PATH → fallbacks.  No SSH: this describes the
    head node the scheduler runs on (D14).
    """
    names = list(required_software)
    if not names:
        return True
    try:
        configured_executables = load_config().get("executables") or {}
    except Exception:
        configured_executables = {}
    for name in names:
        configured = configured_executables.get(name)
        configured_path = configured.get("path") if isinstance(configured, dict) else None
        if resolve_executable(name, configured_path=configured_path) is None:
            return False
    return True


def is_degraded(node_status: Any) -> bool:
    """Whether a node is too loaded or low on disk to prefer for dispatch.

    Single shared implementation (design §2.1) consumed by node selection,
    the matching preview, and frontend badges.  True when
    ``running_jobs >= max_jobs`` or ``disk_usage_pct >= 90``.

    Duck-typed over any status object exposing (a subset of)
    ``running_jobs`` / ``max_jobs`` / ``disk_usage_pct`` — e.g.
    ``NodeManager.NodeStatus``.  Missing or non-positive ``max_jobs``
    disables the load axis; a missing ``disk_usage_pct`` disables the disk
    axis (``NodeStatus`` defaults it to 0, which never trips the 90
    threshold).
    """
    running = getattr(node_status, "running_jobs", 0) or 0
    max_jobs = getattr(node_status, "max_jobs", None)
    disk_pct = getattr(node_status, "disk_usage_pct", None)
    if max_jobs is not None and int(max_jobs) > 0 and int(running) >= int(max_jobs):
        return True
    if disk_pct is not None and int(disk_pct) >= DISK_DEGRADED_THRESHOLD_PCT:
        return True
    return False


# --------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------- #


class NoCapableNodeError(RuntimeError):
    """No configured node (and not the local machine) can run the job.

    Raised at submission time when the derived requirements match no
    eligible execution target (design §2.3 — surfaced as HTTP 400 with
    ``code="no_capable_node"`` by the API layer).

    Attributes:
        code: Stable machine-readable error code (``"no_capable_node"``).
        missing_software: Software no node provided (sorted tuple; empty
            when the failure is tag-driven or context-dependent).
        missing_tags: Tags no node declared (sorted tuple).
    """

    def __init__(
        self,
        message: str,
        *,
        missing_software: Iterable[str] | None = None,
        missing_tags: Iterable[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = "no_capable_node"
        self.missing_software = tuple(sorted(missing_software or ()))
        self.missing_tags = tuple(sorted(missing_tags or ()))
