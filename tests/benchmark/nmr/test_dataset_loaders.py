"""Dataset loaders 2-5 acceptance (todo 53 / gap §10.3 layers 2-5).

TDD acceptance for ``tests/benchmark/nmr/loaders.py``:

* per-layer happy paths against the committed synthetic self-test fixtures
  (assigned-statistical / stereochemistry / raw-spectra / boundary);
* typed rejection of missing provenance — the todo-50 classes
  (``MissingProvenanceError`` / ``DatasetHashMismatchError`` /
  ``ManifestReferenceError``) are reused unchanged; new layer violations raise
  ``UnsupportedLayerError`` / ``LayerSchemaError`` (both
  ``BenchmarkManifestError`` subclasses);
* explicit nulls and closed vocabularies: processing records and boundary
  deferrals never default silently;
* pure/deterministic loaders with NO network access (pinned by a socket-level
  guard and a source scan);
* the runbook documents every layer's required fields plus the
  source/license/hash rules and states that the external dataset campaign is a
  follow-up plan, NOT executed here.

No accuracy or calibration claim is made anywhere in this suite; every fixture
is synthetic and authored in-repo (CC0).
"""

from __future__ import annotations

import dataclasses
import json
import os
import socket
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.benchmark.nmr import canonical_json_bytes
from tests.benchmark.nmr.loaders import (
    BENCHMARK_LAYERS_2_5,
    BOUNDARY_KINDS,
    LAYER_FIELD_SPECS,
    LAYER_PROFILE,
    DatasetLoaderError,
    LayerDataset,
    LayerSchemaError,
    UnsupportedLayerError,
    load_assigned_statistical,
    load_boundary,
    load_layer_datasets,
    load_raw_spectra,
    load_stereochemistry,
)
from tests.benchmark.nmr.schema import (
    BenchmarkManifestError,
    DatasetHashMismatchError,
    ManifestReferenceError,
    MissingProvenanceError,
    canonical_dataset_hash,
)

HARNESS_DIR = Path(__file__).resolve().parent
FIXTURES = HARNESS_DIR / "fixtures"
RUNBOOK = HARNESS_DIR / "RUNBOOK.md"
REPO_ROOT = Path(__file__).resolve().parents[3]
SYNTHETIC_DATASET = FIXTURES / "synthetic_dataset.json"

LAYER_FIXTURES = {
    "assigned_statistical": FIXTURES / "layer2_assigned_statistical.json",
    "stereochemistry": FIXTURES / "layer3_stereochemistry.json",
    "raw_spectra": FIXTURES / "layer4_raw_spectra.json",
    "boundary": FIXTURES / "layer5_boundary.json",
}

