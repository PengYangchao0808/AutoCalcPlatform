from __future__ import annotations

import json
from pathlib import Path

from acp.storage.manifest import ProductKind, ResultManifest


def test_new_kinds_roundtrip(tmp_path: Path) -> None:
    result_dir = tmp_path / "RESULT"
    (result_dir / "profiles").mkdir(parents=True)
    (result_dir / "structures").mkdir()
    (result_dir / "reports").mkdir()
    (result_dir / "profiles" / "pes_profile.json").write_text(
        json.dumps({"energies": [-1.0, -0.8], "coordinates": [0.0, 1.0]}),
        encoding="utf-8",
    )
    (result_dir / "structures" / "irc_endpoint.xyz").write_text(
        "1\nIRC endpoint\nH 0.0 0.0 0.0\n",
        encoding="utf-8",
    )
    (result_dir / "reports" / "thermo_report.json").write_text(
        json.dumps({"G": -75.2, "H": -75.0, "S": 0.001}),
        encoding="utf-8",
    )
    manifest = ResultManifest(task_id="task_008", workflow="Highconfirm", status="completed")

    # When representative artifacts are registered with the new kinds.
    manifest.add_product(
        "pes_profile",
        "PES profile",
        "profiles/pes_profile.json",
        ProductKind.PES_PROFILE,
    )
    manifest.add_product(
        "irc_endpoint",
        "IRC endpoint",
        "structures/irc_endpoint.xyz",
        ProductKind.IRC_ENDPOINT,
    )
    manifest.add_product(
        "thermo_report",
        "Thermochemistry report",
        "reports/thermo_report.json",
        ProductKind.THERMO_REPORT,
    )

    # Then the v2 writer/reader round-trip retains every product field.
    manifest.write(result_dir)
    loaded = ResultManifest.read(result_dir)

    assert loaded.version == 2
    assert loaded.to_dict() == manifest.to_dict()
    assert [product.kind for product in loaded.products] == [
        ProductKind.PES_PROFILE,
        ProductKind.IRC_ENDPOINT,
        ProductKind.THERMO_REPORT,
    ]


def test_old_manifest_backward_compat(tmp_path: Path) -> None:
    result_dir = tmp_path / "RESULT"
    result_dir.mkdir()
    old_manifest = {
        "version": 2,
        "task_id": "legacy_task",
        "workflow": "optfreqsp",
        "status": "completed",
        "products": [
            {
                "id": "final_structure",
                "label": "Final structure",
                "path": "structures/final.xyz",
                "kind": "structure",
            },
            {
                "id": "energy_report",
                "label": "Energy report",
                "path": "reports/energy.json",
                "kind": "energy_report",
            },
        ],
    }
    (result_dir / "result_manifest.json").write_text(
        json.dumps(old_manifest),
        encoding="utf-8",
    )

    loaded = ResultManifest.read(result_dir)

    assert loaded.version == 2
    assert loaded.to_dict() == old_manifest
    assert [product.kind for product in loaded.products] == [
        ProductKind.STRUCTURE,
        ProductKind.ENERGY_REPORT,
    ]
