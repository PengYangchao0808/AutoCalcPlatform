"""Tests for ACP backend capability wrappers and registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

from acp.backends import (
    CAPABILITY_MATRIX,
    CrestBackend,
    ExternalBackend,
    ORCABackend,
    XTBBackend,
    list_capabilities,
    supports,
)
from acp.backends.base import (
    ClusteringTool,
    ConstrainedOptimizer,
    FrequencyCalculator,
    GeometryOptimizer,
    MrrhoThermoCalculator,
    QCResult,
    SinglePointCalculator,
    ThermoCalculator,
    TSMechanismCalculator,
)
from acp.backends.external import batch_process_thermo, run_isostat, run_shermo
from acp.backends.orca import ORCAInterface
from acp.backends.registry import get_backend, require_backend
from acp.backends.xtb import XTBInterface
from cccp.qc.interfaces import CRESTInterface
from cccp.qc.interfaces.constraints import DistanceConstraint
from cccp.qc.interfaces.xtb_thermo import XTBThermoResult
from tests.conftest import requires_isostat, requires_shermo


def _make_config() -> dict[str, Any]:
    return {
        "executables": {
            "orca": {"path": "orca"},
            "crest": {"path": "crest", "gfn_level": 2},
            "xtb": {"path": "xtb"},
            "isostat": {"path": "isostat"},
            "shermo": {"path": "Shermo"},
        },
        "resources": {"nproc": 1, "mem": "1GB"},
        "theory": {
            "optimization": {
                "engine": "orca",
                "method": "B3LYP",
                "basis": "def2-SVP",
                "dispersion": "GD3BJ",
            },
            "single_point": {
                "method": "M062X",
                "basis": "def2-TZVPP",
            },
            "preoptimization": {"gfn_level": 2},
        },
    }


def test_backend_imports_and_capabilities() -> None:
    config = _make_config()

    orca = ORCABackend(config)
    xtb = XTBBackend(config)
    crest = CrestBackend(config)

    assert isinstance(orca, GeometryOptimizer)
    assert isinstance(orca, FrequencyCalculator)
    assert isinstance(orca, SinglePointCalculator)
    assert isinstance(xtb, GeometryOptimizer)
    assert isinstance(xtb, ConstrainedOptimizer)
    assert isinstance(xtb, MrrhoThermoCalculator)
    assert isinstance(xtb, SinglePointCalculator)
    assert not isinstance(xtb, FrequencyCalculator)
    assert isinstance(crest, GeometryOptimizer)


def test_registry_exposes_registered_backends() -> None:
    assert get_backend("orca") is ORCABackend
    assert get_backend("crest") is CrestBackend
    assert get_backend("xtb") is XTBBackend
    assert get_backend("external") is ExternalBackend
    assert issubclass(require_backend("frequency"), FrequencyCalculator)
    assert issubclass(require_backend("single_point"), SinglePointCalculator)
    assert issubclass(require_backend("clustering"), ClusteringTool)
    assert issubclass(require_backend("thermochemistry"), ThermoCalculator)
    assert issubclass(require_backend("irc"), TSMechanismCalculator)


def test_require_backend_rejects_unknown_capability() -> None:
    with pytest.raises(ValueError, match="Unknown capability"):
        _ = require_backend("imaginary")


def test_orca_backend_delegates_to_interface(tmp_path: Path) -> None:
    config = _make_config()
    backend = ORCABackend(config)
    expected = QCResult(success=True, energy=-2.0)

    with patch.object(ORCAInterface, "single_point", return_value=expected) as mock_sp:
        result = backend.single_point(np.zeros((1, 3)), ["H"], output_dir=tmp_path)

    assert result is expected
    mock_sp.assert_called_once()


def test_xtb_backend_delegates_to_interface(tmp_path: Path) -> None:
    config = _make_config()
    backend = XTBBackend(config)
    expected = QCResult(success=True, energy=-3.0)

    with patch.object(XTBInterface, "single_point", return_value=expected) as mock_sp:
        result = backend.single_point(np.zeros((1, 3)), ["H"], output_dir=tmp_path)

    assert result is expected
    mock_sp.assert_called_once()


def test_xtb_backend_constrained_optimize_delegates_to_interface(tmp_path: Path) -> None:
    config = _make_config()
    backend = XTBBackend(config)
    constraints = [DistanceConstraint(atoms=(0, 1), target=1.1)]
    expected = QCResult(success=True, energy=-3.5)

    with patch.object(XTBInterface, "constrained_optimize", return_value=expected) as mock_opt:
        result = backend.constrained_optimize(
            np.zeros((2, 3)),
            ["H", "H"],
            charge=-1,
            multiplicity=2,
            output_dir=tmp_path,
            output_name="warmup",
            constraints=constraints,
            opt_level="tight",
        )

    assert result is expected
    kwargs = mock_opt.call_args.kwargs
    assert kwargs["output_dir"] == tmp_path
    assert kwargs["output_name"] == "warmup"
    assert kwargs["constraints"] == constraints
    assert kwargs["charge"] == -1
    assert kwargs["multiplicity"] == 2
    assert kwargs["opt_level"] == "tight"


def test_xtb_backend_enso_thermo_translates_interface_result(tmp_path: Path) -> None:
    config = _make_config()
    backend = XTBBackend(config)
    expected = XTBThermoResult(
        g_total=-3.25,
        zpve=0.11,
        h_total=-3.1,
        success=True,
        error=None,
    )

    with patch.object(XTBInterface, "enso_thermo", return_value=expected) as mock_thermo:
        result = backend.enso_thermo(
            np.zeros((1, 3)),
            ["H"],
            charge=-1,
            multiplicity=2,
            output_dir=tmp_path,
            temperature_k=310.0,
            sthr=25.0,
        )

    assert result.success is True
    assert result.converged is True
    assert result.gibbs == -3.25
    assert result.zpe == 0.11
    assert result.enthalpy == -3.1
    assert result.error_message is None
    assert result.output_file == tmp_path / "xtb_enso" / "xtb_enso.json"
    assert result.metadata == {"thermo": {"g_total": -3.25, "zpve": 0.11, "h_total": -3.1}}
    kwargs = mock_thermo.call_args.kwargs
    assert kwargs["output_dir"] == tmp_path
    assert kwargs["charge"] == -1
    assert kwargs["multiplicity"] == 2
    assert kwargs["temperature_k"] == 310.0
    assert kwargs["sthr"] == 25.0


def test_crest_backend_search_returns_ensemble_path(tmp_path: Path) -> None:
    config = _make_config()
    backend = CrestBackend(config)

    initial_xyz = tmp_path / "mol.xyz"
    initial_xyz.write_text("1\nt\nH 0 0 0\n", encoding="utf-8")
    ensemble_xyz = tmp_path / "crest_conformers.xyz"
    expected = QCResult(success=True, output_file=ensemble_xyz)

    with patch.object(CrestBackend, "run_conformer_search", return_value=expected) as mock_run:
        result = backend.search(
            initial_xyz,
            charge=-1,
            multiplicity=2,
            output_dir=tmp_path,
            energy_window=4.5,
        )

    assert result == ensemble_xyz
    kwargs = mock_run.call_args.kwargs
    assert kwargs["output_dir"] == tmp_path
    assert kwargs["output_name"] == "mol"
    assert kwargs["charge"] == -1
    assert kwargs["multiplicity"] == 2
    assert kwargs["energy_window"] == 4.5


def test_crest_backend_search_raises_on_failure(tmp_path: Path) -> None:
    config = _make_config()
    backend = CrestBackend(config)

    initial_xyz = tmp_path / "mol.xyz"
    initial_xyz.write_text("1\nt\nH 0 0 0\n", encoding="utf-8")
    failed = QCResult(success=False, error_message="CREST exploded")

    with patch.object(CrestBackend, "run_conformer_search", return_value=failed):
        with pytest.raises(RuntimeError, match="CREST exploded"):
            backend.search(initial_xyz, output_dir=tmp_path)


def test_crest_backend_search_raises_without_output_file(tmp_path: Path) -> None:
    config = _make_config()
    backend = CrestBackend(config)

    initial_xyz = tmp_path / "mol.xyz"
    initial_xyz.write_text("1\nt\nH 0 0 0\n", encoding="utf-8")
    orphan = QCResult(success=True, output_file=None)

    with patch.object(CrestBackend, "run_conformer_search", return_value=orphan):
        with pytest.raises(RuntimeError, match="without an ensemble output file"):
            backend.search(initial_xyz, output_dir=tmp_path)


def test_legacy_interface_imports_remain_available() -> None:
    assert ORCAInterface.__name__ == "ORCAInterface"
    assert CRESTInterface.__name__ == "CRESTInterface"


def test_external_runner_exports_match_legacy_exports() -> None:
    from cccp.qc.runners import (
        batch_process_thermo as legacy_batch_process_thermo,
    )
    from cccp.qc.runners import (
        run_isostat as legacy_run_isostat,
    )
    from cccp.qc.runners import (
        run_shermo as legacy_run_shermo,
    )

    assert run_isostat is legacy_run_isostat
    assert run_shermo is legacy_run_shermo
    assert batch_process_thermo is legacy_batch_process_thermo


def test_capability_matrix_supports_declared_statuses() -> None:
    assert supports("orca", "frequency") is True
    assert supports("orca", "irc") is True
    assert supports("xtb", "constrained_optimization") is True
    assert supports("xtb", "constrained_optimize") is True
    assert supports("xtb", "mrrho_thermochemistry") is True
    assert supports("xtb", "enso_thermo") is True
    assert supports("xtb", "irc") is False
    assert (
        list_capabilities("crest")["conformer_search"]
        == CAPABILITY_MATRIX["crest"]["conformer_search"]
    )


def test_irc_capability_matrix_entries() -> None:
    assert CAPABILITY_MATRIX["orca"]["irc"].value == "available"
    for backend_name in ("censo", "crest", "xtb", "external", "molclus", "isostat"):
        assert CAPABILITY_MATRIX[backend_name]["irc"].value == "not_implemented"


def test_xtb_capability_matrix_entries() -> None:
    assert CAPABILITY_MATRIX["xtb"]["constrained_optimization"].value == "available"
    assert CAPABILITY_MATRIX["xtb"]["mrrho_thermochemistry"].value == "available"
    for backend_name in ("censo", "orca", "crest", "external", "molclus", "isostat"):
        assert (
            CAPABILITY_MATRIX[backend_name]["constrained_optimization"].value == "not_implemented"
        )
        assert CAPABILITY_MATRIX[backend_name]["mrrho_thermochemistry"].value == "not_implemented"


def test_orca_backend_get_version_uses_detect_version() -> None:
    config = _make_config()
    backend = ORCABackend(config)
    backend._interface.executable = Path("/usr/bin/orca")

    with patch("acp.backends.orca.detect_version", return_value="ORCA 6.1.1") as mock_detect:
        assert backend.get_version() == "ORCA 6.1.1"
        assert backend.get_version() == "ORCA 6.1.1"

    mock_detect.assert_called_once_with("orca", Path("/usr/bin/orca"))


def test_orca_backend_get_version_returns_none_when_missing() -> None:
    config = _make_config()
    backend = ORCABackend(config)
    backend._interface.executable = None

    with patch("acp.backends.orca.detect_version", return_value=None) as mock_detect:
        assert backend.get_version() is None

    mock_detect.assert_called_once_with("orca", None)


def test_external_backend_implements_clustering_and_thermo_protocols() -> None:
    backend = ExternalBackend(_make_config())

    assert isinstance(backend, ClusteringTool)
    assert isinstance(backend, ThermoCalculator)


@pytest.mark.parametrize(
    ("backend_cls", "exe_name"),
    [
        (ORCABackend, "orca"),
        (CrestBackend, "crest"),
        (XTBBackend, "xtb"),
    ],
)
def test_backend_is_available_when_binary_on_path(backend_cls: type, exe_name: str) -> None:
    config = _make_config()

    with patch(
        f"cccp.qc.interfaces.{exe_name}.resolve_executable",
        return_value=Path(f"/usr/bin/{exe_name}"),
    ):
        backend = backend_cls(config)
        assert backend.is_available() is True


@pytest.mark.parametrize(
    "backend_cls",
    [ORCABackend, CrestBackend, XTBBackend],
)
def test_backend_is_unavailable_when_binary_missing(backend_cls: type) -> None:
    config = _make_config()
    exe_name = backend_cls.name

    with patch(f"cccp.qc.interfaces.{exe_name}.resolve_executable", return_value=None):
        backend = backend_cls(config)
        assert backend.is_available() is False


def test_external_backend_is_available_when_binaries_on_path() -> None:
    backend = ExternalBackend(_make_config())

    def _resolve(name: str, configured_path: str | Path | None = None) -> Path | None:
        return Path(f"/usr/bin/{name}")

    with patch("acp.backends.external_backend.resolve_executable", side_effect=_resolve):
        assert backend.is_available() is True


def test_external_backend_is_unavailable_when_one_binary_missing() -> None:
    backend = ExternalBackend(_make_config())

    def _resolve(name: str, configured_path: str | Path | None = None) -> Path | None:
        return None if name == "shermo" else Path(f"/usr/bin/{name}")

    with patch("acp.backends.external_backend.resolve_executable", side_effect=_resolve):
        assert backend.is_available() is False


@pytest.mark.slow
@pytest.mark.integration
@requires_isostat
@requires_shermo
def test_external_backend_binary_smoke_check() -> None:
    backend = ExternalBackend(_make_config())

    assert backend.is_available() is True


def test_isostat_title_normalisation_to_molclus_format(tmp_path: Path) -> None:
    """ISOSTAT rejects "Frame N | Energy: X" titles (exit 24 on this
    Fortran build); the backend must rewrite them as Molclus bare-energy
    lines before invoking ISOSTAT (curcusone-test failure root cause)."""
    from cccp.qc.interfaces.isostat import normalise_titles_for_isostat

    src = tmp_path / "isomers.xyz"
    src.write_text(
        "2\n"
        "Frame 0 | Energy: -64.6127037805\n"
        "O  0.0  0.0  0.0\n"
        "O  1.0  0.0  0.0\n"
        "2\n"
        "Frame 1 | Energy: -64.6126000000\n"
        "O  0.0  0.0  0.0\n"
        "O  1.0  0.0  0.0\n",
        encoding="utf-8",
    )

    out = normalise_titles_for_isostat(src)
    try:
        text = out.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert lines[1] == "        -64.6127037805"
        assert lines[5] == "        -64.6126000000"
        # Coordinates are untouched; no "Frame N | Energy:" remains.
        assert "Frame" not in text
        assert "O  0.0  0.0  0.0" in text
        # The original file is never mutated.
        assert "Frame 0 | Energy" in src.read_text(encoding="utf-8")
    finally:
        out.unlink(missing_ok=True)


def test_isostat_title_normalisation_keeps_coord_lines(tmp_path: Path) -> None:
    """Multi-frame inputs with blank-line separation and no-energy titles
    must survive normalisation (frames without a float keep their title)."""
    from cccp.qc.interfaces.isostat import normalise_titles_for_isostat

    src = tmp_path / "mixed.xyz"
    src.write_text(
        "1\nbare  -1.5\nH  0.0  0.0  0.0\n\n1\nno energy here\nH  1.0  0.0  0.0\n",
        encoding="utf-8",
    )
    out = normalise_titles_for_isostat(src)
    try:
        lines = out.read_text(encoding="utf-8").splitlines()
        assert lines[1] == "        -1.5000000000"
        # Frame 2 title has no float → kept verbatim; coordinates intact.
        assert lines[4] == "no energy here"
        assert lines[5] == "H  1.0  0.0  0.0"
    finally:
        out.unlink(missing_ok=True)
