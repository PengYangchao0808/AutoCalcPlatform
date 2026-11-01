"""Round-7 #4: thermochemistry (ZPE/H/G/S) as a readable RESULT product.

The agent could not read thermochemistry through MCP: ``thermo.json`` was
never registered in ``result_manifest.json`` and ``ProductKind.THERMO_REPORT``
had no writer, so ``acp_status.artifacts`` never listed it.

These tests pin:

* the ORCA printed thermochemistry block is parsed verbatim (units preserved,
  no invented values, absent fields stay absent);
* the BatchOptimize thermochemistry step metadata + Shermo sum are projected
  to the same payload shape;
* the product is registered as ``ProductKind.THERMO_REPORT`` only when the
  thermochemistry really exists (no frequency / no success → no product);
* ``describe_job_artifacts`` exposes it with ``kind == "thermochemistry"`` and
  a prepare-valid ``RESULT/`` relative path (the acp_read_artifact input).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from acp.api import artifact_descriptors as descriptors
from acp.calculations.batch._items import BatchCalculationItem
from acp.calculations.batch.engine import BatchOptimizeEngine
from acp.calculations.contracts import ArtifactRef, CalculationResult, StepKind
from acp.results.thermochemistry import (
    PRODUCT_RELATIVE_PATH,
    SCHEMA_VERSION,
    batch_thermochemistry_relative_path,
    build_batch_thermochemistry,
    build_frequency_thermochemistry,
    parse_orca_thermochemistry,
    publish_thermochemistry,
)

#: Excerpt of a real ORCA 6 frequency output (thermochemistry section only);
#: every number below is asserted against this printed text.
_ORCA_THERMO_BLOCK = """\
THERMOCHEMISTRY AT 298.15K
---------------------------

Temperature         ...   298.15 K
Pressure            ...     1.00 atm
Quasi RRHO          ...     True
Cut-Off Frequency   ...     1.00 cm^-1
The molecule is recognized as being linear

Summary of contributions to the inner energy U:
Electronic energy                ...     -1.11677319 Eh
Zero point energy                ...      0.01147878 Eh       7.20 kcal/mol
Thermal vibrational correction   ...      0.00000000 Eh       0.00 kcal/mol
Thermal rotational correction    ...      0.00094418 Eh       0.59 kcal/mol
Thermal translational correction ...      0.00141627 Eh       0.89 kcal/mol
-----------------------------------------------------------------------
Total thermal energy                     -1.10293396 Eh


Summary of corrections to the electronic energy:
(perhaps to be used in another calculation)
Total thermal correction                  0.00236045 Eh       1.48 kcal/mol
Non-thermal (ZPE) correction              0.01147878 Eh       7.20 kcal/mol
-----------------------------------------------------------------------
Total correction                          0.01383923 Eh       8.68 kcal/mol


Total Enthalpy                    ...     -1.10198975 Eh

Final entropy term                ...      0.01478519 Eh      9.28 kcal/mol

Final Gibbs free energy         ...     -1.11677494 Eh
"""

_SHERMO_SUM = """\
 Total S:      317.239 J/mol/K      75.822 cal/mol/K    -TS:   -20.711 kcal/mol
 Zero point energy (ZPE):    362.086 kJ/mol     86.541 kcal/mol   0.137911 a.u.
 Sum of electronic energy and ZPE, namely U/H/G at 0 K:       -234.3350430 a.u.
 Sum of electronic energy and thermal correction to G:        -234.3616254 a.u.
