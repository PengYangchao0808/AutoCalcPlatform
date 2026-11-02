"""Round-8 三.4: thermochemistry must be a first-class product for ``opt_freq_sp``.

The external agent ran ``BatchOptimize`` with the ``opt_freq_sp`` profile — no
Shermo thermochemistry step — and the manifest carried no ZPE/H/G/S product at
all, even though ORCA's frequency output had already printed
``Electronic energy / Zero point energy / Total Enthalpy / Final Gibbs free
energy``.  The only way to read the numbers was to grep ``WORK/04_FREQ/freq.out``.

These tests pin the round-8 contract:

* a completed FREQUENCY step publishes ``RESULT/thermochemistry/<item>.json``
  as a ``THERMO_REPORT`` product with the values ORCA printed verbatim;
* an existing Shermo product wins and is never overwritten;
* a log without ORCA's thermochemistry block publishes **nothing** (no
  fabricated zeros) and the structure product does not advertise one;
* the structure product metadata gains ``energy_hartree`` /
  ``energy_source: "step"`` / ``energy_step`` and ``thermochemistry_product_id``;
* the item ``step_result.json`` gains ``gibbs_hartree`` / ``enthalpy_hartree`` /
  ``zpe_hartree`` only when they were really parsed;
* the product is reachable through the MCP artifact path
  (``describe_job_artifacts`` → ``prepare`` → ``read``) — the "never grep the
  QC log" acceptance.

The ORCA excerpts below are **synthetic** (written from ORCA 6's printed
thermochemistry layout); the repository has no real ``opt_freq_sp`` task
fixture with ``freq.out`` + ``result_manifest.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

from acp.api import artifact_descriptors as descriptors
from acp.calculations.batch._items import BatchCalculationItem, BatchStructureItem
from acp.calculations.batch.engine import BatchOptimizeEngine
from acp.calculations.batch.models import BatchCalculationManifest
from acp.calculations.contracts import StepKind

#: Synthetic ORCA 6 ``THERMOCHEMISTRY AT 298.15K`` block (freq step output).
ORCA_THERMO_BLOCK = """\
THERMOCHEMISTRY AT 298.15K
---------------------------

Temperature         ...   298.15 K
Pressure            ...     1.00 atm
Quasi RRHO          ...     True
Cut-Off Frequency   ...     1.00 cm^-1

Summary of contributions to the inner energy U:
Electronic energy                ...    -154.09321145 Eh
Zero point energy                ...      0.03451221 Eh      21.66 kcal/mol
Total thermal correction                  0.00251412 Eh
Total correction                          0.03702633 Eh

Total Enthalpy                    ...    -154.05568512 Eh

Final entropy term                ...      0.01520331 Eh      9.54 kcal/mol

Final Gibbs free energy         ...    -154.10841476 Eh
"""

#: The same log without the thermochemistry block (e.g. a failed/partial run).
ORCA_FREQ_WITHOUT_THERMO = """\
VIBRATIONAL FREQUENCIES
-----------------------
   0:         0.00 cm**-1
   6:       102.33 cm**-1

****ORCA TERMINATED NORMALLY****
"""

SHERMO_SUM = """\
 Zero point energy (ZPE):    362.086 kJ/mol     86.541 kcal/mol   0.137911 a.u.
