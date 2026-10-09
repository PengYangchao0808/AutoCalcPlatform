# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnannotatedClassAttribute=false, reportUnusedFunction=false
"""P0 low-level isolation probes: ``cccp`` must work without the ``acp`` package.

Design (docs/ACP_CCCP_Architecture_Report.md appendix B + plan todo 3):

* Every behavior probe runs in a **fresh child interpreter** (``sys.executable
  -c <script>``) so import blocking is clean.  The child installs an
  ``importlib.abc.MetaPathFinder`` that raises ``ModuleNotFoundError`` for
  ``acp`` / ``acp.*`` **before any cccp module is imported**, and patches
  ``subprocess.run`` / ``subprocess.Popen`` to raise if called (input
  generation only — no external processes, no ORCA binary needed).
* Two environment variants are probed for every expectation:
  - ``blocked``: cccp+acp both importable (repo ``src`` on ``PYTHONPATH``);
    the meta-path blocker provides the real ``acp`` absence.
  - ``cccp-only``: a temp tree containing **only** the ``cccp`` sources is the
    sole provider of cccp; every ambient sys.path entry that could expose
    ``acp``/``cccp`` (editable installs, user-site ``.pth`` entries) is pruned
    and genuine absence is verified with ``find_spec("acp") is None`` before
    the blocker is installed as a backstop.  This variant does not rely on the
    same-repo import blocker alone.
* This file covers only the **P0 low-level** expectations (input generation /
  protocol parsing / DLPNO aux consistency).  ``cccp.calculation`` task-layer
  isolation is deliberately NOT asserted here (todos 13/17, closes in 35).

Unimplemented expectations are tracked with per-test strict xfail marks bound
to the exact exception raised today (verified with ``--runxfail``; see
``.omo/evidence/acp-cccp-remediation/task-3-isolation.txt``).  Whole-file
xfail is forbidden — enforced by ``test_xfail_marks_are_strict_and_explicit``.
When a marked test turns green (XPASS under strict=True fails the run), the
corresponding feature owner must remove the mark immediately.
"""

from __future__ import annotations

import ast
import builtins
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CCCP_SRC = REPO_ROOT / "src" / "cccp"

_PROBE_MARKER = "::probe::"

# Environment variants probed for every low-level expectation (plan todo 3(b)).
_VARIANTS = ("blocked", "cccp-only")

# Frozen baseline: ideal-gas 1 atm -> 1 mol/L correction at 298.15 K in Hartree
# (= standard_state_correction_kcal(298.15) kcal/mol / HARTREE_TO_KCAL).
_BASELINE_DELTA_HARTREE_298K = 0.003018804534102794

# Xfail lifecycle: raw --runxfail evidence lives in
# .omo/evidence/acp-cccp-remediation/task-3-isolation.txt.  All expectations
# in this file are green since the METHOD_META single-sourcing (plan todo 7);
# any future mark must use the strict+raises convention enforced below and be
# removed the moment its feature lands (plan Verification strategy, XPASS).

