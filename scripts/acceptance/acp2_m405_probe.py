"""Isolated-venv probe for the M4-05 read-only history leg (not collected).

Run with the interpreter of the isolated virtual environment used by
``scripts/acceptance/acp2_m405.py``.  ``PYTHONPATH`` supplies the platform source
tree.  The probe never registers a capability, never starts a Controller and
never imports the plugin module: it only reads the run root's enablement ledger
through the production composition root
(:func:`acp.services_science_plugins_bootstrap.build_science_plugin_registry`)
and re-verifies a stored analysis product.

Modes (each prints one JSON document on stdout):

* ``history <run_root> <plugin> <version> [product.json]`` — installed facts,
  every persisted enablement, the frozen descriptor snapshot of *plugin@version*
  (the historical read) and, when a product file is given, that the stored
  product still digest-verifies against its own task result.
* ``facts <run_root>`` — what the installed environment + ledger say now (used
  after uninstall and after a tamper).

Any assertion failure exits non-zero with a traceback on stderr.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MODULE_NAMES = ("acp2_fixture_validator", "acp2_upgrade_fixture")


def _plugin_imported() -> list[str]:
    names: list[str] = []
    for module_name in MODULE_NAMES:
        if module_name in sys.modules or f"{module_name}.plugin" in sys.modules:
            names.append(module_name)
    return names


def _registry(run_root: Path):  # noqa: ANN202 - production composition root
    from acp.services_science_plugins_bootstrap import build_science_plugin_registry

    registry = build_science_plugin_registry(run_root=run_root)
    registry.scan()
    return registry


def _enablement_record(record) -> dict[str, object]:  # noqa: ANN001 - Services dataclass
    return {
        "capability_id": record.capability_id,
        "plugin_name": record.plugin_name,
        "version": record.version,
        "installed": record.installed,
        "enabled": record.enabled,
        "enabled_by": record.enabled_by,
        "enabled_at": record.enabled_at,
        "disabled_by": record.disabled_by,
        "disabled_at": record.disabled_at,
        "descriptor_digest": record.descriptor_digest,
        "computed_distribution_digest": record.computed_distribution_digest,
        "distribution_digest": dict(record.distribution_digest),
    }


def _snapshot(registry, plugin: str, version: str) -> dict[str, object]:  # noqa: ANN001
    document = registry.frozen_descriptor(plugin, version)
    provenance = document.get("provenance") or {}
    distribution = provenance.get("distribution_digest") if isinstance(provenance, dict) else None
    return {
        "read_only": document.get("read_only"),
        "schema": document.get("schema"),
        "capability_id": document.get("capability_id"),
        "descriptor_digest": document.get("descriptor_digest"),
        "distribution_digest": distribution,
        "output_schema": document.get("output_schema"),
        "plugin": document.get("plugin"),
    }


def probe_history(run_root: Path, plugin: str, version: str, product: Path | None) -> dict:
    from cccp.calculation.products.contracts import compute_input_digest

    registry = _registry(run_root)
    installed = [record.to_metadata() for record in registry.installed_capabilities()]
    enablements = [_enablement_record(record) for record in registry.enablements()]
    snapshot = _snapshot(registry, plugin, version)
    payload: dict[str, object] = {
        "ok": True,
        "mode": "history",
        "run_root": str(run_root),
        "plugin": plugin,
        "version": version,
        "installed": [
            {
                "capability_id": entry["capability_id"],
                "version": entry["version"],
                "matches_declared": entry["distribution_digest"]["matches_declared"],
                "computed_distribution_digest": entry["distribution_digest"]["digest"],
            }
            for entry in installed
        ],
        "enablements": enablements,
        "executable": [record.capability_id for record in registry.authorized_capabilities()],
        "refusals": list(registry.refusals()),
        "snapshot": snapshot,
        "ledger_generation": registry.generation(),
        "module_imported": _plugin_imported(),
    }
    if product is not None:
        document = json.loads(product.read_text(encoding="utf-8"))
        result = document["result"]
        provenance = result["provenance"]
        recomputed = compute_input_digest(result["product"]["task_result"])
        payload["product"] = {
            "path": str(product),
            "capability_ref": provenance["capability_ref"],
            "descriptor_digest": provenance["descriptor_digest"],
            "form_digest": provenance["form_digest"],
            "computed_distribution_digest": provenance["computed_distribution_digest"],
            "product_digest": result["product"]["digest"],
            "product_digest_stable": recomputed == result["product"]["digest"],
            "snapshot_descriptor_matches_product": (
                snapshot["descriptor_digest"] == provenance["descriptor_digest"]
            ),
            "snapshot_distribution_matches_product": (
                (snapshot["distribution_digest"] or {}).get("digest")
                == provenance["computed_distribution_digest"]
            ),
        }
    return payload


def probe_facts(run_root: Path) -> dict:
    registry = _registry(run_root)
    return {
        "ok": True,
        "mode": "facts",
        "run_root": str(run_root),
        "installed": [
            {
                "capability_id": record.capability_id,
                "version": record.version,
                "matches_declared": record.distribution_digest.matches_declared,
                "computed_distribution_digest": record.distribution_digest.digest,
                "declared": record.distribution_digest.declared,
            }
            for record in registry.installed_capabilities()
        ],
        "executable": [record.capability_id for record in registry.authorized_capabilities()],
        "enablements": [_enablement_record(record) for record in registry.enablements()],
        "refusals": list(registry.refusals()),
        "module_imported": _plugin_imported(),
    }


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        raise SystemExit(
            "usage: probe.py history <run_root> <plugin> <version> [product.json] | "
            "facts <run_root>"
        )
    mode = argv[1]
    run_root = Path(argv[2])
    run_root.mkdir(parents=True, exist_ok=True)
    if mode == "history":
        if len(argv) not in (5, 6):
            raise SystemExit("usage: probe.py history <run_root> <plugin> <version> [product]")
        product = Path(argv[5]) if len(argv) == 6 else None
        payload = probe_history(run_root, argv[3], argv[4], product)
    elif mode == "facts":
        payload = probe_facts(run_root)
    else:
        raise SystemExit(f"unknown probe mode {mode!r}")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