"""

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


def _frequency_log(tmp_path: Path, item_id: str, text: str) -> str:
    """Write the batch-layout frequency log; return its task-relative path."""
    log = tmp_path / "task" / "WORK" / "03_OPT" / "batch" / item_id / "frequency" / "freq.out"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(text, encoding="utf-8")
    return log.relative_to(tmp_path / "task").as_posix()


def _geometry(tmp_path: Path, item_id: str, tag: str = "INT") -> str:
    target = (
        tmp_path / "task" / "RESULT" / "structures" / f"{item_id}__TAG_{tag}__optimized.xyz"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_XYZ, encoding="utf-8")
    return target.relative_to(tmp_path / "task" / "RESULT").as_posix()


def _item(
    tmp_path: Path,
    *,
    item_id: str = "int_001",
    tag: str = "INT",
    log_text: str | None = ORCA_THERMO_BLOCK,
    single_point_energy: float | None = -154.093211,
) -> BatchCalculationItem:
    frequency: dict[str, object] = {
        "frequencies": [102.33, 144.02, 3010.5],
        "status": "completed",
        "energy_hartree": -154.0932114,
    }
    if log_text is not None:
        frequency["log_file"] = _frequency_log(tmp_path, item_id, log_text)
    item = BatchCalculationItem(
        item_id=item_id,
        candidate_id=item_id,
        name=tag,
        tag=tag,
        status="completed",
        optimized_xyz=_geometry(tmp_path, item_id, tag),
        frequency=frequency,
    )
    if single_point_energy is not None:
        item.single_point = {"energy_hartree": single_point_energy, "status": "completed"}
    return item


def _run_materialization(
    tmp_path: Path, items: list[BatchCalculationItem], profile: str = "opt_freq_sp"
) -> BatchOptimizeEngine:
    engine = _engine(tmp_path)
    manifest = BatchCalculationManifest(profile=profile, items=items)
    engine._materialize_result_products(manifest)
    return engine


def _manifest(tmp_path: Path) -> dict:
    return json.loads(
        (tmp_path / "task" / "RESULT" / "result_manifest.json").read_text(encoding="utf-8")
    )


def _products(tmp_path: Path) -> dict[str, dict]:
    return {product["id"]: product for product in _manifest(tmp_path)["products"]}


# ── the product itself ──────────────────────────────────────────────────


def test_frequency_log_publishes_a_thermo_report(tmp_path: Path) -> None:
    _run_materialization(tmp_path, [_item(tmp_path)])

    product_path = tmp_path / "task" / "RESULT" / "thermochemistry" / "int_001.json"
    assert product_path.is_file()
    payload = json.loads(product_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "acp_thermochemistry_v1"
    assert payload["gibbs_hartree"] == -154.10841476
    assert payload["enthalpy_hartree"] == -154.05568512
    assert payload["zpe_hartree"] == 0.03451221
    assert payload["entropy_term_hartree"] == 0.01520331
    assert payload["source"]["kind"] == "orca_frequency_output"
    # Provenance is a basename, never an absolute host path.
    assert payload["source"]["path"] == "freq.out"
    assert str(tmp_path) not in json.dumps(payload)

    products = _products(tmp_path)
    thermo = products["thermochemistry_int_001"]
    assert thermo["kind"] == "thermo_report"
    assert thermo["path"] == "thermochemistry/int_001.json"
    assert thermo["metadata"]["source_step"] == "frequency"
    assert thermo["metadata"]["thermochemistry"]["gibbs_hartree"] == -154.10841476
    assert thermo["metadata"]["thermochemistry"]["zpe_hartree"] == 0.03451221


def test_thermo_product_is_reachable_through_the_mcp_artifact_path(tmp_path: Path) -> None:
    """``describe_job_artifacts`` is what ``acp_status.artifacts`` shows."""
    _run_materialization(tmp_path, [_item(tmp_path)])

    items, truncated = descriptors.describe_job_artifacts(tmp_path / "task", attempt=1)
    assert truncated is False
    descriptor = next(
        item for item in items if item["resource_id"] == "thermochemistry_int_001"
    )
    assert descriptor["kind"] == "thermochemistry"
    # prepare-safe RESULT-relative path: the acp_read_artifact input.
    assert descriptor["relative_path"] == "RESULT/thermochemistry/int_001.json"
    assert (tmp_path / "task" / descriptor["relative_path"]).is_file()


# ── structure metadata / step_result projection ─────────────────────────


def test_structure_product_carries_energy_and_thermo_pointer(tmp_path: Path) -> None:
    _run_materialization(tmp_path, [_item(tmp_path)])

    structure = _products(tmp_path)["batch_int_001"]
    assert structure["metadata"]["energy_hartree"] == -154.093211
    assert structure["metadata"]["energy_source"] == "step"
    assert structure["metadata"]["energy_step"] == "singlepoint"
    assert structure["metadata"]["thermochemistry_product_id"] == "thermochemistry_int_001"


def test_opt_freq_energy_falls_back_to_the_frequency_step(tmp_path: Path) -> None:
    """Without an SP step the frequency step's printed energy is used."""
    _run_materialization(
        tmp_path, [_item(tmp_path, single_point_energy=None)], profile="opt_freq"
    )

    structure = _products(tmp_path)["batch_int_001"]
    assert structure["metadata"]["energy_step"] == "frequency"
    assert structure["metadata"]["energy_source"] == "step"
    assert structure["metadata"]["energy_hartree"] == -154.0932114


