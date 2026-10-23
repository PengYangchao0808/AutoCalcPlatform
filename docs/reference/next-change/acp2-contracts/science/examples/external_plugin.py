#!/usr/bin/env python3
"""Read an external plugin manifest and prove it is not auto-enabled.

The manifest path is supplied with ``--manifest``.  Discovery is metadata-only:
the loader is never called, no module is imported from the manifest and no
subprocess or network access happens.  The printed projection keeps the four
states separate; enablement is explicitly ``absent`` until an operator calls
``PluginHost.enable``.

Usage:
    python external_plugin.py --manifest cccp_science_plugin.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cccp.plugin.discovery import PluginCandidate, discover_plugins
from cccp.plugin.metadata import PluginMetadata


def _license_projection(metadata: PluginMetadata) -> dict[str, Any]:
    return {
        "license": metadata.license,
        "accepted_licenses": sorted(metadata.license_policy.accepted_licenses),
        "unknown_policy": metadata.license_policy.unknown_policy,
    }


def main(argv: list[str] | None = None) -> int:
    """Print the metadata/trust/license/enablement projection; return 0."""
    parser = argparse.ArgumentParser(
        description="Read an external science plugin manifest (metadata-only, software-free)."
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="path to a cccp_science_plugin.json manifest",
    )
    args = parser.parse_args(argv)

    path = Path(args.manifest)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        metadata = PluginMetadata.from_dict(payload)
        catalog = discover_plugins([PluginCandidate(metadata=metadata)])
        status = catalog.status(metadata.name, enabled=False)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: cannot read plugin manifest {path}: {exc}", file=sys.stderr)
        return 2
    if status is None:  # pragma: no cover - discover_plugins always registers it
        print(f"ERROR: plugin {metadata.name!r} was not catalogued", file=sys.stderr)
        return 2

    document = {
        "example": "external_plugin",
        "manifest_path": str(path),
        "name": metadata.name,
        "distribution": metadata.distribution,
        "version": metadata.version,
        "entry_point_group": "cccp.science_plugins",
        "default_enabled": metadata.default_enabled,
        "trust_level": status.trust_level,
        "is_trusted": metadata.is_trusted,
        "has_verified_fixture_evidence": metadata.has_verified_fixture_evidence,
        "license_projection": _license_projection(metadata),
        "capability_ids": list(status.capability_ids),
        "validator_refs": list(status.validator_refs),
        "recipe_refs": list(status.recipe_refs),
        "origin": status.origin,
        "projections": status.to_dict(),
        "auto_enabled": status.enabled.state.value == "present",
        "enablement_policy": catalog.enablement_policy,
    }
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