"""

_H2_XYZ = (
    "2\nTAG: INT\n"
    "H 0.0000000000 0.0000000000 0.0000000000\n"
    "H 0.0000000000 0.0000000000 0.7400000000\n"
)


# ── ORCA printed block ──────────────────────────────────────────────────


def test_orca_block_is_parsed_verbatim() -> None:
    values = parse_orca_thermochemistry(_ORCA_THERMO_BLOCK)
    assert values is not None
    assert values["gibbs_hartree"] == -1.11677494
    assert values["enthalpy_hartree"] == -1.10198975
    assert values["zpe_hartree"] == 0.01147878
    assert values["electronic_energy_hartree"] == -1.11677319
    assert values["temperature_k"] == 298.15
    assert values["pressure_atm"] == 1.0
    assert values["entropy_term_hartree"] == 0.01478519
    assert values["entropy_au"] == 0.01478519 / 298.15
    assert values["quasi_rrho"] is True
    assert values["linear_molecule"] is True
    assert values["zpe_included"] is True


def test_orca_payload_keeps_units_and_provenance() -> None:
    from cccp.utils.constants import HARTREE_TO_KCAL

    payload = parse_orca_thermochemistry(_ORCA_THERMO_BLOCK)
    assert payload is not None
    values = payload
    assert values["gibbs_hartree"] * HARTREE_TO_KCAL == pytest.approx(-700.7868552462501, rel=1e-12)
    # G = H - T*S: Gibbs lies below the printed enthalpy.
    assert values["gibbs_hartree"] < values["enthalpy_hartree"] < 0


def test_missing_block_returns_none(tmp_path: Path) -> None:
    assert parse_orca_thermochemistry("no thermochemistry here") is None
    assert parse_orca_thermochemistry("") is None
    missing = tmp_path / "freq.out"
    assert build_frequency_thermochemistry(missing) is None
    partial = tmp_path / "partial.out"
    partial.write_text("Temperature         ...   298.15 K\n", encoding="utf-8")
    assert build_frequency_thermochemistry(partial) is None


def test_frequency_payload_from_log_file(tmp_path: Path) -> None:
    from cccp.utils.constants import HARTREE_TO_KCAL

    freq = tmp_path / "freq.out"
    freq.write_text(_ORCA_THERMO_BLOCK, encoding="utf-8")
    payload = build_frequency_thermochemistry(freq, method="r2SCAN-3c", basis="def2-TZVP")
    assert payload is not None
    assert payload["schema"] == SCHEMA_VERSION
    assert payload["source"]["kind"] == "orca_frequency_output"
    assert payload["source"]["method"] == "r2SCAN-3c"
    assert payload["source"]["basis"] == "def2-TZVP"
    assert payload["gibbs_hartree"] == -1.11677494
    assert payload["gibbs_kcal_mol"] == -1.11677494 * HARTREE_TO_KCAL
    assert payload["zpe_hartree"] == 0.01147878
    assert payload["phase_correction"]["standard_state"] == "1atm"
    assert payload["phase_correction"]["qrrho"] is True
    assert "entropy_au" in payload and "unit_note" in payload


# ── publication + MCP artifact descriptor ──────────────────────────────


def test_published_product_is_reachable_as_a_thermochemistry_artifact(tmp_path: Path) -> None:
    from acp_services.data.artifacts import validate_artifact_relative_path

    result_dir = tmp_path / "RESULT"
    freq = tmp_path / "freq.out"
    freq.write_text(_ORCA_THERMO_BLOCK, encoding="utf-8")
    payload = build_frequency_thermochemistry(freq, method="r2SCAN-3c")
    assert payload is not None
    written = publish_thermochemistry(
        result_dir,
        payload,
        relative_path=PRODUCT_RELATIVE_PATH,
        product_id="thermochemistry",
        label="Thermochemistry (ZPE/H/G/S)",
    )
    assert written.is_file()
    assert json.loads(written.read_text(encoding="utf-8"))["gibbs_hartree"] == -1.11677494

    manifest = json.loads((result_dir / "result_manifest.json").read_text(encoding="utf-8"))
    kind = next(p["kind"] for p in manifest["products"] if p["id"] == "thermochemistry")
    assert kind == "thermo_report"

    items, truncated = descriptors.describe_job_artifacts(tmp_path, attempt=1)
    assert truncated is False
    descriptor = next(item for item in items if item["resource_id"] == "thermochemistry")
    assert descriptor["kind"] == "thermochemistry"
    assert descriptor["relative_path"] == "RESULT/thermochemistry.json"
    assert descriptor["attempt"] == 1
    # The descriptor path is exactly the prepare/read input contract.
    assert validate_artifact_relative_path(descriptor["relative_path"]) == (
        f"RESULT/{PRODUCT_RELATIVE_PATH}"
    )


def test_other_product_kinds_keep_their_wire_name(tmp_path: Path) -> None:
    from acp.storage.manifest import ProductKind, ResultManifest

    result_dir = tmp_path / "RESULT"
    manifest = ResultManifest(task_id="t", workflow="frequency", status="completed")
    manifest.add_product(
        "fusion", "Fusion", "frequencies/normal_modes.json", ProductKind.FREQUENCY_MODES
    )
    manifest.write(result_dir)
    items, _ = descriptors.describe_job_artifacts(tmp_path)
    assert [(item["resource_id"], item["kind"]) for item in items] == [
        ("fusion", "frequency_modes")
    ]


# ── BatchOptimize thermochemistry ──────────────────────────────────────


def test_batch_metadata_projection_includes_zpe_from_shermo_sum(tmp_path: Path) -> None:
    sum_file = tmp_path / "Shermo.sum"
    sum_file.write_text(_SHERMO_SUM, encoding="utf-8")
    metadata: dict[str, Any] = {
        "status": "completed",
        "success": True,
        "gibbs_hartree": -234.3616254,
        "enthalpy_hartree": -234.3286207,
        "entropy_au": 0.0012,
        "sp_energy_hartree": -234.47295415,
        "thermal_correction_u_hartree": 0.14346845,
        "temperature_k": 273.15,
        "pressure_atm": 1.0,
        "standard_state": "1atm",
        "qrrho": True,
        "qrrho_mode": "Shermo ilowfreq=2 quasi-RRHO",
        "output_file": str(sum_file),
    }
    payload = build_batch_thermochemistry(
        metadata,
        item_id="int_001",
        tag="INT",
        method="B3LYP",
        basis="def2-SVP",
        zpe_scale_factor=0.9905,
    )
    assert payload is not None
    assert payload["gibbs_hartree"] == -234.3616254
    assert payload["enthalpy_hartree"] == -234.3286207
    assert payload["zpe_hartree"] == 0.137911
    assert payload["entropy_au"] == 0.0012
    assert payload["temperature_k"] == 273.15
    assert payload["zpe_scale_factor"] == 0.9905
    assert payload["source"]["kind"] == "shermo"
    assert payload["phase_correction"]["standard_state"] == "1atm"
    assert payload["phase_correction"]["qrrho"] is True
    assert payload["item_id"] == "int_001"


def test_batch_projection_never_fabricates_values(tmp_path: Path) -> None:
    # No Gibbs energy / failed step / no thermo step → no product at all.
    assert (
        build_batch_thermochemistry({"status": "completed", "success": True}, item_id="i") is None
    )
    assert (
        build_batch_thermochemistry(
            {"status": "failed", "success": False, "gibbs_hartree": -1.0}, item_id="i"
        )
        is None
    )
    payload = build_batch_thermochemistry(
        {"status": "completed", "success": True, "gibbs_hartree": -1.0}, item_id="i"
    )
    assert payload is not None
    assert "zpe_hartree" not in payload
    assert payload["zpe_included"] is False


def test_batch_publish_section_registers_the_product(tmp_path: Path) -> None:
    engine = BatchOptimizeEngine(
        work_root=tmp_path / "task" / "WORK",
        result_root=tmp_path / "task" / "RESULT",
    )
    # ``run()`` initialises this; a direct publish call needs the same empty map.
    engine._item_stage_facts = {}
    sum_file = tmp_path / "Shermo.sum"
    sum_file.write_text(_SHERMO_SUM, encoding="utf-8")
    geometry = tmp_path / "task" / "RESULT" / "structures" / "int_001__TAG_INT__optimized.xyz"
    geometry.parent.mkdir(parents=True, exist_ok=True)
    geometry.write_text(_H2_XYZ, encoding="utf-8")

    from acp.calculations.batch.models import BatchCalculationManifest

    item = BatchCalculationItem(
        item_id="int_001",
        candidate_id="int_001",
        name="INT",
        tag="INT",
        status="completed",
        optimized_xyz="structures/int_001__TAG_INT__optimized.xyz",
        thermochemistry={
            "status": "completed",
            "success": True,
            "gibbs_hartree": -234.3616254,
            "enthalpy_hartree": -234.3286207,
            "entropy_au": 0.0012,
            "temperature_k": 273.15,
            "pressure_atm": 1.0,
            "standard_state": "1atm",
            "output_file": str(sum_file),
        },
    )
    other = BatchCalculationItem(
        item_id="ts_001",
        candidate_id="ts_001",
        name="TS",
        tag="TS",
        status="completed",
        optimized_xyz="structures/ts_001__TAG_TS__optimized.xyz",
    )
    ts_geometry = tmp_path / "task" / "RESULT" / "structures" / "ts_001__TAG_TS__optimized.xyz"
    ts_geometry.write_text("2\nTAG: TS\nH 0 0 0\nH 0 0 0.8\n", encoding="utf-8")
    manifest = BatchCalculationManifest(profile="opt_freq_sp_thermo", items=[item, other])
    engine._materialize_result_products(manifest)

    relative = batch_thermochemistry_relative_path("int_001")
    product_path = tmp_path / "task" / "RESULT" / relative
    assert product_path.is_file()
    payload = json.loads(product_path.read_text(encoding="utf-8"))
    assert payload["gibbs_hartree"] == -234.3616254
    assert payload["zpe_hartree"] == 0.137911

    written = json.loads(
        (tmp_path / "task" / "RESULT" / "result_manifest.json").read_text(encoding="utf-8")
    )
    products = {product["id"]: product for product in written["products"]}
    thermo = products["batch_int_001_thermochemistry"]
    assert thermo["kind"] == "thermo_report"
    assert thermo["path"] == relative
    assert thermo["metadata"]["thermochemistry"]["gibbs_hartree"] == -234.3616254
    # No thermochemistry step for the TS item → no product, no fake zeros.
    assert "batch_ts_001_thermochemistry" not in products

    items, _ = descriptors.describe_job_artifacts(tmp_path / "task", attempt=1)
    descriptor = next(
        item for item in items if item["resource_id"] == "batch_int_001_thermochemistry"
    )
    assert descriptor["kind"] == "thermochemistry"
    assert descriptor["relative_path"] == f"RESULT/{relative}"


# ── simple frequency workflow wiring ───────────────────────────────────


def test_frequency_workflow_publishes_thermochemistry(tmp_path: Path) -> None:
    from acp.calculations.contracts import CalculationPlan, CalculationStep
    from acp.storage.manifest import ResultManifest
    from acp.workflows.simple import _publish_frequency_thermochemistry

    calc_dir = tmp_path / "task"
    result_dir = calc_dir / "RESULT"
    ResultManifest(task_id="t", workflow="frequency", status="completed").write(result_dir)
    freq = calc_dir / "WORK" / "04_FREQ" / "freq.out"
    freq.parent.mkdir(parents=True, exist_ok=True)
    freq.write_text(_ORCA_THERMO_BLOCK, encoding="utf-8")

    plan = CalculationPlan(
        workflow="frequency",
        profile="default",
        items=[],
        steps=[
            CalculationStep(
                kind=StepKind.FREQUENCY,
                spec={"method": "r2SCAN-3c", "basis": "def2-TZVP"},
            )
        ],
    )
    execution = SimpleNamespace(
        step_states=[
            SimpleNamespace(
                kind=StepKind.FREQUENCY,
                result=CalculationResult(
                    frequencies=[100.0, 200.0],
                    artifacts=[ArtifactRef(path=freq, type="frequency_log")],
                ),
            )
        ]
    )
    _publish_frequency_thermochemistry(plan, execution, calc_dir)

    target = result_dir / PRODUCT_RELATIVE_PATH
    assert target.is_file()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["gibbs_hartree"] == -1.11677494
    manifest = ResultManifest.read(result_dir)
    product = next(p for p in manifest.products if p.id == "thermochemistry")
    assert product.kind.value == "thermo_report"
    # The MCP status projection (acp_status.artifacts) sees it as thermochemistry.
    items, _ = descriptors.describe_job_artifacts(calc_dir, attempt=1)
    descriptor = next(item for item in items if item["resource_id"] == "thermochemistry")
    assert descriptor["kind"] == "thermochemistry"
    assert descriptor["relative_path"] == "RESULT/thermochemistry.json"

    # A non-frequency workflow never publishes this product.
    other_plan = CalculationPlan(
        workflow="singlepoint",
        profile="default",
        items=[],
        steps=[
            CalculationStep(
                kind=StepKind.SINGLEPOINT,
                spec={"method": "r2SCAN-3c", "basis": "def2-TZVP"},
            )
        ],
    )
    before = ResultManifest.read(result_dir)
    _publish_frequency_thermochemistry(other_plan, execution, calc_dir)
    assert ResultManifest.read(result_dir).products == before.products


def test_payload_does_not_leak_an_absolute_host_path(tmp_path: Path) -> None:
    """Round-7 P2: agent-facing payloads carry a basename, never /home/... ."""
    from acp.results.thermochemistry import parse_orca_thermochemistry

    log = tmp_path / "freq.out"
    log.write_text(_ORCA_THERMO_BLOCK, encoding="utf-8")
    values = parse_orca_thermochemistry(_ORCA_THERMO_BLOCK)
    assert values is not None
    from acp.results.thermochemistry import build_frequency_thermochemistry

    payload = build_frequency_thermochemistry(log)
    assert payload is not None
    assert payload["source"]["path"] == "freq.out"
    assert str(tmp_path) not in str(payload)
