"""``schema-form-live`` — a third-party capability form in a real browser (M4-05).

This body is the M4-05 delivery of the registered P3 row.  The scenario id and
its declared observations are unchanged; what changes is that the row is now
**executed** against the real product:

1. a throw-away virtual environment is created and the committed A-22 external
   plugin fixture wheel is built and installed into it with
   ``--no-deps --no-index`` (nothing from a package index);
2. the capability is enabled by the **operator CLI**
   (``acp science-plugin enable``) into an isolated run root — the trust
   allow-list is the environment, never a test-only registration;
3. a **real Controller** (``uvicorn acp.api.server:app``) is started from that
   environment in ``api-key`` identity mode over an isolated identity database,
   so discovery reads the installed distribution and the run root's enablement
   ledger;
4. the **real Workbench** (``frontend/ACP_Workbench_v2.html``) is loaded in the
   Playwright-bundled Chromium with a real project-scoped credential.  The
   page's own capability-form module loads the served
   ``acp2.ui.capability-forms.v1`` document, the third-party form renders, the
   required text field is typed through the rendered control, a numeric edit is
   refused with a **typed** ``ui_value_type`` (never silently coerced), and the
   real preflight response is displayed as the ViewModel with its plan / form /
   descriptor digests;
5. submit is asserted **disabled** with the reason the page states ("An
   approved plan is required before submit"), and a direct module submit is
   refused with the typed ``approval_required`` code — no approval record exists
   in this row, so no task is created;
6. console messages, page errors, every API status, screenshots and the served
   page HTML are recorded beside the receipt.

A missing prerequisite (Playwright, Chromium, the fixture source tree) is
``NOT_VERIFIED`` with a precise reason; a wrong result is ``FAIL``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import acp2_m405_plugin_env as plugin_env
from acp2_browser_leg import (
    BrowserPrerequisiteError,
    BrowserSession,
    PlatformController,
    http_json,
    provision_identities,
    repo_root,
)
from acp2_experience_scenarios import (
    _add_artifact,
    _assertion,
    _finish,
    _not_verified,
    _out_dir,
    _write_json,
    publish_observations,
    register,
)

SCENARIO_ID = "schema-form-live"

PROJECT_NAME = "ACP 2.0 M4-05 schema form"
NATIVE_RECORD = plugin_env.FIXTURE_ROOT / "fixtures" / "native_singlepoint.acp2n"
FORM_BINDINGS = (
    "task.charge",
    "task.multiplicity",
    "task.resources.nproc",
    "task.structure.source",
)


def _ensure_environment(out: Path) -> tuple[Path, dict[str, Any]]:
    """Return the venv interpreter that has the fixture installed.

    The M4-05 orchestrator may hand its own environment over through
    ``ACP2_M405_VENV`` (so the row and the slice run against the same bytes).
    Without it the row provisions its own clean environment: a row must be
    independently runnable.
    """
    explicit = os.environ.get("ACP2_M405_VENV")
    if explicit and Path(explicit).is_file():
        return Path(explicit), {"mode": "reused", "python": explicit}
    root = Path(os.environ.get("ACP2_M405_ENV_ROOT") or (out / "_plugin_env"))
    python, venv_argv = plugin_env.create_isolated_venv(root)
    wheel, build_argv = plugin_env.build_fixture_wheel(plugin_env.FIXTURE_ROOT, root / "wheel")
    install_argv = plugin_env.install_wheel(python, wheel)
    return python, {
        "mode": "provisioned",
        "root": str(root),
        "python": str(python),
        "wheel": str(wheel),
        "wheel_sha256": "sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "venv_argv": venv_argv.argv,
        "build_argv": build_argv.argv,
        "install_argv": install_argv.argv,
    }


def handle(context: Any) -> int:
    """Run the row; Playwright's sync API needs a thread without a live loop."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_row, context)
        try:
            return int(future.result(timeout=max(120.0, float(context.timeout))))
        except Exception as exc:  # noqa: BLE001 - reported as a row failure
            import traceback

            _assertion(
                context,
                "browser.row",
                False,
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-4000:]}",
            )
            return _finish(context, "browser_row_exception")


