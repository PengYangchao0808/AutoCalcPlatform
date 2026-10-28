"""The frozen D06 digest-domain registry (M1-04a).

D06 separates at least eight identities that used to be called "the digest":
the scientific plan definition, the execution plan, the proposal projection, the
idempotent submit request, the worker's frozen plan body, the recipe definition,
the per-step execution identity and the release/contract/artifact digests.
Collapsing two of them is the defect the registry exists to prevent, so this
module is the machine-readable single source of:

* **owner** of the domain (which lane may change it),
* the **canonical input and field order** (what is hashed, in which order),
* the **version** (a wire-compatible change bumps it; a semantic change needs a
  new domain),
* the **unique validation implementation** (platform / CCCP / RPH) — a domain
  with no registered validator may **not** be marked ``frozen``,
* the **legacy wire field** compatibility mapping (which old field carried this
  identity before D06, and how it is read today).

The human-readable table is §6 of
``docs/reference/next-change/acp2-contracts/experience/plan-identity.md``; a test
asserts this module and that document agree row for row.  The companion
acceptance check is ``scripts/acceptance/check_execution_limits_consistency.py``
(capability matrix) — the two are deliberately separate: this registry freezes
*identity*, the capability matrix freezes *enforcement*.

Isolation: stdlib only, no ``acp`` import.  Validation is static (path exists +
symbol present) so importing this module never imports CCCP or RPH.

Author: ACP Services (B lane, M1-04a).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DIGEST_DOMAIN_SCHEMA",
    "DIGEST_DOMAINS",
    "DOMAIN_STATUS_FROZEN",
    "DOMAIN_STATUS_NOT_FROZEN",
    "DigestDomain",
    "DigestDomainError",
    "domain_row",
    "registry_rows",
    "validate_digest_domain_registry",
]

#: Contract identity of the registry itself.
DIGEST_DOMAIN_SCHEMA = "acp2.runtime.digest-domains.v1"

DOMAIN_STATUS_FROZEN = "frozen"
DOMAIN_STATUS_NOT_FROZEN = "not-frozen"

#: Repository root, derived from this file (``src/acp_services/runtime/``).
_REPO_ROOT = Path(__file__).resolve().parents[3]


class DigestDomainError(ValueError):
    """The digest-domain registry violates the frozen registration rule."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f"{rule}: {message}")
        self.rule = rule
        self.message = message


@dataclass(frozen=True, slots=True)
class DigestDomain:
    """One registered digest/identity domain (one contract row)."""

    domain: str
    owner: str
    canonical_inputs: tuple[str, ...]
    version: str
    canonicalizer: str
    validator_path: str
    validator_symbol: str
    validator_side: str
    status: str = DOMAIN_STATUS_FROZEN
    legacy_wire_fields: tuple[tuple[str, str], ...] = ()
    notes: str = ""

    @property
    def frozen(self) -> bool:
        """Whether the domain may be cited as frozen."""
        return self.status == DOMAIN_STATUS_FROZEN

    def legacy_targets(self) -> dict[str, str]:
        """Map legacy wire field name -> this domain's canonical field name."""
        return {old: new for old, new in self.legacy_wire_fields}

    def to_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "owner": self.owner,
            "canonical_inputs": list(self.canonical_inputs),
            "field_order": "canonical_inputs (order-significant)",
            "version": self.version,
            "canonicalizer": self.canonicalizer,
            "validator": f"{self.validator_path}::{self.validator_symbol}"
            if self.validator_path
            else "",
            "validator_side": self.validator_side,
            "status": self.status,
            "legacy_wire_fields": dict(self.legacy_wire_fields),
            "notes": self.notes,
        }