def test_step_result_carries_only_the_parsed_thermo_values(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    record = _item(tmp_path)
    structure_item = BatchStructureItem(
        item_id=record.item_id, name=record.name, tag=record.tag, xyz=_XYZ
    )
    engine._write_item_step_result(
        structure_item, record, index=0, profile="opt_freq_sp", workflow="BatchOptimize"
    )

    payload = json.loads(
        (
            tmp_path / "task" / "WORK" / "03_OPT" / "batch" / "int_001" / "step_result.json"
        ).read_text(encoding="utf-8")
    )
    metadata = payload["metadata"]
    assert metadata["gibbs_hartree"] == -154.10841476
    assert metadata["enthalpy_hartree"] == -154.05568512
    assert metadata["zpe_hartree"] == 0.03451221
    # The pre-existing ``energy`` semantics are untouched (SP step energy).
    assert payload["energy"] == -154.093211


def test_step_result_omits_thermo_when_the_log_has_no_block(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    record = _item(tmp_path, log_text=ORCA_FREQ_WITHOUT_THERMO)
    structure_item = BatchStructureItem(
        item_id=record.item_id, name=record.name, tag=record.tag, xyz=_XYZ
    )
    engine._write_item_step_result(
        structure_item, record, index=0, profile="opt_freq_sp", workflow="BatchOptimize"
    )

    payload = json.loads(
        (
            tmp_path / "task" / "WORK" / "03_OPT" / "batch" / "int_001" / "step_result.json"
        ).read_text(encoding="utf-8")
    )
    for key in ("gibbs_hartree", "enthalpy_hartree", "zpe_hartree"):
        assert key not in payload["metadata"]


# ── no fabrication, no overwrite ────────────────────────────────────────


def test_log_without_thermochemistry_publishes_nothing(tmp_path: Path) -> None:
    _run_materialization(tmp_path, [_item(tmp_path, log_text=ORCA_FREQ_WITHOUT_THERMO)])

    products = _products(tmp_path)
    assert not [pid for pid in products if "thermochemistry" in pid]
    assert not (tmp_path / "task" / "RESULT" / "thermochemistry" / "int_001.json").exists()
    structure = products["batch_int_001"]
    assert "thermochemistry_product_id" not in structure["metadata"]


def test_missing_frequency_log_publishes_nothing(tmp_path: Path) -> None:
    _run_materialization(tmp_path, [_item(tmp_path, log_text=None)])

    products = _products(tmp_path)
    assert not [pid for pid in products if "thermochemistry" in pid]
    assert "thermochemistry_product_id" not in products["batch_int_001"]["metadata"]


def test_existing_shermo_product_wins_and_is_not_overwritten(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    sum_file = tmp_path / "Shermo.sum"
    sum_file.write_text(SHERMO_SUM, encoding="utf-8")
    item = _item(tmp_path)
    item.thermochemistry = {
        "status": "completed",
        "success": True,
        "gibbs_hartree": -154.10841476,
        "enthalpy_hartree": -154.05513122,
        "entropy_au": 0.0012,
        "temperature_k": 298.15,
        "pressure_atm": 1.0,
        "standard_state": "1atm",
        "output_file": str(sum_file),
    }
    manifest = BatchCalculationManifest(profile="opt_freq_sp_thermo", items=[item])
    engine._materialize_result_products(manifest)

    products = _products(tmp_path)
    assert "batch_int_001_thermochemistry" in products
    assert "thermochemistry_int_001" not in products
    payload = json.loads(
        (tmp_path / "task" / "RESULT" / "thermochemistry" / "int_001.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["source"]["kind"] == "shermo"
    assert payload["zpe_hartree"] == 0.137911
    assert (
        products["batch_int_001"]["metadata"]["thermochemistry_product_id"]
        == "batch_int_001_thermochemistry"
    )


def test_shermo_product_survives_a_later_frequency_only_pass(tmp_path: Path) -> None:
    """A resumed/re-materialized item keeps the Shermo product (never overwritten)."""
    engine = _engine(tmp_path)
    sum_file = tmp_path / "Shermo.sum"
    sum_file.write_text(SHERMO_SUM, encoding="utf-8")
    shermo_item = _item(tmp_path)
    shermo_item.thermochemistry = {
        "status": "completed",
        "success": True,
        "gibbs_hartree": -154.10000000,
        "enthalpy_hartree": -154.05000000,
        "output_file": str(sum_file),
    }
    engine._materialize_result_products(
        BatchCalculationManifest(profile="opt_freq_sp_thermo", items=[shermo_item])
    )

    # Second pass: the thermochemistry step record is gone, only the freq log
    # remains.  The already-registered Shermo payload must stay byte-identical.
    thermo_file = tmp_path / "task" / "RESULT" / "thermochemistry" / "int_001.json"
    before = thermo_file.read_text(encoding="utf-8")
    freq_only = _item(tmp_path)
    engine2 = _engine(tmp_path)
    engine2._materialize_result_products(
        BatchCalculationManifest(profile="opt_freq_sp", items=[freq_only])
    )

    assert thermo_file.read_text(encoding="utf-8") == before
    payload = json.loads(before)
    assert payload["source"]["kind"] == "shermo"


def test_ts_validation_failure_does_not_publish_thermochemistry(tmp_path: Path) -> None:
    """A TS without its imaginary mode is not a saddle: no thermo product."""
    item = _item(tmp_path, item_id="ts_001", tag="TS")
    item.frequency["ts_validation"] = "failed"
    _run_materialization(tmp_path, [item], profile="opt_freq_sp")

    products = _products(tmp_path)
    assert "thermochemistry_ts_001" not in products
    assert "batch_ts_001_thermochemistry" not in products


def test_failed_item_publishes_no_thermochemistry(tmp_path: Path) -> None:
    item = _item(tmp_path)
    item.status = "failed"
    item.error = "single-point failed: SCF not converged"
    _run_materialization(tmp_path, [item])

    products = _products(tmp_path)
    assert "thermochemistry_int_001" not in products
    assert "thermochemistry_product_id" not in products["batch_int_001"]["metadata"]


def test_profiles_are_unchanged(tmp_path: Path) -> None:
    """Round-8 adds products, never steps: the profile DAG is untouched."""
    from acp.calculations.batch.profiles import BATCH_PROFILE_STEPS

    assert BATCH_PROFILE_STEPS["opt_freq_sp"] == (
        StepKind.OPTIMIZE,
        StepKind.FREQUENCY,
        StepKind.SINGLEPOINT,
    )
    assert BATCH_PROFILE_STEPS["opt_freq"] == (StepKind.OPTIMIZE, StepKind.FREQUENCY)


# ── shared helper (also used by the frequency workflow) ─────────────────


def _simple_frequency_execution(calc_dir: Path, log_text: str):
    from types import SimpleNamespace

    from acp.calculations.contracts import ArtifactRef, CalculationResult

    freq = calc_dir / "WORK" / "04_FREQ" / "freq.out"
    freq.parent.mkdir(parents=True, exist_ok=True)
    freq.write_text(log_text, encoding="utf-8")
    return SimpleNamespace(
        step_states=[
            SimpleNamespace(
                kind=StepKind.FREQUENCY,
                status="completed",
                result=CalculationResult(
                    frequencies=[102.33, 144.02],
                    artifacts=[ArtifactRef(path=freq, type="frequency_log")],
                ),
            )
        ]
    )


def test_shared_helper_publishes_for_the_frequency_workflow(tmp_path: Path) -> None:
    from acp.calculations.contracts import CalculationPlan, CalculationStep
    from acp.storage.manifest import ResultManifest
    from acp.workflows.simple import _publish_frequency_thermochemistry

    calc_dir = tmp_path / "task"
    ResultManifest(task_id="t", workflow="frequency", status="completed").write(
        calc_dir / "RESULT"
    )
    plan = CalculationPlan(
        workflow="frequency",
        profile="default",
        items=[],
        steps=[
            CalculationStep(
                kind=StepKind.FREQUENCY, spec={"method": "r2SCAN-3c", "basis": "def2-TZVP"}
            )
        ],
    )

    product_id = _publish_frequency_thermochemistry(
        plan, _simple_frequency_execution(calc_dir, ORCA_THERMO_BLOCK), calc_dir
    )

    assert product_id == "thermochemistry"
    payload = json.loads(
        (calc_dir / "RESULT" / "thermochemistry.json").read_text(encoding="utf-8")
    )
    assert payload["gibbs_hartree"] == -154.10841476
    products = {p.id: p for p in ResultManifest.read(calc_dir / "RESULT").products}
    assert products["thermochemistry"].kind.value == "thermo_report"


def test_shared_helper_publishes_nothing_without_the_block(tmp_path: Path) -> None:
    from acp.calculations.contracts import CalculationPlan, CalculationStep
    from acp.storage.manifest import ResultManifest
    from acp.workflows.simple import _publish_frequency_thermochemistry

    calc_dir = tmp_path / "task"
    ResultManifest(task_id="t", workflow="frequency", status="completed").write(
        calc_dir / "RESULT"
    )
    plan = CalculationPlan(
        workflow="frequency",
        profile="default",
        items=[],
        steps=[CalculationStep(kind=StepKind.FREQUENCY, spec={"method": "r2SCAN-3c"})],
    )

    product_id = _publish_frequency_thermochemistry(
        plan, _simple_frequency_execution(calc_dir, ORCA_FREQ_WITHOUT_THERMO), calc_dir
    )

    assert product_id is None
    assert not (calc_dir / "RESULT" / "thermochemistry.json").exists()
    assert ResultManifest.read(calc_dir / "RESULT").products == []


def test_early_materialization_does_not_hide_the_later_thermochemistry(tmp_path: Path) -> None:
    """``run()`` publishes the structure right after OPT — before the freq step.

    That early pass must not cache "no thermochemistry yet": the final pass has
    to publish the product the frequency step later produced.
    """
    engine = _engine(tmp_path)
    item = _item(tmp_path)
    # Early state: optimization finished, frequency not started yet.
    pending_frequency = dict(item.frequency)
    item.frequency = {"status": "running"}
    engine._materialize_result_products(
        BatchCalculationManifest(profile="opt_freq_sp", items=[item])
    )
    assert not (tmp_path / "task" / "RESULT" / "thermochemistry" / "int_001.json").exists()

    # Frequency step completed afterwards → the final pass publishes it.
    item.frequency = pending_frequency
    engine._materialize_result_products(
        BatchCalculationManifest(profile="opt_freq_sp", items=[item])
    )

    assert (tmp_path / "task" / "RESULT" / "thermochemistry" / "int_001.json").is_file()
    assert "thermochemistry_int_001" in _products(tmp_path)


def test_full_profile_run_publishes_thermochemistry(
    tmp_path: Path, fake_backend: object, monkeypatch
) -> None:
    """End-to-end ``run()``: the frequency step's log becomes a product.

    Uses the shared recording fake backend; its ``frequency`` call additionally
    writes a real ORCA thermochemistry block into the step directory.
    """
    from tests.conftest import FakeBackend

    assert isinstance(fake_backend, FakeBackend)
    original_frequency = fake_backend.frequency

    def _frequency(*args: object, **kwargs: object):
        output_dir = kwargs.get("output_dir")
        if output_dir is not None:
            log = Path(str(output_dir)) / "freq.out"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(ORCA_THERMO_BLOCK, encoding="utf-8")
        return original_frequency(*args, **kwargs)

    monkeypatch.setattr(fake_backend, "frequency", _frequency)

    engine = _engine(tmp_path)
    item = BatchStructureItem(
        item_id="int_001",
        name="INT",
        tag="INT",
        xyz="2\nTAG: INT\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74\n",
        candidate_id="int_001",
    )
    outcome = engine.run([item], profile="opt_freq_sp", charge=0, multiplicity=1)

    assert outcome.items[0].status == "completed", outcome.items[0].error
    products = _products(tmp_path)
    thermo = products["thermochemistry_int_001"]
    assert thermo["kind"] == "thermo_report"
    payload = json.loads(
        (tmp_path / "task" / "RESULT" / thermo["path"]).read_text(encoding="utf-8")
    )
    assert payload["gibbs_hartree"] == -154.10841476
    assert payload["source"]["path"] == "freq.out"
    assert str(tmp_path) not in json.dumps(payload)

    # The step receipt records the same values, and the structure product
    # points at the product + carries the step energy.
    step_result = json.loads(
        (
            tmp_path / "task" / "WORK" / "03_OPT" / "batch" / "int_001" / "step_result.json"
        ).read_text(encoding="utf-8")
    )
    assert step_result["metadata"]["zpe_hartree"] == 0.03451221
    assert step_result["metadata"]["hessian_source"] == "freq_numfreq"
    structure = products["batch_int_001"]
    assert structure["metadata"]["thermochemistry_product_id"] == "thermochemistry_int_001"
    assert structure["metadata"]["energy_source"] == "step"
