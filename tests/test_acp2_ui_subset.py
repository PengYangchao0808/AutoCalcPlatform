"""M3-06 — the frozen ``acp2.ui-subset.v1`` capability-form contract.

This file is the Python half of the schema-driven form work (the Node half is
``frontend/js/module_tests/capability_form.test.js``).  It pins:

* the frozen contract documents under
  ``docs/reference/next-change/acp2-contracts/science/`` (they are *not*
  registered in the release contract manifest — see the M3-06 report);
* the digest freeze: ``subset_digest`` over the whitelist/binding/unit tables
  and one ``form_digest`` per form;
* parity between the three authorities that must agree on the whitelist — the
  docs contract, :mod:`acp.api.capability_forms` and the browser validator
  (``frontend/js/workbench/contracts/ui_subset.js``);
* the typed refusals for ``$ref``/combinators/unknown major/unknown keyword/
  unknown binding, i.e. a form the UI cannot interpret is never silently
  ignored and never submitted;
* the discovery projection (available / typed unavailable / read-only history)
  and the read-only HTTP route.
"""

from __future__ import annotations

import json
import re
from collections.abc import Generator, Mapping
from pathlib import Path
from typing import Any

import pytest

from acp.api import capability_forms as forms

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCIENCE = _REPO_ROOT / "docs/reference/next-change/acp2-contracts/science"
_SUBSET_DOC = _SCIENCE / "ui-subset.v1.json"
_FORMS_DOC = _SCIENCE / "ui-forms.v1.json"
_JS_VALIDATOR = _REPO_ROOT / "frontend/js/workbench/contracts/ui_subset.js"
_FIXTURE_FORM = _REPO_ROOT / "tests/fixtures/acp2/science/external_plugin/cccp_ui_form.json"

#: Frozen digests.  A change to any table/form must update these on purpose.
SUBSET_DIGEST = "sha256:870d59473be275fa52785896cdb7e09d03aa15855a8a29dc69b90839fbf08b06"
FORM_DIGESTS = {
    "electronic.energy": "sha256:5d3b3fc9b0702f2689919194ecca3353475ca6e0482abdc72c4a0d6314b14696",
    "geometry.optimize": "sha256:43e94751d7c18c6815ef05f510509373d87f189101932d44ef565598d30aae46",
    "vibration.analyze": "sha256:ccfc081f6637c64f67d0a796fbf410543c830da09399b2d31887a575563b57ba",
}
PLUGIN_FORM_DIGEST = "sha256:5fd46abe5c7c5d3c3b2fb9b3ffc4d1cb9962ae19c2180148de7d689f44f4c7d0"


