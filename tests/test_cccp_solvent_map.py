"""Tests for the xTB-binary solvent-name mapping (plan T9 / IS-4).

Layers under test:
(a) name alias resolution — canonical names + case/whitespace folding,
    unknown names raise :class:`SolventValueError` with the legal list;
(b) combination legality — implementation × solvent model × solvent,
    with ALPB/GBSA official sets and GFN-method restrictions.

Plus the override contract: call-time overrides of initialization defaults
are validated/rendered by the OVERRIDE, never by the init defaults alone.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cccp.qc.interfaces.crest import CRESTInterface
from cccp.qc.interfaces.xtb import XTBInterface
from cccp.qc.interfaces.xtb_path import XTBPathInterface
from cccp.qc.keyword_registry import KeywordValueError
from cccp.utils.solvent_map import (
    LEGAL_SOLVENT_MODELS,
    XTB_ALPB_SOLVENTS,
    XTB_GBSA_METHOD_RESTRICTIONS,
    XTB_GBSA_SOLVENTS,
    SolventValueError,
    resolve_xtb_solvent,
    xtb_method_name,
    xtb_solvent,
    xtb_solvent_args,
)

XTB_REAL_BIN = Path("/home/xieningke/xtb-dist/bin/xtb")
XTB_REAL_SHARE = Path("/home/xieningke/xtb-dist/share/xtb")

ALPB_OFFICIAL = frozenset(
    {
        "acetone",
        "acetonitrile",
        "aniline",
        "benzaldehyde",
        "benzene",
        "ch2cl2",
        "chcl3",
        "cs2",
        "dioxane",
        "dmf",
        "dmso",
        "ether",
        "ethanol",
        "ethylacetate",
        "furane",
        "hexadecane",
        "hexane",
        "methanol",
        "nitromethane",
        "octanol",
        "woctanol",
        "phenol",
        "toluene",
        "thf",
        "water",
    }
)
GBSA_OFFICIAL = frozenset(
    {
        "acetone",
        "acetonitrile",
        "benzene",
        "ch2cl2",
        "chcl3",
        "cs2",
        "dmf",
        "dmso",
        "ether",
        "h2o",
        "methanol",
        "n-hexane",
        "thf",
        "toluene",
    }
)

WATER_XYZ = "3\nwater\nO 0.0 0.0 0.0\nH 0.96 0.0 0.0\nH -0.24 0.93 0.0\n"


# ── (a) alias resolution ───────────────────────────────────────────────


def test_alias_mappings_to_canonical_names() -> None:
    assert xtb_solvent("dichloromethane") == "ch2cl2"
    assert xtb_solvent("dcm") == "ch2cl2"
    assert xtb_solvent("chloroform") == "chcl3"
    assert xtb_solvent("meacn") == "acetonitrile"


def test_alias_case_and_whitespace_folding() -> None:
    assert xtb_solvent("DiChloroMethane") == "ch2cl2"
    assert xtb_solvent("  DCM ") == "ch2cl2"
    assert xtb_solvent("ChloroForm") == "chcl3"
    assert xtb_solvent("Me CN") == "acetonitrile"
    assert xtb_solvent("MEACN") == "acetonitrile"
    assert xtb_solvent("  TetraHydroFuran ") == "thf"


def test_canonical_names_and_model_spellings_map_to_identity() -> None:
    assert xtb_solvent("ch2cl2") == "ch2cl2"
    assert xtb_solvent("h2o") == "water"
    assert xtb_solvent("n-hexane") == "hexane"
    assert xtb_solvent("THF") == "thf"
    assert xtb_solvent(None) == ""
    assert xtb_solvent("") == ""


def test_unknown_name_raises_with_legal_list() -> None:
    with pytest.raises(SolventValueError) as excinfo:
        xtb_solvent("unobtainium")
    message = str(excinfo.value)
    assert "unobtainium" in message
    for name in sorted(XTB_ALPB_SOLVENTS | XTB_GBSA_SOLVENTS):
        assert name in message


def test_unknown_name_raises_with_per_model_legal_list() -> None:
    with pytest.raises(SolventValueError) as excinfo:
        resolve_xtb_solvent("unobtainium", method="GFN2-xTB", solvent_model="alpb")
    message = str(excinfo.value)
    assert "unobtainium" in message
    for name in sorted(XTB_ALPB_SOLVENTS):
        assert name in message

    with pytest.raises(SolventValueError) as excinfo:
        resolve_xtb_solvent("unobtainium", method="GFN2-xTB", solvent_model="gbsa")
    message = str(excinfo.value)
    assert "unobtainium" in message
    for name in sorted(XTB_GBSA_SOLVENTS):
        assert name in message


def test_unknown_name_is_never_passed_through() -> None:
    for model in sorted(LEGAL_SOLVENT_MODELS - {"none"}):
        with pytest.raises(SolventValueError):
            xtb_solvent_args("unobtainium", method="GFN2-xTB", solvent_model=model)


# ── official sets ──────────────────────────────────────────────────────


def test_official_sets_match_xtb_documentation() -> None:
    assert XTB_ALPB_SOLVENTS == ALPB_OFFICIAL
    assert XTB_GBSA_SOLVENTS == GBSA_OFFICIAL
    for name in ("aniline", "benzaldehyde", "phenol", "woctanol", "octanol"):
        assert name in XTB_ALPB_SOLVENTS
    assert "ethanol" in XTB_ALPB_SOLVENTS
    assert "ethanol" not in XTB_GBSA_SOLVENTS
    assert XTB_GBSA_SOLVENTS != XTB_ALPB_SOLVENTS


def test_ethanol_is_alpb_only() -> None:
    assert xtb_solvent("ethanol") == "ethanol"
    assert resolve_xtb_solvent("ethanol", method="GFN2-xTB", solvent_model="alpb") == "ethanol"
    with pytest.raises(SolventValueError) as excinfo:
        resolve_xtb_solvent("ethanol", method="GFN2-xTB", solvent_model="gbsa")
    assert "not parameterized for GBSA" in str(excinfo.value)


def test_gbsa_method_restrictions_documented() -> None:
    assert XTB_GBSA_METHOD_RESTRICTIONS == {
        "benzene": frozenset({"gfn1"}),
        "dmf": frozenset({"gfn2"}),
        "n-hexane": frozenset({"gfn2"}),
    }


# ── (b) combination legality: model × method × solvent ────────────────


def test_gfn0_plus_alpb_rejected() -> None:
    for name in ("water", "toluene", "acetone"):
        with pytest.raises(SolventValueError) as excinfo:
            resolve_xtb_solvent(name, method="GFN0-xTB", solvent_model="alpb")
        message = str(excinfo.value)
        assert "GFN0-xTB has no ALPB parameterization" in message


def test_gfn1_gbsa_benzene_legal_gfn2_rejected() -> None:
    assert resolve_xtb_solvent("benzene", method="GFN1-xTB", solvent_model="gbsa") == "benzene"
    with pytest.raises(SolventValueError) as excinfo:
        resolve_xtb_solvent("benzene", method="GFN2-xTB", solvent_model="gbsa")
    assert "GFN1-only" in str(excinfo.value)


def test_gfn2_gbsa_dmf_legal_gfn1_rejected() -> None:
    assert resolve_xtb_solvent("dmf", method="GFN2-xTB", solvent_model="gbsa") == "dmf"
    with pytest.raises(SolventValueError) as excinfo:
        resolve_xtb_solvent("dmf", method="GFN1-xTB", solvent_model="gbsa")
    assert "GFN2-only" in str(excinfo.value)


def test_gbsa_n_hexane_is_gfn2_only() -> None:
    assert resolve_xtb_solvent("n-hexane", method="GFN2-xTB", solvent_model="gbsa") == "hexane"
    assert resolve_xtb_solvent("hexane", method="GFN2-xTB", solvent_model="gbsa") == "hexane"
    for method in ("GFN1-xTB", "GFN0-xTB", "GFN-FF"):
        with pytest.raises(SolventValueError) as excinfo:
            resolve_xtb_solvent("n-hexane", method=method, solvent_model="gbsa")
        assert "GFN2-only" in str(excinfo.value)


def test_gbsa_base_set_legal_for_gfn0_and_gfnff() -> None:
    for method in ("GFN0-xTB", "GFN-FF"):
        for name in ("acetone", "water", "toluene"):
            assert resolve_xtb_solvent(name, method=method, solvent_model="gbsa"), (name, method)


def test_alpb_set_legal_for_all_non_gfn0_methods() -> None:
    for name in sorted(XTB_ALPB_SOLVENTS):
        for method in ("GFN1-xTB", "GFN2-xTB", "GFN-FF"):
            assert resolve_xtb_solvent(name, method=method, solvent_model="alpb") == xtb_solvent(
                name
            ), (name, method)


def test_gbsa_set_legal_per_method() -> None:
    cases = {
        "GFN1-xTB": {
            "acetone",
            "acetonitrile",
            "benzene",
            "ch2cl2",
            "chcl3",
            "cs2",
            "dmso",
            "ether",
            "h2o",
            "methanol",
            "thf",
            "toluene",
        },
        "GFN2-xTB": {
            "acetone",
            "acetonitrile",
            "ch2cl2",
            "chcl3",
            "cs2",
            "dmf",
            "dmso",
            "ether",
            "h2o",
            "methanol",
            "n-hexane",
            "thf",
            "toluene",
        },
    }
    for method, names in cases.items():
        assert names <= XTB_GBSA_SOLVENTS
        for name in sorted(XTB_GBSA_SOLVENTS):
            if name in names:
                assert resolve_xtb_solvent(name, method=method, solvent_model="gbsa"), (
                    name,
                    method,
                )
            else:
                with pytest.raises(SolventValueError):
                    resolve_xtb_solvent(name, method=method, solvent_model="gbsa")


def test_alpb_only_solvents_rejected_for_gbsa() -> None:
    for name in ("woctanol", "aniline", "benzaldehyde", "phenol", "hexadecane"):
        assert resolve_xtb_solvent(name, method="GFN2-xTB", solvent_model="alpb")
        with pytest.raises(SolventValueError) as excinfo:
            resolve_xtb_solvent(name, method="GFN2-xTB", solvent_model="gbsa")
        assert "not parameterized for GBSA" in str(excinfo.value)


def test_water_and_hexane_render_model_independent_canonical_names() -> None:
    assert resolve_xtb_solvent("h2o", method="GFN2-xTB", solvent_model="alpb") == "water"
    assert resolve_xtb_solvent("water", method="GFN2-xTB", solvent_model="gbsa") == "water"
    assert resolve_xtb_solvent("n-hexane", method="GFN2-xTB", solvent_model="alpb") == "hexane"
    assert xtb_solvent_args("dcm", method="GFN2-xTB", solvent_model="alpb") == [
        "--alpb",
        "ch2cl2",
    ]
    assert xtb_solvent_args("chloroform", method="GFN2-xTB", solvent_model="gbsa") == [
        "--gbsa",
        "chcl3",
    ]


def test_no_solvent_or_model_none_emits_no_flags() -> None:
    assert xtb_solvent_args(None, method="GFN2-xTB", solvent_model="alpb") == []
    assert xtb_solvent_args("", method="GFN2-xTB", solvent_model="gbsa") == []
    assert xtb_solvent_args("toluene", method="GFN2-xTB", solvent_model="none") == []
    assert xtb_solvent_args("toluene", method="GFN2-xTB", solvent_model=None) == []


def test_unknown_solvent_model_raises_instead_of_coercing_to_alpb() -> None:
    with pytest.raises(SolventValueError) as excinfo:
        xtb_solvent_args("toluene", method="GFN2-xTB", solvent_model="smd")
    assert "solvent models" in str(excinfo.value)


# ── method/implementation resolution ───────────────────────────────────


def test_xtb_method_name_maps_levels_and_spellings() -> None:
    assert xtb_method_name(0) == "GFN0-xTB"
    assert xtb_method_name(1) == "GFN1-xTB"
    assert xtb_method_name(2) == "GFN2-xTB"
    assert xtb_method_name("gfnff") == "GFN-FF"
    assert xtb_method_name("GFN-FF") == "GFN-FF"
    assert xtb_method_name("gfn1") == "GFN1-xTB"
    assert xtb_method_name("GFN2-xTB") == "GFN2-xTB"
    with pytest.raises(ValueError):
        xtb_method_name(3)
    with pytest.raises(ValueError):
        xtb_method_name("gfn9")


def test_non_xtb_binary_methods_are_rejected_via_registry() -> None:
    with pytest.raises(KeywordValueError):
        resolve_xtb_solvent("toluene", method="B97-3c", solvent_model="alpb")
    with pytest.raises(KeywordValueError):
        resolve_xtb_solvent("toluene", method="Native-GFN2-xTB", solvent_model="alpb")
    with pytest.raises(SolventValueError):
        resolve_xtb_solvent("toluene", method="GFN2-xTB", solvent_model="alpb", engine="orca")


# ── call-time overrides are validated/rendered by the OVERRIDE ─────────


def test_override_gfn_level_validated_by_override_not_init_default(
    sample_config: dict[str, Any],
) -> None:
    interface = XTBPathInterface(sample_config, gfn_level=2, solvent="dmf", solvent_model="gbsa")
    assert interface._solvent_args(gfn_level=2) == ["--gbsa", "dmf"]
    with pytest.raises(SolventValueError) as excinfo:
        interface._solvent_args(gfn_level=1)
    assert "GFN2-only" in str(excinfo.value)


def test_override_solvent_rendered_by_override_not_init_default(
    sample_config: dict[str, Any],
) -> None:
    interface = XTBInterface(sample_config, gfn_level=2, solvent="toluene", solvent_model="alpb")
    assert interface._solvent_args() == ["--alpb", "toluene"]
    assert interface._solvent_args("DiChloroMethane") == ["--alpb", "ch2cl2"]


def test_override_method_validated_by_override_crest(
    sample_config: dict[str, Any],
) -> None:
    interface = CRESTInterface(
        sample_config, gfn_level=2, solvent="chloroform", solvent_model="alpb"
    )
    assert interface._solvent_args() == ["--alpb", "chcl3"]
    with pytest.raises(SolventValueError) as excinfo:
        interface._solvent_args(gfn_level=0)
    assert "GFN0-xTB has no ALPB parameterization" in str(excinfo.value)


def test_path_search_public_call_uses_override_method(
    sample_config: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interface = XTBPathInterface(sample_config, gfn_level=2, solvent="dmf", solvent_model="gbsa")
    interface.executable = Path("/usr/bin/xtb")
    start_xyz = tmp_path / "start.xyz"
    end_xyz = tmp_path / "end.xyz"
    for path, distance in ((start_xyz, 1.0), (end_xyz, 1.5)):
        path.write_text(
            f"3\nframe\nH 0.0 0.0 0.0\nH {distance:.6f} 0.0 0.0\nH 0.0 1.0 0.0\n",
            encoding="utf-8",
        )
    captured: dict[str, Any] = {}

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        (Path(kwargs["cwd"]) / "xtbpath.txt").write_text(
            "3\nFrame 0 | energy: -1.0\nH 0 0 0\nH 1 0 0\nH 0 1 0\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("cccp.qc.interfaces.xtb_path.subprocess.run", _fake_run)

    legal = interface.path_search(start_xyz, end_xyz, tmp_path / "ok", gfn_level=2)
    assert legal.success is True
    assert captured["cmd"][-2:] == ["--gbsa", "dmf"]

    illegal = interface.path_search(start_xyz, end_xyz, tmp_path / "bad", gfn_level=1)
    assert illegal.success is False
    assert "GFN2-only" in (illegal.error_message or "")


# ── slow-gated real-xTB cross-check ────────────────────────────────────


def _run_real_xtb(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if XTB_REAL_SHARE.is_dir():
        env["XTBPATH"] = str(XTB_REAL_SHARE)
    return subprocess.run(
        [str(XTB_REAL_BIN), "h2o.xyz", *args, "--sp"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )


@pytest.mark.slow
@pytest.mark.skipif(
    not XTB_REAL_BIN.exists(),
    reason="real xTB binary /home/xieningke/xtb-dist/bin/xtb not available",
)
def test_real_xtb_accepts_canonical_solvent_names(
    tmp_path: Path,
) -> None:
    (tmp_path / "h2o.xyz").write_text(WATER_XYZ, encoding="utf-8")
    cases = [
        ("GFN1-xTB", "alpb", "dichloromethane", "ch2cl2"),
        ("GFN1-xTB", "gbsa", "benzene", "benzene"),
        ("GFN2-xTB", "gbsa", "dmf", "dmf"),
        ("GFN2-xTB", "alpb", "woctanol", "woctanol"),
        ("GFN2-xTB", "gbsa", "h2o", "water"),
        ("GFN1-xTB", "alpb", "Me CN", "acetonitrile"),
        ("GFN2-xTB", "alpb", "ethanol", "ethanol"),
    ]
    for method, model, user_name, expected in cases:
        flags = xtb_solvent_args(user_name, method=method, solvent_model=model)
        assert flags[-1] == expected, (user_name, flags)
        gfn = {"GFN1-xTB": "1", "GFN2-xTB": "2"}[method]
        result = _run_real_xtb(["--gfn", gfn, *flags], tmp_path)
        assert result.returncode == 0, (method, model, user_name, result.stderr[-500:])
        assert "TOTAL ENERGY" in result.stdout, (method, model, user_name)


@pytest.mark.slow
@pytest.mark.skipif(
    not XTB_REAL_BIN.exists(),
    reason="real xTB binary /home/xieningke/xtb-dist/bin/xtb not available",
)
def test_real_xtb_rejects_raw_alias_passthrough(tmp_path: Path) -> None:
    (tmp_path / "h2o.xyz").write_text(WATER_XYZ, encoding="utf-8")
    result = _run_real_xtb(["--gfn", "2", "--alpb", "dcm"], tmp_path)
    assert result.returncode != 0
    assert "not parametrized" in (result.stdout + result.stderr)