LAYER_LOADERS = {
    "assigned_statistical": load_assigned_statistical,
    "stereochemistry": load_stereochemistry,
    "raw_spectra": load_raw_spectra,
    "boundary": load_boundary,
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _dataset(payload: dict) -> dict:
    return payload["datasets"][0]


def _item(payload: dict, item_index: int = 0) -> dict:
    return _dataset(payload)["items"][item_index]


def _pop(payload: dict, path: tuple, key: str) -> None:
    target = payload
    for part in path:
        target = target[part]
    target.pop(key)


def _set(payload: dict, path: tuple, key: str, value: object) -> None:
    target = payload
    for part in path:
        target = target[part]
    target[key] = value


def _mutated_manifest(
    tmp_path: Path,
    fixture: Path,
    mutation: Callable[[dict], None],
    *,
    resign: bool = True,
) -> Path:
    """Copy a fixture manifest, mutate it, optionally re-sign each dataset hash."""
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    mutation(payload)
    if resign:
        for dataset in payload["datasets"]:
            dataset["hash"] = canonical_dataset_hash(dataset["items"])
    target = tmp_path / fixture.name
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def _receipt(loaded: tuple[LayerDataset, ...]) -> bytes:
    return canonical_json_bytes(
        [
            {
                "dataset_id": dataset.dataset_id,
                "layer": dataset.layer,
                "content_hash": dataset.content_hash,
                "items": dataset.items,
            }
            for dataset in loaded
        ]
    )


# ---------------------------------------------------------------------------
# schema constants / profiles
# ---------------------------------------------------------------------------


def test_layer_constants_and_profiles() -> None:
    assert BENCHMARK_LAYERS_2_5 == (
        "assigned_statistical",
        "stereochemistry",
        "raw_spectra",
        "boundary",
    )
    assert LAYER_PROFILE == "acp-nmr-benchmark-layer-profile-v1"
    assert set(LAYER_FIELD_SPECS) == set(BENCHMARK_LAYERS_2_5)
    assert BOUNDARY_KINDS == (
        "charged",
        "exchangeable_hydrogen",
        "strong_overlap",
        "few_carbons",
        "halogen_or_domain_edge",
        "large_molecule",
        "symmetry_change",
        "truncated_ensemble",
    )
    for layer, spec in LAYER_FIELD_SPECS.items():
        assert spec.layer == layer
        assert spec.all_fields, f"layer {layer!r} declares no required fields"


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------


def test_load_assigned_statistical_happy() -> None:
    loaded = load_assigned_statistical(LAYER_FIXTURES["assigned_statistical"])
    assert len(loaded) == 1
    dataset = loaded[0]
    assert isinstance(dataset, LayerDataset)
    assert dataset.layer == "assigned_statistical"
    assert dataset.layer_profile == LAYER_PROFILE
    assert len(dataset.content_hash) == 64
    assert dataset.item_ids == tuple(item["item_id"] for item in dataset.items)
    assert dataset.molecule_ids == tuple(item["molecule_id"] for item in dataset.items)
    assert dataset.raw["split"]["train_disjoint"] is True
    assert dataset.raw["split"]["split_by"] != ""
    for item in dataset.items:
        assert item["reference"]["reference_id"] != ""
        for signals in item["experimental"].values():
            for signal in signals:
                assert signal["reference_label"] != ""


def test_load_stereochemistry_happy() -> None:
    loaded = load_stereochemistry(LAYER_FIXTURES["stereochemistry"])
    dataset = loaded[0]
    assert dataset.layer == "stereochemistry"
    assert dataset.raw["external_reference"]["reported_accuracy"] is None
    assert dataset.raw["external_reference"]["reported_accuracy_note"] != ""
    for item in dataset.items:
        if item["true_structure_present"]:
            assert item["true_stereoisomer_label"]
            true_id = item["true_structure_candidate_id"]
            candidate = next(c for c in item["candidates"] if c["candidate_id"] == true_id)
            assert candidate["stereoisomer_label"] == item["true_stereoisomer_label"]
        else:
            assert item["true_stereoisomer_label"] is None
        for candidate in item["candidates"]:
            assert candidate["stereoisomer_label"] != ""
            assert candidate["source"] != ""


def test_load_raw_spectra_happy() -> None:
    loaded = load_raw_spectra(LAYER_FIXTURES["raw_spectra"])
    dataset = loaded[0]
    assert dataset.layer == "raw_spectra"
    for item in dataset.items:
        record_ids = {record["fixture_id"] for record in item["raw_spectra"]}
        assert item["spectra_fixture_id"] in record_ids
        statuses = set()
        for record in item["raw_spectra"]:
            assert len(record["fixture_sha256"]) == 64
            assert record["nucleus"] in ("1H", "13C")
            assert record["annotations_ref"] != ""
            processing = record["processing"]
            statuses.add(processing["status"])
            if processing["status"] == "measured":
                assert processing["metrics_ref"]
            else:
                assert processing["metrics_ref"] is None
                assert processing["note"].startswith("NOT_VERIFIED")
        assert statuses == {"measured", "not_verified"}


def test_load_boundary_happy() -> None:
    loaded = load_boundary(LAYER_FIXTURES["boundary"])
    dataset = loaded[0]
    assert dataset.layer == "boundary"
    statuses = set()
    for item in dataset.items:
        boundary = item["boundary"]
        assert boundary["kind"] in BOUNDARY_KINDS
        assert boundary["reason"] != ""
        statuses.add(boundary["status"])
        if boundary["status"] == "deferred":
            assert boundary["deferred_to"]
        else:
            assert boundary["deferred_to"] is None
    assert statuses == {"deferred", "covered"}


def test_load_layer_datasets_returns_file_order() -> None:
    loaded = load_layer_datasets(LAYER_FIXTURES["stereochemistry"])
    assert [dataset.layer for dataset in loaded] == ["stereochemistry"]
    # Requesting several layers is allowed as long as every dataset matches.
    loaded = load_layer_datasets(
        LAYER_FIXTURES["boundary"], layers=("boundary", "assigned_statistical")
    )
    assert [dataset.layer for dataset in loaded] == ["boundary"]


def test_layer_dataset_is_frozen() -> None:
    dataset = load_boundary(LAYER_FIXTURES["boundary"])[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(dataset, "layer", "synthetic")


# ---------------------------------------------------------------------------
# spectra fixture link resolution (todo 48 manifest)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def spectra_fixture_index() -> dict[str, str]:
    from tests.nmr_spectra_benchmark import run_benchmark

    payload = run_benchmark(nmrglue=False)
    return {str(entry["id"]): str(entry["sha256"]) for entry in payload["fixtures"]}


def test_raw_spectra_fixture_links_resolve(spectra_fixture_index: dict[str, str]) -> None:
    loaded = load_raw_spectra(
        LAYER_FIXTURES["raw_spectra"], spectra_fixture_ids=set(spectra_fixture_index)
    )
    for item in loaded[0].items:
        assert item["spectra_fixture_id"] in spectra_fixture_index
        for record in item["raw_spectra"]:
            # The declared pin must be the digest of the committed todo-48 fixture.
            assert record["fixture_id"] in spectra_fixture_index
            assert record["fixture_sha256"] == spectra_fixture_index[record["fixture_id"]]


def test_raw_spectra_unknown_fixture_rejected() -> None:
    with pytest.raises(ManifestReferenceError):
        load_raw_spectra(LAYER_FIXTURES["raw_spectra"], spectra_fixture_ids={"not_a_fixture"})


# ---------------------------------------------------------------------------
# typed rejections: unsupported layers
# ---------------------------------------------------------------------------


def test_synthetic_manifest_not_loadable() -> None:
    with pytest.raises(UnsupportedLayerError, match="synthetic"):
        load_layer_datasets(SYNTHETIC_DATASET)
    with pytest.raises(UnsupportedLayerError):
        load_assigned_statistical(SYNTHETIC_DATASET)


def test_wrong_layer_for_loader_rejected() -> None:
    with pytest.raises(UnsupportedLayerError):
        load_stereochemistry(LAYER_FIXTURES["assigned_statistical"])


def test_unknown_requested_layer_rejected() -> None:
    with pytest.raises(UnsupportedLayerError):
        load_layer_datasets(LAYER_FIXTURES["boundary"], layers=("synthetic",))


def test_empty_layer_selection_rejected() -> None:
    with pytest.raises(DatasetLoaderError):
        load_layer_datasets(LAYER_FIXTURES["boundary"], layers=())


def test_loader_errors_are_benchmark_manifest_errors() -> None:
    assert issubclass(DatasetLoaderError, BenchmarkManifestError)
    assert issubclass(UnsupportedLayerError, DatasetLoaderError)
    assert issubclass(LayerSchemaError, DatasetLoaderError)
    with pytest.raises(BenchmarkManifestError):
        load_layer_datasets(SYNTHETIC_DATASET)


# ---------------------------------------------------------------------------
# typed rejections: provenance (todo-50 classes reused unchanged)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["source", "license", "version"])
def test_missing_provenance_field_rejected(tmp_path: Path, field: str) -> None:
    path = _mutated_manifest(
        tmp_path, LAYER_FIXTURES["boundary"], lambda payload: _dataset(payload).pop(field)
    )
    with pytest.raises(MissingProvenanceError, match=field):
        load_boundary(path)


def test_missing_hash_rejected(tmp_path: Path) -> None:
    path = _mutated_manifest(
        tmp_path,
        LAYER_FIXTURES["boundary"],
        lambda payload: _dataset(payload).pop("hash"),
        resign=False,
    )
    with pytest.raises(MissingProvenanceError, match="hash"):
        load_boundary(path)


def test_tampered_items_rejected_by_hash(tmp_path: Path) -> None:
    def mutation(payload: dict) -> None:
        _item(payload)["experimental"]["13C"][0]["observed_ppm"] += 1.0

    path = _mutated_manifest(
        tmp_path, LAYER_FIXTURES["assigned_statistical"], mutation, resign=False
    )
    with pytest.raises(DatasetHashMismatchError):
        load_assigned_statistical(path)


# ---------------------------------------------------------------------------
# typed rejections: layer-specific schema
# ---------------------------------------------------------------------------

LAYER_FIELD_CASES = [
    pytest.param(
        load_assigned_statistical,
        LAYER_FIXTURES["assigned_statistical"],
        lambda payload: _pop(payload, ("datasets", 0, "items", 0), "reference"),
        "reference",
        id="layer2-reference-record-missing",
    ),
    pytest.param(
        load_assigned_statistical,
        LAYER_FIXTURES["assigned_statistical"],
        lambda payload: _pop(
            payload, ("datasets", 0, "items", 0, "experimental", "13C", 0), "reference_label"
        ),
        "reference_label",
        id="layer2-signal-reference-label-missing",
    ),
    pytest.param(
        load_assigned_statistical,
        LAYER_FIXTURES["assigned_statistical"],
        lambda payload: _pop(payload, ("datasets", 0, "split"), "train_disjoint"),
        "train_disjoint",
        id="layer2-split-train-disjoint-missing",
    ),
    pytest.param(
        load_stereochemistry,
        LAYER_FIXTURES["stereochemistry"],
        lambda payload: _pop(payload, ("datasets", 0, "items", 0), "true_stereoisomer_label"),
        "true_stereoisomer_label",
        id="layer3-true-stereoisomer-label-missing",
    ),
    pytest.param(
        load_stereochemistry,
        LAYER_FIXTURES["stereochemistry"],
        lambda payload: _pop(
            payload, ("datasets", 0, "items", 0, "candidates", 0), "stereoisomer_label"
        ),
        "stereoisomer_label",
        id="layer3-candidate-label-missing",
    ),
    pytest.param(
        load_stereochemistry,
        LAYER_FIXTURES["stereochemistry"],
        lambda payload: _pop(payload, ("datasets", 0, "items", 0, "candidates", 0), "source"),
        "source",
        id="layer3-candidate-source-missing",
    ),
    pytest.param(
        load_stereochemistry,
        LAYER_FIXTURES["stereochemistry"],
        lambda payload: _set(
            payload, ("datasets", 0, "items", 0, "candidates", 0), "stereoisomer_label", "Z"
        ),
        "true_stereoisomer_label",
        id="layer3-true-label-mismatch",
    ),
    pytest.param(
        load_stereochemistry,
        LAYER_FIXTURES["stereochemistry"],
        lambda payload: _set(payload, ("datasets", 0, "items", 0), "true_stereoisomer_label", None),
        "true_stereoisomer_label",
        id="layer3-true-label-null-while-present",
    ),
    pytest.param(
        load_stereochemistry,
        LAYER_FIXTURES["stereochemistry"],
        lambda payload: _set(payload, ("datasets", 0, "items", 1), "true_stereoisomer_label", "S"),
        "true_stereoisomer_label",
        id="layer3-absent-item-carries-true-label",
    ),
    pytest.param(
        load_stereochemistry,
        LAYER_FIXTURES["stereochemistry"],
        lambda payload: _set(
            payload, ("datasets", 0, "external_reference"), "reported_accuracy", 1.5
        ),
        "reported_accuracy",
        id="layer3-reported-accuracy-out-of-range",
    ),
    pytest.param(
        load_raw_spectra,
        LAYER_FIXTURES["raw_spectra"],
        lambda payload: _pop(payload, ("datasets", 0, "items", 0), "raw_spectra"),
        "raw_spectra",
        id="layer4-raw-spectra-missing",
    ),
    pytest.param(
        load_raw_spectra,
        LAYER_FIXTURES["raw_spectra"],
        lambda payload: _set(
            payload, ("datasets", 0, "items", 0), "spectra_fixture_id", "solvent_large_carbon"
        ),
        "spectra_fixture_id",
        id="layer4-primary-fixture-not-in-records",
    ),
    pytest.param(
        load_raw_spectra,
        LAYER_FIXTURES["raw_spectra"],
        lambda payload: _set(
            payload,
            ("datasets", 0, "items", 0, "raw_spectra", 0),
            "fixture_sha256",
            "zz",
        ),
        "fixture_sha256",
        id="layer4-fixture-sha-invalid",
    ),
    pytest.param(
        load_raw_spectra,
        LAYER_FIXTURES["raw_spectra"],
        lambda payload: _set(
            payload,
            ("datasets", 0, "items", 0, "raw_spectra", 0, "processing"),
            "status",
            "unknown",
        ),
        "status",
        id="layer4-processing-status-unknown",
    ),
    pytest.param(
        load_raw_spectra,
        LAYER_FIXTURES["raw_spectra"],
        lambda payload: _set(
            payload,
            ("datasets", 0, "items", 0, "raw_spectra", 0, "processing"),
            "metrics_ref",
            None,
        ),
        "metrics_ref",
        id="layer4-measured-without-metrics-ref",
    ),
    pytest.param(
        load_raw_spectra,
        LAYER_FIXTURES["raw_spectra"],
        lambda payload: _set(
            payload,
            ("datasets", 0, "items", 0, "raw_spectra", 1, "processing"),
            "note",
            "unverified processing",
        ),
        "NOT_VERIFIED",
        id="layer4-not-verified-note-without-marker",
    ),
    pytest.param(
        load_boundary,
        LAYER_FIXTURES["boundary"],
        lambda payload: _pop(payload, ("datasets", 0, "items", 0), "boundary"),
        "boundary",
        id="layer5-boundary-record-missing",
    ),
    pytest.param(
        load_boundary,
        LAYER_FIXTURES["boundary"],
        lambda payload: _set(
            payload, ("datasets", 0, "items", 0, "boundary"), "kind", "unknown_kind"
        ),
        "kind",
        id="layer5-boundary-kind-unknown",
    ),
    pytest.param(
        load_boundary,
        LAYER_FIXTURES["boundary"],
        lambda payload: _set(payload, ("datasets", 0, "items", 0, "boundary"), "status", "parked"),
        "status",
        id="layer5-boundary-status-unknown",
    ),
    pytest.param(
        load_boundary,
        LAYER_FIXTURES["boundary"],
        lambda payload: _set(payload, ("datasets", 0, "items", 0, "boundary"), "deferred_to", None),
        "deferred_to",
        id="layer5-deferred-without-target",
    ),
    pytest.param(
        load_boundary,
        LAYER_FIXTURES["boundary"],
        lambda payload: _set(
            payload, ("datasets", 0, "items", 1, "boundary"), "deferred_to", "elsewhere"
        ),
        "deferred_to",
        id="layer5-covered-with-target",
    ),
]


@pytest.mark.parametrize("loader,fixture,mutation,match", LAYER_FIELD_CASES)
def test_layer_field_rejections(
    tmp_path: Path,
    loader: Callable[..., tuple[LayerDataset, ...]],
    fixture: Path,
    mutation: Callable[[dict], None],
    match: str,
) -> None:
    path = _mutated_manifest(tmp_path, fixture, mutation)
    with pytest.raises(LayerSchemaError, match=match):
        loader(path)


# ---------------------------------------------------------------------------
# determinism and no-network discipline
# ---------------------------------------------------------------------------


def test_loaders_are_deterministic_in_process() -> None:
    for fixture in LAYER_FIXTURES.values():
        first = _receipt(load_layer_datasets(fixture))
        second = _receipt(load_layer_datasets(fixture))
        assert first == second


def test_loaders_are_deterministic_cross_process() -> None:
    script = "\n".join(
        [
            "import json",
            "from pathlib import Path",
            "from tests.benchmark.nmr.loaders import load_layer_datasets",
            "fixtures = Path('tests/benchmark/nmr/fixtures')",
            "loaded = []",
            "for name in (",
            "    'layer2_assigned_statistical.json',",
            "    'layer3_stereochemistry.json',",
            "    'layer4_raw_spectra.json',",
            "    'layer5_boundary.json',",
            "):",
            "    loaded.extend(load_layer_datasets(fixtures / name))",
            "payload = [",
            "    {'dataset_id': ds.dataset_id, 'layer': ds.layer,",
            "     'content_hash': ds.content_hash, 'items': ds.items}",
            "    for ds in loaded",
            "]",
            "print(json.dumps(payload, sort_keys=True, separators=(',', ':')))",
        ]
    )
    outputs = []
    for seed in ("1", "98765"):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env={**os.environ, "PYTHONPATH": "src", "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1]
    assert "assigned-statistical-selftest-v1" in outputs[0]
    assert "boundary-selftest-v1" in outputs[0]


def test_loaders_never_touch_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("layer loader attempted a network connection")

    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", _forbidden)
    for layer, loader in LAYER_LOADERS.items():
        loaded = loader(LAYER_FIXTURES[layer])
        assert loaded


def test_loader_module_has_no_network_imports() -> None:
    source = (HARNESS_DIR / "loaders.py").read_text(encoding="utf-8")
    forbidden = (
        "import requests",
        "import urllib",
        "from urllib",
        "import httpx",
        "import aiohttp",
        "import socket",
        "import http.client",
        "from http import",
        "import ftplib",
        "import subprocess",
    )
    for token in forbidden:
        assert token not in source, f"loaders.py must not contain {token!r}"


# ---------------------------------------------------------------------------
# package re-exports and runbook coverage
# ---------------------------------------------------------------------------


def test_package_root_reexports_loader_api() -> None:
    import tests.benchmark.nmr as package

    for name in (
        "BENCHMARK_LAYERS_2_5",
        "BOUNDARY_KINDS",
        "LAYER_FIELD_SPECS",
        "LAYER_PROFILE",
        "DatasetLoaderError",
        "LayerDataset",
        "LayerFieldSpec",
        "LayerSchemaError",
        "UnsupportedLayerError",
        "load_assigned_statistical",
        "load_boundary",
        "load_layer_datasets",
        "load_raw_spectra",
        "load_stereochemistry",
    ):
        assert hasattr(package, name), f"tests.benchmark.nmr does not re-export {name!r}"


def test_runbook_documents_layer_fields_and_deferral() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    lowered = text.lower()
    for layer in BENCHMARK_LAYERS_2_5:
        assert layer in text, f"RUNBOOK.md does not mention layer {layer!r}"
        for field in LAYER_FIELD_SPECS[layer].all_fields:
            assert field in text, f"RUNBOOK.md missing field {field!r} for layer {layer!r}"
    for shared in ("source", "license", "hash", "sha256", "version", "canonical_dataset_hash"):
        assert shared in lowered, f"RUNBOOK.md missing shared requirement {shared!r}"
    assert "follow-up plan" in lowered, "RUNBOOK.md must defer the campaign to a follow-up plan"
    assert "not executed in this plan" in lowered, (
        "RUNBOOK.md must state that the external dataset campaign is not executed here"
    )
    assert "NOT_VERIFIED" in text, "RUNBOOK.md must document the three-state raw-spectra rule"