def _document(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _js_array(source: str, name: str) -> list[str]:
    match = re.search(rf"var {name} = \[(.*?)\];", source, re.S)
    assert match is not None, f"array {name} not found in the JS validator"
    return re.findall(r'"([^"]+)"', match.group(1))


def _js_bindings(source: str) -> list[dict[str, Any]]:
    match = re.search(r"var BINDINGS = \[(.*?)\n  \];", source, re.S)
    assert match is not None, "BINDINGS not found in the JS validator"
    rows: list[dict[str, Any]] = []
    for block in re.findall(r"\{(.*?)\}", match.group(1), re.S):
        row: dict[str, Any] = {}
        for key, raw in re.findall(r"(\w+):\s*(\"[^\"]*\"|null|true|false)", block):
            raw = raw.strip()
            if raw == "null":
                row[key] = None
            elif raw == "true":
                row[key] = True
            elif raw == "false":
                row[key] = False
            else:
                row[key] = raw.strip('"')
        if row:
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 1. Frozen contract documents
# ---------------------------------------------------------------------------


def test_subset_contract_document_is_frozen_and_matches_the_module() -> None:
    document = _document(_SUBSET_DOC)
    assert document["schema"] == forms.UI_SUBSET_SCHEMA
    assert document["subset_version"] == forms.UI_SUBSET_VERSION
    assert document["subset_digest"] == SUBSET_DIGEST
    assert forms.subset_digest() == SUBSET_DIGEST

    table = forms.subset_table()
    assert document["keywords"] == table["keywords"]
    assert document["annotations"] == table["annotations"]
    assert document["rejected_keywords"] == table["rejected_keywords"]
    assert document["type_names"] == table["type_names"]
    assert document["formats"] == table["formats"]
    assert document["widgets"] == table["widgets"]
    assert document["units"] == table["units"]
    assert document["bindings"] == table["bindings"]
    assert document["error_codes"] == table["error_codes"]
    # The digest covers exactly the table, never the descriptive header.
    header = {
        key: value for key, value in document.items() if key not in table and key != "subset_digest"
    }
    assert header, "the contract document must carry a descriptive header"
    for key in table:
        del document[key]
    document.pop("subset_digest")
    assert forms._digest(document) != SUBSET_DIGEST  # the header is out of scope
    assert document  # nothing else was silently claimed as part of the digest


def test_form_contract_document_matches_the_platform_forms() -> None:
    document = _document(_FORMS_DOC)
    assert document["schema"] == forms.UI_FORMS_SCHEMA
    assert document["subset_version"] == forms.UI_SUBSET_VERSION
    assert document["subset_digest"] == SUBSET_DIGEST
    entries = {entry["capability_id"]: entry for entry in document["forms"]}
    assert set(entries) == set(FORM_DIGESTS)
    for capability_id, digest in FORM_DIGESTS.items():
        assert entries[capability_id]["form_digest"] == digest
        assert entries[capability_id]["form"]["form_digest"] == digest
        assert entries[capability_id]["origin"] == "platform"
    assert document["forms_digest"].startswith("sha256:")


def test_platform_forms_validate_and_keep_their_frozen_digest() -> None:
    for document in forms.PLATFORM_FORMS:
        assert document.digest == FORM_DIGESTS[document.capability_id]
        validated = forms.validate_form_document(document.form)
        assert validated is document.form
        assert forms.form_digest(document.form) == document.digest
    # A re-sealed copy reproduces the same digest (canonical hash, key order free).
    rebuilt = dict(forms.PLATFORM_FORMS[0].form)
    rebuilt.pop("form_digest")
    reordered = {key: rebuilt[key] for key in sorted(rebuilt, reverse=True)}
    assert forms.form_digest(reordered) == FORM_DIGESTS["electronic.energy"]


def test_third_party_fixture_form_is_the_documented_example() -> None:
    fixture = _document(_FIXTURE_FORM)
    document = _document(_FORMS_DOC)
    example = document["plugin_example_forms"][0]
    assert example["form"] == fixture
    assert fixture["form_digest"] == PLUGIN_FORM_DIGEST == example["form_digest"]
    assert forms.validate_form_document(fixture) is fixture
    assert fixture["capability_id"] == "acp2_external_native_singlepoint"


# ---------------------------------------------------------------------------
# 2. Whitelist parity across the three authorities
# ---------------------------------------------------------------------------


def test_python_and_browser_validators_share_the_whitelist() -> None:
    source = _JS_VALIDATOR.read_text(encoding="utf-8")
    assert sorted(_js_array(source, "ALLOWED_KEYWORDS")) == sorted(forms.ALLOWED_KEYWORDS)
    assert sorted(_js_array(source, "REJECTED_KEYWORDS")) == sorted(forms.REJECTED_KEYWORDS)
    assert sorted(_js_array(source, "ANNOTATIONS")) == sorted(forms.ANNOTATIONS)
    assert sorted(_js_array(source, "TYPE_NAMES")) == sorted(forms.TYPE_NAMES)
    assert sorted(_js_array(source, "FORMATS")) == sorted(forms.FORMATS)
    assert sorted(_js_array(source, "WIDGETS")) == sorted(forms.WIDGETS)
    js_bindings = _js_bindings(source)
    expected = [
        {
            "binding": row.id,
            "task_request": row.task_request,
            "request_body": row.request_body,
            "value_type": row.value_type,
            "unit": row.unit,
            "required": row.required,
        }
        for row in forms.BINDINGS
    ]
    assert js_bindings == expected
    # Every binding names a real TaskRequest path and a real wire location.
    assert {row.task_request for row in forms.BINDINGS} <= {
        "structure.source",
        "charge",
        "multiplicity",
        "level.method",
        "level.basis",
        "resources.nproc",
        "resources.mem",
    }
    assert all(row.request_body for row in forms.BINDINGS)


# ---------------------------------------------------------------------------
# 3. Typed refusals (nothing is ignored and submitted)
# ---------------------------------------------------------------------------


def _copy_form() -> dict[str, Any]:
    return json.loads(json.dumps(dict(forms.PLATFORM_FORMS[0].form)))


def _reseal(form: dict[str, Any]) -> dict[str, Any]:
    form.pop("form_digest", None)
    form["form_digest"] = forms.form_digest(form)
    return form


def test_ref_and_combinators_are_rejected() -> None:
    for keyword in ("$ref", "$defs", "allOf", "anyOf", "oneOf", "not", "if", "then", "else"):
        form = _copy_form()
        form["form_schema"]["properties"]["charge"][keyword] = [
            {"type": "integer"} if keyword in {"allOf", "anyOf", "oneOf"} else "x"
        ]
        _reseal(form)
        with pytest.raises(forms.CapabilityFormError) as excinfo:
            forms.validate_form_document(form)
        assert excinfo.value.code == "ui_keyword_rejected"
        assert excinfo.value.params["keyword"] == keyword
        assert excinfo.value.params["rule"] == "not_in_ui_subset"


def test_unknown_keyword_and_unknown_major_are_rejected() -> None:
    form = _copy_form()
    form["form_schema"]["properties"]["charge"]["transform"] = ["trim"]
    _reseal(form)
    with pytest.raises(forms.CapabilityFormError) as excinfo:
        forms.validate_form_document(form)
    assert excinfo.value.code == "ui_keyword_unknown"

    major = _copy_form()
    major["subset_version"] = "acp2.ui-subset.v2"
    _reseal(major)
    with pytest.raises(forms.CapabilityFormError) as subset_error:
        forms.validate_form_document(major)
    assert subset_error.value.code == "ui_subset_unknown_major"
    assert subset_error.value.params["supported"] == forms.UI_SUBSET_VERSION

    schema_major = _copy_form()
    schema_major["schema_version"] = 2
    _reseal(schema_major)
    with pytest.raises(forms.CapabilityFormError) as form_error:
        forms.validate_form_document(schema_major)
    assert form_error.value.code == "ui_form_unknown_major"


def test_binding_table_is_enforced() -> None:
    missing = _copy_form()
    del missing["form_schema"]["properties"]["charge"]["x-acp"]
    _reseal(missing)
    with pytest.raises(forms.CapabilityFormError) as missing_error:
        forms.validate_form_document(missing)
    assert missing_error.value.code == "ui_binding_missing"

    unknown = _copy_form()
    unknown["form_schema"]["properties"]["charge"]["x-acp"]["binding"] = "task.options.magic"
    _reseal(unknown)
    with pytest.raises(forms.CapabilityFormError) as unknown_error:
        forms.validate_form_document(unknown)
    assert unknown_error.value.code == "ui_binding_unknown"
    assert unknown_error.value.params["binding"] == "task.options.magic"

    unit = _copy_form()
    unit["form_schema"]["properties"]["charge"]["x-acp"]["unit"] = "kelvin"
    _reseal(unit)
    with pytest.raises(forms.CapabilityFormError) as unit_error:
        forms.validate_form_document(unit)
    assert unit_error.value.code == "ui_unit_mismatch"

    wrong_type = _copy_form()
    wrong_type["form_schema"]["properties"]["charge"]["type"] = "string"
    _reseal(wrong_type)
    with pytest.raises(forms.CapabilityFormError) as type_error:
        forms.validate_form_document(wrong_type)
    assert type_error.value.code == "ui_binding_type_mismatch"

    duplicate = _copy_form()
    duplicate["form_schema"]["properties"]["charge_copy"] = {
        "type": "integer",
        "x-acp": {"binding": "task.charge"},
    }
    _reseal(duplicate)
    with pytest.raises(forms.CapabilityFormError) as duplicate_error:
        forms.validate_form_document(duplicate)
    assert duplicate_error.value.code == "ui_binding_duplicate"


def test_malformed_forms_fail_closed() -> None:
    extra_property = _copy_form()
    extra_property["form_schema"]["additionalProperties"] = True
    _reseal(extra_property)
    with pytest.raises(forms.CapabilityFormError) as extra_error:
        forms.validate_form_document(extra_property)
    assert extra_error.value.code == "ui_form_malformed"

    required = _copy_form()
    required["form_schema"]["required"] = ["ghost"]
    _reseal(required)
    with pytest.raises(forms.CapabilityFormError) as required_error:
        forms.validate_form_document(required)
    assert required_error.value.code == "ui_required_undeclared"

    digest = _copy_form()
    digest["form_digest"] = "sha256:" + "0" * 64
    with pytest.raises(forms.CapabilityFormError) as digest_error:
        forms.validate_form_document(digest)
    assert digest_error.value.code == "ui_form_digest_mismatch"

    pattern = _copy_form()
    pattern["form_schema"]["properties"]["source"]["pattern"] = "(["
    _reseal(pattern)
    with pytest.raises(forms.CapabilityFormError) as pattern_error:
        forms.validate_form_document(pattern)
    assert pattern_error.value.code == "ui_form_malformed"

    format_ = _copy_form()
    format_["form_schema"]["properties"]["source"]["format"] = "binary"
    _reseal(format_)
    with pytest.raises(forms.CapabilityFormError) as format_error:
        forms.validate_form_document(format_)
    assert format_error.value.code == "ui_format_unknown"

    container = _copy_form()
    container["form_schema"]["properties"]["nested"] = {
        "type": "object",
        "properties": {"inner": {"type": "string", "x-acp": {"binding": "task.resources.mem"}}},
        "x-acp": {"binding": "task.charge"},
    }
    _reseal(container)
    with pytest.raises(forms.CapabilityFormError) as container_error:
        forms.validate_form_document(container)
    assert container_error.value.code == "ui_form_malformed"
    assert container_error.value.params["rule"] == "binding_on_container"


# ---------------------------------------------------------------------------
# 4. Third-party form reading (metadata only)
# ---------------------------------------------------------------------------


def test_plugin_form_is_read_from_the_installed_distribution() -> None:
    fixture_text = _FIXTURE_FORM.read_text(encoding="utf-8")
    seen: list[str] = []

    def reader(name: str) -> str:
        seen.append(name)
        return fixture_text

    forms.clear_form_cache()
    document = forms.plugin_form(
        "acp2-external-science-validator",
        "acp2_external_native_singlepoint",
        reader=reader,
        descriptor_digest="sha256:" + "a" * 64,
    )
    assert document is not None
    assert document.digest == PLUGIN_FORM_DIGEST
    assert document.origin == "plugin:acp2-external-science-validator"
    assert seen == ["acp2-external-science-validator"]
    # Cached per (distribution, capability, descriptor digest).
    again = forms.plugin_form(
        "acp2-external-science-validator",
        "acp2_external_native_singlepoint",
        reader=reader,
        descriptor_digest="sha256:" + "a" * 64,
    )
    assert again is document
    assert len(seen) == 1


def test_plugin_form_refusals_are_typed() -> None:
    forms.clear_form_cache()
    assert forms.plugin_form("no-such-distribution", "x", reader=lambda name: None) is None

    with pytest.raises(forms.CapabilityFormError) as unreadable:
        forms.plugin_form("dist", "x", reader=lambda name: "{not json")
    assert unreadable.value.code == "ui_form_unreadable"

    fixture = _FIXTURE_FORM.read_text(encoding="utf-8")
    forms.clear_form_cache()
    with pytest.raises(forms.CapabilityFormError) as wrong_capability:
        forms.plugin_form("dist", "another.capability", reader=lambda name: fixture)
    assert wrong_capability.value.code == "ui_form_malformed"

    pinned = json.loads(fixture)
    pinned["descriptor_digest_pin"] = "sha256:" + "b" * 64
    pinned.pop("form_digest")
    pinned["form_digest"] = forms.form_digest(pinned)
    forms.clear_form_cache()
    with pytest.raises(forms.CapabilityFormError) as mismatch:
        forms.plugin_form(
            "dist",
            "acp2_external_native_singlepoint",
            reader=lambda name: json.dumps(pinned),
            descriptor_digest="sha256:" + "c" * 64,
        )
    assert mismatch.value.code == "ui_form_descriptor_mismatch"


# ---------------------------------------------------------------------------
# 5. Discovery projection
# ---------------------------------------------------------------------------


class _Record:
    """Minimal enablement-ledger stand-in (the projection only reads)."""

    def __init__(self, capability_id: str, version: str, enabled: bool) -> None:
        self.capability_id = capability_id
        self.version = version
        self.enabled = enabled
        self.descriptor_digest = "sha256:" + "d" * 64
        self.descriptor = {"schema": "acp2.plugin.capability-descriptor.v1"}


def _descriptors() -> list[dict[str, Any]]:
    return [
        {"capability_id": "electronic.energy", "capability_source": "builtin"},
        {"capability_id": "geometry.optimize", "capability_source": "builtin"},
        {"capability_id": "vibration.analyze", "capability_source": "builtin"},
        {"capability_id": "vibration.compute", "capability_source": "builtin"},
        {
            "capability_id": "acp2_external_native_singlepoint",
            "capability_source": "science_plugin",
            "descriptor_digest": "sha256:" + "e" * 64,
            "plugin": {
                "distribution": "acp2-external-science-validator",
                "name": "acp2_external.native_singlepoint",
            },
        },
    ]


def test_projection_covers_available_unavailable_and_read_only() -> None:
    fixture = _FIXTURE_FORM.read_text(encoding="utf-8")
    forms.clear_form_cache()
    document = forms.capability_forms(
        descriptors=_descriptors(),
        enablements=[_Record("retired.capability", "0.9.0", False)],
        etag="sha256:" + "f" * 64,
        form_reader=lambda name: fixture,
    )
    assert document["schema"] == forms.UI_FORMS_SCHEMA
    assert document["subset_digest"] == SUBSET_DIGEST
    assert document["catalog_etag"] == "sha256:" + "f" * 64
    states = {entry["capability_id"]: entry for entry in document["forms"]}
    assert states["electronic.energy"]["state"] == "available"
    assert states["electronic.energy"]["form_digest"] == FORM_DIGESTS["electronic.energy"]
    assert states["vibration.compute"]["state"] == "unavailable"
    assert states["vibration.compute"]["error"]["code"] == "ui_form_missing"
    plugin = states["acp2_external_native_singlepoint"]
    assert plugin["state"] == "available"
    assert plugin["form_origin"] == "plugin:acp2-external-science-validator"
    assert plugin["form_digest"] == PLUGIN_FORM_DIGEST
    assert plugin["descriptor_digest"] == "sha256:" + "e" * 64
    read_only = {entry["capability_id"]: entry for entry in document["read_only"]}
    assert read_only["retired.capability"]["state"] == "read_only"
    assert read_only["retired.capability"]["descriptor_digest"] == "sha256:" + "d" * 64
    assert read_only["retired.capability"]["reason"] == "capability_not_active"


def test_projection_reports_a_broken_plugin_form_instead_of_rendering_it() -> None:
    forms.clear_form_cache()
    broken = json.loads(_FIXTURE_FORM.read_text(encoding="utf-8"))
    broken["form_schema"]["oneOf"] = [{"type": "object"}]
    broken.pop("form_digest")
    broken["form_digest"] = forms.form_digest(broken)
    document = forms.capability_forms(
        descriptors=_descriptors(),
        form_reader=lambda name: json.dumps(broken),
    )
    entry = next(
        item
        for item in document["forms"]
        if item["capability_id"] == "acp2_external_native_singlepoint"
    )
    assert entry["state"] == "unavailable"
    assert entry["error"]["code"] == "ui_keyword_rejected"
    assert entry["error"]["params"]["keyword"] == "oneOf"


def test_projection_pin_mismatch_is_a_typed_unavailable() -> None:
    forms.clear_form_cache()
    pinned = json.loads(_FIXTURE_FORM.read_text(encoding="utf-8"))
    pinned["descriptor_digest_pin"] = "sha256:" + "1" * 64
    pinned.pop("form_digest")
    pinned["form_digest"] = forms.form_digest(pinned)
    document = forms.capability_forms(
        descriptors=_descriptors(),
        form_reader=lambda name: json.dumps(pinned),
    )
    entry = next(
        item
        for item in document["forms"]
        if item["capability_id"] == "acp2_external_native_singlepoint"
    )
    assert entry["state"] == "unavailable"
    assert entry["error"]["code"] == "ui_form_descriptor_mismatch"


# ---------------------------------------------------------------------------
# 6. The read-only HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Any, None, None]:
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    monkeypatch.delenv("ACP_SERVER_PROJECT", raising=False)
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=1)
    with TestClient(app) as test_client:
        yield test_client


