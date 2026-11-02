"""Round-7 #3 — exactly one orbital basis on the ORCA ``!`` route line.

Defect: ``method="wB97X-D4 def2-TZVP"`` + ``basis="ma-def2-SVP"`` rendered
``! wB97X-D4 def2-TZVP ma-def2-SVP Opt``.  ORCA silently keeps the LAST token,
so the job "completed" with a different wavefunction than requested and no
warning (158 vs 246 basis functions).

The contract locked here:

1. the route-token classifier is the single authority (orbital basis /
   auxiliary basis / keyword) — catalog names, ``ma-*`` / Pople / ECP families,
   ``/J`` ``/C`` ``/JK`` fitting suffixes and ``AutoAux``;
2. the renderer refuses two DISTINCT orbital bases with a typed
   ``rule="basis_conflict"`` error (never picks a winner, never rewrites the
   requested basis);
3. an identical duplicate (method string + basis field, or a duplicated
   ``route_extras`` token) is emitted exactly once;
4. composite (``r2SCAN-3c``) and DLPNO routes are byte-identical to the
   goldens — their basis never rides the ``!`` line;
5. an orbital basis placed in an aux field is refused (``aux_basis_invalid``)
   rather than silently consumed as a fitting basis;
6. the preflight helper reports the same rule/field/tokens without rendering.
"""

from __future__ import annotations

import pytest

from cccp.qc.interfaces.orca import ORCAInterface
from cccp.qc.interfaces.route_render import (
    AuxiliaryBasisError,
    RouteBasisConflictError,
    detect_basis_conflicts,
    resolve_route_orbital_basis,
)
from cccp.qc.method_meta import (
    aux_basis_tokens,
    classify_basis_token,
    is_aux_basis,
    is_orbital_basis,
    orbital_basis_tokens,
)
from cccp.qc.resolved_spec import resolve_calculation_spec


def _bare_orca(method: str = "B3LYP", basis: str = "def2-SVP") -> ORCAInterface:
    iface = ORCAInterface.__new__(ORCAInterface)
    iface.method = method
    iface.basis = basis
    iface.solvent = None
    iface.solvent_model = "none"
    iface.maxcore = 1000
    iface.nproc = 1
    iface.mem_mb = iface.maxcore * iface.nproc
    iface._maxcore_safety = 0.8
    iface._maxcore_pin = iface.maxcore
    iface.charge = 0
    iface.multiplicity = 1
    iface.config = {}
    iface.executable = None
    return iface


def _render(
    *,
    method: str,
    basis: str,
    route_extras: list[str] | None = None,
    calc_type: str = "sp",
    aux_j_basis: str | None = None,
    aux_c_basis: str | None = None,
) -> str:
    blocks, _ = _bare_orca(method, basis)._build_input_blocks(
        calc_type=calc_type,
        method=method,
        basis=basis,
        route_extras=route_extras,
        aux_j_basis=aux_j_basis,
        aux_c_basis=aux_c_basis,
    )
    return blocks


def _route_line(rendered: str) -> str:
    return next(line for line in rendered.splitlines() if line.startswith("!"))


# ── classifier ───────────────────────────────────────────────────────────


def test_classifier_separates_orbital_aux_and_keyword_tokens() -> None:
    for token in ("def2-SVP", "def2-TZVP", "ma-def2-SVP", "cc-pVTZ", "6-311G(d)", "def2-mTZVPP"):
        assert classify_basis_token(token) == "orbital_basis", token
        assert is_orbital_basis(token)
    for token in ("def2/J", "def2-TZVP/C", "cc-pVTZ/C", "AutoAux", "SARC/J", "def2/JK"):
        assert classify_basis_token(token) == "aux_basis", token
        assert is_aux_basis(token)
    for token in ("RIJCOSX", "Opt", "wB97X-D4", "DLPNO-CCSD(T)", "TightSCF", ""):
        assert classify_basis_token(token) == "keyword", token