def _run_row(context: Any) -> int:
    out = _out_dir(context)
    runtime_root = out / "_runtime"
    started = time.time()

    if not (repo_root() / "frontend" / "ACP_Workbench_v2.html").is_file():
        return _not_verified(context, "source_tree_absent:frontend/ACP_Workbench_v2.html")
    if not NATIVE_RECORD.is_file():
        return _not_verified(context, f"fixture_absent:{NATIVE_RECORD}")
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as exc:
        return _not_verified(context, f"playwright_absent:{exc}")

    from playwright.sync_api import sync_playwright

    runtime_root.mkdir(parents=True, exist_ok=True)
    try:
        venv_python, environment = _ensure_environment(out)
    except (RuntimeError, OSError) as exc:
        return _not_verified(context, f"plugin_environment_failed:{exc}")
    _write_json(out, "plugin-environment.json", environment)

    run_root = runtime_root / "runs"
    enabled, enable_payload = plugin_env.run_operator_cli(
        venv_python,
        ["enable", "--capability", plugin_env.CAPABILITY],
        run_root=run_root,
        trusted=(plugin_env.CAPABILITY,),
    )
    record = enable_payload.get("enabled") or {}
    _write_json(
        out,
        "enablement.json",
        {
            "command": enabled.to_dict(),
            "payload": enable_payload,
        },
    )
    _assertion(
        context,
        "plugin.enabled_by_operator_cli",
        enabled.returncode == 0
        and enable_payload.get("status") == "ok"
        and record.get("capability_id") == plugin_env.CAPABILITY
        and record.get("enabled") is True
        and bool(record.get("computed_distribution_digest")),
        json.dumps(
            {
                "exit": enabled.returncode,
                "status": enable_payload.get("status"),
                "capability_id": record.get("capability_id"),
                "enabled": record.get("enabled"),
            }
        )[:800],
    )
    if enabled.returncode != 0:
        return _finish(context, "plugin_enable_failed")

    # The spawned Controller must trust the same capability (the allow-list is
    # an environment fact of the deployment, not a per-request input).
    os.environ[plugin_env.TRUST_ENV] = plugin_env.CAPABILITY

    identity_db = runtime_root / "acp_identity.db"
    platform = PlatformController(
        run_root=run_root,
        identity_db=identity_db,
        log_path=out / "controller.log",
        python=str(venv_python),
        tenant="acp-m405-browser",
    )
    playwright = sync_playwright().start()
    result: dict[str, Any] = {"scenario": SCENARIO_ID, "environment": environment}
    try:
        platform.start()
        base_url = platform.base_url
        result["base_url"] = base_url

        # ---- identity bootstrap: one unscoped operator creates the project,
        # then a project-scoped credential is issued for the browser. -------
        from acp_services.workspace.identity_lifecycle import (
            IdentityLifecycle,
            IdentityLifecyclePolicy,
        )
        from acp_services.workspace.keys import ApiKeyManager

        manager = ApiKeyManager(identity_db, iterations=2_000)
        lifecycle = IdentityLifecycle(
            identity_db,
            policy=IdentityLifecyclePolicy(
                deployment_tier="lab", capabilities=("authentication", "isolation")
            ),
            key_manager=manager,
            password_params={"kdf": "pbkdf2_hmac_sha256", "iterations": 2_000, "salt_bytes": 16},
        )
        invitation = lifecycle.invite_account(
            principal_id="principal_m405_bootstrap",
            login_id="m405-bootstrap",
            roles=["member"],
        )
        account = lifecycle.accept_invitation(
            invitation.token, password="M405-bootstrap-Strong-Passw0rd!"
        )
        bootstrap_key = lifecycle.issue_key(
            account.account_id, scope="workspace", actions=["read", "write", "control"]
        )
        created = http_json(
            base_url, "POST", "/api/v1/projects", {"name": PROJECT_NAME}, key=bootstrap_key.key
        )
        if not created.ok or not isinstance(created.payload, dict):
            _assertion(
                context,
                "platform.project.create",
                False,
                f"HTTP {created.status}: {json.dumps(created.payload)[:500]}",
            )
            return _finish(context, "platform_project_create_failed")
        project_id = str(created.payload["project_id"])
        identities = provision_identities(identity_db, project=project_id)
        result["project_id"] = project_id

        # ---- discovery document read by the page's own module --------------
        forms_result = http_json(
            base_url,
            "GET",
            f"/api/v2/science/capability-forms?project_id={project_id}",
            key=identities["operator"]["key"],
        )
        forms_status = forms_result.status
        forms_document = forms_result.payload if isinstance(forms_result.payload, dict) else {}
        form_entry = next(
            (
                entry
                for entry in (forms_document or {}).get("forms") or []
                if entry.get("capability_id") == plugin_env.CAPABILITY
            ),
            None,
        )
        _write_json(out, "served-capability-forms.json", forms_document or {})
        _assertion(
            context,
            "discovery.plugin_form_served",
            forms_status == 200
            and isinstance(form_entry, dict)
            and form_entry.get("state") == "available"
            and str(form_entry.get("form_origin") or "").startswith("plugin:"),
            json.dumps(
                {
                    "status": forms_status,
                    "state": (form_entry or {}).get("state"),
                    "origin": (form_entry or {}).get("form_origin"),
                    "form_digest": (form_entry or {}).get("form_digest"),
                }
            )[:800],
        )
        if not isinstance(form_entry, dict):
            return _finish(context, "plugin_form_not_served")

        # ---- the real browser ---------------------------------------------
        browser = BrowserSession(
            label="chromium-schema-form",
            key=identities["operator"]["key"],
            out_dir=out,
            base_url=base_url,
            playwright=playwright,
        )
        browser.open()
        result["browser"] = f"chromium/{browser.version}"
        title = browser.goto_workbench()
        _assertion(context, "browser.page_loaded", bool(title), title)
        _add_artifact(context, browser.screenshot("01-workbench-loaded.png").name)
        browser.evaluate("(pid) => { document.body.dataset.m405Project = pid; }", project_id)

        console_mark = len(browser.recorder.console)
        page_error_mark = len(browser.recorder.page_errors)
        browser.recorder.window()

        # The capability-form panel shares the right sidebar's flex space with
        # the other panels; with every panel expanded it is squeezed to a few
        # pixels.  Collapsing the neighbouring panels (the page's own control,
        # persisted by the page) gives it the room a user would need.
        layout: dict[str, Any] = {
            "before": browser.page.locator("#capability-form-panel").bounding_box(),
            "collapsed": [],
            "failed": [],
        }
        for key in ("science_question", "scientific_evidence", "research_approval", "info"):
            button = browser.page.locator(f'.panel-collapse-btn[data-panel-key="{key}"]')
            try:
                if button.count() and button.first.is_visible():
                    button.first.click(timeout=10000)
                    layout["collapsed"].append(key)
            except Exception as exc:  # noqa: BLE001 - layout accommodation, recorded
                layout["failed"].append({"panel": key, "error": str(exc)[:200]})
        browser.page.locator("#capability-form-panel").scroll_into_view_if_needed()

        # The project selector's documented integration point (the panel loads
        # on project selection, not on page load); then the capability is picked
        # through the rendered <select>, exactly like a user would.
        browser.evaluate("(pid) => ACPCapabilityForm.setProject(pid)", project_id)
        browser.page.wait_for_selector("#capability-form-capability", timeout=30000)
        browser.page.select_option("#capability-form-capability", plugin_env.CAPABILITY)
        browser.page.wait_for_selector("#capability-form-field-source", timeout=30000)
        browser.page.wait_for_selector("#capability-form-preflight", timeout=30000)
        state = browser.evaluate("() => ACPCapabilityForm.getState()")
        layout["after_form"] = browser.page.locator("#capability-form-panel").bounding_box()
        result["layout"] = layout
        _assertion(
            context,
            "browser.form_panel_visible",
            layout["after_form"] is not None and float(layout["after_form"]["height"]) >= 200.0,
            json.dumps(layout)[:900],
        )
        browser.page.locator("#capability-form-panel").scroll_into_view_if_needed()
        _add_artifact(context, browser.screenshot("02-third-party-form-rendered.png").name)
        panel_shot = "02-third-party-form-panel.png"
        browser.page.locator("#capability-form-panel").screenshot(path=str(out / panel_shot))
        _add_artifact(context, panel_shot)

        rendered = browser.evaluate(
            """() => {
              const root = document.getElementById('capability-form-body');
              const hint = root.querySelector(
                '.capability-form-actions .capability-form-hint');
              const hintText = hint ? String(hint.textContent || '') : '';
              const fields = Array.from(root.querySelectorAll('[data-binding]'))
                .map((node) => ({id: node.id, binding: node.getAttribute('data-binding')}));
              const options = Array.from(
                root.querySelectorAll('#capability-form-capability option'))
                .map((node) => ({value: node.value, text: node.textContent}));
              return {
                text: root.textContent,
                title: (root.querySelector('.capability-form-title') || {}).textContent || null,
                fields: fields,
                options: options,
                submit_disabled: document.getElementById('capability-form-submit').disabled,
                hint_present: hintText.trim().length > 0,
                hint_text: hintText.trim(),
                selected: window.ACPCapabilityForm.getState().selected,
              };
            }"""
        )
        result["rendered"] = rendered
        bindings = sorted(field["binding"] for field in rendered.get("fields") or [])
        _assertion(
            context,
            "browser.form_rendered_from_discovery",
            rendered.get("title") is not None
            and plugin_env.CAPABILITY
            in {option["value"] for option in rendered.get("options") or []}
            and bindings
            == sorted(
                [
                    "task.charge",
                    "task.multiplicity",
                    "task.resources.nproc",
                    "task.structure.source",
                ]
            ),
            json.dumps(
                {
                    "title": rendered.get("title"),
                    "bindings": bindings,
                    "options": rendered.get("options"),
                    "selected": rendered.get("selected"),
                }
            )[:1200],
        )
        plugin_state_entry = next(
            (
                entry
                for entry in state.get("forms") or []
                if entry.get("capability_id") == plugin_env.CAPABILITY
            ),
            {},
        )
        _assertion(
            context,
            "browser.form_matches_served_digest",
            state.get("subset_digest") == (forms_document or {}).get("subset_digest")
            and plugin_state_entry.get("form_digest") == form_entry.get("form_digest")
            and plugin_state_entry.get("descriptor_digest") == form_entry.get("descriptor_digest"),
            json.dumps(
                {
                    "state_subset_digest": state.get("subset_digest"),
                    "state_form_digest": plugin_state_entry.get("form_digest"),
                    "served_form_digest": form_entry.get("form_digest"),
                    "state_selected": state.get("selected"),
                }
            )[:800],
        )

        # ---- type the required field through the rendered control ---------
        text = NATIVE_RECORD.read_text(encoding="utf-8")
        browser.page.fill("#capability-form-field-source", text)
        browser.page.dispatch_event("#capability-form-field-source", "change")
        values = browser.evaluate("() => ACPCapabilityForm.getState().values")
        _assertion(
            context,
            "browser.value_committed_from_dom",
            values.get("source") == text,
            json.dumps({"source_length": len(values.get("source") or "")}),
        )
        _add_artifact(context, browser.screenshot("03-form-filled.png").name)

        # ---- a numeric edit through the DOM is refused as a typed value ---
        browser.page.locator("#capability-form-field-charge").fill("2")
        browser.page.dispatch_event("#capability-form-field-charge", "change")
        browser.evaluate("() => document.getElementById('capability-form-preflight').click()")
        browser.page.wait_for_selector(
            '.capability-form-notice[data-code="ui_value_type"]', timeout=30000
        )
        numeric = browser.evaluate(
            """() => {
              const notice = document.querySelector(
                '.capability-form-notice[data-code="ui_value_type"]');
              const state = ACPCapabilityForm.getState();
              return {
                stored: state.values.charge,
                stored_type: typeof state.values.charge,
                refusal: {
                  code: notice ? notice.getAttribute('data-code') : null,
                  text: notice ? notice.textContent.slice(0, 300) : null,
                },
              };
            }"""
        )
        result["numeric_edit"] = numeric
        _assertion(
            context,
            "browser.numeric_edit_typed_refusal",
            numeric.get("stored") == "2"
            and (numeric.get("refusal") or {}).get("code") == "ui_value_type",
            json.dumps(numeric)[:800],
        )
        _add_artifact(context, browser.screenshot("04-numeric-edit-typed-refusal.png").name)
        browser.evaluate("() => ACPCapabilityForm.setValue('charge', 0)")

        # ---- real preflight + ViewModel display ---------------------------
        # The panel sits at the bottom of the right sidebar and the vertical
        # splitter overlays the pointer path, so the control is activated by a
        # DOM click on the real button (same listener, same real HTTP call).
        browser.page.evaluate("() => document.getElementById('capability-form-preflight').click()")
        browser.page.wait_for_selector(".capability-form-result", timeout=30000)
        displayed = browser.evaluate(
            """() => {
              const root = document.getElementById('capability-form-body');
              const state = ACPCapabilityForm.getState();
              const hint = root.querySelector(
                '.capability-form-actions .capability-form-hint');
              const hintText = hint ? String(hint.textContent || '') : '';
              return {
                text: root.textContent,
                view_model: state.viewModel,
                submit_disabled: document.getElementById('capability-form-submit').disabled,
                hint_present: hintText.trim().length > 0,
                hint_text: hintText.trim(),
              };
            }"""
        )
        view_model = displayed.get("view_model") or {}
        result["view_model"] = {
            "plan_digest": view_model.get("plan_digest"),
            "preflight_fingerprint": view_model.get("preflight_fingerprint"),
            "form_digest": view_model.get("form_digest"),
            "descriptor_digest": view_model.get("descriptor_digest"),
            "subset_version": view_model.get("subset_version"),
            "subset_digest": view_model.get("subset_digest"),
            "workflow": ((view_model.get("request") or {}).get("workflow")),
            "fields": [
                f"{field.get('binding')}={field.get('task_request')}"
                for field in (view_model.get("request") or {}).get("fields") or []
            ],
            "human_edits": (view_model.get("provenance") or {}).get("human_edits"),
        }
        edited = sorted(
            edit.get("field_path")
            for edit in (view_model.get("provenance") or {}).get("human_edits") or []
        )
        _assertion(
            context,
            "browser.preflight_real_response",
            str(view_model.get("plan_digest") or "").startswith("sha256:")
            and view_model.get("plan_digest") == view_model.get("preflight_fingerprint")
            and view_model.get("form_digest") == form_entry.get("form_digest")
            and view_model.get("descriptor_digest") == form_entry.get("descriptor_digest")
            and (view_model.get("request") or {}).get("workflow") == "singlepoint"
            and "task.structure.source" in edited,
            json.dumps(result["view_model"])[:1500],
        )
        form_title = str((form_entry.get("form") or {}).get("title") or "")
        _assertion(
            context,
            "browser.view_model_displayed",
            str(displayed.get("text") or "").find(str(view_model.get("plan_digest") or "")) >= 0
            and str(displayed.get("text") or "").find("structure.source") >= 0
            and (not form_title or str(displayed.get("text") or "").find(form_title) >= 0),
            str(displayed.get("text") or "")[:400],
        )
        _add_artifact(context, browser.screenshot("05-preflight-view-model.png").name)
        result_shot = "05-preflight-view-model-panel.png"
        browser.page.locator("#capability-form-panel").screenshot(path=str(out / result_shot))
        _add_artifact(context, result_shot)

        # ---- submit needs an approval: the page says so, the module refuses
        approval_gate = browser.evaluate(
            """() => {
              try {
                ACPCapabilityForm.submit({projectId: document.body.dataset.m405Project});
                return {ok: false, code: null, message: 'submit did not refuse'};
              } catch (error) {
                return {ok: true, code: error.code || null, message: String(error.message)};
              }
            }"""
        )
        result["approval_gate"] = {
            "submit_disabled": displayed.get("submit_disabled"),
            "hint_present": displayed.get("hint_present"),
            "hint_text": displayed.get("hint_text"),
            "module_refusal": approval_gate,
        }
        _assertion(
            context,
            "browser.submit_requires_approval",
            displayed.get("submit_disabled") is True
            and displayed.get("hint_present") is True
            and approval_gate.get("code") == "approval_required",
            json.dumps(result["approval_gate"])[:800],
        )

        # ---- evidence: console, page errors, network, HTML -----------------
        _add_artifact(context, browser.html("schema-form-page.html").name)
        browser.record("browser-console.json")
        _add_artifact(context, "browser-console.json")
        _write_json(
            out,
            "browser-network.json",
            {
                "responses": browser.recorder.responses,
                "requests_failed": browser.recorder.requests_failed,
            },
        )
        _add_artifact(context, "browser-network.json")
        console_errors = browser.recorder.console_errors(since=console_mark)
        page_errors = browser.recorder.page_errors[page_error_mark:]
        result["console_error_count"] = len(console_errors)
        result["page_error_count"] = len(page_errors)
        result["response_statuses"] = sorted(
            {entry["status"] for entry in browser.recorder.responses}
        )
        _assertion(
            context,
            "browser.zero_console_and_page_errors",
            not console_errors and not page_errors,
            json.dumps(
                {
                    "console_errors": console_errors[:5],
                    "page_errors": page_errors[:5],
                }
            )[:1200],
        )
        forms_responses = [
            entry
            for entry in browser.recorder.responses
            if "capability-forms" in entry.get("url", "")
        ]
        _assertion(
            context,
            "browser.network_form_and_preflight",
            bool(forms_responses)
            and all(200 <= entry["status"] < 300 for entry in forms_responses)
            and any(
                "preflight" in entry.get("url", "")
                and entry.get("method") == "POST"
                and entry["status"] == 200
                for entry in browser.recorder.responses
            ),
            json.dumps(
                {
                    "forms": forms_responses,
                    "preflight": [
                        entry
                        for entry in browser.recorder.responses
                        if "preflight" in entry.get("url", "")
                    ],
                }
            )[:1200],
        )

        browser.close()
    except BrowserPrerequisiteError as exc:
        return _not_verified(context, f"browser_prerequisite:{exc}")
    finally:
        try:
            playwright.stop()
        except Exception:  # noqa: BLE001 - teardown must not mask the verdict
            pass
        platform.stop()

    _write_json(out, "schema-form-live.json", result)
    _add_artifact(context, "schema-form-live.json")
    context.extensions.update(
        {
            "experience.schema_form_capability": plugin_env.CAPABILITY,
            "experience.schema_form_form_digest": form_entry.get("form_digest"),
            "experience.schema_form_descriptor_digest": form_entry.get("descriptor_digest"),
            "experience.schema_form_plan_digest": (result.get("view_model") or {}).get(
                "plan_digest"
            ),
            "experience.schema_form_submit_disabled": True,
            "experience.schema_form_approval_gate": "approval_required",
            "experience.schema_form_browser": result.get("browser"),
            "experience.schema_form_duration_seconds": round(time.time() - started, 1),
        }
    )
    publish_observations(
        context,
        {
            "transport": "present",
            "controller": "present",
            "model": "absent",
            "qc": "absent",
            "lsf": "absent",
            "browser": "present",
            "artifact": "absent",
            "denial": "absent",
        },
        model_calls=0,
        model_evidence=(
            "schema-form-live drives the Workbench capability-form panel in a real "
            "Chromium against the real Controller; no model client is invoked (0 calls)"
        ),
    )
    return _finish(context, "schema_form_assertion_failed")


register(SCENARIO_ID, handle)
