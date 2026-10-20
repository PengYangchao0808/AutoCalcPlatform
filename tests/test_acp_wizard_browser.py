"""D12 real default-page assembly: wizard state -> submit payload (browser).

Loads the actual ``frontend/ACP_Workbench_v2.html`` page and its real script
bootstrap (no hand-built DOM) through a Playwright route-faked origin: every
request — static assets and ``/api/**`` — is fulfilled or captured at the
network boundary, so no ACP service, no uvicorn and no listening socket is
involved. The catalogs served at that boundary are the live
``acp.catalog`` payloads (``get_workflow_catalog`` / ``get_method_catalog``),
and the submit assertions read the captured ``POST /api/v1/jobs`` bodies built
by the real ``submit-edit.js`` handler.

Contracts (mirrored by frontend/js/module_tests/wizard_submit_payload.test.js;
the Node module test alone is NOT sufficient):
  (a) an xtbmd_censo_energy body forwards every field of the catalog control
      levels xtb_md / xtb_opt / isostat, including explicit 0/false;
  (b) energy and xtbmd_censo_energy get different rank1-only defaults (the
      real openMethodConfig assignment in draft-form.js);
  (c) an active workflow (singlepoint) body does not drop catalog fields;
  (d) retired defaults are confined to the historical-compat branch (explicit
      retired id while the workflow catalog is unavailable) and active
      selections resolve from the live catalog — a retired id self-heals.

Historical-compat reachability: with the live workflow catalog loaded,
``ensureWizardWorkflowValid()`` self-heals any retired workflow id to the live
default, so the retired energyLike branch is only reachable through the
historical state (restored draft / historical data) while the workflow
catalog is unavailable (R5/R16 loading/retry paths). That is the branch
driven for (a)/(b).

Negative control (QA only): ``ACP_WIZARD_SUBMIT_MUTATION`` =
``drop_isostat_edis`` / ``undef_zero_md_seed`` / ``retired_default_for_active``
mutates the derived fixture so both the Node and the page tests must fail;
it never touches production code.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip(
    "playwright.sync_api",
    reason=(
        "NOT_VERIFIED: playwright (browser extra) not installed — "
        "record NOT_VERIFIED, never counted as a pass"
    ),
)
from playwright.sync_api import Browser, Page, sync_playwright

from acp.catalog import (
    FIELD_DEFINITIONS,
    METHOD_SCHEMAS,
    get_method_catalog,
    get_workflow_catalog,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = REPO_ROOT / "frontend"
PAGE_URL = "http://acp.test/ACP_Workbench_v2.html"

MUTATION = os.environ.get("ACP_WIZARD_SUBMIT_MUTATION", "")
CONTROL_LEVEL_IDS = ("xtb_md", "xtb_opt", "isostat")
RETIRED_SCHEMA_IDS = ("censo_energy", "censo_ensemble", "xtbmd_censo_energy")
ENERGY_LIKE_KEYS = ("rank1_only", "threshold", "no_opt", "preset")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".map": "application/json",
    ".wasm": "application/wasm",
}


def _fixture_value(field: str) -> Any:
    field_type = (FIELD_DEFINITIONS.get(field) or {}).get("type")
    if field_type == "bool":
        return False
    if field_type in ("int", "float"):
        return 0
    if field_type == "module":
        return {"mode": f"fix_{field}"}
    return f"fix_{field}"


def _control_fields() -> list[str]:
    keys: list[str] = []
    for level in METHOD_SCHEMAS["xtbmd_censo_energy"]["method_levels"]:
        if level["level_id"] not in CONTROL_LEVEL_IDS:
            continue
        for field in level["fields"]:
            if field not in keys:
                keys.append(field)
    return keys


def _xtbmd_stages() -> dict[str, Any]:
    schema = METHOD_SCHEMAS["xtbmd_censo_energy"]
    profile = next(
        (p for p in schema.get("profiles", []) if p["profile_id"] == "censo-light"),
        schema["profiles"][0],
    )
    stages = json.loads(json.dumps(profile["levels"]))
    for level in schema["method_levels"]:
        if level["level_id"] not in CONTROL_LEVEL_IDS:
            continue
        stage = stages.setdefault(
            level["level_id"], {"engine": (level.get("allowed_engines") or [""])[0]}
        )
        for field in level["fields"]:
            stage[field] = _fixture_value(field)
    if MUTATION == "drop_isostat_edis":
        stages["isostat"].pop("edis", None)
    if MUTATION == "undef_zero_md_seed":
        stages["xtb_md"].pop("md_seed", None)
    return stages


def _active_stages(workflow_id: str) -> dict[str, Any]:
    live = next(w for w in get_workflow_catalog() if w["id"] == workflow_id)
    schema = METHOD_SCHEMAS[
        "censo_energy" if MUTATION == "retired_default_for_active" else live["method_schema_id"]
    ]
    stages: dict[str, Any] = {}
    for level in schema["method_levels"]:
        stage: dict[str, Any] = {"engine": (level.get("allowed_engines") or [""])[0]}
        for field in level["fields"]:
            stage[field] = _fixture_value(field)
        stages[level["level_id"]] = stage
    return stages


class _Scenario:
    """Route-faked page boundary: real frontend files + captured/faked /api."""

    def __init__(self, live_workflow_catalog: bool) -> None:
        self.live_workflow_catalog = live_workflow_catalog
        self.job_bodies: list[dict[str, Any]] = []
        self.page_errors: list[str] = []
        self.method_catalog = get_method_catalog()
        self.workflows = get_workflow_catalog()

    def attach(self, page: Page) -> None:
        page.on("pageerror", lambda err: self.page_errors.append(str(err)))
        page.route("**/*", self._handle)

    def _handle(self, route: Any) -> None:
        request = route.request
        path = request.url.split("://", 1)[-1].split("/", 1)[-1]
        path = "/" + path.split("?", 1)[0]
        if request.method == "POST" and path == "/api/v1/jobs":
            self.job_bodies.append(request.post_data_json)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"job_id": f"captured_{len(self.job_bodies)}", "status": "queued"}),
            )
            return
        if path.startswith("/api/"):
            self._fulfill_api(route, path)
            return
        candidate = (FRONTEND / path.lstrip("/")).resolve()
        if FRONTEND.resolve() in candidate.parents and candidate.is_file():
            route.fulfill(
                status=200,
                body=candidate.read_bytes(),
                content_type=_CONTENT_TYPES.get(candidate.suffix, "application/octet-stream"),
            )
            return
        route.fulfill(status=404, body=b"")

    def _fulfill_api(self, route: Any, path: str) -> None:
        request = route.request
        if path == "/api/v1/workflow-catalog":
            if not self.live_workflow_catalog:
                route.fulfill(
                    status=503, body=json.dumps({"detail": "workflow catalog unavailable"})
                )
                return
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"workflows": self.workflows}),
            )
            return
        if path == "/api/v1/method-catalog":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(self.method_catalog),
            )
            return
        if "task-view" in path:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "groups": [],
                        "facets": {},
                        "total": 0,
                        "counts": {},
                        "offset": 0,
                        "limit": 200,
                        "next_offset": None,
                    }
                ),
            )
            return
        if "event" in path:
            route.fulfill(status=200, content_type="text/event-stream", body="")
            return
        if request.method != "GET":
            route.fulfill(status=200, content_type="application/json", body="{}")
            return
        route.fulfill(status=200, content_type="application/json", body="{}")


@pytest.fixture(scope="module")
def browser() -> Any:
    with sync_playwright() as pw:
        instance: Browser = pw.chromium.launch(headless=True)
        try:
            yield instance
        finally:
            instance.close()


def _load_page(browser: Browser, live_workflow_catalog: bool) -> tuple[Page, _Scenario]:
    scenario = _Scenario(live_workflow_catalog=live_workflow_catalog)
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    scenario.attach(page)
    page.goto(PAGE_URL, wait_until="load")
    page.wait_for_function(
        "() => window.ACPWizardSubmitEdit && window.ACPWizardDraftForm"
        " && Object.keys((window.methodCatalogCache || {}).method_schemas || {}).length > 0",
        timeout=20000,
    )
    if live_workflow_catalog:
        page.wait_for_function(
            "() => (window.workflowCatalogCache || []).length > 0", timeout=20000
        )
    page.evaluate(
        """() => {
            document.getElementById("modal-nproc").value = "4";
            document.getElementById("modal-mem").value = "8";
            document.getElementById("modal-mem-unit").value = "GB";
            document.getElementById("modal-charge").value = "0";
            document.getElementById("modal-mult").value = "1";
            wizardStructures = [{ name: "mol_1", xyz: "1\\n\\nH 0 0 0" }];
        }"""
    )
    return page, scenario


def _submit(
    page: Page,
    workflow: dict[str, Any],
    profile_id: str,
    stages: dict[str, Any],
    rank1_only: Any = None,
) -> None:
    page.evaluate(
        """(args) => {
            const method = { profile_id: args.profile_id, stages: args.stages };
            if (args.rank1_only !== null) {
                method.rank1_only = args.rank1_only;
                method.rank1_wf = args.workflow.id;
            }
            window.ACPWizardDraftForm._restoreWizardDraft({
                workflowState: { workflow: args.workflow, method },
                fields: {},
            });
        }""",
        {
            "workflow": workflow,
            "profile_id": profile_id,
            "stages": stages,
            "rank1_only": rank1_only,
        },
    )
    _submit_now(page)


def _submit_now(page: Page) -> None:
    page.evaluate(
        """() => {
            wizardStructures = [{ name: "mol_1", xyz: "1\\n\\nH 0 0 0" }];
            window.ACPWizardDraftForm.setCreateWizardStep(3);
        }"""
    )
    result = page.evaluate(
        """async () => {
            try {
                await window.ACPWizardSubmitEdit.handleModalSubmit();
                return null;
            } catch (e) {
                return String((e && e.message) || e);
            }
        }"""
    )
    assert result is None, f"real submit handler raised: {result}"


def _assert_control_body(body: dict[str, Any], stages: dict[str, Any]) -> None:
    assert body["workflow"] == "xtbmd_censo_energy"
    assert body["method"]["schema_id"] == "xtbmd_censo_energy"
    by_field: dict[str, Any] = {}
    for level in CONTROL_LEVEL_IDS:
        for field, value in (stages.get(level) or {}).items():
            if field != "engine":
                by_field[field] = value
    for field in _control_fields():
        assert field in body["method"], f"control field missing from body.method: {field}"
        got, want = body["method"][field], by_field[field]
        assert got == want and type(got) is type(want), (
            f"control field changed in transit: {field}: {got!r} != {want!r}"
        )
    if MUTATION != "drop_isostat_edis":
        assert body["method"]["edis"] == stages["isostat"]["edis"]
    assert body["method"]["md_seed"] == 0 and not isinstance(body["method"]["md_seed"], bool)
    assert body["method"]["max_frames"] == 0 and not isinstance(body["method"]["max_frames"], bool)
    assert body["method"]["edis"] == 0 and not isinstance(body["method"]["edis"], bool)
    for flag in ("md_shake", "md_nvt", "resume", "conv_check"):
        assert body["method"][flag] is False, f"explicit false lost in transit: {flag}"


def test_wizard_submit_xtbmd_body_forwards_control_fields_from_real_default_page(browser) -> None:
    page, scenario = _load_page(browser, live_workflow_catalog=False)
    try:
        stages = _xtbmd_stages()
        _submit(
            page,
            {
                "id": "xtbmd_censo_energy",
                "label": "xTB-MD CENSO Energy",
                "schema_id": "xtbmd_censo_energy",
            },
            "censo-light",
            stages,
            rank1_only=False,
        )
        assert len(scenario.job_bodies) == 1, f"captured bodies: {len(scenario.job_bodies)}"
        _assert_control_body(scenario.job_bodies[0], stages)
    finally:
        page.context.close()


def test_wizard_submit_rank1_defaults_differ_per_workflow_on_real_page(browser) -> None:
    page, scenario = _load_page(browser, live_workflow_catalog=False)
    try:
        defaults: dict[str, bool] = {}
        for workflow in (
            {"id": "energy", "label": "Conformer Energy", "schema_id": "censo_energy"},
            {
                "id": "xtbmd_censo_energy",
                "label": "xTB-MD CENSO Energy",
                "schema_id": "xtbmd_censo_energy",
            },
        ):
            page.evaluate(
                """(workflow) => {
                    window.wizardState.workflow = workflow;
                    delete window.wizardState.method.rank1_only;
                    delete window.wizardState.method.rank1_wf;
                }""",
                workflow,
            )
            page.evaluate("() => window.ACPWizardDraftForm.openMethodConfig()")
            defaults[workflow["id"]] = page.evaluate("() => window.wizardState.method.rank1_only")
        assert defaults["energy"] is True, "energy rank1-only default must be ON"
        assert defaults["xtbmd_censo_energy"] is False, (
            "xtbmd_censo_energy rank1-only default must be the full ensemble"
        )
        assert defaults["energy"] != defaults["xtbmd_censo_energy"]

        energy_schema = METHOD_SCHEMAS["censo_energy"]
        energy_profile = energy_schema["profiles"][0]
        energy_stages = json.loads(json.dumps(energy_profile["levels"]))
        _submit(
            page,
            {"id": "energy", "label": "Conformer Energy", "schema_id": "censo_energy"},
            energy_profile["profile_id"],
            energy_stages,
            rank1_only=defaults["energy"],
        )
        _submit(
            page,
            {
                "id": "xtbmd_censo_energy",
                "label": "xTB-MD CENSO Energy",
                "schema_id": "xtbmd_censo_energy",
            },
            "censo-light",
            _xtbmd_stages(),
            rank1_only=defaults["xtbmd_censo_energy"],
        )
        energy_body, xtbmd_body = scenario.job_bodies[-2], scenario.job_bodies[-1]
        assert energy_body["method"]["rank1_only"] is True
        assert xtbmd_body["method"]["rank1_only"] is False
    finally:
        page.context.close()


def test_wizard_submit_active_workflow_keeps_catalog_fields_on_real_page(browser) -> None:
    page, scenario = _load_page(browser, live_workflow_catalog=True)
    try:
        stages = _active_stages("singlepoint")
        page.evaluate(
            """(stages) => {
                window._pendingNewTask = { workflow: "singlepoint", projectId: "" };
                window.ACPWizardDraftForm.applyPendingNewTask();
                window.wizardState.method.profile_id = "default";
                window.wizardState.method.stages = stages;
            }""",
            stages,
        )
        resolved = page.evaluate(
            """() => ({ id: window.wizardState.workflow.id,
                        schema_id: window.wizardState.workflow.schema_id })"""
        )
        assert resolved["id"] == "singlepoint", (
            f"active selection must resolve from the live catalog: {resolved}"
        )
        _submit_now(page)
        assert len(scenario.job_bodies) == 1, f"captured bodies: {len(scenario.job_bodies)}"
        body = scenario.job_bodies[0]
        assert body["workflow"] == "singlepoint"
        live = next(w for w in get_workflow_catalog() if w["id"] == "singlepoint")
        assert body["method"]["schema_id"] == live["method_schema_id"], (
            "active body schema must come from the live catalog"
        )
        live_schema = METHOD_SCHEMAS[live["method_schema_id"]]
        expected = {
            level["level_id"]: {field: _fixture_value(field) for field in level["fields"]}
            for level in live_schema["method_levels"]
        }
        for level in live_schema["method_levels"]:
            got_level = (body["method"].get("levels") or {}).get(level["level_id"])
            assert got_level is not None, f"active body dropped catalog level: {level['level_id']}"
            for field in level["fields"]:
                assert field in got_level, (
                    f"active body dropped catalog field: {level['level_id']}.{field}"
                )
                want = expected[level["level_id"]][field]
                got = got_level[field]
                assert got == want and type(got) is type(want), (
                    f"catalog field changed in transit: {level['level_id']}.{field}"
                )
        for key in ENERGY_LIKE_KEYS:
            assert key not in body["method"], (
                f"retired energyLike default leaked into active body: {key}"
            )
    finally:
        page.context.close()


def test_wizard_submit_retired_defaults_confined_to_historical_compat_on_real_page(browser) -> None:
    page, scenario = _load_page(browser, live_workflow_catalog=True)
    try:
        resolutions = page.evaluate(
            """() => {
                const ids = ["energy", "xtbmd_censo_energy", "ensemble", "singlepoint"];
                const out = {};
                for (const id of ids) {
                    const entry = window.ACPWizardDraftForm.resolveDefaultWorkflow(id);
                    out[id] = entry ? { id: entry.id, status: entry.status,
                        method_schema_id: entry.method_schema_id } : null;
                }
                return out;
            }"""
        )
        active_ids = {w["id"] for w in get_workflow_catalog() if w["status"] == "active"}
        for retired_id in ("energy", "xtbmd_censo_energy", "ensemble"):
            resolved = resolutions[retired_id]
            assert resolved is not None, f"resolution missing for {retired_id}"
            assert resolved["id"] != retired_id, (
                f"retired default literal must not win for {retired_id}"
            )
            assert resolved["id"] in active_ids, (
                f"resolution must come from the live active catalog: {resolved['id']}"
            )
        assert resolutions["singlepoint"]["id"] == "singlepoint"
        assert resolutions["singlepoint"]["method_schema_id"] == "dft_singlepoint"

        _submit(
            page,
            {
                "id": "xtbmd_censo_energy",
                "label": "xTB-MD CENSO Energy",
                "schema_id": "xtbmd_censo_energy",
            },
            "censo-light",
            _xtbmd_stages(),
            rank1_only=False,
        )
        healed = scenario.job_bodies[-1]
        assert healed["workflow"] != "xtbmd_censo_energy", (
            "live catalog must self-heal a retired selection instead of submitting it"
        )
        assert healed["workflow"] in active_ids, (
            f"healed body must target a live active workflow: {healed['workflow']}"
        )
        expected_schema = resolutions["xtbmd_censo_energy"]["method_schema_id"]
        assert healed["method"]["schema_id"] == expected_schema
        for retired_schema in RETIRED_SCHEMA_IDS:
            assert healed["method"]["schema_id"] != retired_schema, (
                f"retired schema leaked into healed body: {retired_schema}"
            )
        assert all(key not in healed["method"] for key in ENERGY_LIKE_KEYS)
    finally:
        page.context.close()
