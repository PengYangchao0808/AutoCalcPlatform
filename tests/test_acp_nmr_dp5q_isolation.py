"""Isolated DP5q stub adapter — optional-dependency loadability + no-replacement guards.

Todo 56 (gap G17) of the NMR Goodman remediation plan. Three test families:

* **Optional-dependency availability**: the adapter module always imports
  (lazy import — never an import error at module load) and reports a typed
  :class:`~acp.nmr.dp5q_stub.Dp5qStubStatus`: ``available`` with the detected
  dependency version, or ``unavailable`` with a closed-vocabulary reason
  (``optional_dependency_missing`` / ``optional_dependency_broken``) and an
  explicit human-readable detail. Loading/registering raises the typed
  :class:`~acp.nmr.dp5q_stub.Dp5qStubUnavailableError` — never a silent
  fallback.
* **Contract**: the stub satisfies the todo-55
  :class:`~acp.nmr.shift_predictor.ShiftPredictor` protocol, carries its own
  pinned ``model_id``/``version`` and an honestly ``uncalibrated`` provenance,
  and maps requested geometries onto per-atom predictions keyed by the
  explicit atom mapping. It is loadable but never trains: without packaged
  parameters it refuses to predict with a typed error instead of fabricating
  values. It registers ONLY when an explicit caller asks
  (``register_dp5q_stub``); merely importing or loading registers nothing.
* **No replacement**: the default NMR/DP4/DP5 chain (``run_nmr_shielding``
  task, ``workflows/`` and the Goodman error models) never references the
  stub — neither in source nor behaviorally: with a parameterized stub
  explicitly registered, the DP4/DP5 probability outputs are byte-identical
  to the unregistered run and the stub's ``predict`` is never invoked. The
  module contains no training paths (no ML-framework imports, no training
  call shapes, no dataset reads — only its packaged parameter file).

The coupling tokens below are deliberately assembled at runtime so this test
file itself never carries the grep-checked isolation literal.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import subprocess
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

import acp.nmr.dp5q_stub as dp5q_stub
from acp.nmr.error_model import PlaceholderStudentTErrorModel
from acp.nmr.probability import (
    compute_dp4,
    compute_dp5,
    compute_dp5_goodman,
    normalize_dp4,
)
from acp.nmr.shift_predictor import (
    DuplicateShiftPredictorError,
    GeometryRequirements,
    PredictorGeometry,
    ShiftDistribution,
    ShiftModelProvenance,
    ShiftPredictionRequest,
    ShiftPredictor,
    get_shift_predictor,
    list_shift_predictors,
    register_shift_predictor,
    unregister_shift_predictor,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DP5Q_MODULE_PATH = REPO_ROOT / "src" / "acp" / "nmr" / "dp5q_stub.py"

#: Formal-chain class/function tokens, assembled from fragments so this file
#: never carries the grep-checked coupling literal itself.
_FORMAL_CHAIN_TOKENS: tuple[str, ...] = (
    "run_" + "nmr_shielding",
    "Goodman" + "ErrorModel",
    "Goodman" + "DP5Model",
)

#: Default-chain surfaces that must never reference the DP5q stub. The nmr
#: files are named individually because ``src/acp/nmr/__init__.py`` (the
#: package surface) legitimately re-exports the stub.
_DEFAULT_CHAIN_GUARD_DIRS: tuple[str, ...] = ("src/acp/workflows", "src/cccp")
_DEFAULT_CHAIN_GUARD_FILES: tuple[str, ...] = (
    "src/acp/nmr/error_model.py",
    "src/acp/nmr/probability.py",
    "src/acp/nmr/shift_predictor.py",
)
_DP5Q_REFERENCE_TOKENS: tuple[str, ...] = ("dp5q",)

#: ML training frameworks that must never appear in the isolated stub module.
_TRAINING_IMPORT_ROOTS: frozenset[str] = frozenset(
    {
        "torch",
        "torch_geometric",
        "pytorch_lightning",
        "lightning",
        "tensorflow",
        "jax",
        "keras",
        "sklearn",
        "dgl",
        "xgboost",
        "catboost",
    }
)

#: Training call shapes that must never appear in the isolated stub module.
_TRAINING_CALL_TOKENS: tuple[str, ...] = (
    "def train(",
    "def fit(",
    ".fit(",
    ".backward(",
    ".train()",
    "training_step",
    "DataLoader",
    "optimizer.",
)

#: Dataset/checkpoint asset extensions that must never appear in the stub
#: module (its only file read is the packaged parameter JSON).
_DATASET_EXTENSIONS: tuple[str, ...] = (
    ".csv",
    ".tsv",
    ".sdf",
    ".mol",
    ".xyz",
    ".npy",
    ".npz",
    ".pt",
    ".pth",
    ".ckpt",
    ".pkl",
    ".pickle",
    ".h5",
    ".parquet",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _module_source() -> str:
    return DP5Q_MODULE_PATH.read_text(encoding="utf-8")


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


def _simulate_missing(
    monkeypatch: pytest.MonkeyPatch,
    *,
    exc: BaseException | None = None,
) -> None:
    """Force the lazy optional-runtime import to fail (before it is attempted)."""

    def _missing() -> types.ModuleType:
        if exc is not None:
            raise exc
        raise ModuleNotFoundError(
            f"No module named {dp5q_stub.DP5Q_STUB_OPTIONAL_DEPENDENCY!r}",
            name=dp5q_stub.DP5Q_STUB_OPTIONAL_DEPENDENCY,
        )

    monkeypatch.setattr(dp5q_stub, "_import_optional_runtime", _missing)


def _simulate_present(
    monkeypatch: pytest.MonkeyPatch,
    *,
    version: str | None = "9.9.9-test",
) -> types.SimpleNamespace:
    """Return a fake optional-runtime module from the lazy import seam."""
    runtime = types.SimpleNamespace(__version__=version)
    monkeypatch.setattr(dp5q_stub, "_import_optional_runtime", lambda: runtime)
    return runtime


def _write_parameters(
    tmp_path: Path,
    *,
    model_id: str | None = None,
    version: str | None = None,
) -> Path:
    payload: dict[str, object] = {
        "nuclei": {
            "13C": {"C": {"mean_ppm": 42.5, "std_ppm": 0.7}},
            "1H": {"H": {"mean_ppm": 1.2}},
        }
    }
    if model_id is not None:
        payload["model_id"] = model_id
    if version is not None:
        payload["version"] = version
    path = tmp_path / "dp5q_stub_params.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _geometry(
    atom_uids: tuple[str, ...] = ("C1", "C2", "H1"),
    *,
    symbols: tuple[str, ...] | None = None,
    level: str = "xtb-GFN2",
) -> PredictorGeometry:
    elements = symbols if symbols is not None else tuple(uid[0].upper() for uid in atom_uids)
    coordinates = tuple((float(index), 0.0, 0.0) for index in range(len(atom_uids)))
    return PredictorGeometry(
        symbols=elements,
        coordinates=coordinates,
        atom_uids=atom_uids,
        level=level,
    )


def _request(
    *,
    atom_uids: tuple[str, ...] = ("C1", "C2", "H1"),
    symbols: tuple[str, ...] | None = None,
    nucleus: str = "13C",
    n_geometries: int = 1,
) -> ShiftPredictionRequest:
    return ShiftPredictionRequest(
        nucleus=nucleus,
        geometries=tuple(_geometry(atom_uids, symbols=symbols) for _ in range(n_geometries)),
    )


class _StandInGoodmanDP5:
    """Stand-in for the loaded Goodman DP5 model (records its calls)."""

    def __init__(self, value: float = 0.37) -> None:
        self.value = value
        self.seen_residuals: list[list[float]] = []

    def probability(self, residuals: list[float]) -> float:
        self.seen_residuals.append(list(residuals))
        return self.value


@pytest.fixture
def registry_snapshot() -> Iterator[None]:
    """Restore the predictor registry to its pre-test content after the test."""
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
# optional-dependency availability
# ---------------------------------------------------------------------------


def test_optional_dependency_absent_reports_typed_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _simulate_missing(monkeypatch)
    parameters_path = tmp_path / "dp5q_stub_params.json"
    status = dp5q_stub.dp5q_stub_status(parameters_path=parameters_path)

    assert isinstance(status, dp5q_stub.Dp5qStubStatus)
    assert status.status == "unavailable"
    assert status.reason == "optional_dependency_missing"
    assert status.available is False
    assert status.detail, "unavailable must carry an explicit human-readable detail"
    assert dp5q_stub.DP5Q_STUB_OPTIONAL_DEPENDENCY in status.detail
    assert status.dependency == dp5q_stub.DP5Q_STUB_OPTIONAL_DEPENDENCY
    assert status.dependency_version is None
    assert status.parameters_path == str(parameters_path)
    assert status.parameters_present is False
    assert dp5q_stub.dp5q_stub_available(parameters_path=parameters_path) is False

    payload = status.as_dict()
    assert payload["status"] == "unavailable"
    assert payload["reason"] == "optional_dependency_missing"
    json.dumps(payload)


def test_unavailable_reason_vocabulary_is_closed_and_typed() -> None:
    with pytest.raises(ValueError):
        dp5q_stub.Dp5qStubStatus(status="bogus")
    with pytest.raises(ValueError):
        dp5q_stub.Dp5qStubStatus(status="unavailable", reason="whatever", detail="why")
    with pytest.raises(ValueError):
        dp5q_stub.Dp5qStubStatus(status="unavailable", reason="optional_dependency_missing")
    with pytest.raises(ValueError):
        dp5q_stub.Dp5qStubStatus(
            status="available", reason="optional_dependency_missing", detail=None
        )
    assert "optional_dependency_missing" in dp5q_stub.DP5Q_STUB_UNAVAILABLE_REASONS
    assert "optional_dependency_broken" in dp5q_stub.DP5Q_STUB_UNAVAILABLE_REASONS
    assert set(dp5q_stub.DP5Q_STUB_STATUSES) == {"available", "unavailable"}


def test_load_and_register_raise_typed_unavailable_when_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _simulate_missing(monkeypatch)
    parameters_path = tmp_path / "dp5q_stub_params.json"

    with pytest.raises(dp5q_stub.Dp5qStubUnavailableError) as excinfo:
        dp5q_stub.load_dp5q_stub_predictor(parameters_path=parameters_path)
    status = excinfo.value.status
    assert isinstance(status, dp5q_stub.Dp5qStubStatus)
    assert status.reason == "optional_dependency_missing"
    assert dp5q_stub.DP5Q_STUB_OPTIONAL_DEPENDENCY in str(excinfo.value)
    assert isinstance(excinfo.value, dp5q_stub.Dp5qStubError)

    with pytest.raises(dp5q_stub.Dp5qStubUnavailableError):
        dp5q_stub.register_dp5q_stub(parameters_path=parameters_path)
    assert list_shift_predictors() == (), "a failed load must never register"


def test_broken_optional_dependency_is_a_distinct_typed_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _simulate_missing(monkeypatch, exc=ImportError("libdp5q.so: cannot open shared object file"))
    status = dp5q_stub.dp5q_stub_status(parameters_path=tmp_path / "dp5q_stub_params.json")
    assert status.status == "unavailable"
    assert status.reason == "optional_dependency_broken"
    assert "cannot open shared object file" in (status.detail or "")
    assert status.available is False


def test_available_state_records_dependency_version_and_parameters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _simulate_present(monkeypatch, version="9.9.9-test")
    parameters_path = tmp_path / "dp5q_stub_params.json"

    status = dp5q_stub.dp5q_stub_status(parameters_path=parameters_path)
    assert status.status == "available", status
    assert status.reason is None
    assert status.detail is None
    assert status.dependency == dp5q_stub.DP5Q_STUB_OPTIONAL_DEPENDENCY
    assert status.dependency_version == "9.9.9-test"
    assert status.parameters_present is False
    assert dp5q_stub.dp5q_stub_available(parameters_path=parameters_path) is True

    _write_parameters(tmp_path)
    assert dp5q_stub.dp5q_stub_status(parameters_path=parameters_path).parameters_present is True


# ---------------------------------------------------------------------------
# contract: provenance / geometry / predictions (loadable but never trained)
# ---------------------------------------------------------------------------


def test_stub_provenance_is_independent_and_honestly_uncalibrated() -> None:
    provenance = dp5q_stub.DP5Q_STUB_PROVENANCE
    assert provenance.model_id == dp5q_stub.DP5Q_STUB_MODEL_ID == "dp5q-stub"
    assert provenance.version == dp5q_stub.DP5Q_STUB_VERSION
    assert provenance.calibration_status == "uncalibrated"
    assert provenance.calibrator is None
    assert provenance.calibration_reference is None
    assert provenance.notes, "documented stub limitations must be recorded"
    json.dumps(provenance.as_dict())


def test_load_without_packaged_parameters_is_loadable_but_refuses_to_predict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _simulate_present(monkeypatch)
    predictor = dp5q_stub.load_dp5q_stub_predictor(
        parameters_path=tmp_path / "dp5q_stub_params.json"
    )

    assert isinstance(predictor, ShiftPredictor)
    assert isinstance(predictor.model_provenance, ShiftModelProvenance)
    assert predictor.model_provenance == dp5q_stub.DP5Q_STUB_PROVENANCE
    assert isinstance(predictor.geometry_requirements, GeometryRequirements)

    with pytest.raises(dp5q_stub.Dp5qStubNotParameterizedError):
        predictor.predict(_request())


def test_predictions_follow_the_explicit_atom_mapping(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _simulate_present(monkeypatch)
    parameters_path = _write_parameters(tmp_path)
    predictor = dp5q_stub.load_dp5q_stub_predictor(parameters_path=parameters_path)

    prediction = predictor.predict(_request(atom_uids=("C1", "C2", "H1")))

    assert prediction.nucleus == "13C"
    assert set(prediction.shifts) == {"C1", "C2"}, (
        "only target-nucleus atoms are predicted; the H atom is not a 13C target"
    )
    assert prediction.model == predictor.model_provenance
    assert prediction.geometry_requirements == predictor.geometry_requirements

    carbon = prediction.shifts["C1"]
    assert carbon.shift_ppm == pytest.approx(42.5)
    distribution = carbon.distribution
    assert isinstance(distribution, ShiftDistribution)
    assert distribution.mean_ppm == pytest.approx(42.5)
    assert distribution.std_ppm == pytest.approx(0.7)

    proton_prediction = predictor.predict(_request(atom_uids=("H1",), nucleus="1H"))
    hydrogen = proton_prediction.shifts["H1"]
    assert hydrogen.shift_ppm == pytest.approx(1.2)
    assert hydrogen.distribution is None, "no std in the parameters → None, never fabricated"


def test_predict_rejects_unknown_nucleus_or_element_with_typed_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _simulate_present(monkeypatch)
    parameters_path = _write_parameters(tmp_path)
    predictor = dp5q_stub.load_dp5q_stub_predictor(parameters_path=parameters_path)

    with pytest.raises(dp5q_stub.Dp5qStubParameterError, match="nucleus"):
        predictor.predict(_request(nucleus="14N"))
    with pytest.raises(dp5q_stub.Dp5qStubParameterError, match="element"):
        predictor.predict(
            _request(atom_uids=("Cl1",), symbols=("Cl",)),
        )


def test_parameter_payload_validation_is_typed() -> None:
    from_payload = dp5q_stub.Dp5qStubParameters.from_payload
    nuclei = {"nuclei": {"13C": {"C": {"mean_ppm": 42.5}}}}

    with pytest.raises(dp5q_stub.Dp5qStubParameterError):
        from_payload(["not", "a", "mapping"])
    with pytest.raises(dp5q_stub.Dp5qStubParameterError):
        from_payload({"nuclei": {}})
    with pytest.raises(dp5q_stub.Dp5qStubParameterError):
        from_payload({"nuclei": {"13C": {}}})
    with pytest.raises(dp5q_stub.Dp5qStubParameterError):
        from_payload({"nuclei": {"13C": {"C": {"mean_ppm": "42"}}}})
    with pytest.raises(dp5q_stub.Dp5qStubParameterError):
        from_payload({"nuclei": {"13C": {"C": {"mean_ppm": float("nan")}}}})
    with pytest.raises(dp5q_stub.Dp5qStubParameterError):
        from_payload({"nuclei": {"13C": {"C": {"mean_ppm": 42.5, "std_ppm": -1.0}}}})
    with pytest.raises(dp5q_stub.Dp5qStubParameterError):
        from_payload({"nuclei": {"13C": {"C": {"mean_ppm": 42.5, "surprise": 1}}}})
    with pytest.raises(dp5q_stub.Dp5qStubParameterError):
        from_payload({"model_id": "not-the-stub", **nuclei})
    with pytest.raises(dp5q_stub.Dp5qStubParameterError):
        from_payload({"version": "999.0", **nuclei})

    parameters = from_payload(
        {"model_id": "dp5q-stub", "version": dp5q_stub.DP5Q_STUB_VERSION, **nuclei}
    )
    assert parameters.nuclei["13C"]["C"].mean_ppm == pytest.approx(42.5)
    assert parameters.nuclei["13C"]["C"].std_ppm is None
    json.dumps(parameters.as_dict())


def test_parameter_load_rejects_missing_or_malformed_json(tmp_path: Path) -> None:
    with pytest.raises(dp5q_stub.Dp5qStubParameterError):
        dp5q_stub.Dp5qStubParameters.load(tmp_path / "missing.json")

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not json", encoding="utf-8")
    with pytest.raises(dp5q_stub.Dp5qStubParameterError):
        dp5q_stub.Dp5qStubParameters.load(malformed)


# ---------------------------------------------------------------------------
# registry: explicit registration only
# ---------------------------------------------------------------------------


def test_register_stub_only_when_explicitly_asked(
    registry_snapshot: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _simulate_present(monkeypatch)
    parameters_path = _write_parameters(tmp_path)

    assert dp5q_stub.DP5Q_STUB_MODEL_ID not in list_shift_predictors()
    dp5q_stub.load_dp5q_stub_predictor(parameters_path=parameters_path)
    assert dp5q_stub.DP5Q_STUB_MODEL_ID not in list_shift_predictors(), (
        "loading the stub must not register it"
    )

    returned = dp5q_stub.register_dp5q_stub(parameters_path=parameters_path)
    assert returned == "dp5q-stub"
    assert dp5q_stub.DP5Q_STUB_MODEL_ID in list_shift_predictors()

    registered = get_shift_predictor(dp5q_stub.DP5Q_STUB_MODEL_ID)
    assert isinstance(registered, ShiftPredictor)
    prediction = registered.predict(_request())
    assert prediction.shifts["C1"].shift_ppm == pytest.approx(42.5)


def test_register_duplicate_stub_is_rejected_unless_replace(
    registry_snapshot: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _simulate_present(monkeypatch)
    parameters_path = _write_parameters(tmp_path)
    dp5q_stub.register_dp5q_stub(parameters_path=parameters_path)

    with pytest.raises(DuplicateShiftPredictorError):
        dp5q_stub.register_dp5q_stub(parameters_path=parameters_path)

    first = get_shift_predictor(dp5q_stub.DP5Q_STUB_MODEL_ID)
    dp5q_stub.register_dp5q_stub(parameters_path=parameters_path, replace=True)
    assert get_shift_predictor(dp5q_stub.DP5Q_STUB_MODEL_ID) is not first


def test_fresh_interpreter_registry_stays_empty_after_importing_the_stub() -> None:
    src_root = REPO_ROOT / "src"
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_root}{os.pathsep}{existing}" if existing else str(src_root)
    code = (
        "import acp.nmr.dp5q_stub as dp5q\n"
        "from acp.nmr.shift_predictor import list_shift_predictors\n"
        "assert list_shift_predictors() == (), 'importing the stub must not register'\n"
        "import acp.nmr.probability  # formal DP4/DP5 chain\n"
        "assert list_shift_predictors() == (), 'eager dp5q registration from the chain'\n"
        "import acp.workflows.nmr  # default NMR workflow\n"
        "assert list_shift_predictors() == (), 'default workflow auto-registered dp5q'\n"
        "status = dp5q.dp5q_stub_status()\n"
        "assert status.status in dp5q.DP5Q_STUB_STATUSES, status\n"
        "assert dp5q.dp5q_stub_available() == status.available\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr


# ---------------------------------------------------------------------------
# no replacement: default DP4/DP5 behavior unchanged
# ---------------------------------------------------------------------------


def test_default_dp5_path_never_consults_a_registered_dp5q_stub(
    registry_snapshot: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _simulate_present(monkeypatch)
    parameters_path = _write_parameters(tmp_path)
    predictor = dp5q_stub.load_dp5q_stub_predictor(parameters_path=parameters_path)
    invoked: list[ShiftPredictionRequest] = []

    def _spy_predict(request: ShiftPredictionRequest):
        invoked.append(request)
        raise AssertionError("the default DP5 path must never call a dp5q predictor")

    monkeypatch.setattr(predictor, "predict", _spy_predict)

    placeholder = PlaceholderStudentTErrorModel()
    stand_in = _StandInGoodmanDP5(value=0.37)
    residuals = {"13C": [0.4, 1.2, 2.0], "1H": [0.05, 0.21]}

    baseline_dp4 = compute_dp4(residuals, placeholder)
    baseline_probs = normalize_dp4([baseline_dp4, baseline_dp4 - 1.5])
    baseline_dp5 = compute_dp5(residuals, placeholder)
    baseline_goodman = compute_dp5_goodman(residuals, stand_in)
    assert baseline_goodman == pytest.approx(0.37)

    register_shift_predictor(predictor)
    assert dp5q_stub.DP5Q_STUB_MODEL_ID in list_shift_predictors()

    assert compute_dp4(residuals, placeholder) == baseline_dp4
    assert normalize_dp4([baseline_dp4, baseline_dp4 - 1.5]) == baseline_probs
    assert compute_dp5(residuals, placeholder) == baseline_dp5
    assert compute_dp5_goodman(residuals, stand_in) == baseline_goodman
    assert invoked == [], "the registered dp5q stub was consulted by the DP5 path"


def test_default_chain_sources_never_reference_the_dp5q_stub() -> None:
    guarded_paths: list[Path] = []
    for directory in _DEFAULT_CHAIN_GUARD_DIRS:
        guarded_paths.extend(sorted((REPO_ROOT / directory).rglob("*.py")))
    guarded_paths.extend(REPO_ROOT / name for name in _DEFAULT_CHAIN_GUARD_FILES)

    offenders: list[str] = []
    for path in guarded_paths:
        text = path.read_text(encoding="utf-8")
        for token in _DP5Q_REFERENCE_TOKENS:
            if token in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {token}")
    assert offenders == [], (
        f"the default NMR/DP4/DP5 chain must never reference the isolated dp5q stub: {offenders}"
    )


# ---------------------------------------------------------------------------
# isolation: dependency-free module, no training paths
# ---------------------------------------------------------------------------


def test_module_never_references_run_nmr_shielding_or_goodman_models() -> None:
    source = _module_source()
    for token in _FORMAL_CHAIN_TOKENS:
        assert token not in source, f"dp5q_stub.py must not reference {token!r}"


def test_module_imports_stay_stdlib_plus_the_predictor_protocol() -> None:
    tree = ast.parse(_module_source(), filename=str(DP5Q_MODULE_PATH))
    imported = _imported_module_names(tree)

    acp_imports = [name for name in imported if name == "acp" or name.startswith("acp.")]
    assert acp_imports == ["acp.nmr.shift_predictor"], (
        f"dp5q_stub.py may import only the todo-55 protocol, got {acp_imports}"
    )
    assert not [name for name in imported if name == "cccp" or name.startswith("cccp.")]
    assert not [name for name in imported if "error_model" in name]
    assert not [name for name in imported if "workflows" in name]
    assert not [name for name in imported if "probability" in name]


def test_module_imports_no_ml_training_frameworks() -> None:
    tree = ast.parse(_module_source(), filename=str(DP5Q_MODULE_PATH))
    roots = {name.split(".")[0] for name in _imported_module_names(tree)}
    offenders = sorted(roots & _TRAINING_IMPORT_ROOTS)
    assert offenders == [], f"dp5q_stub.py must not import training frameworks: {offenders}"


def test_module_has_no_training_call_shapes() -> None:
    source = _module_source()
    offenders = [token for token in _TRAINING_CALL_TOKENS if token in source]
    assert offenders == [], f"dp5q_stub.py must not contain training paths: {offenders}"


def test_module_reads_only_packaged_parameters_no_datasets() -> None:
    source = _module_source()
    offenders = [extension for extension in _DATASET_EXTENSIONS if extension in source]
    assert offenders == [], f"dp5q_stub.py must not reference dataset assets: {offenders}"

    tree = ast.parse(source, filename=str(DP5Q_MODULE_PATH))
    read_calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name in {"open", "read_text", "read_bytes", "load"}:
            read_calls.append(ast.get_source_segment(source, node) or "")
    assert read_calls, "expected the packaged-parameter loader to read one file"
    outsiders = [segment for segment in read_calls if "parameters" not in segment.lower()]
    assert outsiders == [], f"only the packaged parameter file may be read: {outsiders}"


def test_module_all_is_complete_and_exports_the_stub_surface() -> None:
    module = importlib.import_module("acp.nmr.dp5q_stub")
    declared = tuple(module.__all__)
    assert len(declared) == len(set(declared))
    missing = [name for name in declared if not hasattr(module, name)]
    assert missing == [], f"__all__ names missing from module: {missing}"
    required = {
        "DP5Q_STUB_MODEL_ID",
        "DP5Q_STUB_OPTIONAL_DEPENDENCY",
        "DP5Q_STUB_PARAMETERS_PATH",
        "DP5Q_STUB_PROVENANCE",
        "DP5Q_STUB_STATUSES",
        "DP5Q_STUB_UNAVAILABLE_REASONS",
        "DP5Q_STUB_VERSION",
        "Dp5qShiftEntry",
        "Dp5qStubError",
        "Dp5qStubNotParameterizedError",
        "Dp5qStubParameterError",
        "Dp5qStubParameters",
        "Dp5qStubPredictor",
        "Dp5qStubStatus",
        "Dp5qStubUnavailableError",
        "dp5q_stub_available",
        "dp5q_stub_status",
        "load_dp5q_stub_predictor",
        "register_dp5q_stub",
    }
    assert required <= set(declared)


def test_package_level_exports_are_identical_objects() -> None:
    package = importlib.import_module("acp.nmr")
    module = importlib.import_module("acp.nmr.dp5q_stub")
    for name in module.__all__:
        exported = getattr(package, name)
        assert exported is getattr(module, name), f"acp.nmr.{name} is not the module identity"
        assert name in package.__all__, f"acp.nmr.__all__ is missing {name!r}"


def test_reloading_the_module_without_the_dependency_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Module load never imports the optional runtime (no import error, ever)."""

    def _missing() -> types.ModuleType:
        raise ModuleNotFoundError("No module named 'dp5q'", name="dp5q")

    original_state = dict(dp5q_stub.__dict__)
    monkeypatch.setattr(dp5q_stub, "_import_optional_runtime", _missing)
    reloaded = importlib.reload(dp5q_stub)
    try:
        status = reloaded.dp5q_stub_status()
        assert status.status == "unavailable"
        assert status.reason == "optional_dependency_missing"
        assert list_shift_predictors() == ()
    finally:
        # Reloading rebinds every module-global (including the exception
        # classes) to fresh objects; other modules that imported those names
        # before the reload keep the originals and would stop catching the
        # raised types. Restore the pre-reload namespace exactly instead of
        # reloading again (which would leave yet another class generation).
        dp5q_stub.__dict__.clear()
        dp5q_stub.__dict__.update(original_state)