#: The D06 registry.  Ten domains are frozen; ``recipe_definition_digest`` is
#: deliberately **not** frozen: CCCP computes an internal recipe identity but no
#: cross-side verifier is registered yet (owner A, M2-05), and a domain without
#: a registered validator must not be cited as frozen.
DIGEST_DOMAINS: tuple[DigestDomain, ...] = (
    DigestDomain(
        domain="plan_definition_digest",
        owner="C-RPH science / B16 canonicalization",
        canonical_inputs=(
            "recipe_digest",
            "science.capability_steps",
            "science.initial_inputs",
            "science.method",
            "science.mapping",
            "science.charge",
            "science.multiplicity",
            "science.allowed_substitutions",
            "budget.model_requests",
            "budget.input_tokens",
            "budget.output_tokens",
            "budget.task_refs",
            "budget.recipe_steps",
            "budget.qc_invocations",
            "budget.wall_seconds",
            "budget.queue_seconds",
        ),
        version="1.0.0",
        canonicalizer="acp2.canonical-digest.v1",
        validator_path="src/acp_services/runtime/canonical_digest.py",
        validator_symbol="proposal_plan_definition_digest",
        validator_side="platform",
        legacy_wire_fields=(("plan_digest", "plan_definition_digest"),),
        notes=(
            "the science the approver signs; C's reference projection with B16 as "
            "the only canonicalizer. A pre-D06 client that sent a science digest as "
            "plan_digest is read as plan_definition_digest, never as the execution plan."
        ),
    ),
    DigestDomain(
        domain="execution_plan_digest",
        owner="B",
        canonical_inputs=(
            "workflow",
            "input",
            "method",
            "resources",
            "plan_version",
            "recipe_ref",
            "parameters",
            "dependency_selectors",
            "allowed_bounded_alternatives",
        ),
        version="1.0.0",
        canonicalizer="acp2.canonical-digest.v1",
        validator_path="src/acp_services/runtime/canonical_digest.py",
        validator_symbol="plan_digest",
        validator_side="platform",
        legacy_wire_fields=(("plan_digest", "execution_plan_digest"),),
        notes=(
            "the wire name plan_digest is unchanged; its meaning is narrowed to the "
            "frozen workflow/input/method/resources plan the submit authorizes."
        ),
    ),
    DigestDomain(
        domain="proposal_digest",
        owner="B",
        canonical_inputs=(
            "proposal_id",
            "project_id",
            "title",
            "question",
            "capability_ids",
            "recipe_ref",
            "plan_digest",
            "budget_request",
        ),
        version="4",
        canonicalizer="acp2.canonical-digest.v1",
        validator_path="src/acp_services/runtime/canonical_digest.py",
        validator_symbol="proposal_digest",
        validator_side="platform",
        legacy_wire_fields=(("proposal_digest", "proposal_digest"),),
        notes="semantic Proposal projection; approval state/revision are excluded.",
    ),
    DigestDomain(
        domain="submit_request_digest",
        owner="B",
        canonical_inputs=(
            "schema_version",
            "workflow",
            "input",
            "method",
            "resources",
            "project_id",
            "execution_mode",
            "target_node",
            "node_tags",
            "name",
            "tags",
            "molecule_name",
            "task_name",
            "remark",
            "preflight_fingerprint",
            "plan_digest",
            "proposal_ref",
            "proposal_digest",
            "budget",
            "extensions",
        ),
        version="2",
        canonicalizer="acp2.idempotency.request-digest.v2",
        validator_path="src/acp_services/runtime/idempotency.py",
        validator_symbol="canonical_request_digest",
        validator_side="platform",
        legacy_wire_fields=(("request_digest", "submit_request_digest"),),
        notes=(
            "idempotency-scoped only; never compared with a plan/proposal digest. "
            "Pre-M0-06 rows stay verifiable through their own frozen field table."
        ),
    ),
    DigestDomain(
        domain="frozen_plan_body_digest",
        owner="B",
        canonical_inputs=(
            "plan_identity",
            "release_id",
            "steps",
            "symbols",
            "inputs",
            "resources",
        ),
        version="2",
        canonicalizer="sha256 body manifest",
        validator_path="src/acp_services/runtime/attempt_freeze.py",
        validator_symbol="frozen_group",
        validator_side="platform",
        legacy_wire_fields=(("payload_sha256", "frozen_plan_body_digest"),),
        notes=(
            "worker frozen-body integrity (v2: prefix), not a submit/approval digest; "
            "it never substitutes for execution_plan_digest."
        ),
    ),
    DigestDomain(
        domain="recipe_definition_digest",
        owner="A (CCCP)",
        canonical_inputs=("recipe_id", "recipe_digest", "steps", "dependencies"),
        version="0.0.0-pending",
        canonicalizer="cccp.recipe.identity_fingerprint",
        validator_path="",
        validator_symbol="",
        validator_side="cccp",
        status=DOMAIN_STATUS_NOT_FROZEN,
        legacy_wire_fields=(("recipe_digest", "recipe_definition_digest"),),
        notes=(
            "CCCP computes an internal recipe identity; no cross-side verifier is "
            "registered yet (owner A, M2-05). A not-frozen domain may not be cited as "
            "frozen in a BOM, an approval or a release statement."
        ),
    ),
    DigestDomain(
        domain="science_criteria_digest",
        owner="A (CCCP)",
        canonical_inputs=(
            "schema",
            "version",
            "regime",
            "validator_version",
            "thresholds",
            "downgrade_rules",
            "rejection_rules",
            "criteria",
            "questions",
        ),
        version="1.0.0",
        canonicalizer="cccp.analysis.research._common.canonical_json (sorted keys)",
        validator_path="src/cccp/analysis/research/science_criteria.py",
        validator_symbol="criteria_definition_digest",
        validator_side="cccp",
        notes=(
            "frozen ADR-008 science criteria table (M1-05); never a recipe or plan "
            "identity — a criteria change is a new version plus a new approval, so an "
            "inline edit after seeing a result becomes visible in the signed digest."
        ),
    ),
    DigestDomain(
        domain="step_execution_identity",
        owner="B worker + A science",
        canonical_inputs=(
            "step_index",
            "step_identity",
            "implementation_version",
            "upstream_digests",
            "materialized_parameters_digest",
        ),
        version="2",
        canonicalizer="acp2.canonical-digest.v1",
        validator_path="src/acp_services/runtime/receipt_identity.py",
        validator_symbol="identity_digest",
        validator_side="platform",
        legacy_wire_fields=(("step_identity", "step_execution_identity"),),
        notes=(
            "definition identity + actual upstream bytes; materialized at dispatch, "
            "never written back into the frozen envelope."
        ),
    ),
    DigestDomain(
        domain="contract_digest",
        owner="B",
        canonical_inputs=("path", "sha256"),
        version="1.0.0",
        canonicalizer="sorted path+sha256 line join",
        validator_path="scripts/acceptance/validate_acp2_contracts.py",
        validator_symbol="contract_digest_for",
        validator_side="platform",
        legacy_wire_fields=(("contract_digest", "contract_digest"),),
        notes="manifest contract set identity; never an artifact or release digest.",
    ),
    DigestDomain(
        domain="artifact_digest",
        owner="B",
        canonical_inputs=("relative_path", "sha256", "size"),
        version="1.0.0",
        canonicalizer="sha256 file bytes",
        validator_path="src/acp_services/data/artifacts.py",
        validator_symbol="normalize_artifact_digest",
        validator_side="platform",
        legacy_wire_fields=(("artifact_digest", "artifact_digest"),),
        notes="published artifact bytes; a product read must re-verify it.",
    ),
    DigestDomain(
        domain="release_digest",
        owner="B",
        canonical_inputs=("role", "name", "sha256"),
        version="1.0.0",
        canonicalizer="sha256 wheel bytes",
        validator_path="scripts/build_release.py",
        validator_symbol="_wheel_facts",
        validator_side="platform",
        legacy_wire_fields=(("release_digest", "release_digest"),),
        notes="release artifact identity; unrelated to step/plan identity.",
    ),
)