def test_token_helpers_split_method_strings() -> None:
    assert orbital_basis_tokens("wB97X-D4 def2-TZVP") == ("def2-TZVP",)
    assert orbital_basis_tokens("r2SCAN-3c") == ()
    assert aux_basis_tokens("def2/J def2-TZVP/C") == ("def2/J", "def2-TZVP/C")


# ── single basis rendered exactly once ──────────────────────────────────


def test_basis_field_renders_one_orbital_basis() -> None:
    line = _route_line(_render(method="wB97X-D4", basis="def2-TZVP"))
    assert line == "! wB97X-D4 def2-TZVP SP"
    assert len(orbital_basis_tokens(line)) == 1


def test_basis_inlined_in_method_string_is_not_duplicated() -> None:
    line = _route_line(_render(method="wB97X-D4 def2-TZVP", basis=""))
    assert len(orbital_basis_tokens(line)) == 1
    assert line.count("def2-TZVP") == 1


def test_identical_basis_in_method_and_field_renders_once() -> None:
    rendered = _render(method="wB97X-D4 def2-TZVP", basis="def2-TZVP")
    line = _route_line(rendered)
    assert line == "! wB97X-D4 def2-TZVP SP"
    assert line.count("def2-TZVP") == 1


def test_identical_basis_in_route_extras_is_deduplicated() -> None:
    rendered = _render(
        method="B3LYP",
        basis="def2-TZVP",
        route_extras=["def2-TZVP", "RIJCOSX"],
    )
    line = _route_line(rendered)
    assert line.count("def2-TZVP") == 1
    assert "RIJCOSX" in line


def test_route_extras_supplying_the_single_basis_is_kept() -> None:
    line = _route_line(
        _render(method="B3LYP", basis="", route_extras=["def2-TZVP", "RIJCOSX"])
    )
    assert len(orbital_basis_tokens(line)) == 1
    assert "def2-TZVP" in line


# ── conflict refusal ────────────────────────────────────────────────────


def test_two_distinct_orbital_bases_raise_typed_basis_conflict() -> None:
    with pytest.raises(RouteBasisConflictError) as excinfo:
        _render(method="wB97X-D4 def2-TZVP", basis="ma-def2-SVP")

    error = excinfo.value
    assert error.rule == "basis_conflict"
    assert error.params["rule"] == "basis_conflict"
    assert error.params["field"] == "basis"
    assert error.params["tokens"] == ["def2-TZVP", "ma-def2-SVP"]
    # ORCA's real precedence: the last token would win.
    assert error.params["effective_if_emitted"] == "ma-def2-SVP"
    message = str(error)
    assert "def2-TZVP" in message and "ma-def2-SVP" in message
    assert "last" in message.lower()


def test_route_extras_cannot_smuggle_a_second_orbital_basis() -> None:
    with pytest.raises(RouteBasisConflictError):
        _render(method="B3LYP", basis="def2-TZVP", route_extras=["ma-def2-SVP"])


def test_two_bases_inside_one_method_string_raise() -> None:
    with pytest.raises(RouteBasisConflictError):
        _render(method="wB97X-D4 def2-TZVP ma-def2-SVP", basis="")


def test_resolution_api_reports_the_single_basis() -> None:
    resolution = resolve_route_orbital_basis("wB97X-D4 def2-TZVP", "def2-TZVP", ["RIJCOSX"])
    assert resolution.orbital_basis == "def2-TZVP"
    assert resolution.emit_field_basis is False
    assert resolution.extras == ("RIJCOSX",)


# ── composite / DLPNO unchanged ─────────────────────────────────────────


def test_composite_method_route_carries_no_orbital_basis() -> None:
    rendered = _render(method="r2SCAN-3c", basis="")
    line = _route_line(rendered)
    assert line == "! r2SCAN-3c SP"
    assert orbital_basis_tokens(line) == ()
    assert "%basis" not in rendered


