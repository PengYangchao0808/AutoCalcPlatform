"""Checker for plan todo 75 (B13-7): four-combination joint acceptance records.

Consumer-only test. It parses the machine-readable record block in
``docs/reference/current/ACP_Services_Four_Combination_Acceptance.md`` and
enforces the acceptance rules for the four-combination joint acceptance
(F0+S0 / F1+S0 / F0+S1 / F1+S1):

* all four combinations present (no single-combo substitution);
* every *executed* record is bound to the document's ``binding.commit``
  (no cross-commit splicing);
* historical references (other commits) are labelled and never used as an
  S1 pass record (S0 records cannot prove S1);
* every ``NOT_VERIFIED`` row carries a non-empty reason and a known channel;
* scientific equivalence references a C judgement, or is explicitly
  ``NOT_VERIFIED`` on channel ``C10``;
* lane A/C receipt tasks and their deferral scope are registered;
* the prose states that A04 is not enabled (delivery = frontend-compat
  refactor) and that F0/S0 baseline passing does not imply new prepare/account UI.

No product code is imported; this is a pure doc/record consistency checker.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

_THIS = Path(__file__).resolve().parent
_DOC = (
    _THIS.parent / "docs" / "reference" / "current" / "ACP_Services_Four_Combination_Acceptance.md"
)

_BEGIN = "<!-- FOUR_COMBINATION_RECORDS_BEGIN -->"
_END = "<!-- FOUR_COMBINATION_RECORDS_END -->"
_SCHEMA = "acp.services.four_combination_records"
_FOUR = ("F0+S0", "F1+S0", "F0+S1", "F1+S1")
_S1_COMBOS = ("F0+S1", "F1+S1")
_CHANNELS_OK = frozenset({"A05", "C10", "B", "A04-lane"})
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX16 = re.compile(r"^[0-9a-f]{16}$")
_JSON_FENCE = re.compile(r"```json\s*(?P<body>.*?)```", re.DOTALL)


# ── parsing ─────────────────────────────────────────────────────────────────


def _parse(text: str) -> dict[str, Any]:
    """Extract and JSON-parse the record block between the markers."""
    if _BEGIN not in text or _END not in text:
        raise AssertionError("record markers missing")
    block = text.split(_BEGIN, 1)[1].split(_END, 1)[0]
    match = _JSON_FENCE.search(block)
    if not match:
        raise AssertionError("json fenced block missing between markers")
    return json.loads(match.group("body"))


def _load() -> tuple[dict[str, Any], str]:
    text = _DOC.read_text(encoding="utf-8")
    records = _parse(text)
    _validate(records)
    return records, text


# ── validation (shared by positive + negative-control tests) ────────────────


def _validate(records: dict[str, Any]) -> None:
    assert records.get("schema") == _SCHEMA, "unexpected schema tag"
    assert records.get("schema_version") == 1, "unexpected schema version"
    assert records.get("plan_todo") == 75, "record must declare plan_todo 75"

    binding = records["binding"]
    assert _HEX40.match(str(binding["commit"])), "binding.commit must be 40-hex"
    versions = binding["contract_versions"]
    assert versions["first_slice_contract_version"] == 1
    assert _HEX16.match(str(versions["first_slice_contract_doc_sha256_16"]))
    assert versions["a04_status"] == "DEFERRED"
    assert isinstance(binding.get("config"), str) and binding["config"]
    digests = binding["fixture_digests"]
    for key in ("contract_samples", "frames_mapping", "cache_only_switch_matrix"):
        entry = digests[key]
        assert entry["files"] >= 1
        assert _HEX16.match(str(entry["sha256_16"])), f"{key} digest not sha256[0:16]"

    combos = records["combinations"]
    ids = [c["id"] for c in combos]
    assert len(ids) == len(set(ids)) == 4, "exactly four distinct combinations required"
    assert set(ids) == set(_FOUR), f"combination set mismatch: {ids}"

    for combo in combos:
        cid = combo["id"]
        assert "covered_by" not in combo, f"{cid}: single-combo substitution forbidden"
        caps = combo.get("expected_capabilities")
        assert isinstance(caps, list) and caps, f"{cid}: expected capabilities required"
        assert all(isinstance(c, str) and c.strip() for c in caps), f"{cid}: empty capability"

        executed = combo.get("executed") or []
        assert executed, f"{cid}: at least one executed record required"
        for rec in executed:
            assert rec["commit"] == binding["commit"], (
                f"{cid}: executed record commit {rec['commit']} != binding commit "
                "(cross-commit splicing forbidden)"
            )
            assert isinstance(rec.get("command"), str) and rec["command"]
            assert isinstance(rec.get("result"), str) and rec["result"]

        # References may bind other commits, but must be explicitly labelled and
        # must never be counted as executed pass records.
        for ref in combo.get("references") or []:
            assert isinstance(ref.get("note"), str) and ref["note"].strip(), (
                f"{cid}: reference to {ref.get('path')} lacks a note"
            )
            if ref.get("commit") != binding["commit"]:
                assert _HEX40.match(str(ref["commit"])), f"{cid}: bad reference commit"

        for nv in combo.get("not_verified") or []:
            assert isinstance(nv.get("item"), str) and nv["item"].strip(), f"{cid}: NV item"
            assert isinstance(nv.get("reason"), str) and nv["reason"].strip(), (
                f"{cid}: NOT_VERIFIED row must carry a reason"
            )
            assert nv.get("channel") in _CHANNELS_OK, (
                f"{cid}: NOT_VERIFIED channel {nv.get('channel')!r} not recognised"
            )

    # S0 records cannot prove S1: S1 combos need their own executed evidence on
    # the binding commit (already enforced above), and must not rely solely on a
    # historical reference.
    by_id = {c["id"]: c for c in combos}
    for cid in _S1_COMBOS:
        assert by_id[cid]["executed"], f"{cid}: S1 needs executed evidence, not an S0 reference"

    science = records["scientific_equivalence"]
    if science["status"] == "NOT_VERIFIED":
        assert science.get("channel") == "C10", "science NOT_VERIFIED must use channel C10"
        assert isinstance(science.get("reason"), str) and science["reason"].strip()
        assert isinstance(science.get("reference"), str) and science["reference"]
    else:
        assert science.get("reference_commit"), "verified science must cite a C record"

    receipts = records["lane_receipts"]
    lanes = {r["lane"] for r in receipts}
    assert {"A", "C"} <= lanes, "A and C receipt tasks must be registered"
    for rec in receipts:
        assert rec.get("status") == "REGISTERED", "receipt tasks must be REGISTERED"
        assert isinstance(rec.get("deferral_scope"), str) and rec["deferral_scope"].strip()

    deferrals = {d["item"]: d["status"] for d in records["deferrals"]}
    assert any("A04" in k for k in deferrals), "A04 deferral must be registered"
    assert any("Lab" in k or "todo 73" in k for k in deferrals), "Lab/todo-73 status required"


# ── positive tests ──────────────────────────────────────────────────────────


def test_doc_exists_and_records_parse() -> None:
    records, _ = _load()
    assert len(records["combinations"]) == 4


def test_all_four_combinations_present() -> None:
    records, _ = _load()
    assert [c["id"] for c in records["combinations"]] == list(_FOUR)


def test_no_single_combo_substitution() -> None:
    records, _ = _load()
    for combo in records["combinations"]:
        assert "covered_by" not in combo
        assert combo["executed"], f"{combo['id']} has no executed evidence"


def test_executed_records_bound_to_binding_commit() -> None:
    records, _ = _load()
    commit = records["binding"]["commit"]
    for combo in records["combinations"]:
        for rec in combo["executed"]:
            assert rec["commit"] == commit


def test_s1_combos_have_own_executed_evidence() -> None:
    records, _ = _load()
    for combo in records["combinations"]:
        if combo["id"] in _S1_COMBOS:
            assert combo["executed"], f"{combo['id']} lacks S1 evidence"


def test_not_verified_rows_have_reason_and_channel() -> None:
    records, _ = _load()
    seen = set()
    for combo in records["combinations"]:
        for nv in combo["not_verified"]:
            assert nv["reason"].strip()
            assert nv["channel"] in _CHANNELS_OK
            seen.add(nv["channel"])
    assert {"A05", "C10"} <= seen, "browser and scientific channels must be declared"


def test_scientific_equivalence_reference_or_not_verified() -> None:
    records, _ = _load()
    science = records["scientific_equivalence"]
    assert science["status"] == "NOT_VERIFIED"
    assert science["channel"] == "C10"
    assert science["reason"].strip()


def test_lane_receipts_cover_a_and_c() -> None:
    records, _ = _load()
    receipts = {r["lane"]: r for r in records["lane_receipts"]}
    assert receipts["A"]["task"] == "A05"
    assert receipts["C"]["task"] == "C10"
    for rec in receipts.values():
        assert rec["deferral_scope"].strip()


def test_binding_binds_commit_contract_config_and_fixtures() -> None:
    records, _ = _load()
    binding = records["binding"]
    assert _HEX40.match(binding["commit"])
    assert binding["contract_versions"]["first_slice_contract_version"] == 1
    assert binding["config"]
    for entry in binding["fixture_digests"].values():
        assert entry["files"] >= 1
        assert _HEX16.match(entry["sha256_16"])


def test_prose_declares_four_combos_and_a04_frontend_compat() -> None:
    _, text = _load()
    for cid in _FOUR:
        assert cid in text, f"prose must mention {cid}"
    assert "A04" in text
    assert "前端兼容重构" in text, "delivery must be stated as the frontend-compat refactor"


def test_prose_states_baseline_does_not_imply_new_capabilities() -> None:
    _, text = _load()
    assert "不代表" in text
    assert "prepare" in text
    assert "账户 UI" in text or "account UI" in text


# ── negative controls (prove the validator can fail) ────────────────────────


def test_negative_control_missing_combo_rejected() -> None:
    records, _ = _load()
    bad = copy.deepcopy(records)
    bad["combinations"] = [c for c in bad["combinations"] if c["id"] != "F1+S1"]
    with pytest.raises(AssertionError):
        _validate(bad)


def test_negative_control_cross_commit_splice_rejected() -> None:
    records, _ = _load()
    bad = copy.deepcopy(records)
    bad["combinations"][0]["executed"][0]["commit"] = "0" * 40
    with pytest.raises(AssertionError):
        _validate(bad)


def test_negative_control_not_verified_without_reason_rejected() -> None:
    records, _ = _load()
    bad = copy.deepcopy(records)
    bad["combinations"][0]["not_verified"][0]["reason"] = ""
    with pytest.raises(AssertionError):
        _validate(bad)


def test_negative_control_single_combo_covered_by_rejected() -> None:
    records, _ = _load()
    bad = copy.deepcopy(records)
    bad["combinations"][1]["covered_by"] = "F0+S0"
    with pytest.raises(AssertionError):
        _validate(bad)


def test_negative_control_missing_a04_deferral_rejected() -> None:
    records, _ = _load()
    bad = copy.deepcopy(records)
    bad["deferrals"] = [d for d in bad["deferrals"] if "A04" not in d["item"]]
    with pytest.raises(AssertionError):
        _validate(bad)
