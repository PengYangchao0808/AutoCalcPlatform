"""P0 characterization pins — Wave 0 / todo 2 (pre-migration goldens companion).

These tests lock the CURRENT integration-path (ACP present) behavior that
already passes today:

* Hessian ``recalc_hess`` semantics — default / explicit / boundaries and the
  ORCA ``%geom`` emission seam;
* ``METHOD_META`` field snapshot (translation-layer defaults authority);
* capability-selection status quo (``require_backend`` / ``supports`` /
  ``CAPABILITY_MATRIX``).

They are pure characterization: when an implementation todo intentionally
changes one of these behaviors, the corresponding change MUST be recorded in
``tests/baseline/cccp_calculation_goldens/expected_behavior_delta.md`` and this
pin updated in the same commit.  The pre-migration goldens under
``tests/baseline/cccp_calculation_goldens/`` carry the full translation
artifacts; this file only pins the P0-relevant semantics.

The "silent degradation when ACP is missing" isolation bug is deliberately NOT
pinned anywhere: isolation success is the target change, not a regression.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.backends.capabilities import (
    CAPABILITY_MATRIX,
    BackendCapabilityStatus,
    supports,
)
from acp.backends.registry import require_backend
from acp.chem.composition import (
    AUTO_RECALC_HESS,
    MAX_RECALC_HESS_INTERVAL,
    NON_LIGHT_DEFAULT_INTERVAL,
    HessianResolution,
    resolve_recalc_hess,
)

GOLDENS_DIR = Path(__file__).resolve().parent / "baseline" / "cccp_calculation_goldens"

# Deterministic ORCA-interface config: nproc/maxcore appear verbatim in the
# generated input text, so pin them instead of relying on derived values.
_ORCA_CONFIG = {
    "executables": {"orca": {"path": "orca", "nproc": 4, "maxcore": 2000}},
    "resources": {"mem": "8GB", "nproc": 4},
}


# ---------------------------------------------------------------------------
# Hessian semantics (P0-relevant pins; exhaustive normalize/classify coverage
# lives in tests/test_acp_chem_composition.py)
# ---------------------------------------------------------------------------


class TestHessianSemantics:
    """resolve_recalc_hess defaults, explicit overrides, boundaries."""

    def test_default_auto_light_elements_is_off(self):
        resolution = resolve_recalc_hess(None, None, ["C", "H", "H", "H", "O", "H"])
        assert resolution.interval == 0
        assert resolution.source == "config"
        assert resolution.reason == "auto"
        assert resolution.enabled is False

    def test_default_auto_heavy_elements_uses_non_light_interval(self):
        resolution = resolve_recalc_hess(None, None, ["Fe", "C", "H"])
        assert resolution.interval == NON_LIGHT_DEFAULT_INTERVAL
        assert resolution.source == "config"
        assert resolution.reason == "auto"
        assert resolution.enabled is True
        assert resolution.heavy_elements == ["Fe"]
        assert resolution.triggering_elements == ["Fe"]

    def test_heteroatom_only_uses_non_light_interval_without_triggers(self):
        resolution = resolve_recalc_hess(None, None, ["S", "P", "C"])
        assert resolution.interval == NON_LIGHT_DEFAULT_INTERVAL
        assert resolution.triggering_elements == []

    def test_explicit_zero_wins_and_reports_off(self):
        resolution = resolve_recalc_hess(0, 10, ["Fe"])
        assert resolution.interval == 0
        assert resolution.source == "explicit"
        assert resolution.reason == "explicit_off"
        assert resolution.enabled is False

    def test_explicit_interval_wins_over_config(self):
        resolution = resolve_recalc_hess(5, 0, ["C", "H"])
        assert resolution.interval == 5
        assert resolution.source == "explicit"
        assert resolution.reason == "explicit_interval"
        assert resolution.enabled is True

    def test_explicit_auto_overrides_config_interval(self):
        # "auto" at the explicit level triggers element inference and IGNORES
        # the configured fixed interval.
        resolution = resolve_recalc_hess(AUTO_RECALC_HESS, 7, ["Fe"])
        assert resolution.interval == NON_LIGHT_DEFAULT_INTERVAL
        assert resolution.reason == "auto"

    @pytest.mark.parametrize("value", [1, 500, MAX_RECALC_HESS_INTERVAL])
    def test_boundary_intervals_accepted(self, value):
        resolution = resolve_recalc_hess(value, None, None)
        assert resolution.interval == value

    @pytest.mark.parametrize(
        "value",
        [MAX_RECALC_HESS_INTERVAL + 1, -1, True, 1.5, "2.5", "abc"],
    )
    def test_boundary_violations_rejected(self, value):
        with pytest.raises(ValueError):
            resolve_recalc_hess(value, None, None)


class TestHessianOrcaEmission:
    """The integration-path emission seam: ``_build_input_blocks`` opt routes."""

    def _build(self, *, recalc_hess, symbols, calc_type="opt"):
        from cccp.qc.interfaces.orca import ORCAInterface

        interface = ORCAInterface(
            dict(_ORCA_CONFIG), method="B3LYP", basis="def2-TZVPP"
        )
        text, resolution = interface._build_input_blocks(
            calc_type=calc_type, symbols=symbols, recalc_hess=recalc_hess
        )
        return text, resolution

    def test_auto_light_emits_no_recalc_hess_line(self):
        text, resolution = self._build(recalc_hess="auto", symbols=["C", "H"])
        assert resolution.interval == 0
        assert resolution.enabled is False
        assert "Recalc_Hess" not in text

    def test_auto_heavy_emits_recalc_hess_with_inferred_interval(self):
        text, resolution = self._build(recalc_hess="auto", symbols=["Fe"])
        assert resolution.interval == NON_LIGHT_DEFAULT_INTERVAL
        assert f"Recalc_Hess {NON_LIGHT_DEFAULT_INTERVAL}" in text

    def test_explicit_zero_emits_no_line_even_for_heavy(self):
        text, resolution = self._build(recalc_hess=0, symbols=["Fe"])
        assert resolution.source == "explicit"
        assert "Recalc_Hess" not in text

    def test_explicit_interval_emits_line(self):
        text, resolution = self._build(recalc_hess=3, symbols=["C"])
        assert resolution.interval == 3
        assert "Recalc_Hess 3" in text

    def test_non_opt_route_resolves_no_hessian_policy(self):
        _, resolution = self._build(recalc_hess="auto", symbols=["Fe"], calc_type="sp")
        assert resolution is None


# ---------------------------------------------------------------------------
# METHOD_META field snapshot (translation-layer authority)
# ---------------------------------------------------------------------------

# Generated from acp.catalog.METHOD_META at main@ff9b26e; every field is part
# of the translation contract consumed by _clamp_to_functional /
# _resolve_field_default / the ORCA route renderer.  Deltas must land in
# expected_behavior_delta.md first.
EXPECTED_METHOD_META = {
    "B3LYP": {
        "basis_inline": True,
        "builtin_dispersion": None,
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": True},
        "default_aux_c": None,
        "default_aux_j": None,
        "default_basis": "def2-TZVPP",
        "default_dispersion": "D4",
        "family": "conventional_dft",
        "implementation": "orca_dft",
        "needs_aux_c": False,
        "ri_support": "user",
        "solvent_models": None,
    },
    "B97-3c": {
        "basis_inline": False,
        "builtin_dispersion": "D3BJ",
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": True},
        "default_aux_c": None,
        "default_aux_j": None,
        "default_basis": "mTZVP",
        "default_dispersion": "none",
        "family": "composite_3c",
        "implementation": "orca_dft",
        "needs_aux_c": None,
        "ri_support": "composite",
        "solvent_models": None,
    },
    "DLPNO-CCSD(T)": {
        "basis_inline": False,
        "builtin_dispersion": None,
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": False},
        "default_aux_c": "def2-TZVPP/C",
        "default_aux_j": "def2/J",
        "default_basis": "def2-TZVPP",
        "default_dispersion": "none",
        "family": "conventional_dft",
        "implementation": "orca_dft",
        "needs_aux_c": True,
        "ri_support": "automatic",
        "solvent_models": None,
    },
    "GFN-FF": {
        "basis_inline": True,
        "builtin_dispersion": "builtin",
        "default_aux_c": None,
        "default_aux_j": None,
        "default_basis": "",
        "default_dispersion": "none",
        "family": "gfnff",
        "implementation": "orca_external_xtb",
        "needs_aux_c": None,
        "ri_support": "composite",
        "solvent_models": ["none", "ALPB"],
    },
    "GFN0-xTB": {
        "basis_inline": True,
        "builtin_dispersion": "D4",
        "default_aux_c": None,
        "default_aux_j": None,
        "default_basis": "",
        "default_dispersion": "none",
        "family": "gfn",
        "implementation": "orca_external_xtb",
        "needs_aux_c": None,
        "ri_support": "composite",
        "solvent_models": ["none", "ALPB"],
    },
    "GFN1-xTB": {
        "basis_inline": True,
        "builtin_dispersion": "D3",
        "default_aux_c": None,
        "default_aux_j": None,
        "default_basis": "",
        "default_dispersion": "none",
        "family": "gfn",
        "implementation": "orca_external_xtb",
        "needs_aux_c": None,
        "ri_support": "composite",
        "solvent_models": ["none", "ALPB"],
    },
    "GFN2-xTB": {
        "basis_inline": True,
        "builtin_dispersion": "D4",
        "default_aux_c": None,
        "default_aux_j": None,
        "default_basis": "",
        "default_dispersion": "none",
        "family": "gfn",
        "implementation": "orca_external_xtb",
        "needs_aux_c": None,
        "ri_support": "composite",
        "solvent_models": ["none", "ALPB"],
    },
    "M062X": {
        "basis_inline": True,
        "builtin_dispersion": None,
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": False},
        "default_aux_c": None,
        "default_aux_j": None,
        "default_basis": "def2-TZVPP",
        "default_dispersion": "none",
        "family": "conventional_dft",
        "implementation": "orca_dft",
        "needs_aux_c": False,
        "ri_support": "user",
        "solvent_models": None,
    },
    "PBE0": {
        "basis_inline": True,
        "builtin_dispersion": None,
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": True},
        "default_aux_c": None,
        "default_aux_j": None,
        "default_basis": "def2-TZVPP",
        "default_dispersion": "D4",
        "family": "conventional_dft",
        "implementation": "orca_dft",
        "needs_aux_c": False,
        "ri_support": "user",
        "solvent_models": None,
    },
    "PBEh-3c": {
        "basis_inline": False,
        "builtin_dispersion": "D3BJ",
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": False},
        "default_aux_c": None,
        "default_aux_j": None,
        "default_basis": "def2-mSVP",
        "default_dispersion": "none",
        "family": "composite_3c",
        "implementation": "orca_dft",
        "needs_aux_c": None,
        "ri_support": "composite",
        "solvent_models": None,
    },
    "PWPB95": {
        "basis_inline": True,
        "builtin_dispersion": None,
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": False},
        "default_aux_c": None,
        "default_aux_j": None,
        "default_basis": "def2-TZVPP",
        "default_dispersion": "D3BJ",
        "family": "conventional_dft",
        "implementation": "orca_dft",
        "needs_aux_c": True,
        "ri_support": "user",
        "solvent_models": None,
    },
    "mPW1PW91": {
        "basis_inline": True,
        "builtin_dispersion": None,
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": False},
        "default_aux_c": None,
        "default_aux_j": None,
        "default_basis": "6-311G(d)",
        "default_dispersion": "none",
        "family": "conventional_dft",
        "implementation": "orca_dft",
        "needs_aux_c": False,
        "ri_support": "user",
        "solvent_models": None,
    },
    "r2SCAN-3c": {
        "basis_inline": False,
        "builtin_dispersion": "D4",
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": True},
        "default_aux_c": None,
        "default_aux_j": None,
        "default_basis": "def2-mTZVPP",
        "default_dispersion": "none",
        "family": "composite_3c",
        "implementation": "orca_dft",
        "needs_aux_c": None,
        "ri_support": "composite",
        "solvent_models": None,
    },
    "revDSD-PBEP86": {
        "basis_inline": True,
        "builtin_dispersion": None,
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": False},
        "default_aux_c": None,
        "default_aux_j": None,
        "default_basis": "def2-TZVPP",
        "default_dispersion": "D4",
        "family": "conventional_dft",
        "implementation": "orca_dft",
        "needs_aux_c": True,
        "ri_support": "user",
        "solvent_models": None,
    },
    "wB97M-V": {
        "basis_inline": True,
        "builtin_dispersion": "VV10",
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": False},
        "default_aux_c": None,
        "default_aux_j": None,
        "default_basis": "def2-TZVPP",
        "default_dispersion": "none",
        "family": "conventional_dft",
        "implementation": "orca_dft",
        "needs_aux_c": False,
        "ri_support": "user",
        "solvent_models": None,
    },
    "wB97X-D4": {
        "basis_inline": True,
        "builtin_dispersion": "D4",
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": False},
        "default_aux_c": None,
        "default_aux_j": None,
        "default_basis": "def2-TZVPP",
        "default_dispersion": "none",
        "family": "conventional_dft",
        "implementation": "orca_dft",
        "needs_aux_c": False,
        "ri_support": "user",
        "solvent_models": None,
    },
}


class TestMethodMetaSnapshot:
    """METHOD_META field snapshot — the translation-layer defaults authority."""

    @staticmethod
    def _snapshot() -> dict:
        from acp.catalog import METHOD_META

        snapshot: dict = {}
        for name in sorted(METHOD_META):
            meta = METHOD_META[name]
            row = {
                "basis_inline": meta.get("basis_inline"),
                "builtin_dispersion": meta.get("builtin_dispersion"),
                "default_aux_c": meta.get("default_aux_c"),
                "default_aux_j": meta.get("default_aux_j"),
                "default_basis": meta.get("default_basis"),
                "default_dispersion": meta.get("default_dispersion"),
                "family": meta.get("family"),
                "implementation": meta.get("implementation"),
                "needs_aux_c": meta.get("needs_aux_c"),
                "ri_support": meta.get("ri_support"),
                "solvent_models": meta.get("solvent_models"),
            }
            if "capabilities" in meta:
                row["capabilities"] = meta["capabilities"]
            snapshot[name] = row
        return snapshot

    def test_method_meta_field_snapshot_matches(self):
        assert self._snapshot() == EXPECTED_METHOD_META

    def test_ri_support_partition_is_complete(self):
        # composite (3c/GFN) / automatic (DLPNO) / user (plain DFT) — no others.
        snapshot = self._snapshot()
        values = {row["ri_support"] for row in snapshot.values()}
        assert values == {"composite", "automatic", "user"}
        automatic = [n for n, r in snapshot.items() if r["ri_support"] == "automatic"]
        assert automatic == ["DLPNO-CCSD(T)"]

    def test_dlpno_carries_default_aux_pair(self):
        dlpno = self._snapshot()["DLPNO-CCSD(T)"]
        assert dlpno["default_aux_j"] == "def2/J"
        assert dlpno["default_aux_c"] == "def2-TZVPP/C"
        assert dlpno["needs_aux_c"] is True


# ---------------------------------------------------------------------------
# Capability selection (declared semantics, delta D1–D4)
# ---------------------------------------------------------------------------
# Delta citations below reference tests/baseline/cccp_calculation_goldens/
# expected_behavior_delta.md — the authorization for these pin values.


class TestCapabilitySelectionStatusQuo:
    def test_require_backend_selection_map(self):
        import acp.backends  # noqa: F401  — populates the registry

        expected = {
            "optimization": "ORCABackend",
            "single_point": "ORCABackend",
            "frequency": "ORCABackend",
            "clustering": "ExternalBackend",
            "thermochemistry": "ExternalBackend",
            "irc": "ORCABackend",
            "ts": "ORCABackend",
            "relaxed_scan": "ORCABackend",
            "conformer_search": "CensoBackend",
        }
        for capability, class_name in expected.items():
            selected = require_backend(capability)
            assert selected.__name__ == class_name, capability

    def test_selected_optimization_is_implemented_not_stub(self):
        # D1: declared-status selection never lands on a stub; D2 declares
        # CrestBackend.optimize STUBBED, so it is unreachable via require.
        import acp.backends  # noqa: F401

        selected = require_backend("optimization")
        assert selected.__name__ == "ORCABackend"

    def test_stubbed_capabilities_are_rejected_before_construction(self):
        # D1: a registry holding only stub-declaring backends must raise
        # UnsupportedCapabilityError instead of selecting a stub.
        from acp.backends.crest import CrestBackend
        from acp.backends.registry import BackendRegistry
        from cccp.calculation.errors import UnsupportedCapabilityError

        registry = BackendRegistry()
        registry.register(CrestBackend)
        with pytest.raises(UnsupportedCapabilityError):
            registry.require("geometry_optimization")
        with pytest.raises(UnsupportedCapabilityError):
            registry.require("single_point")

    def test_supports_matrix_statuses(self):
        assert supports("orca", "optimization") is True
        assert supports("orca", "single_point") is True
        assert supports("orca", "frequency") is True
        # D2 — declaration = implemented: stubs are not AVAILABLE.
        assert supports("crest", "optimization") is False
        assert supports("crest", "geometry_optimization") is False
        assert supports("crest", "single_point") is False
        assert supports("crest", "frequency") is False
        # D3 — declaration says implemented; runtime probes
        # (is_shermo_available / is_isostat_available) judge the binary.
        assert supports("external", "thermochemistry") is True
        assert supports("external", "clustering") is True
        assert supports("xtb", "mrrho_thermo") is True
        assert supports("xtb", "frequency") is False
        assert supports("censo", "conformer_search") is True

    def test_capability_matrix_is_complete_rectangle(self):
        # Every registered backend declares every known capability exactly once.
        for backend, row in CAPABILITY_MATRIX.items():
            assert row, backend
            for capability, status in row.items():
                assert isinstance(status, BackendCapabilityStatus), (backend, capability)
        widths = {len(row) for row in CAPABILITY_MATRIX.values()}
        assert len(widths) == 1


# ---------------------------------------------------------------------------
# Goldens integrity (deliverable guard)
# ---------------------------------------------------------------------------


class TestGoldenArtifactsExist:
    def test_goldens_dir_non_empty_with_manifest(self):
        manifest_path = GOLDENS_DIR / "manifest.json"
        assert manifest_path.is_file(), "goldens manifest.json missing"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest.get("source_commit"), "manifest lacks source commit"
        assert manifest.get("generator"), "manifest lacks generator script"
        assert manifest.get("files"), "manifest lacks file hashes"

    def test_golden_file_hashes_match_manifest(self):
        import hashlib

        manifest = json.loads((GOLDENS_DIR / "manifest.json").read_text(encoding="utf-8"))
        for name, entry in sorted(manifest["files"].items()):
            path = GOLDENS_DIR / name
            assert path.is_file(), f"golden file missing: {name}"
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert digest == entry["sha256"], f"golden hash mismatch: {name}"

    def test_expected_behavior_delta_table_exists(self):
        delta = GOLDENS_DIR / "expected_behavior_delta.md"
        assert delta.is_file()
        text = delta.read_text(encoding="utf-8")
        assert "approved" in text.lower()