def test_route_serves_the_frozen_forms_without_side_effects(client: Any) -> None:
    manager = client.app.state.job_manager
    project = str(manager.default_project_id)
    before = manager.store.counts()

    response = client.get("/api/v2/science/capability-forms", params={"project_id": project})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema"] == forms.UI_FORMS_SCHEMA
    assert body["subset_version"] == forms.UI_SUBSET_VERSION
    assert body["subset_digest"] == SUBSET_DIGEST
    assert body["subset"]["bindings"] == forms.subset_table()["bindings"]
    available = {entry["capability_id"]: entry for entry in body["forms"]}
    assert {"electronic.energy", "geometry.optimize", "vibration.analyze"} <= set(available)
    assert available["vibration.analyze"]["state"] == "available"
    assert available["vibration.analyze"]["form"]["request"]["workflow"] == "frequency"
    assert body["read_only"] == []
    assert manager.store.counts() == before

    # A foreign project is refused before any form is projected (no existence leak).
    foreign = client.get("/api/v2/science/capability-forms", params={"project_id": "ghost-project"})
    assert foreign.status_code in (403, 404), foreign.text
    assert manager.store.counts() == before


def test_route_is_read_only_across_repeated_reads(client: Any) -> None:
    manager = client.app.state.job_manager
    project = str(manager.default_project_id)
    before = manager.store.counts()
    for _ in range(3):
        assert (
            client.get(
                "/api/v2/science/capability-forms", params={"project_id": project}
            ).status_code
            == 200
        )
    assert manager.store.counts() == before


