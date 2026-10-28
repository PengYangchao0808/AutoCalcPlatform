"""M3-03: cross-end canonical vectors for the D06 ``plan_definition_digest``.

D06 freezes the scientific plan identity (platform canonicalizer
``acp2.canonical-digest.v1``) and RPH implements the same projection in
``@rph/research``.  The two sides must agree byte-for-byte, so the *same*
proposal vectors are hashed by both:

* here, through :func:`acp_services.runtime.canonical_digest.
  proposal_plan_definition_digest` (the platform implementation);
* in ``rph/test/plan-identity-vectors.test.ts``, through
  ``computePlanDefinitionDigest`` from ``@rph/research``.

The shared fixture is ``rph/test/fixtures/plan-definition-digest-vectors.v1.json``.
A vector edit must update both sides (the expected digest is stored in the
fixture, not derived at test time by either side).
"""

from __future__ import annotations

import json
from pathlib import Path

from acp_services.runtime.canonical_digest import (
    CANONICAL_DIGEST_VERSION,
    proposal_plan_definition_digest,
)
from acp_services.runtime.digest_domains import domain_row, validate_digest_domain_registry

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VECTORS = _REPO_ROOT / "rph/test/fixtures/plan-definition-digest-vectors.v1.json"


def _load() -> dict:
    return json.loads(_VECTORS.read_text(encoding="utf-8"))


def test_plan_definition_vectors_are_registered_and_frozen() -> None:
    assert _VECTORS.is_file(), _VECTORS
    assert validate_digest_domain_registry(_REPO_ROOT) == []
    domain = domain_row("plan_definition_digest")
    assert domain.frozen is True
    assert domain.version == "1.0.0"
    assert domain.canonicalizer == "acp2.canonical-digest.v1"
    payload = _load()
    assert payload["schema"] == "acp2.test.plan-definition-digest-vectors.v1"
    names = [vector["name"] for vector in payload["vectors"]]
    assert len(names) == len(set(names)) >= 6


def test_platform_matches_every_cross_end_vector() -> None:
    payload = _load()
    for vector in payload["vectors"]:
        projection = proposal_plan_definition_digest(vector["proposal"])
        assert projection.digest == vector["digest"], vector["name"]
        assert projection.version == CANONICAL_DIGEST_VERSION


def test_vectors_cover_the_m3_03_scientific_matrix() -> None:
    by_name = {vector["name"]: vector for vector in _load()["vectors"]}
    charged = by_name["charged-doublet-nondefault-method"]
    science = charged["proposal"]["science"]
    assert (science["charge"], science["multiplicity"]) == (1, 2)
    assert science["method"] == "wB97X-D4"
    assert science["mapping"] == "atom-map-2"

    multi = by_name["multi-input-ts"]["proposal"]["science"]["initial_inputs"]
    assert [item["role"] for item in multi] == ["reactant_complex", "transition_state"]
    assert len({item["sha256"] for item in multi}) == 2

    # The same neutral singlet science with different input bytes / budget must
    # be a different digest: D06 identity is not "the science shape only".
    baseline = by_name["singlet-neutral"]["digest"]
    assert by_name["changed-input-bytes"]["digest"] != baseline
    assert by_name["changed-budget"]["digest"] != baseline
    assert by_name["triplet-anion"]["proposal"]["science"]["charge"] == -1
