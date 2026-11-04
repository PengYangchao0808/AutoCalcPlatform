"""Round-9 M5: BatchOptimize publishes one energy report per item.

The external agent's BatchOptimize run only exposed a per-item
``energy_hartree`` inside the result manifest (which the read surface refused),
so reading an energy meant submitting extra single-point jobs.  These tests pin
the round-9 contract:

* a completed item publishes ``RESULT/energy/<item>_<step>.json``
  (``acp_energy_product_v1``) as a ``kind=energy_report`` manifest product;
* the document's key set is exactly the optimize writer's key set (no schema
  drift between the two workflows);
* the method is the resolved level, ``null`` + ``method_unknown_reason`` when
  no level resolves — never ``""``;
* a step-printed single-point energy wins over the frequency energy;
* an item without a step energy publishes nothing (no fabrication);
* the product is discoverable through ``describe_job_artifacts`` (the MCP
  ``acp_status.artifacts`` path), and an I/O failure never fails the item.

No QC runs: item records are constructed directly, as in the round-8
thermochemistry suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from acp.api import artifact_descriptors as descriptors
from acp.calculations.batch._items import BatchCalculationItem
from acp.calculations.batch.engine import BatchOptimizeEngine
from acp.calculations.batch.models import BatchCalculationManifest
from acp.calculations.batch.options import BatchMethodOptions
from acp.results.energy_product import (
    ENERGY_METHOD_UNKNOWN_REASON,
    ENERGY_PRODUCT_SCHEMA,
    build_energy_product_payload,
)

#: The optimize writer's frozen key set (``acp.calculations.executor``,
#: round-8 D6/T11).  BatchOptimize must publish the same document.
_OPTIMIZE_KEY_SET = {
    "schema_version",
    "step_id",
    "step_kind",
    "energy",
    "energy_hartree",
    "unit",
    "method",
    "source",
}

_XYZ = (
    "2\nTAG: INT\n"
    "H 0.0000000000 0.0000000000 0.0000000000\n"
    "H 0.0000000000 0.0000000000 0.7400000000\n"
)


def _engine(tmp_path: Path) -> BatchOptimizeEngine:
    return BatchOptimizeEngine(
        work_root=tmp_path / "task" / "WORK",
        result_root=tmp_path / "task" / "RESULT",
    )


def _geometry(tmp_path: Path, item_id: str, tag: str = "INT") -> str:
    target = tmp_path / "task" / "RESULT" / "structures" / f"{item_id}__TAG_{tag}__optimized.xyz"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_XYZ, encoding="utf-8")
    return target.relative_to(tmp_path / "task" / "RESULT").as_posix()


def _item(
    tmp_path: Path,
    *,
    item_id: str = "int_001",
    tag: str = "INT",
    single_point_energy: float | None = -76.5,
    frequency_energy: float | None = -76.4,
) -> BatchCalculationItem:
    item = BatchCalculationItem(
        item_id=item_id,
        candidate_id=item_id,
        name=tag,
        tag=tag,
        status="completed",
        optimized_xyz=_geometry(tmp_path, item_id, tag),
        frequency={"status": "completed", "frequencies": [102.33]},
    )
    if frequency_energy is not None:
        item.frequency["energy_hartree"] = frequency_energy
    if single_point_energy is not None:
        item.single_point = {"energy_hartree": single_point_energy, "status": "completed"}
    return item


def _materialize(
    tmp_path: Path,
    items: list[BatchCalculationItem],
    *,
    profile: str = "opt_freq_sp",
    methods: BatchMethodOptions | None = None,
) -> BatchOptimizeEngine:
    engine = _engine(tmp_path)
    if methods is not None:
        engine._active_methods = methods
    engine._materialize_result_products(BatchCalculationManifest(profile=profile, items=items))
    return engine


def _manifest(tmp_path: Path) -> dict[str, Any]:
    return json.loads(
        (tmp_path / "task" / "RESULT" / "result_manifest.json").read_text(encoding="utf-8")
    )


def _products(tmp_path: Path) -> dict[str, dict[str, Any]]:
    return {product["id"]: product for product in _manifest(tmp_path)["products"]}


def _energy_payload(tmp_path: Path, item_id: str = "int_001", step: str = "singlepoint") -> dict:
    return json.loads(
        (tmp_path / "task" / "RESULT" / "energy" / f"{item_id}_{step}.json").read_text(
            encoding="utf-8"
        )
    )


_METHODS = BatchMethodOptions(
    optimization_method="r2SCAN-3c",
    single_point_method="wB97M-V",
    single_point_basis="def2-TZVPPD",
)


# ── the product itself ──────────────────────────────────────────────────


def test_each_item_publishes_one_energy_report_product(tmp_path: Path) -> None:
    _materialize(
        tmp_path,
        [_item(tmp_path, item_id="int_001"), _item(tmp_path, item_id="int_002")],
        methods=_METHODS,
    )

    products = _products(tmp_path)
    energy_products = [row for row in products.values() if row["kind"] == "energy_report"]
    assert [row["id"] for row in energy_products] == [
        "batch_int_001_energy",
        "batch_int_002_energy",
    ]
    assert energy_products[0]["path"] == "energy/int_001_singlepoint.json"
    assert (tmp_path / "task" / "RESULT" / energy_products[0]["path"]).is_file()
    assert energy_products[0]["metadata"]["energy_hartree"] == pytest.approx(-76.5)
    assert energy_products[1]["metadata"]["energy_hartree"] == pytest.approx(-76.5)
    # The structure product points at the energy product (one manifest read).
    assert products["batch_int_001"]["metadata"]["energy_product_id"] == "batch_int_001_energy"


def test_energy_document_matches_the_optimize_key_set(tmp_path: Path) -> None:
    _materialize(tmp_path, [_item(tmp_path)], methods=_METHODS)

    payload = _energy_payload(tmp_path)
    assert set(payload) == _OPTIMIZE_KEY_SET
    # One writer, one shape: the builder cannot drift from the file either.
    assert (
        set(build_energy_product_payload(step_id="x", step_kind="singlepoint", energy_hartree=1.0))
        == _OPTIMIZE_KEY_SET
    )
    assert payload["schema_version"] == ENERGY_PRODUCT_SCHEMA
    assert payload["step_id"] == "int_001_singlepoint"
    assert payload["step_kind"] == "singlepoint"
    assert payload["energy"] == payload["energy_hartree"] == pytest.approx(-76.5)
    assert payload["unit"] == "hartree"
    assert payload["source"] == "scientific_result.json"


def test_method_and_basis_come_from_the_resolved_level(tmp_path: Path) -> None:
    _materialize(tmp_path, [_item(tmp_path)], methods=_METHODS)

    payload = _energy_payload(tmp_path)
    assert payload["method"] == "wB97M-V"
    metadata = _products(tmp_path)["batch_int_001_energy"]["metadata"]
    assert metadata["basis"] == "def2-TZVPPD"
    assert metadata["method_source"] == "single_point_method"
    assert metadata["energy_step"] == "singlepoint"
    assert metadata["item_id"] == "int_001"
    assert metadata["profile"] == "opt_freq_sp"
    assert metadata["tag"] == "INT"
    assert "method_unknown_reason" not in metadata


def test_unresolved_method_is_null_with_a_reason_never_empty(tmp_path: Path) -> None:
    _materialize(tmp_path, [_item(tmp_path)], methods=BatchMethodOptions())

    payload = _energy_payload(tmp_path)
    assert payload["method"] is None
    assert payload["method"] != ""
    metadata = _products(tmp_path)["batch_int_001_energy"]["metadata"]
    assert metadata["method_unknown_reason"] == ENERGY_METHOD_UNKNOWN_REASON
    assert "method_source" not in metadata


def test_single_point_energy_wins_over_the_frequency_energy(tmp_path: Path) -> None:
    _materialize(tmp_path, [_item(tmp_path)], profile="opt_freq_sp", methods=_METHODS)
    assert _energy_payload(tmp_path)["step_kind"] == "singlepoint"
    assert _energy_payload(tmp_path)["energy_hartree"] == pytest.approx(-76.5)

    # opt_freq has no SP step: the frequency step's printed energy is published.
    _materialize(
        tmp_path,
        [_item(tmp_path, item_id="int_002", single_point_energy=None)],
        profile="opt_freq",
        methods=_METHODS,
    )
    payload = _energy_payload(tmp_path, item_id="int_002", step="frequency")
    assert payload["step_kind"] == "frequency"
    assert payload["energy_hartree"] == pytest.approx(-76.4)


# ── no fabrication / failure behaviour ──────────────────────────────────


def test_item_without_a_step_energy_publishes_nothing(tmp_path: Path) -> None:
    item = _item(tmp_path, single_point_energy=None, frequency_energy=None)
    _materialize(tmp_path, [item], profile="opt_only", methods=_METHODS)

    products = _products(tmp_path)
    assert not [pid for pid in products if products[pid]["kind"] == "energy_report"]
    assert not (tmp_path / "task" / "RESULT" / "energy").exists()
    assert "energy_product_id" not in products["batch_int_001"]["metadata"]


def test_write_failure_never_fails_the_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(path: Path, text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("acp.results.energy_product.atomic_write_text", _boom)
    _materialize(tmp_path, [_item(tmp_path)], methods=_METHODS)

    products = _products(tmp_path)
    # Publication is best effort: the structure product is still registered.
    assert "batch_int_001" in products
    assert not [pid for pid in products if products[pid]["kind"] == "energy_report"]


# ── MCP reachability / invariants ───────────────────────────────────────


def test_energy_product_is_discoverable_for_prepare(tmp_path: Path) -> None:
    _materialize(tmp_path, [_item(tmp_path)], methods=_METHODS)

    items, truncated = descriptors.describe_job_artifacts(tmp_path / "task", attempt=1)
    assert truncated is False
    descriptor = next(row for row in items if row["resource_id"] == "batch_int_001_energy")
    assert descriptor["kind"] == "energy_report"
    assert descriptor["relative_path"] == "RESULT/energy/int_001_singlepoint.json"
    assert (tmp_path / "task" / descriptor["relative_path"]).is_file()
    assert descriptor["prepare"]["arguments"]["artifact_id"] == "batch_int_001_energy"


def test_batch_provenance_is_still_not_a_manifest_product(tmp_path: Path) -> None:
    """Round-8 invariant: provenance is discoverable by name, never a product."""
    item = _item(tmp_path)
    engine = _materialize(tmp_path, [item], methods=_METHODS)
    engine._provenance_records = [{"item_id": "int_001", "role": "int"}]
    engine._write_batch_provenance(
        engine._provenance_records,
        BatchCalculationManifest(profile="opt_freq_sp", items=[item]),
    )

    assert (tmp_path / "task" / "RESULT" / "batch_provenance.json").is_file()
    assert "batch_provenance" not in _products(tmp_path)


def test_no_absolute_host_paths_in_the_energy_product(tmp_path: Path) -> None:
    _materialize(tmp_path, [_item(tmp_path)], methods=_METHODS)

    text = json.dumps(_energy_payload(tmp_path))
    assert str(tmp_path) not in text
    assert "/ACPServer" not in text