def test_dlpno_route_matches_golden_and_keeps_basis_in_basis_block() -> None:
    rendered = _render(method="DLPNO-CCSD(T)", basis="def2-TZVPP")
    line = _route_line(rendered)
    assert line == "! DLPNO-CCSD(T) TightSCF SP"
    assert orbital_basis_tokens(line) == ()
    assert '  basis "def2-TZVPP"' in rendered
    assert 'auxJ  "def2/J"' in rendered
    assert 'auxC  "def2-TZVPP/C"' in rendered


def test_gfn_route_is_unchanged() -> None:
    line = _route_line(_render(method="GFN2-xTB", basis=""))
    assert line == "! GFN2-xTB  SP"


def test_default_dft_route_is_byte_identical() -> None:
    assert _route_line(_render(method="B3LYP", basis="def2-TZVP")) == "! B3LYP def2-TZVP SP"


# ── auxiliary basis validation ──────────────────────────────────────────


def test_suffixed_aux_basis_is_rendered_in_the_basis_block() -> None:
    rendered = _render(
        method="B3LYP",
        basis="def2-TZVP",
        aux_j_basis="def2/J",
        calc_type="sp",
    )
    line = _route_line(rendered)
    assert "def2/J" not in line
    assert 'auxJ  "def2/J"' in rendered


def test_orbital_basis_in_aux_field_is_refused() -> None:
    with pytest.raises(AuxiliaryBasisError) as excinfo:
        _render(method="B3LYP", basis="def2-TZVP", aux_j_basis="ma-def2-SVP")
    assert excinfo.value.rule == "aux_basis_invalid"
    assert excinfo.value.params["field"] == "aux_j_basis"
    assert "ma-def2-SVP" in str(excinfo.value)


def test_resolved_spec_reports_aux_basis_invalid_and_clamps_to_fitting_default() -> None:
    spec = resolve_calculation_spec(
        "B3LYP",
        explicit={"basis": "def2-TZVPP", "aux_j_basis": "ma-def2-SVP"},
    )
    resolution = spec["aux_j_basis"]
    assert resolution.requested == "ma-def2-SVP"
    assert resolution.effective == "def2/J"
    assert resolution.adjustment_reason == "aux_basis_invalid"


# ── preflight helper (wired by the API batch) ───────────────────────────


def test_detect_basis_conflicts_matches_the_renderer_refusal() -> None:
    reasons = detect_basis_conflicts(
        method="wB97X-D4 def2-TZVP",
        basis="ma-def2-SVP",
    )
    assert len(reasons) == 1
    reason = reasons[0]
    assert reason["rule"] == "basis_conflict"
    assert reason["field"] == "basis"
    assert reason["tokens"] == ["def2-TZVP", "ma-def2-SVP"]
    assert reason["effective_if_emitted"] == "ma-def2-SVP"
    assert reason["message"]


def test_detect_basis_conflicts_accepts_valid_combinations() -> None:
    assert detect_basis_conflicts(method="wB97X-D4 def2-TZVP", basis="") == ()
    assert detect_basis_conflicts(method="wB97X-D4", basis="def2-TZVP") == ()
    assert (
        detect_basis_conflicts(
            method="DLPNO-CCSD(T)",
            basis="def2-TZVPP",
            aux_j_basis="def2/J",
            aux_c_basis="def2-TZVPP/C",
        )
        == ()
    )


def test_detect_basis_conflicts_flags_orbital_basis_in_aux_field() -> None:
    reasons = detect_basis_conflicts(
        method="B3LYP", basis="def2-TZVP", aux_c_basis="cc-pVTZ"
    )
    assert len(reasons) == 1
    assert reasons[0]["rule"] == "aux_basis_invalid"
    assert reasons[0]["field"] == "aux_c_basis"
    assert reasons[0]["allowed"] == ["AutoAux", "def2/J", "def2-<basis>/C"]