_FROZEN_DOMAIN_NAMES = frozenset(row.domain for row in DIGEST_DOMAINS)


def domain_row(domain: str) -> DigestDomain:
    """Return the registered row for *domain* or raise."""
    for row in DIGEST_DOMAINS:
        if row.domain == str(domain):
            return row
    raise DigestDomainError("unknown_domain", f"{domain!r} is not a registered D06 domain")


def registry_rows() -> list[dict[str, object]]:
    """Return the registry as contract rows (stable order, JSON-ready)."""
    return [row.to_dict() for row in DIGEST_DOMAINS]


def _resolve(root: Path, row: DigestDomain) -> str:
    """Static validator resolution: the file exists and names the symbol."""
    path = root / row.validator_path
    if not path.is_file():
        return f"validator file missing: {row.validator_path}"
    text = path.read_text(encoding="utf-8", errors="ignore")
    if row.validator_symbol and row.validator_symbol not in text:
        return f"validator symbol {row.validator_symbol!r} absent from {row.validator_path}"
    return ""


def validate_digest_domain_registry(root: Path | str | None = None) -> list[str]:
    """Validate the registry; return the list of violations (empty = valid).

    Rules:

    1. every domain name is unique and non-empty;
    2. a ``frozen`` domain has an owner, a version, a non-empty ordered field
       list, a canonicalizer and a **registered** validator (file + symbol);
    3. a domain without a validator is ``not-frozen`` — never silently frozen;
    4. every validator side is one of ``platform``/``cccp``/``rph``;
    5. a ``not-frozen`` domain carries a note explaining the gap.
    """
    violations: list[str] = []
    base = Path(root) if root is not None else _REPO_ROOT
    seen: set[str] = set()
    for row in DIGEST_DOMAINS:
        if not row.domain:
            violations.append("empty domain name")
            continue
        if row.domain in seen:
            violations.append(f"duplicate domain {row.domain}")
        seen.add(row.domain)
        if row.validator_side not in {"platform", "cccp", "rph"}:
            violations.append(f"{row.domain}: unknown validator side {row.validator_side!r}")
        if not row.owner.strip():
            violations.append(f"{row.domain}: owner is required")
        if row.frozen:
            for label, value in (
                ("version", row.version),
                ("canonicalizer", row.canonicalizer),
                ("validator_path", row.validator_path),
                ("validator_symbol", row.validator_symbol),
            ):
                if not str(value).strip():
                    violations.append(f"{row.domain}: frozen domain needs a {label}")
            if not row.canonical_inputs:
                violations.append(f"{row.domain}: frozen domain needs an ordered field list")
            if row.validator_path and row.validator_symbol:
                problem = _resolve(base, row)
                if problem:
                    violations.append(f"{row.domain}: {problem}")
        else:
            if row.validator_path and row.validator_symbol:
                violations.append(
                    f"{row.domain}: not-frozen domain must not claim a registered validator"
                )
            if not row.notes.strip():
                violations.append(f"{row.domain}: not-frozen domain needs a documented gap")
    return violations
