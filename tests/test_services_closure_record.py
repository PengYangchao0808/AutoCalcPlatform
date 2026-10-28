"""Checker for plan todo 69 (B13-4): layered Services closure record.

Consumer-only doc-consistency checker. It parses the machine-readable block in
``docs/reference/current/ACP_Services_Closure_20261019.md`` and enforces the
acceptance rules for the three-layer closure record:

* three layers present and separated — module verification / default-entry
  integration / real-function acceptance;
* every row uses one of the three verdicts ``PASS`` / ``FAIL`` /
  ``NOT_VERIFIED``;
* every referenced evidence path exists (relative to the repo root);
  ``.omo/`` paths are skipped gracefully when the local evidence tree is absent
  (``.omo/`` is gitignored);
* every ``NOT_VERIFIED`` row carries a non-empty reason and a known channel;
* no mock-as-real claims: ``claim="real"`` rows must carry a *tracked* evidence
  path, ``claim="in_repo"`` rows may not be presented as real, and
  ``claim="none"`` rows must be ``NOT_VERIFIED``;
* the four-combination record is consumed (not re-executed) from todo 75;
* scientific equivalence is C's call (``C10``) or explicitly ``NOT_VERIFIED``,
  and frontend simulation is never accepted as proof;
* A and C receipt tasks plus the deferral scope are registered, and every
  deferral states that the old functionality remains available.

No product code is imported; this is a pure doc/record consistency checker.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

_THIS = Path(__file__).resolve().parent
_REPO_ROOT = _THIS.parent
_DOC = _REPO_ROOT / "docs" / "reference" / "current" / "ACP_Services_Closure_20261019.md"

_BEGIN = "<!-- SERVICES_CLOSURE_RECORD_BEGIN -->"
_END = "<!-- SERVICES_CLOSURE_RECORD_END -->"
_SCHEMA = "acp.services.layered_closure_record"
_LAYERS = (
    "module_verification",
    "default_entry_integration",
    "real_function_acceptance",
)
_STATES = frozenset({"PASS", "FAIL", "NOT_VERIFIED"})
_CLAIMS = frozenset({"real", "in_repo", "none"})
_CHANNELS_OK = frozenset({"A05", "C10", "B", "A04-lane", "C01"})
_GATES = ("browser", "scientific_software", "lsf", "production")
_FOUR = ("F0+S0", "F1+S0", "F0+S1", "F1+S1")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX16 = re.compile(r"^[0-9a-f]{16}$")
_JSON_FENCE = re.compile(r"```json\s*(?P<body>.*?)```", re.DOTALL)


# ── parsing ─────────────────────────────────────────────────────────────────


def _parse(text: str) -> dict[str, Any]:
    """Extract and JSON-parse the record block between the markers."""
    if _BEGIN not in text or _END not in text:
        raise AssertionError("closure record markers missing")
    block = text.split(_BEGIN, 1)[1].split(_END, 1)[0]
    match = _JSON_FENCE.search(block)
    if not match:
        raise AssertionError("json fenced block missing between markers")
    return json.loads(match.group("body"))


def _load() -> tuple[dict[str, Any], str]:
    text = _DOC.read_text(encoding="utf-8")
    records = _parse(text)
    _validate(records, text)
    return records, text


# ── digest helpers (same algorithm documented in the record) ────────────────


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _dir_digest(directory: Path) -> tuple[int, str]:
    """sha256 over ``relpath_bytes + file_bytes`` for every file, sorted by relpath.

    This is the same accumulation the todo-75 four-combination record used, so
    both records can be cross-checked without a re-derivation.
    """
    files = sorted(p for p in directory.rglob("*") if p.is_file())
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(path.read_bytes())
    return len(files), digest.hexdigest()[:16]


def _check_digest_entry(entry: dict[str, Any], label: str) -> None:
    rel = entry["path"]
    assert isinstance(rel, str) and rel, f"{label}: digest entry needs a path"
    target = _REPO_ROOT / rel
    assert target.exists(), f"{label}: digest target missing: {rel}"
    assert _HEX16.match(str(entry["sha256_16"])), f"{label}: digest must be sha256[0:16]"
    if entry["kind"] == "file":
        assert _file_digest(target) == entry["sha256_16"], f"{label}: file digest drift"
    elif entry["kind"] == "dir":
        count, digest = _dir_digest(target)
        assert count == entry["files"], f"{label}: file count drift ({count} != {entry['files']})"
        assert digest == entry["sha256_16"], f"{label}: dir digest drift"
    else:  # pragma: no cover - defensive
        raise AssertionError(f"{label}: unknown digest kind {entry['kind']!r}")


def _is_tracked_evidence(rel: str) -> bool:
    """``.omo/`` evidence is local-only (gitignored) and never counts as tracked."""
    return not rel.startswith(".omo/")


# ── validation (shared by positive + negative-control tests) ────────────────


def _validate(records: dict[str, Any], text: str | None = None) -> None:
    assert records.get("schema") == _SCHEMA, "unexpected schema tag"
    assert records.get("schema_version") == 1, "unexpected schema version"
    assert records.get("plan_todo") == 69, "record must declare plan_todo 69"

    binding = records["binding"]
    assert _HEX40.match(str(binding["commit"])), "binding.commit must be 40-hex"
    versions = binding["contract_versions"]
    assert versions["first_slice_contract_version"] == 1
    assert _HEX16.match(str(versions["first_slice_contract_doc_sha256_16"]))
    assert _HEX16.match(str(versions["a04_events_contract_sha256_16"]))
    assert versions["a04_status"] == "DEFERRED"
    assert isinstance(binding.get("config"), str) and binding["config"]

    verified = binding["verified_digests"]
    assert set(verified) == {
        "first_slice_contract_doc",
        "a04_events_contract_doc",
        "contract_samples",
        "frames_mapping",
    }, "binding must verify contract docs plus the two fixture digests"
    for label, entry in verified.items():
        _check_digest_entry(entry, f"verified_digests.{label}")

    recorded = binding["recorded_digests"]
    for label, entry in recorded.items():
        assert _HEX16.match(str(entry["sha256_16"])), f"recorded_digests.{label}: bad digest"
        target = _REPO_ROOT / entry["path"]
        assert target.exists(), f"recorded_digests.{label}: missing {entry['path']}"
        assert _file_digest(target) == entry["sha256_16"], (
            f"recorded_digests.{label}: digest drift for {entry['path']}"
        )
        if "commit" in entry:
            assert _HEX40.match(str(entry["commit"]))

    # ── three layers, three states ─────────────────────────────────────────
    layers = records["layers"]
    assert set(layers) == set(_LAYERS), f"exactly three layers required, got {sorted(layers)}"

    seen_ids: set[str] = set()
    counts = {"PASS": 0, "FAIL": 0, "NOT_VERIFIED": 0}
    nv_channels: set[str] = set()

    for layer_key in _LAYERS:
        layer = layers[layer_key]
        assert layer.get("id"), f"{layer_key}: layer id required"
        rows = layer.get("rows")
        assert isinstance(rows, list) and rows, f"{layer_key}: at least one row required"
        for row in rows:
            rid = row["id"]
            assert rid not in seen_ids, f"duplicate row id {rid}"
            seen_ids.add(rid)
            assert row["item"].strip(), f"{rid}: item required"
            status = row["status"]
            assert status in _STATES, f"{rid}: status {status!r} not in {sorted(_STATES)}"
            counts[status] += 1

            claim = row.get("claim")
            assert claim in _CLAIMS, f"{rid}: claim {claim!r} not recognised"
            evidence = row.get("evidence") or []
            assert isinstance(evidence, list), f"{rid}: evidence must be a list"

            if status in {"PASS", "FAIL"}:
                assert evidence, f"{rid}: executed rows must cite evidence"
            if claim == "none":
                assert status == "NOT_VERIFIED", (
                    f"{rid}: claim='none' rows must be NOT_VERIFIED (no mock-as-real)"
                )
            if claim == "real":
                assert status == "PASS", f"{rid}: claim='real' must be an executed PASS"
                assert any(_is_tracked_evidence(str(p)) for p in evidence), (
                    f"{rid}: claim='real' requires at least one tracked evidence path "
                    "(.omo/ evidence is local-only and cannot prove a real claim)"
                )
            if claim == "in_repo":
                # in-repo rows may pass, but must never be phrased as a real result.
                blob = json.dumps(row, ensure_ascii=False).lower()
                assert "real_science" not in blob, f"{rid}: in_repo row claims real science"
            if status == "NOT_VERIFIED":
                reason = row.get("reason")
                assert isinstance(reason, str) and reason.strip(), (
                    f"{rid}: NOT_VERIFIED rows must carry a per-item reason"
                )
                channel = row.get("channel")
                assert channel in _CHANNELS_OK, (
                    f"{rid}: NOT_VERIFIED channel {channel!r} not recognised"
                )
                nv_channels.add(str(channel))
            if status == "FAIL":
                reason = row.get("reason")
                assert isinstance(reason, str) and reason.strip(), (
                    f"{rid}: FAIL rows must carry a failure reason"
                )

    assert counts["NOT_VERIFIED"] > 0, "a closure record with no NOT_VERIFIED rows is dishonest"
    assert {"A05", "C10"} <= nv_channels, "browser (A05) and science (C10) channels required"

    # every layer must actually separate: L3 must not be a copy of L1
    l1_ids = {r["id"] for r in layers["module_verification"]["rows"]}
    l3_ids = {r["id"] for r in layers["real_function_acceptance"]["rows"]}
    assert l1_ids.isdisjoint(l3_ids), "layers must be separated (duplicate ids across L1/L3)"

    # ── environment gates ─────────────────────────────────────────────────
    gates = records["environment_gates"]
    assert set(gates) == set(_GATES), f"environment gates must be {_GATES}"
    expected_channel = {
        "browser": "A05",
        "scientific_software": "C10",
        "lsf": "C10",
        "production": "B",
    }
    for name in _GATES:
        gate = gates[name]
        assert gate["status"] in _STATES, f"gate {name}: bad status"
        assert gate["channel"] == expected_channel[name], (
            f"gate {name}: expected channel {expected_channel[name]}"
        )
        if gate["status"] == "NOT_VERIFIED":
            assert gate["reason"].strip(), f"gate {name}: NOT_VERIFIED needs a reason"
        assert gate.get("evidence"), f"gate {name}: evidence required"

    # ── four-combination consumption (no re-execution) ────────────────────
    consumes = records["consumes"]
    assert consumes["path"].endswith("ACP_Services_Four_Combination_Acceptance.md")
    assert _HEX40.match(str(consumes["commit"]))
    assert consumes["combination_execution_owner"] == "plan todo 75"
    assert consumes["re_executed_here"] is False, "todo 69 must not re-run the combinations"
    assert tuple(consumes["combinations"]) == _FOUR, "all four combinations must be consumed"
    assert set(consumes["combination_status"]) == set(_FOUR)

    science = records["scientific_equivalence"]
    assert science["method"].strip(), "scientific equivalence method required"
    assert science["frontend_simulation_used"] is False, (
        "frontend simulation is not acceptable as scientific proof"
    )
    if science["status"] == "NOT_VERIFIED":
        assert science["channel"] == "C10", "science NOT_VERIFIED must use channel C10"
        assert science["reason"].strip()
        assert science["reference"], "science NOT_VERIFIED must still cite C's evidence"
        assert _HEX40.match(str(science["reference_commit"]))
    else:
        assert science["status"] == "PASS"
        assert science["reference_commit"], "verified science must cite a C record"

    # ── A/C receipts and deferrals ────────────────────────────────────────
    receipts = records["lane_receipts"]
    by_lane = {r["lane"]: r for r in receipts}
    assert {"A", "C"} <= set(by_lane), "A and C receipt tasks must be registered"
    assert by_lane["A"]["task"] == "A05"
    assert by_lane["C"]["task"] == "C10"
    for rec in receipts:
        assert rec["status"] == "REGISTERED", "receipt tasks must be REGISTERED"
        assert rec["deliverable"].strip()
        assert rec["deferral_scope"].strip()

    deferrals = records["deferrals"]
    assert deferrals, "deferrals must be listed"
    for item in deferrals:
        assert item["item"].strip()
        assert item["status"] in {"DEFERRED", "NOT_VERIFIED"}
        assert item["old_functionality_kept"].strip(), (
            f"{item['item']}: unintegrated items must state the old functionality stays available"
        )
        for field in ("owner", "blocker", "entry_condition", "next_gate"):
            assert isinstance(item.get(field), str) and item[field].strip(), (
                f"{item['item']}: deferred capability must carry {field}"
            )

    # ── evidence index: every path must resolve ──────────────────────────
    index = records["evidence_index"]
    assert index, "evidence index required"
    for entry in index:
        rel = entry["path"]
        assert entry["kind"] in {"tracked", "local"}
        if entry["kind"] == "tracked":
            assert _is_tracked_evidence(rel), f"{rel}: tracked entries must not live under .omo/"
        target = _REPO_ROOT / rel
        if rel.startswith(".omo/"):
            # .omo/ is gitignored: skip gracefully when the local tree is absent.
            continue
        assert target.exists(), f"evidence path missing: {rel}"

    # every row evidence path must also resolve (same graceful .omo/ skip)
    for layer_key in _LAYERS:
        for row in layers[layer_key]["rows"]:
            for rel in row.get("evidence") or []:
                if str(rel).startswith(".omo/"):
                    continue
                assert (_REPO_ROOT / str(rel)).exists(), f"{row['id']}: missing evidence {rel}"

    # ── prose requirements ───────────────────────────────────────────────
    if text is not None:
        for phrase in ("模块验证", "默认入口接入", "真实功能验收"):
            assert phrase in text, f"prose must name the layer {phrase}"
        for state in _STATES:
            assert state in text, f"prose must use the state word {state}"
        assert "已交字段" in text and "前端已接入" in text, (
            "prose must distinguish 'field delivered' from 'frontend integrated'"
        )
        assert "S0" in text and "S1" in text, "prose must state the S0/S1 evidence boundary"
        assert "禁前端模拟" in text or "frontend simulation is not accepted" in text.lower(), (
            "prose must forbid frontend simulation as scientific proof"
        )


# ── positive tests ──────────────────────────────────────────────────────────


def test_doc_exists_and_record_parses() -> None:
    records, _ = _load()
    assert records["plan_todo"] == 69


def test_three_layers_are_present_and_separated() -> None:
    records, _ = _load()
    assert tuple(records["layers"]) == _LAYERS
    for key in _LAYERS:
        assert records["layers"][key]["rows"]


def test_every_row_uses_one_of_three_states() -> None:
    records, _ = _load()
    statuses = {row["status"] for key in _LAYERS for row in records["layers"][key]["rows"]}
    assert statuses <= _STATES
    assert "PASS" in statuses and "NOT_VERIFIED" in statuses


def test_every_not_verified_row_has_reason_and_channel() -> None:
    records, _ = _load()
    for key in _LAYERS:
        for row in records["layers"][key]["rows"]:
            if row["status"] == "NOT_VERIFIED":
                assert row["reason"].strip()
                assert row["channel"] in _CHANNELS_OK


def test_environment_gates_split_three_states() -> None:
    records, _ = _load()
    gates = records["environment_gates"]
    assert gates["browser"]["channel"] == "A05"
    assert gates["scientific_software"]["channel"] == "C10"
    assert gates["lsf"]["channel"] == "C10"
    assert gates["production"]["channel"] == "B"
    for name in _GATES:
        assert gates[name]["status"] in _STATES


def test_no_mock_as_real() -> None:
    records, _ = _load()
    for key in _LAYERS:
        for row in records["layers"][key]["rows"]:
            if row["claim"] == "real":
                assert row["status"] == "PASS"
                assert any(_is_tracked_evidence(str(p)) for p in row["evidence"])
            if row["claim"] == "none":
                assert row["status"] == "NOT_VERIFIED"


def test_binding_verifies_contract_versions_and_fixture_digests() -> None:
    records, _ = _load()
    binding = records["binding"]
    assert _HEX40.match(binding["commit"])
    assert binding["contract_versions"]["first_slice_contract_version"] == 1
    for entry in binding["verified_digests"].values():
        assert _HEX16.match(entry["sha256_16"])


def test_four_combination_record_is_consumed_not_reexecuted() -> None:
    records, _ = _load()
    consumes = records["consumes"]
    assert consumes["combination_execution_owner"] == "plan todo 75"
    assert consumes["re_executed_here"] is False
    assert tuple(consumes["combinations"]) == _FOUR


def test_scientific_equivalence_is_c_call_or_not_verified() -> None:
    records, _ = _load()
    science = records["scientific_equivalence"]
    assert science["frontend_simulation_used"] is False
    assert science["status"] in {"PASS", "NOT_VERIFIED"}
    if science["status"] == "NOT_VERIFIED":
        assert science["channel"] == "C10"
        assert science["reason"].strip()


def test_a_and_c_receipts_and_deferrals_registered() -> None:
    records, _ = _load()
    lanes = {r["lane"]: r for r in records["lane_receipts"]}
    assert lanes["A"]["task"] == "A05"
    assert lanes["C"]["task"] == "C10"
    assert records["deferrals"]
    for item in records["deferrals"]:
        assert item["old_functionality_kept"].strip()
        for field in ("owner", "blocker", "entry_condition", "next_gate"):
            assert item[field].strip()


def test_deferred_capability_handoffs_are_registered() -> None:
    records, _ = _load()
    items = {entry["item"] for entry in records["deferrals"]}
    assert {
        "A04 refresh protocol",
        "MCP default-entry binding",
        "archive export/import CLI-API binding",
        "metering production wiring",
        "frontend cache-only consumption",
        "admission/quota pre-dispatch wiring",
        "Lab identity HTTP/UI (todo 73)",
    } <= items
    for entry in records["deferrals"]:
        assert entry["status"] in {"DEFERRED", "NOT_VERIFIED"}


def test_negative_control_deferral_missing_owner_rejected() -> None:
    records, _ = _load()
    bad = copy.deepcopy(records)
    bad["deferrals"][0]["owner"] = ""
    with pytest.raises(AssertionError):
        _validate(bad)


def test_evidence_paths_exist() -> None:
    records, _ = _load()
    missing = []
    for entry in records["evidence_index"]:
        rel = entry["path"]
        if rel.startswith(".omo/"):
            continue
        if not (_REPO_ROOT / rel).exists():
            missing.append(rel)
    assert not missing, f"missing evidence paths: {missing}"


def test_prose_names_layers_states_and_field_vs_integration() -> None:
    _, text = _load()
    for phrase in ("模块验证", "默认入口接入", "真实功能验收", "已交字段", "前端已接入"):
        assert phrase in text
    for state in _STATES:
        assert state in text


# ── negative controls (prove the validator can fail) ────────────────────────


def test_negative_control_missing_layer_rejected() -> None:
    records, _ = _load()
    bad = copy.deepcopy(records)
    bad["layers"].pop("real_function_acceptance")
    with pytest.raises(AssertionError):
        _validate(bad)


def test_negative_control_not_verified_without_reason_rejected() -> None:
    records, _ = _load()
    bad = copy.deepcopy(records)
    row = next(
        r for key in _LAYERS for r in bad["layers"][key]["rows"] if r["status"] == "NOT_VERIFIED"
    )
    row["reason"] = ""
    with pytest.raises(AssertionError):
        _validate(bad)


def test_negative_control_mock_as_real_rejected() -> None:
    records, _ = _load()
    bad = copy.deepcopy(records)
    bad["layers"]["module_verification"]["rows"][0]["claim"] = "real"
    bad["layers"]["module_verification"]["rows"][0]["evidence"] = [
        ".omo/evidence/acp-parallel-plan-2-services/task-67-acp-parallel-plan-2-services.md"
    ]
    with pytest.raises(AssertionError):
        _validate(bad)


def test_negative_control_frontend_simulation_rejected() -> None:
    records, _ = _load()
    bad = copy.deepcopy(records)
    bad["scientific_equivalence"]["frontend_simulation_used"] = True
    with pytest.raises(AssertionError):
        _validate(bad)


def test_negative_control_missing_receipt_rejected() -> None:
    records, _ = _load()
    bad = copy.deepcopy(records)
    bad["lane_receipts"] = [r for r in bad["lane_receipts"] if r["lane"] != "C"]
    with pytest.raises(AssertionError):
        _validate(bad)


def test_negative_control_reexecution_rejected() -> None:
    records, _ = _load()
    bad = copy.deepcopy(records)
    bad["consumes"]["re_executed_here"] = True
    with pytest.raises(AssertionError):
        _validate(bad)


def test_negative_control_digest_drift_rejected() -> None:
    records, _ = _load()
    bad = copy.deepcopy(records)
    bad["binding"]["verified_digests"]["contract_samples"]["sha256_16"] = "0" * 16
    with pytest.raises(AssertionError):
        _validate(bad)


def test_negative_control_missing_old_functionality_rejected() -> None:
    records, _ = _load()
    bad = copy.deepcopy(records)
    bad["deferrals"][0]["old_functionality_kept"] = ""
    with pytest.raises(AssertionError):
        _validate(bad)
