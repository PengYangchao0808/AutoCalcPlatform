"""B26b H7 final-index tests: evidence aggregation integrity and release digests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = REPO_ROOT / "reports/acp2_implementation/bb45a46"
INTEGRATION = BASE / "integration"
H7_INDEX = INTEGRATION / "h7-index.json"
SUMMARY = INTEGRATION / "summary.json"
RELEASE_DIR = BASE / "B/B25/release-fix5"
WORKFLOWS = ("H0", "H1", "H2", "H3", "H4", "H5", "H6", "H7")
RESULT_VALUES = {"PASS", "FAIL", "NOT_VERIFIED"}
#: C's live evidence is owned outside this repo.  A row that cites it must
#: declare the external reference and its status instead of pointing at a
#: repo-relative file that a clean checkout cannot contain.
EXTERNAL_EVIDENCE_ROOTS = (
    Path("/ACPServer/acp2-development/A"),
    Path("/ACPServer/acp2-development/C"),
    Path("/ACPServer/acp2-development/evidence/C"),
    REPO_ROOT / "reports/acp2_implementation/bb45a46/C",
)
EXTERNAL_EVIDENCE_STATUS = "NOT_VERIFIED(external_evidence_not_in_repo)"


def _load_harness() -> Any:
    script = REPO_ROOT / "scripts/acceptance/acp2_runtime.py"
    spec = importlib.util.spec_from_file_location("acp2_runtime_h7", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["acp2_runtime_h7"] = module
    spec.loader.exec_module(module)
    return module


HARNESS = _load_harness()


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_summary_validates_with_the_shared_harness() -> None:
    errors = HARNESS.validate_summary(SUMMARY)
    assert errors == []


def _evidence_candidates(entry: str | dict[str, Any]) -> list[Path]:
    """Resolve one evidence entry under the repo or a declared external root."""
    if isinstance(entry, str):
        return [REPO_ROOT / entry]
    path = Path(entry["path"])
    candidates = [REPO_ROOT / path]
    external_ref = entry.get("external_ref")
    if external_ref:
        ref = Path(external_ref)
        if ref.is_absolute():
            candidates.append(ref)
        else:
            candidates.extend(root / ref for root in EXTERNAL_EVIDENCE_ROOTS)
    return candidates


def test_every_h0_h7_row_has_a_result_and_artifact_provenance() -> None:
    index = _read(H7_INDEX)
    rows = index["rows"]
    assert [row["handoff"] for row in rows] == list(WORKFLOWS)
    for row in rows:
        assert row["result"] in RESULT_VALUES, row
        assert row["scope"] in {"IN_SCOPE", "DEFERRED", "NOT_APPLICABLE"}
        assert row["evidence"], row["handoff"]
        for evidence in row["evidence"]:
            if isinstance(evidence, dict):
                assert evidence.get("status") == EXTERNAL_EVIDENCE_STATUS, (
                    row["handoff"],
                    evidence,
                )
                external_ref = evidence.get("external_ref")
                assert external_ref, (row["handoff"], evidence)
                assert not Path(evidence.get("path", "")).is_absolute(), (row["handoff"], evidence)
                if Path(external_ref).is_absolute():
                    ref = Path(external_ref)
                    assert any(
                        ref == root or ref.is_relative_to(root) for root in EXTERNAL_EVIDENCE_ROOTS
                    ), (row["handoff"], evidence)
            candidates = _evidence_candidates(evidence)
            resolved = [candidate for candidate in candidates if candidate.is_file()]
            if resolved:
                continue
            assert isinstance(evidence, dict), (row["handoff"], evidence)
            assert evidence["status"] == EXTERNAL_EVIDENCE_STATUS, (row["handoff"], evidence)


def test_h4_h6_h7_stay_open_and_are_not_signed() -> None:
    index = _read(H7_INDEX)
    by_gate = {row["handoff"]: row for row in index["rows"]}
    for gate in ("H4", "H6", "H7"):
        assert by_gate[gate]["result"] == "NOT_VERIFIED", gate
        assert by_gate[gate]["gate_status"] == "OPEN", gate
        assert by_gate[gate]["signed_by"] in ([], ["B"]), gate
    assert {row["handoff"] for row in index["open_rows"]} == {"H4", "H6", "H7"}


def test_no_row_is_signed_by_a_non_owner() -> None:
    index = _read(H7_INDEX)
    for row in index["rows"]:
        assert set(row["signed_by"]) <= {row["owner"]}, row["handoff"]
        if row["gate_status"] == "OPEN":
            assert "A" not in row["signed_by"] or row["owner"] == "A"
            assert "C" not in row["signed_by"] or row["owner"] == "C"
    open_rows = _read(BASE / "B/B26/open-rows.json")
    assert open_rows["non_owner_signature_count"] == 0
    for row in open_rows["rows"]:
        if row["status"].startswith("NOT_VERIFIED"):
            assert row["owner"] in {"A", "C", "B"}
    assert open_rows["h7_status"] == "OPEN"


def test_every_reused_pass_has_an_applicability_mapping() -> None:
    index = _read(H7_INDEX)
    checked = 0
    for row in index["rows"]:
        if row["result"] != "PASS":
            continue
        mapping = row["reuse_mapping"]
        assert mapping, f"{row['handoff']} is a reused PASS without a mapping"
        for entry in mapping:
            assert entry["receipt"] and entry["rationale"], entry
            assert entry["receipt_sha256"], entry
            assert entry["unchanged"], entry
            path = REPO_ROOT / entry["receipt"]
            assert path.is_file(), entry["receipt"]
            assert _sha256(path) == entry["receipt_sha256"], entry["receipt"]
            checked += 1
    for row in index["tested_matrix"]:
        if row["result"] != "PASS":
            continue
        assert row["reuse_mapping"], row["row_id"]
        for entry in row["reuse_mapping"]:
            path = REPO_ROOT / entry["receipt"]
            assert path.is_file(), entry["receipt"]
            assert _sha256(path) == entry["receipt_sha256"], entry["receipt"]
            assert entry["unchanged"], entry["receipt"]
            checked += 1
    assert checked >= 6


def test_production_was_not_deployed_or_restarted() -> None:
    index = _read(H7_INDEX)
    assert index["production_deployed"] is False
    assert index["cleanup"]["production_deployed"] is False
    assert index["cleanup"]["production_service_restarted"] is False
    cleanup = _read(INTEGRATION / "cleanup.json")
    assert cleanup["production_deployed"] is False
    assert cleanup["production_service_restarted"] is False
    assert cleanup["active_test_jobs"] == []
    assert cleanup["active_test_pids"] == []
    assert cleanup["test_processes"] == []
    assert cleanup["listening_test_ports"] == []
    assert cleanup["reconciled_stale_jobs"][0]["now"] == "cancelled"


def test_release_fix5_digests_resolve() -> None:
    manifest = _read(RELEASE_DIR / "release-manifest.json")
    source = _read(RELEASE_DIR / "source.json")
    candidate = _read(INTEGRATION / "release-candidate.json")
    index = _read(H7_INDEX)
    assert manifest["version"] == "0.3.0"
    assert manifest["source"]["revision"] == source["source_revision"]
    assert index["source_candidate"]["release_source_revision"] == source["source_revision"]
    assert source["source_revision"] == "6ec01ec96fa81b89bec8ff321c38e3db2dc154d2"
    assert len(manifest["artifacts"]) == 6
    for name, digest in manifest["artifacts"].items():
        artifact = RELEASE_DIR / name
        assert artifact.is_file(), name
        assert _sha256(artifact) == digest, name
        recorded = candidate["artifacts"][name]
        assert recorded["sha256"] == digest, name
        assert recorded["size_bytes"] == artifact.stat().st_size, name
    for name in (
        "root-build/acp-0.3.0-py3-none-any.whl",
        "root-build/acp-0.3.0.tar.gz",
    ):
        artifact = RELEASE_DIR / name
        assert artifact.is_file(), name
        recorded = candidate["artifacts"][name]
        assert recorded["sha256"] == _sha256(artifact), name
        assert recorded["size_bytes"] == artifact.stat().st_size, name
    assert len(candidate["artifacts"]) == 8
    assert candidate["digests_verified"] is True
    assert candidate["production_deployed"] is False


def test_source_and_client_revisions_are_distinguished() -> None:
    index = _read(H7_INDEX)
    source = index["source_candidate"]
    assert source["landed_head"] != source["release_source_revision"]
    assert source["raw_receipt_shas_need_not_match"] is True
    assert index["client_revision"]["value"] == "39d4aeb20451c984e20d27e12270a15070adce12"
    assert "absent" in index["client_revision"]["status"]
    # The index is a frozen historical record of candidate bb45a46: its
    # harness_revision is the digest that candidate shipped.  M0-03 changes
    # acp2_runtime.py (required observations), so the recorded digest is pinned
    # as history instead of being re-derived from the current working tree.
    assert index["harness_revision"]["value"] == (
        "b5499a1ce889392b16f7724b556636a1cfa283d664ab1d890ca366c49fea5e8f"
    )
    live_harness_digest = _sha256(REPO_ROOT / "scripts/acceptance/acp2_runtime.py")
    assert live_harness_digest != index["harness_revision"]["value"]
    assert len(live_harness_digest) == 64
    assert index["evidence_revision"]["value"]
    assert index["c35"]["disposition"] in {"c35_absent", "C35_INPUT_CYCLE"}
    assert index["c35"]["disposition"] == "c35_absent"