# Probe executed inside a child interpreter.  Only stdlib is imported before
# the BlockACP finder is installed — the probe must never import ``acp``
# before blocking.  Result protocol: one ``::probe::`` JSON line with either
# ``{"ok": true, "data": ...}`` or ``{"ok": false, "exc": <exc name>, "msg": ...}``.
_PROBE_SCRIPT = r"""
import importlib.abc
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch


class BlockACP(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "acp" or fullname.startswith("acp."):
            raise ModuleNotFoundError("audit blocked " + fullname, name=fullname)


scenario = sys.argv[1]
variant = sys.argv[2]
allowed_cccp_root = sys.argv[3] if len(sys.argv) > 3 else ""

if variant == "cccp-only":
    # Build a genuine cccp-only sys.path: keep stdlib/site-packages but drop
    # every ambient entry that could expose the acp or cccp packages (editable
    # installs / user-site .pth entries), then prove acp is truly absent.
    kept = []
    for entry in sys.path:
        if entry == allowed_cccp_root:
            kept.append(entry)
            continue
        base = Path(entry or ".")
        try:
            exposes = (
                (base / "acp").exists()
                or (base / "acp.py").exists()
                or (base / "cccp").exists()
                or (base / "cccp.py").exists()
            )
        except OSError:
            exposes = False
        if exposes:
            continue
        kept.append(entry)
    sys.path[:] = kept
    if importlib.util.find_spec("acp") is not None:
        raise RuntimeError("acp still importable in cccp-only environment")
elif variant == "blocked":
    if importlib.util.find_spec("acp") is None:
        raise RuntimeError("acp not importable; the import blocker would be meaningless")
else:
    raise RuntimeError("unknown variant " + variant)

blocker = BlockACP()
sys.meta_path.insert(0, blocker)
if any(n == "acp" or n.startswith("acp.") for n in sys.modules):
    raise RuntimeError("acp preloaded before the blocker was installed")

result = None
try:
    with patch("subprocess.run", side_effect=AssertionError("no process")), \
         patch("subprocess.Popen", side_effect=AssertionError("no process")):
        from cccp.config import _get_default_config
        from cccp.core.protocols import resolve_protocol_spec
        from cccp.qc.interfaces.orca import ORCAInterface

        def _interface():
            with patch("cccp.qc.interfaces.orca.resolve_executable", return_value=None):
                return ORCAInterface({}, method="B3LYP", basis="def2-SVP")

        def _aux(text):
            found = {}
            for line in text.splitlines():
                stripped = line.strip()
                for key in ("auxJ", "auxC"):
                    if stripped.startswith(key):
                        parts = stripped.split(None, 1)
                        found[key] = parts[1].strip().strip('"') if len(parts) > 1 else ""
            return found

        if scenario == "optimize_input":
            # Documented appendix B call shape: 4 recalc_hess values on Opt.
            rows = []
            interface = _interface()
            for value in (None, "auto", 0, 10):
                blocks, _meta = interface._build_input_blocks(
                    calc_type="opt", symbols=["H", "H"], recalc_hess=value
                )
                rows.append(
                    {
                        "value": repr(value),
                        "has_geom_block": "%geom" in blocks,
                        "recalc_lines": [
                            ln.strip() for ln in blocks.splitlines() if "Recalc_Hess" in ln
                        ],
                    }
                )
            result = {"ok": True, "data": rows}
        elif scenario == "protocol_plain":
            spec = resolve_protocol_spec(_get_default_config(), "lite", levels=None)
            result = {
                "ok": True,
                "data": {"name": spec.name, "opt_recalc_hess": repr(spec.opt_recalc_hess)},
            }
        elif scenario == "protocol_override":
            spec = resolve_protocol_spec(
                _get_default_config(), "lite", levels={"optimization": {"recalc_hess": 0}}
            )
            result = {
                "ok": True,
                "data": {"name": spec.name, "opt_recalc_hess": repr(spec.opt_recalc_hess)},
            }
        elif scenario == "dlpno":
            interface = _interface()
            isolated, _ = interface._build_input_blocks(
                calc_type="sp", method="DLPNO-CCSD(T)", basis="def2-TZVPP"
            )
            if any(n == "acp" or n.startswith("acp.") for n in sys.modules):
                raise RuntimeError("acp leaked into sys.modules during isolated build")
            sys.meta_path.remove(blocker)
            integrated, _ = interface._build_input_blocks(
                calc_type="sp", method="DLPNO-CCSD(T)", basis="def2-TZVPP"
            )
            result = {
                "ok": True,
                "data": {"isolated": _aux(isolated), "integrated": _aux(integrated)},
            }
        elif scenario == "shermo_path":
            from cccp.qc import shermo_adapter, thermo_normalize

            freq = Path("probe_freq.log")
            freq.write_text("frequency output", encoding="utf-8")
            calls = []

            def fake_run_shermo(**kwargs):
                calls.append(kwargs)
                return {"u_sum": -99.9, "h_sum": -99.88, "g_sum": -99.95, "s_total": 0.0123}

            with patch("cccp.qc.shermo_adapter.run_shermo", fake_run_shermo):
                before = len(calls)
                atm = shermo_adapter.execute_shermo(
                    freq, -100.0, output_dir="thermo_atm", standard_state="1atm"
                )
                mid = len(calls)
                molar = shermo_adapter.execute_shermo(
                    freq, -100.0, output_dir="thermo_m", standard_state="1M"
                )
                after = len(calls)
            if any(n == "acp" or n.startswith("acp.") for n in sys.modules):
                raise RuntimeError("acp leaked into sys.modules during Shermo path")
            result = {
                "ok": True,
                "data": {
                    "launches": [mid - before, after - mid],
                    "modules": [shermo_adapter.__name__, thermo_normalize.__name__],
                    "one_atm": {
                        "gibbs": atm.metadata["gibbs_hartree"],
                        "source": atm.metadata["selected_gibbs_source"],
                        "standard_state": atm.metadata["standard_state"],
                        "conc": calls[0]["conc"],
                    },
                    "one_m": {
                        "gibbs": molar.metadata["gibbs_hartree"],
                        "source": molar.metadata["selected_gibbs_source"],
                        "standard_state": molar.metadata["standard_state"],
                        "delta_hartree": molar.metadata["standard_state_delta_g_hartree"],
                        "conc": calls[1]["conc"],
                    },
                },
            }
        elif scenario == "optimize_task":
            from cccp.calculation.context import TaskContext
            from cccp.calculation.contracts import OptimizationMode, StructureRole
            from cccp.calculation.requests import (
                MethodSpec,
                OptimizeOptions,
                StructureInput,
                TaskKind,
                TaskRequest,
                TsSpec,
            )
            from cccp.calculation.tasks.optimize import run_optimize

            class _StubBackend:
                name = "orca"

                def __init__(self):
                    self.methods = []

                def _ok(self, symbols):
                    from cccp.qc.interfaces.base import QCResult

                    return QCResult(
                        success=True,
                        energy=-1.0,
                        coordinates=[[0.0, 0.0, 0.0] for _ in symbols],
                        symbols=list(symbols),
                        converged=True,
                    )

                def optimize(
                    self, coordinates, symbols, charge=0, multiplicity=1,
                    output_dir=None, **kwargs,
                ):
                    self.methods.append("optimize")
                    return self._ok(symbols)

                def transition_state_opt(
                    self, coordinates, symbols, charge=0, multiplicity=1,
                    output_dir=None, **kwargs,
                ):
                    self.methods.append("transition_state_opt")
                    return self._ok(symbols)

            def _request(role, options):
                return TaskRequest(
                    task=TaskKind.OPTIMIZE,
                    structure=StructureInput(
                        coordinates=((0.0, 0.0, 0.0),), symbols=("C",), role=role
                    ),
                    level=MethodSpec(method="r2SCAN-3c"),
                    options=options,
                )

            normal_backend = _StubBackend()
            normal = run_optimize(
                _request(StructureRole.MINIMUM, OptimizeOptions()),
                context=TaskContext(backend=normal_backend),
            )
            ts_backend = _StubBackend()
            ts = run_optimize(
                _request(
                    StructureRole.TRANSITION_STATE,
                    OptimizeOptions(
                        mode=OptimizationMode.TRANSITION_STATE, ts=TsSpec(enabled=True)
                    ),
                ),
                context=TaskContext(backend=ts_backend),
            )
            if any(n == "acp" or n.startswith("acp.") for n in sys.modules):
                raise RuntimeError("acp leaked into sys.modules during optimize task")
            result = {
                "ok": True,
                "data": {
                    "normal": {
                        "status": normal.status,
                        "methods": normal_backend.methods,
                        "optimization_status": normal.payload.optimization_status,
                    },
                    "ts": {
                        "status": ts.status,
                        "methods": ts_backend.methods,
                        "optimization_status": ts.payload.optimization_status,
                    },
                },
            }
        else:
            result = {"ok": False, "exc": "ValueError", "msg": "unknown scenario " + scenario}
except BaseException as exc:
    result = {"ok": False, "exc": type(exc).__name__, "msg": str(exc)}
print(::PROBE_MARKER:: + json.dumps(result))
""".replace("::PROBE_MARKER::", '"' + _PROBE_MARKER + '"')


