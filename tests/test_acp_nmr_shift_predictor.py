"""ShiftPredictor protocol + independent registry — contract and isolation (todo 55 / G17).

Two test families:

* **Contract**: a stub implementation satisfies the runtime-checkable
  :class:`~acp.nmr.shift_predictor.ShiftPredictor` protocol; per-atom output
  is keyed by the explicit atom mapping into typed records (never bare
  floats); the predictive distribution is optional and an absent one stays
  ``None`` (never fabricated as 0); geometry requirements and provenance
  travel with the prediction; registry register/get/list/unregister round
  trips and misuse raises typed errors.
* **Isolation**: the predictor module imports nothing from the ACP/CCCP
  calculation chain; the formal NMR/DP4/DP5 pipeline (``workflows/`` +
  ``error_model``/``probability``) never references the predictor; a fresh
  interpreter starts with an EMPTY registry (no default predictor, no eager
  wiring); and the source never mentions the formal error-model classes.

The coupling tokens below are deliberately assembled at runtime so this test
file itself never contains the grep-checked isolation token.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from acp.nmr.shift_predictor import (
    CALIBRATION_STATUSES,
    DuplicateShiftPredictorError,
    GeometryRequirements,
    InvalidShiftPredictorError,
    PredictedShift,
    PredictorGeometry,
    ShiftDistribution,
    ShiftModelProvenance,
    ShiftPrediction,
    ShiftPredictionRequest,
    ShiftPredictor,
    ShiftPredictorError,
    UnknownShiftPredictorError,
    get_shift_predictor,
    list_shift_predictors,
    register_shift_predictor,
    unregister_shift_predictor,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PREDICTOR_MODULE_PATH = REPO_ROOT / "src" / "acp" / "nmr" / "shift_predictor.py"

#: Formal error-model class tokens, assembled from fragments so this file
#: never carries the grep-checked coupling literal itself.
_FORMAL_ERROR_MODEL_TOKENS: tuple[str, ...] = (
    "Goodman" + "ErrorModel",
    "Gaussian" + "ErrorModel",
)

#: The formal (default) NMR/DP4/DP5 pipeline surfaces that must never consume
#: the research predictor registry.
_PIPELINE_GUARDED_DIRS: tuple[str, ...] = ("src/acp/workflows",)
_PIPELINE_GUARDED_FILES: tuple[str, ...] = (
    "src/acp/nmr/error_model.py",
    "src/acp/nmr/probability.py",
)
_PREDICTOR_REFERENCE_TOKENS: tuple[str, ...] = ("shift_predictor", "ShiftPredictor")


# ---------------------------------------------------------------------------
# stubs / helpers
# ---------------------------------------------------------------------------


def _provenance(model_id: str = "stub-gnn-v0") -> ShiftModelProvenance:
    """Stub provenance: no calibration data exists → honestly uncalibrated."""
    return ShiftModelProvenance(
        model_id=model_id,
        version="0.0.1-stub",
        calibration_status="uncalibrated",
    )


class _StubPredictor:
    """Minimal ShiftPredictor implementation (no training, no calibration data)."""

    def __init__(
        self,
        model_id: str = "stub-gnn-v0",
        *,
        emit_distribution: bool = True,
        min_conformers: int = 1,
        requires_optimized_geometry: bool = False,
        geometry_level: str = "any",
    ) -> None:
        self._model = _provenance(model_id)
        self._geometry = GeometryRequirements(
            min_conformers=min_conformers,
            requires_optimized_geometry=requires_optimized_geometry,
            geometry_level=geometry_level,
        )
        self._emit_distribution = emit_distribution

    @property
    def model_provenance(self) -> ShiftModelProvenance:
        return self._model

    @property
    def geometry_requirements(self) -> GeometryRequirements:
        return self._geometry

    def predict(self, request: ShiftPredictionRequest) -> ShiftPrediction:
        base = 128.0
        shifts: dict[str, PredictedShift] = {}
        for index, atom_uid in enumerate(request.geometries[0].atom_uids):
            shift = base + float(index)
            distribution = (
                ShiftDistribution(mean_ppm=shift, std_ppm=0.5) if self._emit_distribution else None
            )
            shifts[atom_uid] = PredictedShift(shift_ppm=shift, distribution=distribution)
        return ShiftPrediction(
            nucleus=request.nucleus,
            model=self._model,
            geometry_requirements=self._geometry,
            shifts=shifts,
        )


class _ForeignProvenanceStub:
    """Duck object with protocol members but a non-typed provenance record."""

    model_provenance = {"model_id": "foreign"}
    geometry_requirements = GeometryRequirements()

    def predict(self, request: ShiftPredictionRequest) -> ShiftPrediction:  # pragma: no cover
        raise NotImplementedError


def _geometry(
    atom_uids: tuple[str, ...] = ("C1", "C2", "H1"),
    *,
    level: str = "xtb-GFN2",
) -> PredictorGeometry:
    symbols = tuple(atom_uid[:1].upper() for atom_uid in atom_uids)
    coordinates = tuple((float(index), 0.0, 0.0) for index in range(len(atom_uids)))
    return PredictorGeometry(
        symbols=symbols,
        coordinates=coordinates,
        atom_uids=atom_uids,
        level=level,
    )


def _request(
    *,
    atom_uids: tuple[str, ...] = ("C1", "C2", "H1"),
    n_geometries: int = 1,
    nucleus: str = "13C",
) -> ShiftPredictionRequest:
    return ShiftPredictionRequest(
        nucleus=nucleus,
        geometries=tuple(_geometry(atom_uids) for _ in range(n_geometries)),
    )


@pytest.fixture
def registry_snapshot() -> Iterator[None]:
    """Restore the registry to its pre-test content after the test."""
    before = {model_id: get_shift_predictor(model_id) for model_id in list_shift_predictors()}
    yield
    for model_id in list_shift_predictors():
        if model_id not in before:
            unregister_shift_predictor(model_id)
    for model_id, predictor in before.items():
        current = get_shift_predictor(model_id) if model_id in list_shift_predictors() else None
        if current is not predictor:
            register_shift_predictor(predictor, replace=True)


# ---------------------------------------------------------------------------
# protocol contract
# ---------------------------------------------------------------------------


def test_stub_satisfies_runtime_checkable_protocol() -> None:
    stub = _StubPredictor()
    assert isinstance(stub, ShiftPredictor)
    assert isinstance(stub.model_provenance, ShiftModelProvenance)
    assert isinstance(stub.geometry_requirements, GeometryRequirements)
    assert callable(stub.predict)


def test_objects_missing_protocol_members_fail_isinstance() -> None:
    class _MissingPredict:
        @property
        def model_provenance(self) -> ShiftModelProvenance:
            return _provenance()

        @property
        def geometry_requirements(self) -> GeometryRequirements:
            return GeometryRequirements()

    assert not isinstance(object(), ShiftPredictor)
    assert not isinstance(_MissingPredict(), ShiftPredictor)


def test_prediction_keys_follow_the_explicit_atom_mapping() -> None:
    stub = _StubPredictor()
    request = _request(atom_uids=("C1", "C2", "H1"))
    prediction = stub.predict(request)
    assert isinstance(prediction, ShiftPrediction)
    assert prediction.nucleus == "13C"
    assert set(prediction.shifts) == {"C1", "C2", "H1"}
    for atom_uid in request.geometries[0].atom_uids:
        record = prediction.shifts[atom_uid]
        assert isinstance(record, PredictedShift)
        assert not isinstance(record, float)


def test_prediction_rejects_bare_float_values() -> None:
    with pytest.raises(ValueError, match="PredictedShift"):
        ShiftPrediction(
            nucleus="13C",
            model=_provenance(),
            geometry_requirements=GeometryRequirements(),
            shifts={"C1": 128.0},  # type: ignore[dict-item]
        )


def test_distribution_none_is_preserved_never_fabricated() -> None:
    without = _StubPredictor(emit_distribution=False).predict(_request())
    assert without.shifts["C1"].distribution is None

    with_distribution = _StubPredictor(emit_distribution=True).predict(_request())
    distribution = with_distribution.shifts["C1"].distribution
    assert isinstance(distribution, ShiftDistribution)
    assert distribution.std_ppm == pytest.approx(0.5)


def test_geometry_requirements_travel_with_the_prediction() -> None:
    stub = _StubPredictor(
        min_conformers=2,
        requires_optimized_geometry=True,
        geometry_level="DFT-optimized",
    )
    request = _request(n_geometries=2)
    prediction = stub.predict(request)
    assert prediction.geometry_requirements == GeometryRequirements(
        min_conformers=2,
        requires_optimized_geometry=True,
        geometry_level="DFT-optimized",
    )
    assert prediction.model == stub.model_provenance


# ---------------------------------------------------------------------------
# typed records: provenance / distribution / geometry / request validation
# ---------------------------------------------------------------------------


def test_provenance_requires_model_id_and_version() -> None:
    with pytest.raises(ValueError):
        ShiftModelProvenance(model_id="", version="1", calibration_status="uncalibrated")
    with pytest.raises(ValueError):
        ShiftModelProvenance(model_id="stub", version="  ", calibration_status="uncalibrated")


def test_stub_provenance_is_honestly_uncalibrated() -> None:
    provenance = _provenance()
    assert provenance.calibration_status == "uncalibrated"
    assert provenance.calibrator is None
    assert provenance.calibration_reference is None
    assert set(CALIBRATION_STATUSES) == {"calibrated", "uncalibrated"}


def test_calibrated_claim_requires_calibrator_and_reference() -> None:
    with pytest.raises(ValueError, match="calibrator"):
        ShiftModelProvenance(model_id="stub", version="1", calibration_status="calibrated")
    with pytest.raises(ValueError, match="calibration reference"):
        ShiftModelProvenance(
            model_id="stub",
            version="1",
            calibration_status="calibrated",
            calibrator="some lab",
        )
    calibrated = ShiftModelProvenance(
        model_id="stub",
        version="1",
        calibration_status="calibrated",
        calibrator="some lab",
        calibration_reference="dataset:abc123",
    )
    assert calibrated.calibration_status == "calibrated"


def test_uncalibrated_must_not_carry_a_calibrator() -> None:
    with pytest.raises(ValueError, match="uncalibrated"):
        ShiftModelProvenance(
            model_id="stub",
            version="1",
            calibration_status="uncalibrated",
            calibrator="some lab",
        )
    with pytest.raises(ValueError, match="uncalibrated"):
        ShiftModelProvenance(
            model_id="stub",
            version="1",
            calibration_status="uncalibrated",
            calibration_reference="dataset:abc123",
        )


def test_unknown_calibration_status_is_rejected() -> None:
    with pytest.raises(ValueError):
        ShiftModelProvenance(model_id="stub", version="1", calibration_status="maybe")


def test_provenance_and_geometry_as_dict_are_json_safe() -> None:
    provenance = _provenance()
    payload = provenance.as_dict()
    assert payload == {
        "model_id": "stub-gnn-v0",
        "version": "0.0.1-stub",
        "calibration_status": "uncalibrated",
        "calibrator": None,
        "calibration_reference": None,
        "notes": "",
    }
    json.dumps(payload)

    geometry = GeometryRequirements(
        min_conformers=2,
        requires_optimized_geometry=True,
        geometry_level="DFT-optimized",
    )
    assert geometry.as_dict() == {
        "min_conformers": 2,
        "requires_optimized_geometry": True,
        "geometry_level": "DFT-optimized",
        "requires_explicit_hydrogens": True,
    }


def test_distribution_requires_an_uncertainty_term() -> None:
    with pytest.raises(ValueError, match="uncertainty"):
        ShiftDistribution(mean_ppm=128.0)
    assert ShiftDistribution(mean_ppm=128.0, std_ppm=0.5).std_ppm == pytest.approx(0.5)
    quantile_only = ShiftDistribution(mean_ppm=128.0, quantiles=((0.1, 127.0), (0.9, 129.0)))
    assert quantile_only.quantiles == ((0.1, 127.0), (0.9, 129.0))


def test_distribution_rejects_invalid_spread() -> None:
    with pytest.raises(ValueError):
        ShiftDistribution(mean_ppm=128.0, std_ppm=-0.1)
    with pytest.raises(ValueError):
        ShiftDistribution(mean_ppm=128.0, std_ppm=float("nan"))
    with pytest.raises(ValueError):
        ShiftDistribution(mean_ppm=128.0, quantiles=((0.0, 127.0),))
    with pytest.raises(ValueError):
        ShiftDistribution(mean_ppm=128.0, quantiles=((0.9, 127.0), (0.1, 129.0)))
    with pytest.raises(ValueError):
        ShiftDistribution(mean_ppm=float("inf"), std_ppm=0.5)


def test_geometry_requirements_validation() -> None:
    with pytest.raises(ValueError):
        GeometryRequirements(min_conformers=0)
    with pytest.raises(ValueError):
        GeometryRequirements(geometry_level="   ")
    with pytest.raises(ValueError):
        GeometryRequirements(requires_optimized_geometry="yes")  # type: ignore[arg-type]


def test_predictor_geometry_validation() -> None:
    with pytest.raises(ValueError, match="length"):
        PredictorGeometry(symbols=("C",), coordinates=((0.0, 0.0, 0.0),), atom_uids=("C1", "H1"))
    with pytest.raises(ValueError, match="duplicate"):
        PredictorGeometry(
            symbols=("C", "C"),
            coordinates=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
            atom_uids=("C1", "C1"),
        )
    with pytest.raises(ValueError):
        PredictorGeometry(symbols=("C",), coordinates=((0.0, 0.0),), atom_uids=("C1",))


def test_request_requires_at_least_one_geometry() -> None:
    with pytest.raises(ValueError):
        ShiftPredictionRequest(nucleus="13C", geometries=())
    with pytest.raises(ValueError):
        ShiftPredictionRequest(nucleus="  ", geometries=(_geometry(),))
    with pytest.raises(ValueError):
        ShiftPredictionRequest(nucleus="13C", geometries=(_geometry(),), multiplicity=0)


def test_request_rejects_mismatched_atom_mappings_across_conformers() -> None:
    with pytest.raises(ValueError, match="atom mapping"):
        ShiftPredictionRequest(
            nucleus="13C",
            geometries=(_geometry(("C1", "C2")), _geometry(("C2", "C1"))),
        )


# ---------------------------------------------------------------------------
# independent registry
# ---------------------------------------------------------------------------


def test_register_get_list_round_trip(registry_snapshot: None) -> None:
    stub = _StubPredictor("stub-gnn-v0")
    returned_id = register_shift_predictor(stub)
    assert returned_id == "stub-gnn-v0"
    assert get_shift_predictor("stub-gnn-v0") is stub
    listed = list_shift_predictors()
    assert "stub-gnn-v0" in listed
    assert list(listed) == sorted(listed)


def test_register_duplicate_raises_typed_error(registry_snapshot: None) -> None:
    register_shift_predictor(_StubPredictor("stub-dup"))
    with pytest.raises(DuplicateShiftPredictorError) as excinfo:
        register_shift_predictor(_StubPredictor("stub-dup"))
    assert isinstance(excinfo.value, ShiftPredictorError)


def test_register_replace_flag_overwrites(registry_snapshot: None) -> None:
    first = _StubPredictor("stub-replace")
    second = _StubPredictor("stub-replace")
    register_shift_predictor(first)
    register_shift_predictor(second, replace=True)
    assert get_shift_predictor("stub-replace") is second


def test_register_rejects_nonconforming_object(registry_snapshot: None) -> None:
    with pytest.raises(InvalidShiftPredictorError, match="ShiftPredictor"):
        register_shift_predictor(object())  # type: ignore[arg-type]


def test_register_rejects_foreign_provenance_record(registry_snapshot: None) -> None:
    with pytest.raises(InvalidShiftPredictorError, match="ShiftModelProvenance"):
        register_shift_predictor(_ForeignProvenanceStub())  # type: ignore[arg-type]


def test_unknown_predictor_id_raises_typed_error() -> None:
    with pytest.raises(UnknownShiftPredictorError) as excinfo:
        get_shift_predictor("no-such-model-xyz")
    assert "no-such-model-xyz" in str(excinfo.value)
    assert isinstance(excinfo.value, ShiftPredictorError)
    assert isinstance(excinfo.value, ValueError)


def test_unregister_round_trip(registry_snapshot: None) -> None:
    register_shift_predictor(_StubPredictor("stub-remove"))
    assert unregister_shift_predictor("stub-remove") is True
    assert "stub-remove" not in list_shift_predictors()
    with pytest.raises(UnknownShiftPredictorError):
        get_shift_predictor("stub-remove")
    assert unregister_shift_predictor("stub-remove") is False


def test_multiple_predictors_coexist_independently(registry_snapshot: None) -> None:
    first = _StubPredictor("stub-a")
    second = _StubPredictor("stub-b")
    register_shift_predictor(first)
    register_shift_predictor(second)
    assert get_shift_predictor("stub-a") is first
    assert get_shift_predictor("stub-b") is second


# ---------------------------------------------------------------------------
# isolation guards (no replacement / no coupling)
# ---------------------------------------------------------------------------


def _predictor_source() -> str:
    return PREDICTOR_MODULE_PATH.read_text(encoding="utf-8")


def _imported_module_names(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                names.append("." * node.level + (node.module or ""))
            elif node.module:
                names.append(node.module)
    return names


def test_module_docstring_documents_the_no_replacement_boundary() -> None:
    module = importlib.import_module("acp.nmr.shift_predictor")
    doc = module.__doc__ or ""
    assert "does NOT replace the GIAO/DP4/DP5 chain" in doc
    assert "never selected by the default NMR workflow" in doc
    assert "Speed alone is never a reason to substitute" in doc
    assert "consumed only by explicit callers" in doc


def test_predictor_module_imports_nothing_from_the_calculation_chain() -> None:
    tree = ast.parse(_predictor_source(), filename=str(PREDICTOR_MODULE_PATH))
    imported = _imported_module_names(tree)
    offenders = [
        name
        for name in imported
        if name == "acp" or name.startswith("acp.") or name == "cccp" or name.startswith("cccp.")
    ]
    assert offenders == [], f"shift_predictor.py must stay dependency-free, got {offenders}"
    assert not [name for name in imported if "error_model" in name]
    assert not [name for name in imported if "workflows" in name]


def test_predictor_source_never_references_formal_error_model_classes() -> None:
    source = _predictor_source()
    for token in _FORMAL_ERROR_MODEL_TOKENS:
        assert token not in source, f"shift_predictor.py must not reference {token!r}"


def test_default_nmr_pipeline_never_references_the_predictor() -> None:
    guarded_paths: list[Path] = []
    for directory in _PIPELINE_GUARDED_DIRS:
        guarded_paths.extend(sorted((REPO_ROOT / directory).rglob("*.py")))
    guarded_paths.extend(REPO_ROOT / name for name in _PIPELINE_GUARDED_FILES)
    offenders: list[str] = []
    for path in guarded_paths:
        text = path.read_text(encoding="utf-8")
        for token in _PREDICTOR_REFERENCE_TOKENS:
            if token in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {token}")
    assert offenders == [], (
        "the default NMR/DP4/DP5 pipeline must never consume the predictor registry "
        f"(explicit callers only): {offenders}"
    )


def test_fresh_process_registry_is_empty_and_never_auto_registers() -> None:
    src_root = REPO_ROOT / "src"
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_root}{os.pathsep}{existing}" if existing else str(src_root)
    code = (
        "import acp.nmr.shift_predictor as predictor\n"
        "assert predictor.list_shift_predictors() == (), 'registry must start empty'\n"
        "import acp.nmr.probability  # formal DP4/DP5 chain\n"
        "assert predictor.list_shift_predictors() == (), 'eager predictor registration'\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr


def test_module_all_is_complete_and_declares_the_contract_symbols() -> None:
    module = importlib.import_module("acp.nmr.shift_predictor")
    declared = tuple(module.__all__)
    assert len(declared) == len(set(declared))
    missing = [name for name in declared if not hasattr(module, name)]
    assert missing == [], f"__all__ names missing from module: {missing}"
    required = {
        "CALIBRATION_STATUSES",
        "DuplicateShiftPredictorError",
        "GeometryRequirements",
        "InvalidShiftPredictorError",
        "PredictedShift",
        "PredictorGeometry",
        "ShiftDistribution",
        "ShiftModelProvenance",
        "ShiftPrediction",
        "ShiftPredictionRequest",
        "ShiftPredictor",
        "ShiftPredictorError",
        "UnknownShiftPredictorError",
        "get_shift_predictor",
        "list_shift_predictors",
        "register_shift_predictor",
        "unregister_shift_predictor",
    }
    assert required <= set(declared)


def test_package_level_exports_are_identical_objects() -> None:
    package = importlib.import_module("acp.nmr")
    module = importlib.import_module("acp.nmr.shift_predictor")
    for name in module.__all__:
        exported = getattr(package, name)
        assert exported is getattr(module, name), f"acp.nmr.{name} is not the module identity"
        assert name in package.__all__, f"acp.nmr.__all__ is missing {name!r}"