def test_served_document_is_json_safe() -> None:
    document = forms.capability_forms(
        descriptors=[{"capability_id": "electronic.energy", "capability_source": "builtin"}],
    )
    encoded = json.dumps(document, sort_keys=True)
    assert "sha256:" in encoded
    assert isinstance(document["forms"], list)


def test_error_codes_are_unique_and_documented() -> None:
    assert len(set(forms.ERROR_CODES)) == len(forms.ERROR_CODES)
    for code in forms.ERROR_CODES:
        assert code.startswith("ui_")
    # A rejection always names a code that the browser can branch on.
    error = forms.CapabilityFormError("ui_keyword_rejected", "boom", params={"keyword": "$ref"})
    payload = error.to_dict()
    assert payload == {
        "code": "ui_keyword_rejected",
        "message": "boom",
        "params": {"keyword": "$ref"},
    }


def test_binding_rows_point_at_real_wire_locations() -> None:
    from acp.api.v1_schemas import V1JobCreateRequest

    fields = set(V1JobCreateRequest.model_fields)
    for row in forms.BINDINGS:
        root = row.request_body.split(".")[0]
        assert root in fields, f"{row.request_body}: {root} is not a submit field"
    assert forms.BINDING_TABLE["task.charge"].unit is None
    assert isinstance(forms.UNITS, Mapping)