@pytest.fixture(scope="module")
def cccp_only_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Temp tree containing only the ``cccp`` package sources."""
    root = tmp_path_factory.mktemp("cccp_only_src")
    shutil.copytree(CCCP_SRC, root / "cccp")
    return root


def _run_probe(cccp_only_root: Path, workdir: Path, scenario: str, variant: str) -> dict[str, Any]:
    """Run the isolation probe in a child interpreter and return its payload."""
    env = dict(os.environ)
    env["ACP_DISABLE_MPI_SNIFF"] = "1"
    if variant == "cccp-only":
        env["PYTHONPATH"] = str(cccp_only_root)
        allowed_root = str(cccp_only_root)
    else:
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        allowed_root = ""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE_SCRIPT, scenario, variant, allowed_root],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(workdir),
        timeout=180,
    )
    payload = None
    for line in proc.stdout.splitlines():
        if line.startswith(_PROBE_MARKER):
            payload = json.loads(line[len(_PROBE_MARKER) :])
    if payload is None:
        raise AssertionError(
            f"probe produced no result (scenario={scenario}, variant={variant}, "
            f"rc={proc.returncode})\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return payload


def _require_ok(payload: dict[str, Any]) -> Any:
    """Return probe data, replaying the child exception (type + message) otherwise."""
    if payload.get("ok"):
        return payload["data"]
    exc_name = str(payload.get("exc"))
    exc_cls = getattr(builtins, exc_name, None)
    if not isinstance(exc_cls, type) or not issubclass(exc_cls, BaseException):
        raise RuntimeError(f"probe raised unknown exception {exc_name}: {payload.get('msg')}")
    raise exc_cls(str(payload.get("msg")))


def test_optimize_input_isolated(cccp_only_tree: Path, tmp_path: Path) -> None:
    """P0: ORCA optimize input generation succeeds with acp imports blocked.

    Appendix B low-level expectation: ``_build_input_blocks(calc_type="opt",
    symbols=["H", "H"], recalc_hess=value)`` succeeds for all four documented
    ``recalc_hess`` values (None / "auto" / 0 / 10).  Emission shape follows
    the shared Hessian policy (H2 = light elements): no ``Recalc_Hess`` line
    for None/"auto"/0, ``Recalc_Hess 10`` for the explicit interval 10.
    Green since todo 6 (Hessian policy relocated to cccp).
    """
    for variant in _VARIANTS:
        rows = _require_ok(_run_probe(cccp_only_tree, tmp_path, "optimize_input", variant))
        by_value = {row["value"]: row for row in rows}
        assert set(by_value) == {"None", "'auto'", "0", "10"}, f"{variant}: {sorted(by_value)}"
        for value in ("None", "'auto'", "0"):
            row = by_value[value]
            assert row["has_geom_block"], f"{variant} recalc_hess={value}: missing %geom block"
            assert row["recalc_lines"] == [], (
                f"{variant} recalc_hess={value}: light H2 must not emit Recalc_Hess, "
                f"got {row['recalc_lines']}"
            )
        row = by_value["10"]
        assert row["has_geom_block"], f"{variant} recalc_hess=10: missing %geom block"
        assert row["recalc_lines"] == ["Recalc_Hess 10"], (
            f"{variant} recalc_hess=10: expected explicit Recalc_Hess 10, got {row['recalc_lines']}"
        )


def test_optimize_task_isolated(cccp_only_tree: Path, tmp_path: Path) -> None:
    """P1 task isolation: ``run_optimize`` executes with acp imports blocked.

    Plan todo 18: the optimize task core runs end-to-end on the stub backend
    seam in both isolation variants and dispatches normal vs TS capability
    (``optimize`` / ``transition_state_opt``) from ``mode``/``TsSpec``.
    """
    for variant in _VARIANTS:
        data = _require_ok(_run_probe(cccp_only_tree, tmp_path, "optimize_task", variant))
        assert data["normal"]["status"] == "completed", variant
        assert data["normal"]["methods"] == ["optimize"], variant
        assert data["normal"]["optimization_status"] == "converged", variant
        assert data["ts"]["status"] == "completed", variant
        assert data["ts"]["methods"] == ["transition_state_opt"], variant
        assert data["ts"]["optimization_status"] == "converged", variant


def test_protocol_parse_isolated(cccp_only_tree: Path, tmp_path: Path) -> None:
    """P0: protocol parsing without a recalc_hess override succeeds isolated.

    Touches ``cccp.core.protocols.resolve_protocol_spec`` (the
    ``protocols.py:273-283`` Hessian-policy region without an override, which
    must not require acp).  Green today — no xfail mark.
    """
    for variant in _VARIANTS:
        data = _require_ok(_run_probe(cccp_only_tree, tmp_path, "protocol_plain", variant))
        assert data["name"] == "lite", variant
        assert data["opt_recalc_hess"] == "None", variant


def test_protocol_parse_recalc_hess_override_isolated(cccp_only_tree: Path, tmp_path: Path) -> None:
    """P0: protocol parsing with a ``recalc_hess`` override succeeds isolated.

    Touches ``cccp.core.protocols.resolve_protocol_spec`` lines 273-283 with
    ``levels={"optimization": {"recalc_hess": 0}}`` (normalises through the
    shared Hessian policy; must not require acp).  Green since todo 6
    (protocols.py imports ``cccp.qc.hessian_policy``).
    """
    for variant in _VARIANTS:
        data = _require_ok(_run_probe(cccp_only_tree, tmp_path, "protocol_override", variant))
        assert data["name"] == "lite", variant
        assert data["opt_recalc_hess"] == "0", variant


def test_dlpno_aux_consistency_isolated(cccp_only_tree: Path, tmp_path: Path) -> None:
    """P0: DLPNO-CCSD(T) auxJ/auxC values identical isolated vs integrated.

    Exact call: ``ORCAInterface({}, method="B3LYP", basis="def2-SVP")
    ._build_input_blocks(calc_type="sp", method="DLPNO-CCSD(T)",
    basis="def2-TZVPP")`` — once with the ``acp`` blocker active (isolated,
    appendix B) and once after unblocking (integrated, acp importable).  The
    two rendered aux values must match.  Also cross-checks that the isolated
    values do not depend on how the isolated environment was constructed.
    Green since the METHOD_META single-sourcing removed the isolated
    degradation (plan todo 7 / delta D5).
    """
    blocked = _require_ok(_run_probe(cccp_only_tree, tmp_path, "dlpno", "blocked"))
    cccp_only = _require_ok(_run_probe(cccp_only_tree, tmp_path, "dlpno", "cccp-only"))
    assert cccp_only["isolated"] == blocked["isolated"], (
        "isolated aux depends on env construction: "
        f"{cccp_only['isolated']} vs {blocked['isolated']}"
    )
    assert blocked["isolated"] == blocked["integrated"], (
        f"DLPNO auxJ/auxC differ isolated vs integrated: "
        f"{blocked['isolated']} vs {blocked['integrated']}"
    )


def test_shermo_path_isolated(cccp_only_tree: Path, tmp_path: Path) -> None:
    """P0: shared Shermo adapter + normalization run with ``acp`` imports blocked.

    Plan todo 14 isolation probe: ``cccp.qc.shermo_adapter.execute_shermo``
    launches Shermo exactly once per request (spy on the shared runner seam,
    no subprocess) and ``cccp.qc.thermo_normalize`` yields the frozen 1atm/1M
    baselines — in both env variants, without the ``acp`` package.
    """
    for variant in _VARIANTS:
        data = _require_ok(_run_probe(cccp_only_tree, tmp_path, "shermo_path", variant))
        assert data["launches"] == [1, 1], f"{variant}: {data['launches']}"
        assert data["modules"] == ["cccp.qc.shermo_adapter", "cccp.qc.thermo_normalize"], variant
        atm = data["one_atm"]
        molar = data["one_m"]
        assert atm["gibbs"] == pytest.approx(-99.95, abs=1e-12), variant
        assert atm["source"] == "g_sum", variant
        assert atm["standard_state"] == "1atm", variant
        assert atm["conc"] is None, variant
        assert molar["gibbs"] == pytest.approx(-99.95 + _BASELINE_DELTA_HARTREE_298K, abs=1e-12), (
            variant
        )
        assert molar["source"] == "g_sum_plus_standard_state", variant
        assert molar["standard_state"] == "1M", variant
        assert molar["delta_hartree"] == pytest.approx(_BASELINE_DELTA_HARTREE_298K, abs=1e-12), (
            variant
        )
        assert molar["conc"] == 1.0, variant


def test_no_acp_imports_in_cccp() -> None:
    """Static AST scan: ``src/cccp/**`` contains no import of ``acp``.

    Walks every ``ast.Import`` / ``ast.ImportFrom`` node (indentation,
    function-level and ``TYPE_CHECKING`` imports are all visited by
    ``ast.walk``).  Relative imports (``level > 0``) resolve inside ``cccp``
    (``acp`` is a sibling top-level package, unreachable relatively) and are
    reported separately, never flagged as reverse imports.
    """
    violations: list[str] = []
    relative_imports: list[str] = []
    for path in sorted(CCCP_SRC.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "acp" or alias.name.startswith("acp."):
                        violations.append(f"{rel}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    relative_imports.append(f"{rel}:{node.lineno}")
                    continue
                module = node.module or ""
                if module == "acp" or module.startswith("acp."):
                    names = ", ".join(alias.name for alias in node.names)
                    violations.append(f"{rel}:{node.lineno}: from {module} import {names}")
    assert not violations, (
        "src/cccp must not import the acp package (reverse dependency); "
        "fix the listed imports (plan todo 9):\n" + "\n".join(violations)
    )


def test_xfail_marks_are_strict_and_explicit() -> None:
    """Any xfail in this file must be strict + raises-bound — no sloppy masks.

    Guard against whole-file xfail and reason-less/raises-less xfail masks.
    A test without an xfail mark is fine (the post-remediation state).
    """
    module = sys.modules[__name__]
    module_marks = [mark for mark in getattr(module, "pytestmark", []) if mark.name == "xfail"]
    assert not module_marks, "whole-file xfail is forbidden; track each test individually"
    allowed_kwargs: list[dict[str, object]] = []
    for name, obj in sorted(vars(module).items()):
        if not name.startswith("test_") or not callable(obj):
            continue
        xfail_marks = [mark for mark in getattr(obj, "pytestmark", []) if mark.name == "xfail"]
        assert len(xfail_marks) <= 1, f"{name}: at most one xfail mark allowed"
        if not xfail_marks:
            continue
        kwargs = xfail_marks[0].kwargs
        assert kwargs.get("strict") is True, f"{name}: xfail must be strict"
        assert "raises" in kwargs, f"{name}: xfail must declare raises="
        assert "reason" in kwargs, f"{name}: xfail must declare reason="
        assert kwargs in allowed_kwargs, f"{name}: unexpected xfail kwargs {kwargs}"
