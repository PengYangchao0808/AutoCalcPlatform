#!/usr/bin/env python3.11
"""M4-05 — the third-party capability plugin vertical slice (G16/G18).

This is the acceptance orchestrator for the M4-05 checkpoint.  It proves that a
third-party science capability becomes usable through the **real public path**
and nowhere else — no ACP/RPH/Workbench source change, no in-test
``register()``:

==============  =============================================================
step            what really happens
==============  =============================================================
``environment`` two committed fixture distributions (the A-22 external plugin
                and the A-23 v1/v2 upgrade pair) are built offline and
                installed with ``--no-deps --no-index`` into a throw-away venv
``enable``      the operator CLI (``acp science-plugin list/enable``) reads the
                installed fact and writes the run root's enablement ledger
                (installed/enabled/enabled_by/enabled_at + descriptor and
                computed distribution digest)
``discovery``   a **new** Controller process discovers the capability on both
                capabilities surfaces and serves its third-party form
``form``        the real Workbench modules render the served form, commit a
                value through the rendered control, refuse an untyped numeric
                edit and display the real preflight ViewModel
``execution``   one prepared artifact is analysed by the enabled capability
                through the plugin host; provenance carries
                capability@version + descriptor/form/computed distribution
                digests; a tampered ref and an invented capability are refused
``upgrade``     the upgrade pair moves 1.0.0 → 2.0.0; a new Controller
                discovers 2.0.0, the next analysis freezes the new pin and the
                new computed distribution digest, and the 1.0.0 frozen
                descriptor stays readable
``browser``     the registered ``schema-form-live`` C row runs in a real
                Chromium through ``scripts/acceptance/acp2_experience.py``
``uninstall``   the external distribution is removed; a new Controller no
                longer discovers it, the same capability is projected
                ``read_only`` from the frozen descriptor, and execution is
                refused — the historical product still digest-verifies
``negatives``   incompatible API range, unknown licence, insufficient
                authorization and installed-bytes/digest mismatch all fail
                closed through the same operator path; a control install proves
                the mutation caused the refusal
==============  =============================================================

Declared boundaries (never silently assumed):

* the **upstream science run** is a seeded completed task row holding the
  fixture's fixed native record — the plugin analysis consumes a real prepared
  artifact but no ORCA/xTB/LSF/model process is started by this slice;
* the A-23 v1/v2 pair ships no UI form, so the form leg uses the A-22 external
  plugin (the one that ships ``cccp_ui_form.json``);
* the Workbench has no dedicated panel for a third-party analysis *product*;
  the capability-form panel displays the preflight ViewModel and the product is
  surfaced by the analyze response with its digests (a scope limitation, not a
  hidden pass).

Exit codes: ``0`` PASS, ``1`` FAIL, ``2`` NOT_VERIFIED (a prerequisite such as
Node or Playwright is missing), ``3`` usage error.

Usage::

    set -a; . /ACPServer/acp2-runs/remediation/env.sh; set +a
    PYTHONPATH=src python3.11 scripts/acceptance/acp2_m405.py \
        --out reports/acp2_remediation_20261026/m4-05/
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_PATH = Path(__file__).resolve()
REPO = SCRIPT_PATH.parents[2]
sys.path.insert(0, str(SCRIPT_PATH.parent))
sys.path.insert(0, str(REPO / "src"))

import acp2_m405_plugin_env as plugin_env  # noqa: E402 - sibling acceptance module
from acp2_thin_slice import (  # noqa: E402 - sibling acceptance module
    EXIT_FAIL,
    EXIT_NOT_VERIFIED,
    EXIT_PASS,
    SliceError,
    Trace,
    digest_of,
    file_digest,
    run_node,
    utc_now,
    which_node,
)

SCHEMA = "acp2.m4-05.plugin-slice.v1"
EXPERIENCE_HARNESS = SCRIPT_PATH.parent / "acp2_experience.py"
FORM_DRIVER = SCRIPT_PATH.parent / "acp2_m405_form.js"
PROBE = SCRIPT_PATH.parent / "acp2_m405_probe.py"
BROWSER_SCENARIO = "schema-form-live"

FIXTURE_INPUT = plugin_env.FIXTURE_ROOT / "fixtures" / "native_singlepoint.acp2n"
FIXTURE_EXPECTED = plugin_env.FIXTURE_ROOT / "fixtures" / "expected_output.json"
UPGRADE_NATIVE = plugin_env.UPGRADE_ROOT / "v1" / "fixtures" / "native_singlepoint.json"
UPGRADE_EXPECTED_V2 = plugin_env.UPGRADE_ROOT / "v2" / "fixtures" / "expected_output.json"
APPROVAL_HINT = "An approved plan is required before submit"

#: Job identifiers are fixed so every run's evidence names the same task.
JOB_EXTERNAL = "20261028_090000_101_m405plugin"
JOB_UPGRADE_V1 = "20261028_090000_102_m405upgradev1"
JOB_UPGRADE_V2 = "20261028_090000_103_m405upgradev2"
JOB_NEGATIVE = "20261028_090000_104_m405negative"


class M405:
    """One M4-05 acceptance run."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.out = Path(args.out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.work = Path(args.work_root).resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.python = sys.executable
        self.node = which_node()
        self.trace = Trace(self.out)
        self.steps: list[dict[str, Any]] = []
        self.reasons: list[str] = []
        self.not_verified: list[dict[str, Any]] = []
        self.status = "FAIL"
        self.venv_python: Path | None = None
        self.run_root = self.work / "run-root"
        self.negatives_root = self.work / "negatives"
        self.wheels: dict[str, Path] = {}
        self.records: dict[str, Any] = {}
        self.documents: dict[str, Any] = {}
        self.findings: list[dict[str, Any]] = []

    # -- plumbing ---------------------------------------------------------

    def step(self, name: str, fn: Any, *, critical: bool = True) -> Any:
        started = time.perf_counter()
        try:
            value = fn()
        except SliceError as exc:
            self._step_failed(name, str(exc), started, critical)
            if critical:
                raise
            return None
        except Exception as exc:  # noqa: BLE001 - the trace is the evidence
            self._step_failed(name, f"{type(exc).__name__}: {exc}", started, critical)
            if critical:
                raise
            return None
        duration = round((time.perf_counter() - started) * 1000, 2)
        self.steps.append({"name": name, "status": "PASS", "duration_ms": duration})
        self.trace.record(name, response=self._summary_of(value), duration_ms=duration)
        return value

    def _step_failed(self, name: str, reason: str, started: float, critical: bool) -> None:
        duration = round((time.perf_counter() - started) * 1000, 2)
        self.steps.append(
            {"name": name, "status": "FAIL", "reason": reason, "duration_ms": duration}
        )
        self.trace.record(name, status="FAIL", response={"message": reason}, duration_ms=duration)
        self.reasons.append(f"{name}: {reason}")
        if critical:
            raise SliceError(name, reason)

    @staticmethod
    def _summary_of(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: value[key] for key in list(value)[:12]}
        return value

    def write(self, name: str, document: Any) -> Path:
        path = self.out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
        )
        return path

    def record(self, name: str, document: Any) -> dict[str, Any]:
        path = self.write(f"{name}.json", document)
        self.records[name] = {"path": path.name, "digest": file_digest(path)}
        self.documents[name] = document
        self.trace.record(
            f"evidence.{name}",
            response={"bytes": path.stat().st_size},
            digest=self.records[name]["digest"],
        )
        return document

    # -- helpers ----------------------------------------------------------

    def cli(
        self,
        args: list[str],
        *,
        run_root: Path | None = None,
        trusted: tuple[str, ...] = (plugin_env.CAPABILITY, plugin_env.UPGRADE_CAPABILITY),
        principals: str | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        assert self.venv_python is not None
        return plugin_env.run_operator_cli(
            self.venv_python,
            args,
            run_root=run_root or self.run_root,
            trusted=trusted,
            principals=principals,
        )

    def probe(self, *args: str) -> dict[str, Any]:
        assert self.venv_python is not None
        result = subprocess.run(
            [str(self.venv_python), str(PROBE), *args],
            cwd=str(REPO),
            env=plugin_env.venv_env(),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if result.returncode != 0:
            raise SliceError("probe", f"{args[0]} probe failed: {result.stderr[-2000:]}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise SliceError("probe", f"{args[0]} probe emitted no JSON: {exc}") from exc
        return dict(payload)

    @staticmethod
    def default_project(base_url: str) -> str:
        status, payload = plugin_env.http_json(base_url, "GET", "/api/v1/projects")
        if status != 200 or not isinstance(payload, dict):
            raise SliceError("project", f"GET /api/v1/projects -> HTTP {status}")
        projects = payload.get("projects") or payload.get("items") or []
        if not projects:
            raise SliceError("project", "the Controller reports no project")
        preferred = [item for item in projects if item.get("is_default") or item.get("default")]
        entry = preferred[0] if preferred else projects[0]
        project_id = str(entry.get("project_id") or entry.get("id") or "")
        if not project_id:
            raise SliceError("project", f"project row has no id: {entry}")
        return project_id

    def prepare(
        self, base_url: str, job_id: str, *, attempt: int, artifact_id: str, relative_path: str
    ) -> dict[str, Any]:
        from acp_services.entrypoints.controller_client import ControllerClient

        client = ControllerClient(base_url)
        prepared = dict(
            client.prepare(
                {
                    "schema_version": 1,
                    "purpose": "artifact",
                    "job_id": job_id,
                    "artifact_id": artifact_id,
                    "attempt": attempt,
                    "generation": 1,
                    "relative_path": relative_path,
                    "media_type": "text/plain",
                }
            )
        )
        deadline = time.monotonic() + 30.0
        operation: dict[str, Any] = {}
        while time.monotonic() < deadline:
            operation = dict(client.operation(str(prepared["operation_id"])))
            if operation.get("state") in {"ready", "failed", "cancelled"}:
                break
            time.sleep(0.02)
        if operation.get("state") != "ready":
            raise SliceError("prepare", f"prepare did not settle ready: {operation}")
        ref = operation.get("artifact_ref")
        if not isinstance(ref, dict):
            raise SliceError("prepare", f"prepare returned no artifact ref: {operation}")
        return {
            "task_id": job_id,
            "attempt": attempt,
            "artifact_id": artifact_id,
            "generation": int(ref["generation"]),
            "digest": str(ref["digest"]),
            "operation": operation,
            "request": prepared,
        }

    def analyze(
        self,
        base_url: str,
        job_id: str,
        *,
        capability: str,
        ref: dict[str, Any],
        envelope_task: str = "singlepoint",
    ) -> tuple[int, Any]:
        return plugin_env.http_json(
            base_url,
            "POST",
            f"/api/v1/jobs/{job_id}/analyze",
            {
                "analysis_id": capability,
                "input_refs": [
                    {
                        "task_id": ref["task_id"],
                        "attempt": ref["attempt"],
                        "artifact_id": ref["artifact_id"],
                        "generation": ref["generation"],
                        "digest": ref["digest"],
                    }
                ],
                "parameters": {"envelope": {"task": envelope_task}},
                "explicit": True,
            },
        )

    # -- legs -------------------------------------------------------------

    def leg_environment(self) -> dict[str, Any]:
        root = self.work / "environment"
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        wheel_out = root / "wheel"
        external_wheel, external_build = plugin_env.build_fixture_wheel(
            plugin_env.FIXTURE_ROOT, wheel_out
        )
        upgrade_v1_wheel, upgrade_v1_build = plugin_env.build_fixture_wheel(
            plugin_env.UPGRADE_ROOT / "v1", wheel_out
        )
        upgrade_v2_wheel, upgrade_v2_build = plugin_env.build_fixture_wheel(
            plugin_env.UPGRADE_ROOT / "v2", wheel_out
        )
        python, venv_command = plugin_env.create_isolated_venv(root)
        external_install = plugin_env.install_wheel(python, external_wheel)
        upgrade_install = plugin_env.install_wheel(python, upgrade_v1_wheel)
        self.venv_python = python
        self.wheels = {
            "external_v1": external_wheel,
            "upgrade_v1": upgrade_v1_wheel,
            "upgrade_v2": upgrade_v2_wheel,
        }
        installed = self.probe("facts", str(self.run_root))
        data = {
            "schema": SCHEMA,
            "python": sys.executable,
            "venv_python": str(python),
            "venv_root": str(root),
            "build": {
                str(path.name): {
                    "wheel": str(path),
                    "sha256": file_digest(path),
                    "command": command.to_dict(),
                }
                for path, command in (
                    (external_wheel, external_build),
                    (upgrade_v1_wheel, upgrade_v1_build),
                    (upgrade_v2_wheel, upgrade_v2_build),
                )
            },
            "venv": venv_command.to_dict(),
            "install": {
                "external": external_install.to_dict(),
                "upgrade_v1": upgrade_install.to_dict(),
            },
            "installed_before_enable": installed,
            "install_is_offline": all(
                "--no-deps" in item.argv and "--no-index" in item.argv
                for item in (external_install, upgrade_install)
            )
            and "--no-isolation" in external_build.argv,
        }
        if not data["install_is_offline"]:
            raise SliceError("environment", "an install was not offline")
        capabilities = sorted(entry["capability_id"] for entry in installed.get("installed") or [])
        expected = sorted([plugin_env.CAPABILITY, plugin_env.UPGRADE_CAPABILITY])
        if capabilities != expected:
            raise SliceError(
                "environment",
                f"the isolated environment discovered {capabilities}, expected {expected}",
            )
        if not all(entry["matches_declared"] for entry in installed.get("installed") or []):
            raise SliceError(
                "environment",
                "an installed distribution's computed bytes disagree with its declaration",
            )
        return self.record("environment", data)

    def leg_enable(self) -> dict[str, Any]:
        listed, list_payload = self.cli(["list"])
        installed = list_payload.get("installed") or []
        external = next(
            (entry for entry in installed if entry["capability_id"] == plugin_env.CAPABILITY),
            None,
        )
        if listed.returncode != 0 or external is None:
            raise SliceError(
                "enable",
                f"the operator list did not see the installed capability: {list_payload}",
            )
        computed = (external.get("distribution_digest") or {}).get("digest")
        enabled, enable_payload = self.cli(["enable", "--capability", plugin_env.CAPABILITY])
        if enabled.returncode != 0 or enable_payload.get("status") != "ok":
            raise SliceError("enable", f"the external capability was not enabled: {enable_payload}")
        enabled_upgrade, upgrade_payload = self.cli(
            ["enable", "--capability", plugin_env.UPGRADE_CAPABILITY]
        )
        if enabled_upgrade.returncode != 0 or upgrade_payload.get("status") != "ok":
            raise SliceError("enable", f"the upgrade pair v1 was not enabled: {upgrade_payload}")
        after, after_payload = self.cli(["list"])
        executable = sorted(after_payload.get("executable") or [])
        ledger = (
            self.run_root
            / "science-plugins"
            / "plugins"
            / plugin_env.PLUGIN
            / "1.0.0"
            / plugin_env.CAPABILITY
            / "enablement.json"
        )
        if not ledger.is_file():
            raise SliceError("enable", f"no persisted enablement at {ledger}")
        document = json.loads(ledger.read_text(encoding="utf-8"))
        record = enable_payload.get("enabled") or {}
        data = {
            "list_before": list_payload,
            "enable_command": enabled.to_dict(),
            "enablement": enable_payload,
            "enable_command_upgrade": enabled_upgrade.to_dict(),
            "enablement_upgrade": upgrade_payload,
            "list_after": after_payload,
            "persisted_ledger": {
                "path": str(ledger),
                "sha256": file_digest(ledger),
                "document": document,
            },
            "installed_computed_distribution_digest": computed,
            "persisted_computed_distribution_digest": record.get("computed_distribution_digest"),
            "descriptor_digest": record.get("descriptor_digest"),
            "executable": executable,
        }
        if executable != sorted([plugin_env.CAPABILITY, plugin_env.UPGRADE_CAPABILITY]):
            raise SliceError(
                "enable", f"the ledger did not report both capabilities executable: {executable}"
            )
        if record.get("computed_distribution_digest") != computed:
            raise SliceError(
                "enable",
                "the persisted digest is not the computed distribution digest",
            )
        if not record.get("enabled_by") or not record.get("enabled_at"):
            raise SliceError("enable", "the enablement record lost its operator attribution")
        if document.get("enabled") is not True:
            raise SliceError("enable", "the persisted ledger does not say enabled")
        return self.record("enable", data)

    def _discovery_document(self, base_url: str, project: str) -> dict[str, Any]:
        status, inventory = plugin_env.http_json(base_url, "GET", "/api/capabilities")
        if status != 200 or not isinstance(inventory, dict):
            raise SliceError("discovery", f"GET /api/capabilities -> HTTP {status}")
        status_v2, v2 = plugin_env.http_json(
            base_url, "GET", f"/api/v2/science/capabilities?project_id={project}"
        )
        if status_v2 != 200 or not isinstance(v2, dict):
            raise SliceError("discovery", f"GET /api/v2/science/capabilities -> HTTP {status_v2}")
        status_forms, forms = plugin_env.http_json(
            base_url, "GET", f"/api/v2/science/capability-forms?project_id={project}"
        )
        if status_forms != 200 or not isinstance(forms, dict):
            raise SliceError(
                "discovery", f"GET /api/v2/science/capability-forms -> HTTP {status_forms}"
            )
        return {
            "inventory": inventory,
            "v2": v2,
            "forms": forms,
            "statuses": {
                "capabilities": status,
                "v2_capabilities": status_v2,
                "capability_forms": status_forms,
            },
        }

    @staticmethod
    def _plugin_entries(inventory: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            entry
            for entry in inventory.get("capability_descriptors") or []
            if entry.get("capability_source") == "science_plugin"
        ]

    def leg_discovery(self, base_url: str) -> dict[str, Any]:
        project = self.default_project(base_url)
        documents = self._discovery_document(base_url, project)
        entries = self._plugin_entries(documents["inventory"])
        v2_entries = [
            entry
            for entry in (documents["v2"].get("capabilities") or {})
            .get("descriptors", {})
            .get("capabilities", [])
            if entry.get("capability_source") == "science_plugin"
        ]
        forms = documents["forms"]
        form_entries = {entry["capability_id"]: entry for entry in forms.get("forms") or []}
        external = next(
            (entry for entry in entries if entry["capability_id"] == plugin_env.CAPABILITY),
            None,
        )
        upgrade = next(
            (entry for entry in entries if entry["capability_id"] == plugin_env.UPGRADE_CAPABILITY),
            None,
        )
        builtin = sorted(
            entry["capability_id"]
            for entry in documents["inventory"].get("capability_descriptors") or []
            if entry.get("capability_source") != "science_plugin"
        )
        data = {
            "project_id": project,
            "statuses": documents["statuses"],
            "plugin_entries": entries,
            "v2_plugin_entries": v2_entries,
            "descriptors_agree": entries == v2_entries,
            "builtin_capability_ids": builtin,
            "analysis_entries": documents["v2"].get("capabilities", {}).get("analysis"),
            "inventory_analysis": documents["inventory"].get("analysis_capabilities"),
            "form_subset_version": forms.get("subset_version"),
            "form_subset_digest": forms.get("subset_digest"),
            "catalog_etag": forms.get("catalog_etag"),
            "form_entries": form_entries,
            "external_form": form_entries.get(plugin_env.CAPABILITY),
            "upgrade_form": form_entries.get(plugin_env.UPGRADE_CAPABILITY),
            "read_only": forms.get("read_only") or [],
        }
        if external is None or upgrade is None:
            raise SliceError("discovery", "a discovery surface lost an enabled plugin capability")
        if not data["descriptors_agree"]:
            raise SliceError("discovery", "the two capabilities surfaces disagree")
        external_form = data["external_form"]
        if not isinstance(external_form, dict) or external_form.get("state") != "available":
            raise SliceError("discovery", f"the external form is not served: {external_form}")
        if not str(external_form.get("form_origin") or "").startswith("plugin:"):
            raise SliceError("discovery", "the served form is not the plugin's form")
        if not (external.get("distribution_digest") or {}).get("matches_declared"):
            raise SliceError("discovery", "the discovered descriptor is not byte-verified")
        analysis = {
            entry["analysis_id"]: entry
            for entry in documents["v2"].get("capabilities", {}).get("analysis") or []
        }
        data["analysis_executable"] = {
            name: entry.get("state") for name, entry in sorted(analysis.items())
        }
        if (analysis.get(plugin_env.CAPABILITY) or {}).get("state") != "executable":
            raise SliceError("discovery", "the capability is discovered but not executable")
        return self.record("discovery", data)

    def leg_form(self, base_url: str) -> dict[str, Any]:
        if self.node is None:
            raise SliceError("form", "node is not available for the Workbench modules")
        discovery = self.documents["discovery"]
        external_form = discovery["external_form"]
        subset_digest = discovery["form_subset_digest"]
        scenario = {
            "repo_root": str(REPO),
            "base_url": base_url,
            "project_id": discovery["project_id"],
            "capability_id": plugin_env.CAPABILITY,
            "subset_version": discovery["form_subset_version"],
            "subset_digest": subset_digest,
            "form_digest": external_form["form_digest"],
            "expected_title": (external_form.get("form") or {}).get("title") or "",
            "expected_workflow": "singlepoint",
            "expected_edits": ["task.charge", "task.structure.source"],
            "structure_text": FIXTURE_INPUT.read_text(encoding="utf-8"),
            "numeric_edit": "2",
            "numeric_restore": 0,
            "approval_hint": APPROVAL_HINT,
            "store_module": "frontend/js/client/resource-store.js",
            "experience_module": "frontend/js/workbench/contracts/experience_contract.js",
            "subset_module": "frontend/js/workbench/contracts/ui_subset.js",
            "science_module": "frontend/js/client/science-query.js",
            "feature_module": "frontend/js/workbench/features/capability-form.js",
            "now": "2026-10-28T00:00:00.000Z",
        }
        scenario_path = self.out / "form-scenario.json"
        scenario_path.write_text(json.dumps(scenario, indent=2) + "\n", encoding="utf-8")
        completed = run_node([self.node, str(FORM_DRIVER), str(scenario_path)], timeout=180)
        (self.out / "form-driver.log").write_text(
            completed.stdout[-20000:] + "\n--- stderr ---\n" + completed.stderr[-10000:],
            encoding="utf-8",
        )
        raw = completed.stdout.strip()
        if not raw:
            raise SliceError(
                "form", f"the Workbench driver wrote no output: {completed.stderr[-800:]}"
            )
        payload = json.loads(raw)
        if payload.get("ok") is not True:
            raise SliceError(
                "form",
                "the Workbench driver refused: "
                + json.dumps(payload.get("error") or payload)[-1200:],
            )
        steps = {step["name"]: step for step in payload.get("steps") or []}
        for required in (
            "discovery.capability_forms",
            "discovery.form_entry",
            "form.render",
            "form.numeric_edit_stored_verbatim",
            "preflight.numeric_edit_refused",
            "preflight",
            "view_model",
            "discovery.capabilities",
            "descriptor_cross_check",
        ):
            if required not in steps or steps[required].get("ok") is not True:
                raise SliceError("form", f"the Workbench driver has no passing step {required!r}")
        view_model = steps["view_model"]["value"]
        if view_model.get("descriptor_digest") != discovery["external_form"]["descriptor_digest"]:
            raise SliceError("form", "the rendered ViewModel names a different descriptor")
        data = {
            "driver": str(FORM_DRIVER),
            "exit_code": completed.returncode,
            "steps": payload.get("steps"),
            "view_model": view_model,
            "rendered_ids": steps["form.render"]["value"].get("ids"),
            "numeric_edit_refusal": steps["preflight.numeric_edit_refused"]["value"],
            "submit": view_model.get("submit"),
        }
        return self.record("form", data)

    def leg_execution(self, base_url: str) -> dict[str, Any]:
        project = self.documents["discovery"]["project_id"]
        seeded_external = plugin_env.seed_completed_task(
            self.run_root,
            job_id=JOB_EXTERNAL,
            project_id=project,
            relative_path="RESULT/native.acp2n",
            payload=FIXTURE_INPUT.read_bytes(),
        )
        prepared_external = self.prepare(
            base_url,
            JOB_EXTERNAL,
            attempt=1,
            artifact_id="native_record",
            relative_path="RESULT/native.acp2n",
        )
        status, product = self.analyze(
            base_url,
            JOB_EXTERNAL,
            capability=plugin_env.CAPABILITY,
            ref=prepared_external,
        )
        if status != 200 or not isinstance(product, dict):
            raise SliceError("execution", f"POST analyze -> HTTP {status}: {product}")
        self.write("product-external-v1.json", product)
        analysis = product["result"]
        result = analysis["product"]["task_result"]
        provenance = analysis["provenance"]
        expected = json.loads(FIXTURE_EXPECTED.read_text(encoding="utf-8"))
        checks = {
            "state_computed": analysis["state"] == "computed",
            "started_nothing": product.get("started") is False,
            "output_schema": analysis["product"]["capability_output_schema"] == plugin_env.SCHEMA,
            "task_completed": result["status"] == "completed" and result["complete"] is True,
            "energy_matches_fixture": result["energy_hartree"] == expected["energy_hartree"],
            "atom_order": result["metadata"]["atom_order"] == expected["atom_order"],
            "pin_version": provenance["capability_version"] == "1.0.0",
            "pin_ref": provenance["capability_ref"] == f"{plugin_env.CAPABILITY}@1.0.0",
            "descriptor_digest": provenance["descriptor_digest"]
            == self.documents["discovery"]["external_form"]["descriptor_digest"],
            "analysis_form_digest": str(provenance["form_digest"]).startswith("sha256:"),
            "computed_distribution_digest": provenance["computed_distribution_digest"]
            == self.documents["enable"]["persisted_computed_distribution_digest"],
            "distribution_record": provenance["distribution_digest"]["digest"]
            == provenance["computed_distribution_digest"],
            "no_subprocess": provenance["starts_subprocess"] is False,
            "no_task": provenance["starts_task"] is False,
            "controller_executed": provenance["executed_in"] == "controller",
            "input_digest": provenance["input_digests"] == [prepared_external["digest"]],
        }
        failed = sorted(name for name, ok in checks.items() if not ok)
        if failed:
            raise SliceError("execution", f"plugin analysis failed checks: {failed}")

        tampered_status, tampered = plugin_env.http_json(
            base_url,
            "POST",
            f"/api/v1/jobs/{JOB_EXTERNAL}/analyze",
            {
                "analysis_id": plugin_env.CAPABILITY,
                "input_refs": [
                    {
                        "task_id": prepared_external["task_id"],
                        "attempt": prepared_external["attempt"],
                        "artifact_id": prepared_external["artifact_id"],
                        "generation": prepared_external["generation"],
                        "digest": "sha256:" + "0" * 64,
                    }
                ],
                "parameters": {"envelope": {"task": "singlepoint"}},
                "explicit": True,
            },
        )
        unknown_status, unknown = plugin_env.http_json(
            base_url,
            "POST",
            f"/api/v1/jobs/{JOB_EXTERNAL}/analyze",
            {
                "analysis_id": "plugin.invented",
                "input_refs": [
                    {
                        "task_id": prepared_external["task_id"],
                        "attempt": prepared_external["attempt"],
                        "artifact_id": prepared_external["artifact_id"],
                        "generation": prepared_external["generation"],
                        "digest": prepared_external["digest"],
                    }
                ],
                "parameters": {"envelope": {"task": "singlepoint"}},
                "explicit": True,
            },
        )
        tampered_detail = (tampered or {}).get("detail", {}) if isinstance(tampered, dict) else {}
        unknown_detail = (unknown or {}).get("detail", {}) if isinstance(unknown, dict) else {}
        refusal_checks = {
            "tampered_status": tampered_status == 409,
            "tampered_rule": (tampered_detail.get("params") or {}).get("rule") == "digest_mismatch",
            "unknown_status": unknown_status == 422,
            "unknown_code": unknown_detail.get("code") == "unsupported_capability",
            "unknown_allowed_names_plugin": plugin_env.CAPABILITY
            in ((unknown_detail.get("params") or {}).get("allowed") or []),
        }
        failed = sorted(name for name, ok in refusal_checks.items() if not ok)
        if failed:
            raise SliceError("execution", f"analysis refusals are wrong: {failed}")

        seeded_upgrade = plugin_env.seed_completed_task(
            self.run_root,
            job_id=JOB_UPGRADE_V1,
            project_id=project,
            relative_path="RESULT/native.json",
            payload=UPGRADE_NATIVE.read_bytes(),
        )
        prepared_upgrade = self.prepare(
            base_url,
            JOB_UPGRADE_V1,
            attempt=1,
            artifact_id="native_record",
            relative_path="RESULT/native.json",
        )
        upgrade_status, upgrade_product = self.analyze(
            base_url,
            JOB_UPGRADE_V1,
            capability=plugin_env.UPGRADE_CAPABILITY,
            ref=prepared_upgrade,
        )
        if upgrade_status != 200 or not isinstance(upgrade_product, dict):
            raise SliceError(
                "execution", f"upgrade v1 analyze -> HTTP {upgrade_status}: {upgrade_product}"
            )
        self.write("product-upgrade-v1.json", upgrade_product)
        upgrade_provenance = upgrade_product["result"]["provenance"]
        if upgrade_provenance["capability_version"] != "1.0.0":
            raise SliceError("execution", "the upgrade pair v1 analysis froze a wrong pin")
        data = {
            "job": seeded_external,
            "upgrade_job": seeded_upgrade,
            "artifact_ref": {
                key: prepared_external[key]
                for key in ("task_id", "attempt", "artifact_id", "generation", "digest")
            },
            "artifact_operation": prepared_external["operation"],
            "product": product,
            "product_digest": analysis["product"]["digest"],
            "provenance": provenance,
            "ui_form_digest": self.documents["discovery"]["external_form"]["form_digest"],
            "analysis_form_digest": provenance["form_digest"],
            "checks": checks,
            "refusals": {
                "tampered": {"status": tampered_status, "detail": tampered_detail},
                "unknown": {"status": unknown_status, "detail": unknown_detail},
                "checks": refusal_checks,
            },
            "upgrade_v1": {
                "job": seeded_upgrade,
                "artifact_ref": prepared_upgrade,
                "provenance": upgrade_provenance,
                "product_digest": upgrade_product["result"]["product"]["digest"],
            },
            "product_files": {
                "external_v1": "product-external-v1.json",
                "upgrade_v1": "product-upgrade-v1.json",
            },
        }
        return self.record("execution", data)

    def leg_upgrade(self) -> dict[str, Any]:
        assert self.venv_python is not None
        uninstall = plugin_env.uninstall_distribution(
            self.venv_python, plugin_env.UPGRADE_DISTRIBUTION
        )
        install = plugin_env.install_wheel(self.venv_python, self.wheels["upgrade_v2"])
        enabled, enable_payload = self.cli(
            ["enable", "--capability", plugin_env.UPGRADE_CAPABILITY]
        )
        if enabled.returncode != 0 or enable_payload.get("status") != "ok":
            raise SliceError("upgrade", f"the v2 pin was not enabled: {enable_payload}")
        listed, list_payload = self.cli(["list"])
        if listed.returncode != 0:
            raise SliceError("upgrade", f"the operator list failed after upgrade: {listed.log}")
        record = enable_payload.get("enabled") or {}
        if record.get("version") != "2.0.0":
            raise SliceError("upgrade", f"the upgraded enablement names {record.get('version')}")
        v1_enablement = next(
            (
                entry
                for entry in list_payload.get("enablements") or []
                if entry["capability_id"] == plugin_env.UPGRADE_CAPABILITY
                and entry["version"] == "1.0.0"
            ),
            None,
        )
        if v1_enablement is None:
            raise SliceError("upgrade", "the v1 enablement disappeared from the ledger")
        executable = sorted(list_payload.get("executable") or [])
        if plugin_env.UPGRADE_CAPABILITY not in executable:
            raise SliceError("upgrade", "the upgraded capability is not executable")
        return self.record(
            "upgrade-install",
            {
                "uninstall": uninstall.to_dict(),
                "install": install.to_dict(),
                "enable_command": enabled.to_dict(),
                "enablement": enable_payload,
                "list": list_payload,
                "v1_enablement": v1_enablement,
                "executable": executable,
                "installed_v2_wheel": {
                    "path": str(self.wheels["upgrade_v2"]),
                    "sha256": file_digest(self.wheels["upgrade_v2"]),
                },
            },
        )

    def leg_upgrade_after(self) -> dict[str, Any]:
        """New Controller: v2 discovery, v2 pin on a new analysis, v1 history."""
        project = self.documents["discovery"]["project_id"]
        seeded = plugin_env.seed_completed_task(
            self.run_root,
            job_id=JOB_UPGRADE_V2,
            project_id=project,
            relative_path="RESULT/native.json",
            payload=UPGRADE_NATIVE.read_bytes(),
        )
        controller = plugin_env.PluginController(
            python=self.venv_python,  # type: ignore[arg-type]
            run_root=self.run_root,
            log_path=self.out / "controller-upgrade.log",
            trusted=(plugin_env.CAPABILITY, plugin_env.UPGRADE_CAPABILITY),
        )
        with controller:
            base_url = controller.base_url
            documents = self._discovery_document(base_url, project)
            entries = self._plugin_entries(documents["inventory"])
            upgrade = next(
                (
                    entry
                    for entry in entries
                    if entry["capability_id"] == plugin_env.UPGRADE_CAPABILITY
                ),
                None,
            )
            external = next(
                (entry for entry in entries if entry["capability_id"] == plugin_env.CAPABILITY),
                None,
            )
            if upgrade is None or external is None:
                raise SliceError("upgrade.after", "discovery lost a capability after the upgrade")
            if upgrade["plugin"]["version"] != "2.0.0":
                raise SliceError(
                    "upgrade.after",
                    f"discovery still reports {upgrade['plugin']['version']}",
                )
            if external["plugin"]["version"] != "1.0.0":
                raise SliceError("upgrade.after", "the unrelated capability changed version")
            old_digest = self.documents["execution"]["upgrade_v1"]["provenance"][
                "computed_distribution_digest"
            ]
            new_digest = (upgrade.get("distribution_digest") or {}).get("digest")
            if new_digest == old_digest:
                raise SliceError("upgrade.after", "the upgraded distribution digest did not change")
            prepared = self.prepare(
                base_url,
                JOB_UPGRADE_V2,
                attempt=1,
                artifact_id="native_record",
                relative_path="RESULT/native.json",
            )
            status, product = self.analyze(
                base_url,
                JOB_UPGRADE_V2,
                capability=plugin_env.UPGRADE_CAPABILITY,
                ref=prepared,
            )
            if status != 200 or not isinstance(product, dict):
                raise SliceError("upgrade.after", f"v2 analyze -> HTTP {status}: {product}")
            self.write("product-upgrade-v2.json", product)
            provenance = product["result"]["provenance"]
        history_v1 = self.probe(
            "history",
            str(self.run_root),
            plugin_env.UPGRADE_PLUGIN,
            "1.0.0",
            str(self.out / "product-upgrade-v1.json"),
        )
        history_v2 = self.probe(
            "history",
            str(self.run_root),
            plugin_env.UPGRADE_PLUGIN,
            "2.0.0",
        )
        checks = {
            "v2_pin": provenance["capability_version"] == "2.0.0",
            "v2_ref": provenance["capability_ref"] == f"{plugin_env.UPGRADE_CAPABILITY}@2.0.0",
            "new_computed_digest": provenance["computed_distribution_digest"] == new_digest,
            "digest_changed": provenance["computed_distribution_digest"] != old_digest,
            "v1_snapshot_readable": history_v1["snapshot"]["read_only"] is True,
            "v1_snapshot_matches_v1_product": bool(
                (history_v1.get("product") or {}).get("snapshot_descriptor_matches_product")
            ),
            "v1_snapshot_distribution": bool(
                (history_v1.get("product") or {}).get("snapshot_distribution_matches_product")
            ),
            "v2_snapshot_readable": history_v2["snapshot"]["read_only"] is True,
            "v2_snapshot_digest": history_v2["snapshot"]["descriptor_digest"]
            == provenance["descriptor_digest"],
            "both_versions_in_ledger": sorted(
                record["version"]
                for record in history_v2["enablements"]
                if record["capability_id"] == plugin_env.UPGRADE_CAPABILITY
            )
            == ["1.0.0", "2.0.0"],
            "no_plugin_import": not history_v2["module_imported"],
        }
        failed = sorted(name for name, ok in checks.items() if not ok)
        if failed:
            raise SliceError("upgrade.after", f"upgrade checks failed: {failed}")
        data = {
            "job": seeded,
            "artifact_ref": prepared,
            "product_digest": product["result"]["product"]["digest"],
            "provenance": provenance,
            "discovery_upgrade_entry": upgrade,
            "discovery_external_entry": external,
            "old_computed_distribution_digest": old_digest,
            "new_computed_distribution_digest": new_digest,
            "history_v1": history_v1,
            "history_v2": history_v2,
            "checks": checks,
            "product_files": {"upgrade_v2": "product-upgrade-v2.json"},
        }
        return self.record("upgrade", data)

    def leg_uninstall(self) -> dict[str, Any]:
        assert self.venv_python is not None
        project = self.documents["discovery"]["project_id"]
        uninstall = plugin_env.uninstall_distribution(self.venv_python, plugin_env.DISTRIBUTION)
        listed, list_payload = self.cli(["list"])
        installed_ids = sorted(
            entry["capability_id"] for entry in list_payload.get("installed") or []
        )
        if plugin_env.CAPABILITY in installed_ids:
            raise SliceError("uninstall", "the operator list still sees the removed distribution")
        refusals = list_payload.get("refusals") or []
        controller = plugin_env.PluginController(
            python=self.venv_python,  # type: ignore[arg-type]
            run_root=self.run_root,
            log_path=self.out / "controller-uninstall.log",
            trusted=(plugin_env.CAPABILITY, plugin_env.UPGRADE_CAPABILITY),
        )
        with controller:
            base_url = controller.base_url
            documents = self._discovery_document(base_url, project)
            entries = self._plugin_entries(documents["inventory"])
            if any(entry["capability_id"] == plugin_env.CAPABILITY for entry in entries):
                raise SliceError("uninstall", "discovery still lists the removed capability")
            read_only = {
                entry["capability_id"]: entry for entry in documents["forms"].get("read_only") or []
            }
            historical = read_only.get(plugin_env.CAPABILITY)
            if historical is None:
                raise SliceError("uninstall", "the removed capability has no read-only form entry")
            if historical.get("state") != "read_only":
                raise SliceError("uninstall", "the historical entry is not read_only")
            if historical.get("reason") != "capability_not_active":
                raise SliceError("uninstall", "the historical entry names another reason")
            frozen_digest = self.documents["execution"]["provenance"][
                "computed_distribution_digest"
            ]
            frozen_descriptor = self.documents["discovery"]["external_form"]["descriptor_digest"]
            if historical.get("descriptor_digest") != frozen_descriptor:
                raise SliceError(
                    "uninstall", "the read-only entries do not carry the frozen descriptor digest"
                )
            prepared = self.prepare(
                base_url,
                JOB_EXTERNAL,
                attempt=1,
                artifact_id="native_record",
                relative_path="RESULT/native.acp2n",
            )
            refused_status, refused = self.analyze(
                base_url,
                JOB_EXTERNAL,
                capability=plugin_env.CAPABILITY,
                ref=prepared,
            )
        history = self.probe(
            "history",
            str(self.run_root),
            plugin_env.PLUGIN,
            "1.0.0",
            str(self.out / "product-external-v1.json"),
        )
        detail = (refused or {}).get("detail", {}) if isinstance(refused, dict) else {}
        checks = {
            "list_dropped_install": plugin_env.CAPABILITY not in installed_ids,
            "refusal_plugin_not_installed": any(
                entry.get("capability_id") == plugin_env.CAPABILITY
                and entry.get("reason") == "plugin_not_installed"
                for entry in refusals
            ),
            "discovery_absent": True,
            "read_only_entry": historical.get("state") == "read_only",
            "analyze_refused": refused_status == 422,
            "analyze_unsupported": detail.get("code") == "unsupported_capability",
            "history_snapshot_readable": history["snapshot"]["read_only"] is True,
            "history_snapshot_digest": history["snapshot"]["descriptor_digest"]
            == self.documents["execution"]["provenance"]["descriptor_digest"],
            "history_product_stable": bool(
                (history.get("product") or {}).get("product_digest_stable")
            ),
            "history_distribution": (history["snapshot"]["distribution_digest"] or {}).get("digest")
            == frozen_digest,
            "no_plugin_import": not history["module_imported"],
        }
        failed = sorted(name for name, ok in checks.items() if not ok)
        if failed:
            raise SliceError("uninstall", f"uninstall checks failed: {failed}")
        return self.record(
            "uninstall",
            {
                "uninstall": uninstall.to_dict(),
                "list": list_payload,
                "plugin_entries_after": entries,
                "read_only_entry": historical,
                "analyze_refusal": {"status": refused_status, "detail": detail},
                "history": history,
                "checks": checks,
            },
        )

    def leg_browser(self) -> dict[str, Any]:
        assert self.venv_python is not None
        browser_root = self.out / "browser"
        leaf = browser_root / BROWSER_SCENARIO
        if leaf.exists():
            shutil.rmtree(leaf)
        leaf.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "PYTHONPATH": str(REPO / "src"),
            "ACP2_PROJECT_ROOT": str(REPO),
            "ACP2_C_SOURCE": str(REPO),
            "ACP2_M405_VENV": str(self.venv_python),
            "ACP2_M405_ENV_ROOT": str(self.work / "browser-env"),
        }
        started = time.perf_counter()
        completed = subprocess.run(
            [
                self.python,
                str(EXPERIENCE_HARNESS),
                "--scenario",
                BROWSER_SCENARIO,
                "--mode",
                "live",
                "--out",
                str(leaf),
                "--timeout",
                "1200",
            ],
            cwd=str(REPO),
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        duration = round(time.perf_counter() - started, 1)
        (leaf / "harness.stdout.log").write_text(
            completed.stdout[-30000:] + "\n--- stderr ---\n" + completed.stderr[-20000:],
            encoding="utf-8",
        )
        receipts = sorted(leaf.glob("experience_*.json"))
        receipt: dict[str, Any] = {}
        if receipts:
            try:
                receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                receipt = {}
        # The row's transient runtime (identity DB, run root, SQLite WAL) is not
        # evidence: the receipt, screenshots, console/network records and the
        # ledger/form documents beside them are.  Keeping a live identity DB in
        # a git-tracked report root is noise, not proof.
        shutil.rmtree(leaf / "_runtime", ignore_errors=True)
        artifacts = receipt.get("artifacts") or []
        screenshots = [
            item.get("path") for item in artifacts if str(item.get("path", "")).endswith(".png")
        ]
        extensions = receipt.get("extensions") or {}
        row = {
            "scenario_id": BROWSER_SCENARIO,
            "invocation": [
                "--scenario",
                BROWSER_SCENARIO,
                "--mode",
                "live",
            ],
            "exit_code": completed.returncode,
            "duration_seconds": duration,
            "dir": str(leaf),
            "receipt_path": str(receipts[0]) if receipts else None,
            "receipt_digest": file_digest(receipts[0]) if receipts else None,
            "result": receipt.get("result"),
            "reason": receipt.get("reason"),
            "evidence_kind": receipt.get("evidence_kind"),
            "phase": receipt.get("phase"),
            "assertions": receipt.get("assertions") or [],
            "failed_assertions": [
                item.get("name")
                for item in receipt.get("assertions") or []
                if item.get("status") != "PASS"
            ],
            "screenshots": screenshots,
            "artifact_digests": {str(item.get("path")): item.get("sha256") for item in artifacts},
            "observations": (extensions.get("experience.observations") or {}).get("observed"),
            "declared_observations": extensions.get("experience.declared_observations"),
            "browser": extensions.get("experience.schema_form_browser"),
            "capability": extensions.get("experience.schema_form_capability"),
            "form_digest": extensions.get("experience.schema_form_form_digest"),
            "plan_digest": extensions.get("experience.schema_form_plan_digest"),
            "submit_disabled": extensions.get("experience.schema_form_submit_disabled"),
        }
        self.trace.record(
            f"browser.{BROWSER_SCENARIO}",
            request={"invocation": row["invocation"]},
            response={
                "result": row["result"],
                "reason": row["reason"],
                "failed": row["failed_assertions"],
            },
            digest=row["receipt_digest"] or digest_of(row["result"]),
            duration_ms=duration * 1000,
        )
        data = {
            "schema": "acp2.m4-05.browser-legs.v1",
            "scenario_ids": [BROWSER_SCENARIO],
            "rows": [row],
            "all_pass": row["result"] == "PASS",
        }
        if row["result"] not in {"PASS", "NOT_VERIFIED"}:
            self.record("browser", data)
            raise SliceError(
                "browser",
                f"the browser row did not pass: {row['result'] or 'missing'} "
                f"({row['reason'] or f'exit_{completed.returncode}'})",
            )
        if row["result"] == "NOT_VERIFIED":
            self.not_verified.append(
                {
                    "row": BROWSER_SCENARIO,
                    "status": row["result"],
                    "reason": row["reason"] or f"exit_{completed.returncode}",
                    "dir": str(leaf),
                }
            )
        return self.record("browser", data)

    def leg_negatives(self) -> dict[str, Any]:
        assert self.venv_python is not None
        root = self.negatives_root
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        run_root = root / "run-root"
        variant_root = root / "variants"
        rows: list[dict[str, Any]] = []

        # 1 + 2. Permission: the installed, byte-verified capability is refused
        # when the operator is not trusted and when the authorization principal
        # is not allowed to authorize capabilities.
        plugin_env.install_wheel(self.venv_python, self.wheels["external_v1"])
        untrusted, untrusted_payload = self.cli(
            ["enable", "--capability", plugin_env.CAPABILITY],
            run_root=run_root,
            trusted=(),
        )
        rows.append(
            {
                "row": "untrusted_capability",
                "argv": untrusted.argv,
                "exit_code": untrusted.returncode,
                "status": untrusted_payload.get("status"),
                "error": untrusted_payload.get("error"),
                "reason": str(untrusted_payload.get("reason") or "")[:800],
                "closed": untrusted.returncode != 0
                and untrusted_payload.get("status") == "failed"
                and untrusted_payload.get("error") == "capability_untrusted",
            }
        )
        unauthorized, unauthorized_payload = self.cli(
            [
                "enable",
                "--capability",
                plugin_env.CAPABILITY,
                "--authorized-by",
                "reviewer",
            ],
            run_root=run_root,
            principals="controller",
        )
        rows.append(
            {
                "row": "insufficient_authorization",
                "argv": unauthorized.argv,
                "exit_code": unauthorized.returncode,
                "status": unauthorized_payload.get("status"),
                "error": unauthorized_payload.get("error"),
                "reason": str(unauthorized_payload.get("reason") or "")[:800],
                "closed": unauthorized.returncode != 0
                and unauthorized_payload.get("error") == "capability_authorization_invalid",
            }
        )

        # 3. Installed bytes vs the manifest's own digest claim: enable a clean
        # install, then mutate the installed payload and prove the runtime
        # refuses discovery and execution, and that restoring the bytes makes
        # the same facts agree again.
        enabled, enabled_payload = self.cli(
            ["enable", "--capability", plugin_env.CAPABILITY], run_root=run_root
        )
        if enabled.returncode != 0:
            raise SliceError("negatives", f"the clean enable failed: {enabled_payload}")
        package_init = (
            self.venv_python.parent.parent
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
            / "acp2_fixture_validator"
            / "__init__.py"
        )
        if not package_init.is_file():
            raise SliceError("negatives", f"the installed payload is not at {package_init}")
        original = package_init.read_bytes()
        package_init.write_bytes(original + b"\n# tampered-installed-bytes\n")
        try:
            tampered_list, tampered_payload = self.cli(["list"], run_root=run_root)
            tampered_enable, tampered_enable_payload = self.cli(
                ["enable", "--capability", plugin_env.CAPABILITY], run_root=run_root
            )
            plugin_env.seed_completed_task(
                run_root,
                job_id=JOB_NEGATIVE,
                project_id="uncategorized",
                relative_path="RESULT/native.acp2n",
                payload=FIXTURE_INPUT.read_bytes(),
            )
            controller = plugin_env.PluginController(
                python=self.venv_python,
                run_root=run_root,
                log_path=self.out / "controller-negatives-tampered.log",
                trusted=(plugin_env.CAPABILITY,),
            )
            with controller:
                base_url = controller.base_url
                documents = self._discovery_document(base_url, "uncategorized")
                entries = self._plugin_entries(documents["inventory"])
                prepared = self.prepare(
                    base_url,
                    JOB_NEGATIVE,
                    attempt=1,
                    artifact_id="native_record",
                    relative_path="RESULT/native.acp2n",
                )
                refused_status, refused = self.analyze(
                    base_url,
                    JOB_NEGATIVE,
                    capability=plugin_env.CAPABILITY,
                    ref=prepared,
                )
            detail = (refused or {}).get("detail", {}) if isinstance(refused, dict) else {}
            rows.append(
                {
                    "row": "installed_bytes_digest_mismatch",
                    "tampered_file": str(package_init),
                    "list_exit_code": tampered_list.returncode,
                    "list_status": tampered_payload.get("status"),
                    "executable": tampered_payload.get("executable"),
                    "refusals": tampered_payload.get("refusals"),
                    "enable_exit_code": tampered_enable.returncode,
                    "enable_error": tampered_enable_payload.get("error"),
                    "discovery_plugin_entries": entries,
                    "analyze_status": refused_status,
                    "analyze_code": detail.get("code"),
                    "closed": (
                        tampered_list.returncode == 0
                        and tampered_payload.get("executable") == []
                        and any(
                            entry.get("reason") == "distribution_digest_mismatch"
                            for entry in tampered_payload.get("refusals") or []
                        )
                        and tampered_enable.returncode != 0
                        and tampered_enable_payload.get("error")
                        == "capability_distribution_digest_mismatch"
                        and entries == []
                        and refused_status == 422
                        and detail.get("code") == "unsupported_capability"
                    ),
                }
            )
        finally:
            package_init.write_bytes(original)
        restored, restored_payload = self.cli(["list"], run_root=run_root)
        restored_installed = next(
            (
                entry
                for entry in restored_payload.get("installed") or []
                if entry["capability_id"] == plugin_env.CAPABILITY
            ),
            None,
        )
        rows.append(
            {
                "row": "restored_bytes_control",
                "exit_code": restored.returncode,
                "matches_declared": bool(
                    (restored_installed or {})
                    .get("distribution_digest", {})
                    .get("matches_declared")
                ),
                "executable": restored_payload.get("executable"),
                "closed": restored.returncode == 0
                and bool(restored_installed)
                and bool(
                    (restored_installed.get("distribution_digest") or {}).get("matches_declared")
                )
                and plugin_env.CAPABILITY in (restored_payload.get("executable") or []),
            }
        )

        # 4. Incompatible API range and 5. unknown licence: real wheels built
        # from a mutated copy of the committed source tree.
        plugin_env.uninstall_distribution(self.venv_python, plugin_env.DISTRIBUTION)
        for row_name, kwargs, needle in (
            (
                "incompatible_api_range",
                {"api_range": ">=2,<3"},
                "requires API",
            ),
            ("unknown_license", {"license_id": "WTFPL"}, "license"),
        ):
            destination = variant_root / row_name
            applied = plugin_env.prepare_variant_tree(
                plugin_env.FIXTURE_ROOT, destination, **kwargs
            )
            wheel, build = plugin_env.build_fixture_wheel(
                destination, variant_root / row_name / "wheel"
            )
            install = plugin_env.install_wheel(self.venv_python, wheel)
            listed, payload = self.cli(
                ["list"], run_root=run_root, trusted=(plugin_env.CAPABILITY,)
            )
            enable, enable_payload = self.cli(
                ["enable", "--capability", plugin_env.CAPABILITY],
                run_root=run_root,
                trusted=(plugin_env.CAPABILITY,),
            )
            reason = str(payload.get("reason") or "")
            rows.append(
                {
                    "row": row_name,
                    "mutation": applied,
                    "wheel": str(wheel),
                    "wheel_sha256": file_digest(wheel),
                    "build": build.to_dict(),
                    "install": install.to_dict(),
                    "list_exit_code": listed.returncode,
                    "list_status": payload.get("status"),
                    "list_error": payload.get("error"),
                    "reason": reason[:1200],
                    "enable_exit_code": enable.returncode,
                    "enable_error": enable_payload.get("error"),
                    "closed": listed.returncode != 0
                    and payload.get("status") == "failed"
                    and needle in reason
                    and enable.returncode != 0,
                }
            )
            plugin_env.uninstall_distribution(self.venv_python, plugin_env.DISTRIBUTION)

        # Re-install the clean v1 so the environment ends in a known state.
        plugin_env.install_wheel(self.venv_python, self.wheels["external_v1"])
        final, final_payload = self.cli(["list"], run_root=run_root)
        rows.append(
            {
                "row": "clean_reinstall_control",
                "exit_code": final.returncode,
                "installed": sorted(
                    entry["capability_id"] for entry in final_payload.get("installed") or []
                ),
                "closed": final.returncode == 0
                and plugin_env.CAPABILITY
                in {entry["capability_id"] for entry in final_payload.get("installed") or []},
            }
        )

        failed = [row["row"] for row in rows if not row.get("closed")]
        if failed:
            raise SliceError("negatives", f"fail-closed rows did not close: {failed}")
        return self.record(
            "negatives",
            {
                "root": str(root),
                "rows": rows,
                "all_closed": not failed,
                "note": (
                    "every negative row uses the same operator CLI/public service path as "
                    "the positive rows; the mutation is applied to an isolated copy or to "
                    "the installed payload, never to the committed fixture"
                ),
            },
        )

    # -- report ------------------------------------------------------------

    def write_readme(self) -> None:
        text = (
            "# M4-05 — third-party capability plugin vertical slice (G16/G18)\n\n"
            "Generated by `scripts/acceptance/acp2_m405.py`.\n\n"
            "## What ran (real public path, no in-test registration)\n\n"
            "1. `environment.json` — the committed A-22 external plugin and the A-23\n"
            "   v1/v2 upgrade pair are built offline (`python -m build --wheel\n"
            "   --no-isolation`) and installed into a throw-away venv with\n"
            "   `--no-deps --no-index`; the wheel digests and every command are kept.\n"
            "2. `enable.json` — the operator CLI (`acp science-plugin enable`) reads the\n"
            "   installed fact and persists installed/enabled/enabled_by/enabled_at plus\n"
            "   the descriptor digest and the **computed** distribution digest.\n"
            "3. `discovery.json` — a new Controller process discovers the capability on\n"
            "   `GET /api/capabilities` and `GET /api/v2/science/capabilities` (the two\n"
            "   surfaces agree) and serves its third-party form on\n"
            "   `GET /api/v2/science/capability-forms`.\n"
            "4. `form.json` — the real Workbench modules (`resource-store`, `ui_subset`,\n"
            "   `science-query`, `features/capability-form`) render the served form,\n"
            "   commit the required field through the rendered control, refuse a\n"
            "   numeric edit with the typed `ui_value_type` and display the real\n"
            "   preflight ViewModel (plan/form/descriptor digests + bound fields +\n"
            "   disabled submit with its reason).\n"
            "5. `execution.json` + `product-external-v1.json` — one prepared artifact is\n"
            "   analysed by the enabled capability through the plugin host; provenance\n"
            "   carries `capability@version`, descriptor/form digests and the computed\n"
            "   distribution digest; a tampered ref (409 `digest_mismatch`) and an\n"
            "   invented capability (422 `unsupported_capability`) are refused.\n"
            "6. `upgrade-install.json` + `upgrade.json` + `product-upgrade-v*.json` —\n"
            "   the upgrade pair moves 1.0.0 → 2.0.0; a **new** Controller discovers\n"
            "   2.0.0, the next analysis freezes the new pin and the new computed\n"
            "   distribution digest, and the 1.0.0 frozen descriptor + product stay\n"
            "   readable.\n"
            "7. `browser/schema-form-live/` — the registered C row runs in a real\n"
            "   Chromium against the real Controller: third-party form rendered from\n"
            "   discovery, value typed, typed numeric refusal, real preflight response\n"
            "   displayed, submit disabled with the approval reason, plus its\n"
            "   `acp2.acceptance.v1` receipt, screenshots, console and network records.\n"
            "8. `uninstall.json` — the distribution is removed; a new Controller no\n"
            "   longer discovers it, the capability is projected `read_only` from the\n"
            "   frozen descriptor, execution is refused, and the historical product\n"
            "   still digest-verifies.\n"
            "9. `negatives.json` — incompatible API range, unknown licence, untrusted\n"
            "   capability, insufficient authorization and installed-bytes/digest\n"
            "   mismatch all fail closed through the same operator/public path; the\n"
            "   control installs prove the mutation caused each refusal.\n\n"
            "## Declared boundaries\n\n"
            "* The upstream science run is a seeded completed task row holding the\n"
            "  fixture's fixed native record (`seed_completed_task`): the plugin really\n"
            "  consumes a prepared artifact, but no ORCA/xTB/LSF/model process starts.\n"
            "* The A-23 upgrade pair ships no UI form, so the form leg uses the A-22\n"
            "  external plugin (the one that ships `cccp_ui_form.json`). Upgrade pin,\n"
            "  digest and history are demonstrated on the A-23 pair.\n"
            "* The Workbench has no panel for a third-party analysis *product*; the\n"
            "  capability-form panel displays the preflight ViewModel and the product is\n"
            "  surfaced by the analyze response and its digests.\n"
            "* The browser row is the Playwright-bundled Chromium (the system Edge\n"
            "  channel is exercised by the M4-02 rows on this same host).\n\n"
            "## Findings recorded by this run\n\n"
            "* M4-05-F1: the capability-form DOM `change` handler stores the control's\n"
            "  string value verbatim, so a human edit of an integer field is refused as\n"
            "  `ui_value_type` until the value is typed. The refusal is recorded in both\n"
            "  the headless and the browser rows; the Workbench source is frozen by this\n"
            "  task, so the row keeps the typed default for the numeric fields.\n"
            "* M4-05-F2: `SciencePluginRegistry` scans installed distributions once per\n"
            "  process, so an upgrade/uninstall is only observable to a **new**\n"
            "  Controller process. Every upgrade/uninstall leg therefore restarts the\n"
            "  service instead of assuming in-process invalidation.\n"
            "* M4-05-F3: with every right-sidebar panel expanded the capability-form\n"
            "  panel is squeezed to about one pixel; the browser row collapses the\n"
            "  neighbouring panels with the page's own control before using the form.\n"
            "  The layout itself is Workbench source and is not changed here.\n"
            "* M4-05-F4: the Workbench has no panel for a third-party analysis\n"
            "  product; the product is surfaced by the analyze response and its\n"
            "  digests.\n\n"
            "Files: `summary.json`, `trace.jsonl`, per-leg JSON documents with their\n"
            "SHA-256 in `summary.json.records`, and the browser row directory.\n"
        )
        (self.out / "README.md").write_text(text, encoding="utf-8")

    def findings_document(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "M4-05-F1",
                "title": (
                    "integer fields edited through the rendered DOM are refused as ui_value_type"
                ),
                "found_by": "the headless Workbench driver and the schema-form-live browser row",
                "before": (
                    "capability-form.js renderField() commits input.value (a string) for every "
                    "non-checkbox control; the ui-subset validator requires a JSON number for "
                    "type integer/number"
                ),
                "after": (
                    "not fixed in this task (Workbench source is frozen): the typed refusal is "
                    "recorded as the correct fail-closed behaviour, and the rows keep the typed "
                    "defaults for numeric fields while committing the string field through the "
                    "real control"
                ),
                "evidence": "form.json form.numeric_edit_refused / browser row 04-*.png",
            },
            {
                "id": "M4-05-F2",
                "title": "plugin discovery is per-process; upgrade/uninstall need a new Controller",
                "found_by": "the upgrade and uninstall legs",
                "before": (
                    "SciencePluginRegistry caches its installed scan; a running Controller keeps "
                    "the capability it started with"
                ),
                "after": (
                    "each lifecycle leg starts a new Controller process; the evidence records the "
                    "process boundary instead of claiming in-process invalidation"
                ),
                "evidence": "upgrade.json / uninstall.json controller logs",
            },
            {
                "id": "M4-05-F3",
                "title": ("the capability-form panel is squeezed when every panel is expanded"),
                "found_by": "the schema-form-live browser row",
                "before": (
                    "the capability-form panel shares the right sidebar's flex space with the "
                    "science-question, evidence, approval and info panels; with every panel "
                    "expanded its bounding box is about one pixel tall, so a user cannot see or "
                    "operate the form without collapsing neighbours"
                ),
                "after": (
                    "not fixed in this task (Workbench source is frozen): the browser row "
                    "collapses the neighbouring panels through the page's own control (a real "
                    "user action, persisted by the page) before interacting with the form"
                ),
                "evidence": "browser/schema-form-live/schema-form-live.json layout before/after",
            },
            {
                "id": "M4-05-F4",
                "title": "no Workbench panel renders a third-party analysis product",
                "found_by": "the slice scope review",
                "before": (
                    "capability-form.js displays the preflight ViewModel; there is no feature "
                    "module for acp2.science.plugin_analysis.v1 products"
                ),
                "after": (
                    "declared as a scope limitation: the product is surfaced by the analyze "
                    "response with its digests; adding a panel would be a Workbench source change, "
                    "which this task forbids"
                ),
                "evidence": "execution.json product/provenance",
            },
        ]

    # -- run ---------------------------------------------------------------

    def run(self) -> int:
        exit_code = EXIT_PASS
        # The work root is this run's own isolated state: a re-run starts from a
        # clean ledger/DB instead of inheriting the previous run's enablements
        # and task rows (the environment is rebuilt by the first leg anyway).
        for path in (self.run_root, self.negatives_root):
            if path.exists():
                shutil.rmtree(path)
        try:
            self.step("environment", self.leg_environment)
            self.step("enable", self.leg_enable)
            controller = plugin_env.PluginController(
                python=self.venv_python,  # type: ignore[arg-type]
                run_root=self.run_root,
                log_path=self.out / "controller-main.log",
                trusted=(plugin_env.CAPABILITY, plugin_env.UPGRADE_CAPABILITY),
            )
            with controller:
                base_url = controller.base_url
                self.step("discovery", lambda: self.leg_discovery(base_url))
                self.step("form", lambda: self.leg_form(base_url))
                self.step("execution", lambda: self.leg_execution(base_url))
            self.step("upgrade", self.leg_upgrade)
            self.step("upgrade.after", self.leg_upgrade_after)
            self.step("browser", self.leg_browser, critical=False)
            self.step("uninstall", self.leg_uninstall)
            self.step("negatives", self.leg_negatives)
        except SliceError as exc:
            print(f"ABORT: {exc}", flush=True)
            exit_code = EXIT_FAIL
        except Exception as exc:  # noqa: BLE001 - the trace is the evidence
            import traceback

            print(f"ABORT: {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
            self.reasons.append(f"unexpected: {type(exc).__name__}: {exc}")
            exit_code = EXIT_FAIL
        finally:
            for log_name in (
                "controller-main.log",
                "controller-upgrade.log",
                "controller-uninstall.log",
                "controller-negatives-tampered.log",
            ):
                path = self.out / log_name
                if path.is_file():
                    self.trace.record(
                        f"log.{log_name}",
                        response={"bytes": path.stat().st_size},
                        digest=file_digest(path),
                    )

        failed = [step["name"] for step in self.steps if step["status"] != "PASS"]
        if exit_code == EXIT_PASS and failed:
            exit_code = EXIT_FAIL
        if exit_code == EXIT_PASS and self.not_verified:
            exit_code = EXIT_NOT_VERIFIED
        self.status = {0: "PASS", 1: "FAIL", 2: "NOT_VERIFIED"}.get(exit_code, "FAIL")
        summary = {
            "schema": SCHEMA,
            "generated_at": utc_now(),
            "status": self.status,
            "exit_code": exit_code,
            "git": self._git(),
            "work_root": str(self.work),
            "run_root": str(self.run_root),
            "venv_python": str(self.venv_python) if self.venv_python else None,
            "steps": self.steps,
            "failed_steps": failed,
            "reasons": self.reasons,
            "not_verified": self.not_verified,
            "records": self.records,
            "findings": self.findings_document(),
            "limitations": [
                "upstream science is a seeded completed task row (fixed native record)",
                "the A-23 upgrade pair ships no UI form; forms come from the A-22 plugin",
                "no Workbench panel renders the plugin analysis product itself",
                "browser row uses the bundled Chromium (Edge is covered by M4-02 rows)",
            ],
        }
        self.write("summary.json", summary)
        self.write_readme()
        print(
            json.dumps(
                {
                    "status": self.status,
                    "steps": {step["name"]: step["status"] for step in self.steps},
                    "failed_steps": failed,
                    "not_verified": self.not_verified,
                },
                indent=2,
            ),
            flush=True,
        )
        return exit_code

    @staticmethod
    def _git() -> dict[str, Any]:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO), capture_output=True, text=True
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(REPO), capture_output=True, text=True
        )
        return {
            "head": head.stdout.strip(),
            "dirty_paths": len([line for line in status.stdout.splitlines() if line.strip()]),
        }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="M4-05 third-party capability plugin slice")
    parser.add_argument(
        "--out",
        default="reports/acp2_remediation_20261026/m4-05",
        help="evidence directory (git-tracked reports path by convention)",
    )
    parser.add_argument(
        "--work-root",
        default="/ACPServer/acp2-runs/remediation/m4-05",
        help="isolated work root (venv, run root, negatives)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run = M405(args)
    print(f"M4-05 plugin slice → {run.out}", flush=True)
    return run.run()


if __name__ == "__main__":
    sys.exit(main())
