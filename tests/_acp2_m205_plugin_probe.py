"""Isolated-venv probe for M2-05 third-party science plugin production assembly.

Run with the interpreter of a throw-away virtual environment that has the
external-plugin fixture wheel installed.  ``PYTHONPATH`` supplies the platform
source tree; the fixture itself is imported only when an enabled capability is
invoked (never during discovery).

Modes (each prints one JSON document on stdout):

* ``enable <run_root>`` — metadata-only scan, fail-closed negatives (no
  authorization / untrusted / wrong descriptor digest), explicit enablement and
  the persisted ``installed/enabled/enabled_by/enabled_at`` record.
* ``serve <run_root> <native-input> <product-out>`` — start the **real** service
  (uvicorn + FastAPI lifespan), read both discovery surfaces, prepare a real
  artifact through the Controller, run the plugin analysis, and record the
  product + provenance.  Then disable -> discover (absent), re-enable.
* ``discovery <run_root>`` — start the real service and report what discovery
  says now (used after disable / upgrade / uninstall).
* ``historical <run_root> <product-in>`` — after uninstall: read the frozen
  descriptor snapshot from the ledger only and re-verify the stored product
  digest (the plugin module is never imported).

Any assertion failure exits non-zero with a traceback on stderr.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

DISTRIBUTION_NAME = "acp2-external-science-validator"
MODULE_NAME = "acp2_fixture_validator"
PLUGIN_NAME = "acp2_external.native_singlepoint"
CAPABILITY_NAME = "acp2_external_native_singlepoint"
PROJECT = "uncategorized"
JOB_ID = "20261027_130000_000_singlepoint"
ARTIFACT_ID = "native_record"
ATTEMPT = 1


# ---------------------------------------------------------------------------
# Fixture access (never imports the plugin)
# ---------------------------------------------------------------------------


def _installed_distribution():  # noqa: ANN202 - importlib type is version-dependent
    import importlib.metadata

    return importlib.metadata.distribution(DISTRIBUTION_NAME)


def _installed_input() -> Path:
    """Return the fixed native record shipped inside the installed wheel."""
    distribution = _installed_distribution()
    located = distribution.locate_file(f"{MODULE_NAME}/data/native_singlepoint.acp2n")
    path = Path(str(located))
    if not path.is_file():
        raise AssertionError(f"installed fixture input is missing: {path}")
    return path


def _plugin_imported() -> bool:
    return MODULE_NAME in sys.modules or f"{MODULE_NAME}.plugin" in sys.modules


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _authorization(action: str, record) -> object:  # noqa: ANN001 - record is a Services dataclass
    from acp_services.runtime.capability_registry import CapabilityAuthorization

    return CapabilityAuthorization(
        action=action,
        capability_id=record.capability_id,
        version=record.version,
        descriptor_digest=record.descriptor_digest,
        authorized_by="controller",
        issued_at=_utc_now(),
    )


def _registry(run_root: Path):  # noqa: ANN202 - composed registry
    from acp.services_science_plugins_bootstrap import build_science_plugin_registry

    return build_science_plugin_registry(
        run_root=run_root,
        environ={
            "ACP_SCIENCE_PLUGIN_TRUSTED_CAPABILITIES": CAPABILITY_NAME,
        },
    )


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def probe_enable(run_root: Path) -> dict[str, object]:
    from acp_services.runtime.capability_registry import (
        CapabilityAuthorizationError,
        CapabilityConflictError,
        CapabilityUntrustedError,
    )

    untrusted = _registry_without_trust(run_root)
    untrusted.scan()
    untrusted_error: str | None = None
    try:
        untrusted.enable(CAPABILITY_NAME, authorization=None)
    except CapabilityUntrustedError as exc:
        untrusted_error = exc.code
    except CapabilityAuthorizationError as exc:  # pragma: no cover - trust check comes first
        untrusted_error = exc.code

    registry = _registry(run_root)
    installed = registry.scan()
    module_imported_after_scan = _plugin_imported()
    record = registry.installed_capability(CAPABILITY_NAME)

    no_authorization_error: str | None = None
    try:
        registry.enable(CAPABILITY_NAME, authorization=None)
    except CapabilityAuthorizationError as exc:
        no_authorization_error = exc.code

    wrong_digest_error: str | None = None
    forged = _authorization("capability.enable", record)
    forged = type(forged)(
        action=forged.action,
        capability_id=forged.capability_id,
        version=forged.version,
        descriptor_digest="sha256:" + "0" * 64,
        authorized_by=forged.authorized_by,
        issued_at=forged.issued_at,
    )
    try:
        registry.enable(CAPABILITY_NAME, authorization=forged)
    except CapabilityAuthorizationError as exc:
        wrong_digest_error = exc.code
    except CapabilityConflictError as exc:  # pragma: no cover - auth is checked first
        wrong_digest_error = exc.code

    enablement = registry.enable(
        CAPABILITY_NAME, authorization=_authorization("capability.enable", record)
    )
    after_enable_imported = _plugin_imported()
    authorized = registry.authorized_capabilities()
    # The persisted document plus the derived identity: the ledger stores the
    # digest record (digest/declared/matches_declared) and the platform exposes
    # the computed value under its own name for readers such as the CLI.
    persisted = dict(enablement.to_record())
    persisted["computed_distribution_digest"] = enablement.computed_distribution_digest
    return {
        "ok": True,
        "installed": [entry.to_metadata() for entry in installed],
        "untrusted_error": untrusted_error,
        "no_authorization_error": no_authorization_error,
        "wrong_digest_error": wrong_digest_error,
        "enablement": persisted,
        "generation": registry.generation(),
        "executable": [entry.capability_id for entry in authorized],
        "module_imported_after_scan": module_imported_after_scan,
        "module_imported_after_enable": after_enable_imported,
    }


def _registry_without_trust(run_root: Path):  # noqa: ANN202 - composed registry
    from acp.services_science_plugins_bootstrap import build_science_plugin_registry

    return build_science_plugin_registry(run_root=run_root, environ={})


def probe_serve(run_root: Path, native_input: Path, product_out: Path) -> dict[str, object]:
    payload: dict[str, object] = {"ok": True}
    with _served(run_root) as served:
        base_url = served.base_url
        job_id = _seed_job(served.app.state.job_manager, run_root, native_input)
        import httpx

        http = httpx.Client(base_url=base_url, timeout=30.0)
        try:
            inventory = http.get("/api/capabilities")
            inventory.raise_for_status()
            inventory_body = inventory.json()
            v2 = http.get("/api/v2/science/capabilities")
            v2.raise_for_status()
            v2_body = v2.json()
            payload.update(
                {
                    "application_plugin_entries": [
                        entry
                        for entry in inventory_body["capability_descriptors"]
                        if entry.get("capability_source") == "science_plugin"
                    ],
                    "application_building_capabilities": sorted(
                        entry["capability_id"]
                        for entry in inventory_body["capability_descriptors"]
                        if entry.get("capability_source") != "science_plugin"
                    ),
                    "v2_plugin_entries": [
                        entry
                        for entry in v2_body["capabilities"]["descriptors"]["capabilities"]
                        if entry.get("capability_source") == "science_plugin"
                    ],
                    "descriptors_agree": inventory_body["capability_descriptors"]
                    == v2_body["capabilities"]["descriptors"]["capabilities"],
                    "analysis_entries": v2_body["capabilities"]["analysis"],
                    "analysis_agree": inventory_body["analysis_capabilities"]
                    == v2_body["capabilities"]["analysis"],
                }
            )

            ref = _prepare_artifact(base_url, job_id)
            payload["artifact_ref"] = ref
            analyze_body = {
                "analysis_id": CAPABILITY_NAME,
                "input_refs": [ref],
                "parameters": {"envelope": {"task": "singlepoint"}},
                "explicit": True,
            }
            analysed = http.post(f"/api/v1/jobs/{job_id}/analyze", json=analyze_body)
            payload["analyze_status"] = analysed.status_code
            analysed.raise_for_status()
            product = analysed.json()
            payload["product"] = product
            product_out.write_text(
                json.dumps(product, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )

            tampered = http.post(
                f"/api/v1/jobs/{job_id}/analyze",
                json={
                    **analyze_body,
                    "input_refs": [{**ref, "digest": "sha256:" + "0" * 64}],
                },
            )
            payload["tampered_status"] = tampered.status_code
            tampered_detail = tampered.json().get("detail", {})
            payload["tampered_code"] = (
                tampered_detail.get("code") if tampered.status_code != 200 else None
            )
            payload["tampered_rule"] = (
                tampered_detail.get("params", {}).get("rule")
                if tampered.status_code != 200
                else None
            )

            unknown = http.post(
                f"/api/v1/jobs/{job_id}/analyze",
                json={**analyze_body, "analysis_id": "plugin.invented"},
            )
            payload["unknown_status"] = unknown.status_code
            unknown_detail = unknown.json().get("detail", {})
            payload["unknown_code"] = unknown_detail.get("code")
            payload["unknown_allowed"] = unknown_detail.get("params", {}).get("allowed")

            envelope_missing = http.post(
                f"/api/v1/jobs/{job_id}/analyze",
                json={**analyze_body, "parameters": {}},
            )
            payload["envelope_missing_status"] = envelope_missing.status_code
            payload["envelope_missing_rule"] = (
                envelope_missing.json().get("detail", {}).get("params", {}).get("rule")
            )
        finally:
            http.close()

    # Disable in a *new* process view: the ledger is the only fact.
    registry = _registry(run_root)
    registry.scan()
    record = registry.installed_capability(CAPABILITY_NAME)
    registry.disable(CAPABILITY_NAME, authorization=_authorization("capability.disable", record))
    with _served(run_root) as served:
        import httpx

        with httpx.Client(base_url=served.base_url, timeout=30.0) as http:
            after = http.get("/api/capabilities").json()
            payload["after_disable_plugin_entries"] = [
                entry
                for entry in after["capability_descriptors"]
                if entry.get("capability_source") == "science_plugin"
            ]
            payload["after_disable_analysis_ids"] = [
                entry["analysis_id"] for entry in after["analysis_capabilities"]
            ]
    registry.enable(CAPABILITY_NAME, authorization=_authorization("capability.enable", record))
    payload["generation_after_reenable"] = registry.generation()
    return payload


def probe_discovery(run_root: Path) -> dict[str, object]:
    with _served(run_root) as served:
        import httpx

        with httpx.Client(base_url=served.base_url, timeout=30.0) as http:
            inventory = http.get("/api/capabilities")
            inventory.raise_for_status()
            body = inventory.json()
            v2 = http.get("/api/v2/science/capabilities")
            v2.raise_for_status()
            v2_body = v2.json()
    return {
        "ok": True,
        "plugin_entries": [
            {
                "capability_id": entry.get("capability_id"),
                "plugin": entry.get("plugin"),
                "descriptor_digest": entry.get("descriptor_digest"),
                "distribution_digest": entry.get("distribution_digest"),
            }
            for entry in body["capability_descriptors"]
            if entry.get("capability_source") == "science_plugin"
        ],
        "analysis_entries": body["analysis_capabilities"],
        "v2_analysis_entries": v2_body["capabilities"]["analysis"],
        "descriptors_agree": body["capability_descriptors"]
        == v2_body["capabilities"]["descriptors"]["capabilities"],
    }


def probe_historical(run_root: Path, product_in: Path) -> dict[str, object]:
    payload = json.loads(product_in.read_text(encoding="utf-8"))
    from cccp.calculation.products.contracts import compute_input_digest

    analysis = payload["result"]
    provenance = analysis["provenance"]
    product = analysis["product"]
    registry = _registry(run_root)
    snapshot = registry.frozen_descriptor(PLUGIN_NAME, str(provenance["capability_version"]))
    recomputed = compute_input_digest(product["task_result"])
    enablements = registry.enablements()
    installed_now = [entry.capability_id for entry in registry.scan()]
    return {
        "ok": True,
        "discovered_names": installed_now,
        "snapshot_capability_id": snapshot.get("capability_id"),
        "snapshot_schema": snapshot.get("schema"),
        "snapshot_descriptor_digest_matches_product": snapshot.get("descriptor_digest")
        == provenance["descriptor_digest"],
        "snapshot_distribution_digest_matches_product": (
            (snapshot.get("provenance") or {}).get("distribution_digest", {}).get("digest")
            == provenance["computed_distribution_digest"]
        ),
        "product_digest_stable": recomputed == product["digest"],
        "product_capability_ref": provenance["capability_ref"],
        "enablements": [record.to_record() for record in enablements],
        "installed_after_uninstall": installed_now,
        "module_imported": _plugin_imported(),
    }


# ---------------------------------------------------------------------------
# Real service helpers
# ---------------------------------------------------------------------------


class _served:  # noqa: N801 - context manager helper
    """Run the real FastAPI application under uvicorn on an ephemeral port."""

    def __init__(self, run_root: Path) -> None:
        self.run_root = run_root
        self._thread: threading.Thread | None = None
        self._server = None
        self.app = None
        self.base_url = ""

    def __enter__(self) -> _served:
        import uvicorn

        from acp.api.server import create_app

        app = create_app(run_root=self.run_root, max_running=1)
        config = uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="warning", access_log=False
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True, name="m205-uvicorn")
        thread.start()
        deadline = time.monotonic() + 30.0
        while not server.started:
            if not thread.is_alive():
                raise AssertionError("uvicorn thread exited before startup")
            if time.monotonic() > deadline:
                raise AssertionError("uvicorn did not start within 30 s")
            time.sleep(0.05)
        port = int(server.servers[0].sockets[0].getsockname()[1])
        self._thread = thread
        self._server = server
        self.app = app
        self.base_url = f"http://127.0.0.1:{port}"
        return self

    def __exit__(self, *exc_info: object) -> None:
        server = self._server
        thread = self._thread
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=15)
            if thread.is_alive():
                server.force_exit = True
                thread.join(timeout=10)


def _seed_job(manager, run_root: Path, native_input: Path) -> str:  # noqa: ANN001 - real manager
    """Seed one completed single-point task whose RESULT holds the native record.

    The science output is the fixture's fixed native record; the task record is
    created through the application's own store (the declared stub boundary: no
    QC binary, no LSF, no model is reached).
    """
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    work_dir = run_root / PROJECT / f"{JOB_ID}-{ATTEMPT}"
    (work_dir / "RESULT").mkdir(parents=True, exist_ok=True)
    (work_dir / "RESULT" / "native.acp2n").write_bytes(native_input.read_bytes())
    manager.store.create(
        JobRecord(
            id=JOB_ID,
            spec=JobSpec(workflow="singlepoint", name=JOB_ID, project_id=PROJECT),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            attempt=ATTEMPT,
            project_id=PROJECT,
        )
    )
    return JOB_ID


def _prepare_artifact(base_url: str, job_id: str) -> dict[str, object]:
    """Prepare the seeded record through the real Controller HTTP commands."""
    from acp_services.entrypoints.controller_client import ControllerClient

    client = ControllerClient(base_url)
    prepared = dict(
        client.prepare(
            {
                "schema_version": 1,
                "purpose": "artifact",
                "job_id": job_id,
                "artifact_id": ARTIFACT_ID,
                "attempt": ATTEMPT,
                "generation": 1,
                "relative_path": "RESULT/native.acp2n",
                "media_type": "text/plain",
            }
        )
    )
    deadline = time.monotonic() + 20.0
    operation: dict[str, object] = {}
    while time.monotonic() < deadline:
        operation = dict(client.operation(str(prepared["operation_id"])))
        if operation.get("state") in {"ready", "failed", "cancelled"}:
            break
        time.sleep(0.01)
    if operation.get("state") != "ready":
        raise AssertionError(f"prepare did not settle ready: {operation}")
    ref = operation["artifact_ref"]
    if not isinstance(ref, dict):
        raise AssertionError(f"prepare returned no artifact ref: {operation}")
    return {
        "task_id": job_id,
        "attempt": ATTEMPT,
        "artifact_id": ARTIFACT_ID,
        "generation": int(ref["generation"]),
        "digest": str(ref["digest"]),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        raise SystemExit(
            "usage: probe.py enable <run_root> | serve <run_root> <native> <out> | "
            "discovery <run_root> | historical <run_root> <product>"
        )
    mode = argv[1]
    run_root = Path(argv[2])
    run_root.mkdir(parents=True, exist_ok=True)
    if mode == "enable":
        payload = probe_enable(run_root)
    elif mode == "serve":
        if len(argv) != 5:
            raise SystemExit("usage: probe.py serve <run_root> <native> <product-out>")
        payload = probe_serve(run_root, Path(argv[3]), Path(argv[4]))
    elif mode == "discovery":
        payload = probe_discovery(run_root)
    elif mode == "historical":
        if len(argv) != 4:
            raise SystemExit("usage: probe.py historical <run_root> <product>")
        payload = probe_historical(run_root, Path(argv[3]))
    else:
        raise SystemExit(f"unknown probe mode {mode!r}")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
