# ruff: noqa: E501
"""OrcaGradient workflow engine tests (work unit X4′-A).

FAKE ORCA binary + patched ``subprocess.run`` — no real ORCA is executed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

from acp.core.workflow import WorkflowResult
from acp.storage.manifest import ResultManifest
from acp.workflows.orca_gradient import (
    GRADIENT_PRODUCT_SCHEMA,
    ORCA_GRADIENT_E_GRADIENT,
    ORCA_GRADIENT_E_SCHEMA,
    ORCA_GRADIENT_STAGES,
    ORCA_GRADIENT_WORKFLOW,
    OrcaGradientError,
    OrcaGradientInputError,
    run_orca_gradient,
)
from tests.test_acp_orca_gradient_backend import (
    ENGRAAD_OK,
    STDOUT_ENERGY_ONLY,
    STDOUT_OK,
)

XYZ_TEXT = "2\nH2 fixture\nH 0.0000000000 0.0000000000 0.0000000000\nH 0.0000000000 0.0000000000 0.7400000000\n"
ENERGY = -1.166
GRADIENT = [[0.01, 0.0, 0.0], [-0.01, 0.0, 0.0]]


def _request(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "pes2ts_orca_gradient_request_v1",
        "xyz": XYZ_TEXT,
        "method": "GFN2-xTB",
        "basis": "",
        "charge": 0,
        "multiplicity": 1,
        "route_extras": [],
        "timeout_seconds": 600,
        "nproc": 2,
    }
    payload.update(overrides)
    return payload


def _config_with_fake_orca(tmp_path: Path) -> dict[str, Any]:
    exe = tmp_path / "fake_orca"
    exe.write_text("#!/bin/sh\necho fake-orca\n", encoding="utf-8")
    exe.chmod(0o755)
    return {
        "executables": {"orca": {"path": str(exe)}},
        "resources": {"nproc": 1, "mem": "1GB"},
        "theory": {"single_point": {"method": "GFN2-xTB", "basis": ""}},
    }


def _install_fake_orca_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str = STDOUT_OK,
    engrad_text: str | None = ENGRAAD_OK,
) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        input_file = Path(cmd[-1])
        cwd = Path(kwargs.get("cwd") or input_file.parent)
        captured.append({"input_file": input_file, "cwd": cwd})
        if engrad_text is not None:
            (cwd / "grad.engrad").write_text(engrad_text, encoding="utf-8")
        return subprocess.CompletedProcess(args=list(cmd), returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("cccp.qc.interfaces.orca.subprocess.run", _fake_run)
    return captured


def _patch_resolve_executable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    exe = tmp_path / "fake_orca"
    if not exe.exists():
        exe.write_text("#!/bin/sh\necho fake-orca\n", encoding="utf-8")
        exe.chmod(0o755)
    monkeypatch.setattr("cccp.qc.interfaces.orca.resolve_executable", lambda *a, **k: exe)
    return exe


def test_run_orca_gradient_writes_acp_standard_products(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_resolve_executable(monkeypatch, tmp_path)
    _install_fake_orca_run(monkeypatch)
    output_dir = tmp_path / "out"

    result = run_orca_gradient(
        request=_request(), output_dir=output_dir, config=_config_with_fake_orca(tmp_path)
    )

    assert isinstance(result, WorkflowResult)
    assert result.status == "completed"
    assert result.stages_completed == list(ORCA_GRADIENT_STAGES)
    assert result.metadata["workflow"] == ORCA_GRADIENT_WORKFLOW
    assert result.metadata["energy_hartree"] == pytest.approx(ENERGY)
    assert result.metadata["gradient_unit"] == "hartree/bohr"
    assert result.metadata["gradient_source"] == "engrad_file:grad.engrad"
    assert result.metadata["request_sha256"]

    manifest = ResultManifest.read(output_dir / "RESULT")
    assert manifest.version == 2
    assert manifest.workflow == ORCA_GRADIENT_WORKFLOW
    assert manifest.status == "completed"
    product_ids = {product.id for product in manifest.products}
    assert product_ids == {"gradient", "geometry", "energy"}

    gradient_payload = json.loads(
        (output_dir / "RESULT" / "gradient" / "gradient.json").read_text(encoding="utf-8")
    )
    assert gradient_payload["schema_version"] == GRADIENT_PRODUCT_SCHEMA
    assert gradient_payload["workflow"] == ORCA_GRADIENT_WORKFLOW
    assert gradient_payload["energy_hartree"] == pytest.approx(ENERGY)
    np.testing.assert_allclose(gradient_payload["gradient_hartree_per_bohr"], GRADIENT, atol=1e-9)
    assert gradient_payload["gradient_unit"] == "hartree/bohr"
    assert gradient_payload["gradient_convention"] == "energy_gradient_dE_dX"
    assert "forces = -gradient" in gradient_payload["gradient_sign_note"]
    assert gradient_payload["symbols"] == ["H", "H"]
    assert gradient_payload["request_sha256"] == result.metadata["request_sha256"]
    assert gradient_payload["gradient_source"] == "engrad_file:grad.engrad"
    expected_angstrom = (np.asarray(GRADIENT) / 0.529177210903).tolist()
    np.testing.assert_allclose(
        gradient_payload["gradient_hartree_per_angstrom"], expected_angstrom, atol=1e-9
    )

    geometry_text = (output_dir / "RESULT" / "geometry" / "geometry.xyz").read_text(
        encoding="utf-8"
    )
    assert geometry_text.splitlines()[0] == "2"
    assert "0.7400000000" in geometry_text

    energy_payload = json.loads(
        (output_dir / "RESULT" / "energy" / "energy.json").read_text(encoding="utf-8")
    )
    assert energy_payload["energy_hartree"] == pytest.approx(ENERGY)
    assert energy_payload["method"] == "GFN2-xTB"
    assert energy_payload["basis"] == ""

    run_dir = Path(result.metadata["run_dir"])
    assert run_dir.is_dir()
    assert (run_dir / "input.xyz").is_file()
    assert (run_dir / "grad.inp").is_file()
    assert (run_dir / "grad.out").is_file()
    route_line = next(
        line
        for line in (run_dir / "grad.inp").read_text(encoding="utf-8").splitlines()
        if line.startswith("!")
    )
    assert "EnGrad" in route_line


def test_run_orca_gradient_accepts_geometry_elements_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_resolve_executable(monkeypatch, tmp_path)
    _install_fake_orca_run(monkeypatch)
    request = _request(
        xyz=None,
        geometry=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.74]],
        elements=["H", "H"],
    )
    del request["xyz"]

    result = run_orca_gradient(
        request=request, output_dir=tmp_path / "out", config=_config_with_fake_orca(tmp_path)
    )

    assert result.status == "completed"
    manifest = ResultManifest.read(tmp_path / "out" / "RESULT")
    assert manifest.status == "completed"


@pytest.mark.parametrize(
    "payload,match",
    [
        (_request(schema_version="wrong"), ORCA_GRADIENT_E_SCHEMA),
        (_request(method=""), "method"),
        (_request(multiplicity=0), "multiplicity"),
        (_request(geometry=[[0, 0, 0]], elements=["H", "H"]), "geometry"),
    ],
)
def test_run_orca_gradient_invalid_request_raises_input_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any], match: str
) -> None:
    _patch_resolve_executable(monkeypatch, tmp_path)
    with pytest.raises(OrcaGradientInputError) as excinfo:
        run_orca_gradient(
            request=payload, output_dir=tmp_path / "out", config=_config_with_fake_orca(tmp_path)
        )
    assert match in str(excinfo.value)


def test_run_orca_gradient_failure_writes_no_completed_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_resolve_executable(monkeypatch, tmp_path)
    _install_fake_orca_run(monkeypatch, stdout=STDOUT_ENERGY_ONLY, engrad_text=None)
    output_dir = tmp_path / "out"

    with pytest.raises(OrcaGradientError) as excinfo:
        run_orca_gradient(
            request=_request(), output_dir=output_dir, config=_config_with_fake_orca(tmp_path)
        )

    assert excinfo.value.code == ORCA_GRADIENT_E_GRADIENT
    manifest_path = output_dir / "RESULT" / "result_manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest.get("status") != "completed"
    assert not (output_dir / "RESULT" / "gradient" / "gradient.json").exists()


def test_run_orca_gradient_backend_unavailable_raises_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cccp.qc.interfaces.orca.resolve_executable", lambda *a, **k: None)

    class _Unavailable:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def is_available(self) -> bool:
            return False

    with patch("acp.backends.registry.get_backend", return_value=_Unavailable):
        with pytest.raises(OrcaGradientError) as excinfo:
            run_orca_gradient(
                request=_request(),
                output_dir=tmp_path / "out",
                config=_config_with_fake_orca(tmp_path),
            )
    assert "unavailable" in str(excinfo.value).lower()
