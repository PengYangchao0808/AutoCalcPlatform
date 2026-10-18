"""T53 (C07-s3b): identity fingerprint pure-algorithm sink to ``cccp``.

Guards the single-sink contract declared by the C00 closure ledger (T2 identity
ruling → ``cccp.analysis.identity``) and the write registry (§4.4 / §5.1 #5):

* the historical ``acp.calculations.identity`` path re-exports the **same**
  objects (``A is B``), so every existing consumer keeps working unchanged;
* ``cccp.analysis.identity`` never pulls ``acp`` into ``sys.modules`` (the
  no-reverse-load rule);
* the fingerprint output is pinned character-level — any canonicalisation drift
  is a hard failure (plan acceptance: 老/新 fingerprint 对固定输入输出一致).
"""

from __future__ import annotations

import subprocess
import sys

import acp.calculations.identity as acp_identity
import cccp.analysis.identity as cccp_identity

#: Names the old ACP module must surface as the same objects from cccp.
_REEXPORTED = (
    "IDENTITY_SCHEMA",
    "IdentityInputMissing",
    "ScientificIdentity",
    "config_digest",
    "content_sha256",
    "current_config_digest",
    "identity_fingerprint",
    "inputs_not_newer_than",
    "resolve_stored_location",
)

#: Character-level goldens captured before the move (see task-53 evidence).
_GOLDEN_FINGERPRINT_SIMPLE = "v2:ca7a7c6cde8062a260c631854aab1e42"
_GOLDEN_FINGERPRINT_NESTED = "v2:458224291f68c563e160306bb836bd72"
_GOLDEN_CONFIG_DIGEST = "sha256:c96a815a0909374691c311a99e83f8b321957d3e733a4b3e4ea4626446507375"


def test_identity_schema_is_two() -> None:
    assert cccp_identity.IDENTITY_SCHEMA == 2
    assert acp_identity.IDENTITY_SCHEMA is cccp_identity.IDENTITY_SCHEMA


def test_old_path_reexports_the_same_objects() -> None:
    for name in _REEXPORTED:
        assert getattr(acp_identity, name) is getattr(cccp_identity, name), name


def test_cccp_identity_import_does_not_load_acp() -> None:
    code = (
        "import sys; import cccp.analysis.identity; "
        "print(any(m == 'acp' or m.startswith('acp.') for m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"


def test_fingerprint_output_is_character_level_pinned() -> None:
    assert (
        cccp_identity.identity_fingerprint({"a": 1, "b": {"x": 1, "y": 2}})
        == _GOLDEN_FINGERPRINT_SIMPLE
    )
    assert (
        cccp_identity.identity_fingerprint(
            {"a": 1, "b": {"x": 1, "y": 2}, "c": [1, 2, 3], "d": None}
        )
        == _GOLDEN_FINGERPRINT_NESTED
    )
    # key order must never shift the digest (canonical sorted keys)
    assert cccp_identity.identity_fingerprint({"b": 2, "a": 1}) == (
        cccp_identity.identity_fingerprint({"a": 1, "b": 2})
    )
    assert cccp_identity.config_digest({"n": 2, "theory": {"a": 1}}) == _GOLDEN_CONFIG_DIGEST


def test_compute_identity_stays_in_acp_and_returns_cccp_type(tmp_path) -> None:
    from acp.calculations.contracts import CalculationPlan, StructureArtifact

    item = tmp_path / "input.xyz"
    item.write_text("1\ninput\nC 0.0 0.0 0.0\n", encoding="utf-8")
    plan = CalculationPlan(
        workflow="test",
        items=[StructureArtifact(path=item, elements=["C"])],
        steps=[],
    )
    identity = acp_identity.compute_identity(plan)
    assert isinstance(identity, cccp_identity.ScientificIdentity)
    assert identity.plan_identity.startswith("v2:")
