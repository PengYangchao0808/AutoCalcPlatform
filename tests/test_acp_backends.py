"""Tests for ACP backend capability wrappers and registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import call, patch

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
    FrequencyCalculator,
    GeometryOptimizer,
    NMRCalculator,
    QCResult,
    SinglePointCalculator,
    ThermoCalculator,
)
from acp.backends.external import batch_process_thermo, run_isostat, run_shermo
from acp.backends.orca import ORCAInterface
from acp.backends.registry import get_backend, require_backend
from acp.backends.xtb import XTBInterface
from conformer_search.qc.interfaces import CRESTInterface
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
            "nmr": {
                "method": "B3LYP",
                "basis": "def2-TZVPP",
                "solvent": "chloroform",
                "solvent_model": "smd",
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
    assert isinstance(orca, NMRCalculator)
    assert isinstance(orca, SinglePointCalculator)
    assert isinstance(xtb, GeometryOptimizer)
    assert isinstance(xtb, SinglePointCalculator)
    assert not isinstance(xtb, FrequencyCalculator)
    assert isinstance(crest, GeometryOptimizer)


def test_registry_exposes_registered_backends() -> None:
    assert get_backend("orca") is ORCABackend
    assert get_backend("crest") is CrestBackend
    assert get_backend("xtb") is XTBBackend
    assert get_backend("external") is ExternalBackend
    assert issubclass(require_backend("frequency"), FrequencyCalculator)
    assert issubclass(require_backend("nmr"), NMRCalculator)
    assert issubclass(require_backend("single_point"), SinglePointCalculator)
    assert issubclass(require_backend("clustering"), ClusteringTool)
    assert issubclass(require_backend("thermochemistry"), ThermoCalculator)


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


def test_orca_backend_nmr_delegates_to_interface(tmp_path: Path) -> None:
    config = _make_config()
    backend = ORCABackend(config)
    expected = QCResult(success=True, energy=-5.0)

    with patch.object(ORCAInterface, "nmr_shielding", return_value=expected) as mock_nmr:
        result = backend.nmr_shielding(np.zeros((1, 3)), ["H"], output_dir=tmp_path)

    assert result is expected
    mock_nmr.assert_called_once()


def test_xtb_backend_delegates_to_interface(tmp_path: Path) -> None:
    config = _make_config()
    backend = XTBBackend(config)
    expected = QCResult(success=True, energy=-3.0)

    with patch.object(XTBInterface, "single_point", return_value=expected) as mock_sp:
        result = backend.single_point(np.zeros((1, 3)), ["H"], output_dir=tmp_path)

    assert result is expected
    mock_sp.assert_called_once()


def test_legacy_interface_imports_remain_available() -> None:
    assert ORCAInterface.__name__ == "ORCAInterface"
    assert CRESTInterface.__name__ == "CRESTInterface"


def test_external_runner_exports_match_legacy_exports() -> None:
    from conformer_search.qc.runners import (
        batch_process_thermo as legacy_batch_process_thermo,
    )
    from conformer_search.qc.runners import (
        run_isostat as legacy_run_isostat,
    )
    from conformer_search.qc.runners import (
        run_shermo as legacy_run_shermo,
    )

    assert run_isostat is legacy_run_isostat
    assert run_shermo is legacy_run_shermo
    assert batch_process_thermo is legacy_batch_process_thermo


def test_capability_matrix_supports_declared_statuses() -> None:
    assert supports("orca", "frequency") is True
    assert supports("orca", "nmr") is True
    assert (
        list_capabilities("crest")["conformer_search"]
        == CAPABILITY_MATRIX["crest"]["conformer_search"]
    )


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
    backend = backend_cls(config)

    with patch("shutil.which", return_value=f"/usr/bin/{exe_name}") as mock_which:
        assert backend.is_available() is True

    mock_which.assert_called_once_with(exe_name)


@pytest.mark.parametrize(
    "backend_cls",
    [ORCABackend, CrestBackend, XTBBackend],
)
def test_backend_is_unavailable_when_binary_missing(backend_cls: type) -> None:
    config = _make_config()
    backend = backend_cls(config)

    with patch("shutil.which", return_value=None):
        assert backend.is_available() is False


def test_external_backend_is_available_when_binaries_on_path() -> None:
    backend = ExternalBackend(_make_config())

    with patch(
        "shutil.which",
        side_effect=["/usr/bin/isostat", "/usr/bin/Shermo"],
    ) as mock_which:
        assert backend.is_available() is True

    assert mock_which.call_args_list == [call("isostat"), call("Shermo")]


def test_external_backend_is_unavailable_when_one_binary_missing() -> None:
    backend = ExternalBackend(_make_config())

    with patch(
        "shutil.which",
        side_effect=["/usr/bin/isostat", None],
    ) as mock_which:
        assert backend.is_available() is False

    assert mock_which.call_args_list == [call("isostat"), call("Shermo")]


@pytest.mark.slow
@pytest.mark.integration
@requires_isostat
@requires_shermo
def test_external_backend_binary_smoke_check() -> None:
    backend = ExternalBackend(_make_config())

    assert backend.is_available() is True