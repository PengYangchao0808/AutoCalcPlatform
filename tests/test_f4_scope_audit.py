"""F4 scope-fidelity audit — deterministic audit test.

Verifies that changes from ``refactor-baseline`` to the working tree in the
interfaces/orca + backends/orca scope are limited to declared target regions
(opt_freq deletion, calc_type_map cleanup, NumFreq branch simplification),
with no algorithm-body additions or interface-scope overflows.

Groups:
    ① AST function-scope audit (deletion-only)
    ② Scheduler DB mechanism tables (fixture via migrations)
    ③ .omo/ directory — no new files
    ④ Catalog retired-ID final-state audit
    ⑤ Must-NOT-Have (grep gates + BatchOptimize no-IRC)

Amendments (plan-sanctioned, wave-2 backend wiring):
    A. ``backends/orca.py``: ``relaxed_scan()`` thin wrapper + 2 imports from
       ``cccp.qc.interfaces.{constraints,xtb_scan}`` — designated thin-adapter
       home per plan todo 11/16.  AST-thinness assertion enforced: no loops,
       no numeric arithmetic, no regex, must delegate to ``self._interface``.
    B. ``orca.py``: 2 modified lines in ``_build_input_blocks`` — mechanical
       consequence of deleting the ``"optfreq": "Opt Freq"`` calc_type_map entry.
       Modification only removes "Opt Freq" handling; no algorithm-body change.
    C. ``orca.py`` + ``backends/orca.py``: live optimization-trajectory
       streaming (2026-09 wave) — ``_run_orca`` gains an ``output_callback``
       streaming branch (threading/Callable imports) with call-site plumbing
       in ``optimize``/``transition_state_opt``; ``relaxed_scan`` gains
       multi-coordinate support via ``ReactionCoordinatePlan`` and the new
       ``_run_synchronous_relaxed_scan`` helper.  Sanctioned scopes are the
        named functions only; presence teeth assert the wave actually landed.
    D. ``constraints.py``: ``orca_constraint_block`` syntax fix (2026-09-04
       incident) — ORCA ``%geom`` indices are 0-based and the target value
       precedes the trailing ``C`` flag; the erroneous ``+1`` conversion is
       removed.  ``CoordinateSpec.constraint_at`` monitor-only error reformat
       rides along.  Teeth: the writer body must not contain ``+ 1`` and
       must emit the target before ``C``.
    E. ``orca.py`` + ``backends/orca.py``: electronic-state & CASSCF wave
       (2026-09-07, ``docs/ACP_Electronic_State_CASSCF_Design.md``) — the
       structured ``%scf`` renderer (HFTyp/GuessMix/FlipSpin/BrokenSym/MORead/
       STAB), spin-diagnostics parsing (``<S²>`` + Mulliken/Loewdin spin
       populations), CASSCF/NEVPT2 input rendering and output parsing, the
       ``ORCAInterface.casscf`` method, plus ``scf_options`` threading through
       the six existing input methods.  Sanctioned scopes: the contiguous
       module-level block from ``_SCF_OPTIONS_KEYS`` through
       ``parse_casscf_output``, the six method bodies, and the
       ``ORCABackend.casscf`` thin forwarder.  Teeth: the renderer must emit
        ``FlipSpin`` and the CASSCF parser must read the NEVPT2 results block.
    F. ``orca_ts.py`` + ``orca.py``: IRC path-capture wave (2026-09) — the
       ``IrcPathPoint`` / ``discover_irc_trajectory_files`` /
       ``parse_irc_trajectory_xyz`` / ``parse_irc_iteration_energies`` module
       block, the ``IrcResult.trajectory_files`` field, and the
       ``ORCAInterface.irc`` ``output_callback`` + ``discover_irc_trajectory_files``
       plumbing.  Sanctioned scopes: the named module block and the
       ``ORCAInterface.irc`` method only.  Teeth: the parsers and
       ``IrcPathPoint`` must exist, ``parse_irc_endpoints`` must survive, and
       ``ORCAInterface.irc`` must accept ``output_callback``.
    G. ``orca_ts.py`` + ``orca.py`` + ``hess_file.py``(NEW): TS Mode wave
       (2026-09-22, ``docs/ACP_TSMode_Optimization_Implementation_Plan.md``) —
       ``ts_geom_block`` read-Hessian input generation (``InHess Read`` /
       ``InHessName`` / read+calculate guard / TS_Mode int type check),
       ``TsOptResult`` ``energy``/``frequencies`` aliases,
       ``ORCAInterface.transition_state_opt`` ``hess_file`` staging, and the
       pure ``.hess`` parser module.  Teeth: ``InHess Read`` + guard must
       render, ``transition_state_opt`` must stage ``hess_file``,
       ``parse_ts_mode_vectors`` must survive.
    H. ``orca.py``: Hessian policy relocation (2026-10-04, plan todo 6) —
       the ``Recalc_Hess`` graded-default policy moves verbatim to
       ``cccp.qc.hessian_policy``; ``orca.py`` gains the direct
       ``from cccp.qc.hessian_policy import resolve_recalc_hess`` import and
       ``_get_resolver`` drops its lazy ``acp`` import while keeping the
       module-level cache.  Sanctioned scopes: the import line plus the
       ``# --- Hessian resolver`` comment block through
       ``_resolve_recalc_hess_lazy``.  Teeth: no ``acp`` import inside
       ``_get_resolver``, cached cccp assignment must remain.
    I. ``orca.py``: METHOD_META query relocation (2026-10-04, plan todo 7) —
       ``_resolve_method_meta`` drops its lazy ``acp.catalog`` import and
       queries ``cccp.qc.method_meta.method_meta`` (single-source METHOD_META,
       delta D5).  Sanctioned scopes: the ``from cccp.qc.method_meta import``
       line plus the ``_resolve_method_meta`` function in baseline and
       worktree.  Teeth: no ``acp`` import inside ``_resolve_method_meta``,
       the body must call ``method_meta(``, and the lookup must survive.
    J. ``interfaces/base.py`` + ``acp/backends/orca.py``: backends move
       (2026-10-04, plan todo 12) — the QCResult merge adds ``to_qc_result``
       to ``cccp/qc/interfaces/base.py`` (single definition; the backend
       layer re-exports it), and ``src/acp/backends/orca.py`` becomes a pure
       re-export shim for the implementation moved verbatim to
       ``src/cccp/backends/orca.py``.  Sanctioned scopes: the ``to_qc_result``
       function in ``interfaces/base.py`` and the shim body of
       ``acp/backends/orca.py``.  Teeth: ``QCResult`` keeps its full field
       set as the only definition, the shim must be a pure re-export, and
       the moved ``ORCABackend`` must keep ``relaxed_scan`` thinness and the
       ``casscf`` delegation (Amendment A/E teeth redirected to the new
       station) plus ``A is B`` identity between the two module paths.
"""

from __future__ import annotations

import ast
import inspect
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Re-baselined for the acp→cccp architecture remediation (Wave 0, todo 1):
# the audit now measures deltas from the remediation starting point
# main@2a23b93 (original remediation baseline 88def44 archived in evidence).
# The historical `refactor-baseline` ref belonged to the 2026-05 F4 wave and
# is not present in this clone.  The scope redirect onto the migration files
# is delivered by tier ⑥ (todo 16); tier ① keeps the QC interface layer.
BASELINE = "2a23b93"
ALLOWED_PY = frozenset(
    {
        "src/cccp/qc/interfaces/orca.py",
        "src/cccp/qc/interfaces/orca_ts.py",
        "src/cccp/qc/interfaces/constraints.py",
        "src/cccp/qc/interfaces/hess_file.py",
        "src/cccp/qc/interfaces/base.py",
        "src/acp/backends/orca.py",
    }
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _git(*args: str) -> str:
    """Run a git command and return stdout."""
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
    ).stdout


def _changed_files() -> set[str]:
    """Files changed between *BASELINE* and worktree in the audited scope."""
    out = _git(
        "diff",
        "--name-only",
        BASELINE,
        "--",
        "src/cccp/qc/interfaces/",
        "src/acp/backends/orca.py",
    )
    return set(out.strip().splitlines()) if out.strip() else set()


def _diff_hunks(
    path: str,
) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Parse unified diff for *path*.

    Returns ``(added, deleted)`` where each entry is
    ``(line_number_in_respective_version, content)``.
    """
    out = _git("diff", BASELINE, "--", path)
    added: list[tuple[int, str]] = []
    deleted: list[tuple[int, str]] = []
    old = new = 0
    for raw in out.splitlines():
        if raw.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            if m:
                old, new = int(m[1]), int(m[2])
        elif raw.startswith("-") and not raw.startswith("---"):
            deleted.append((old, raw[1:]))
            old += 1
        elif raw.startswith("+") and not raw.startswith("+++"):
            added.append((new, raw[1:]))
            new += 1
        elif raw.startswith("\\"):
            continue  # "\ No newline at end of file"
        else:
            old += 1
            new += 1
    return added, deleted


def _baseline_content(path: str) -> str:
    """Return file content at the baseline ref, or skip if absent."""
    r = subprocess.run(
        ["git", "show", f"{BASELINE}:{path}"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if r.returncode != 0:
        pytest.skip(f"Not in baseline: {path}")
    return r.stdout


def _worktree_content(path: str) -> str:
    """Return file content from the working tree."""
    return (ROOT / path).read_text()


def _func_ranges(src: str) -> dict[str, tuple[int, int]]:
    """Parse AST and return ``{name: (start_line, end_line)}``."""
    out: dict[str, tuple[int, int]] = {}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = (node.lineno, getattr(node, "end_lineno", node.lineno))
    return out


def _func_range(src: str, func_name: str, cls_name: str | None = None) -> tuple[int, int] | None:
    """Return ``(start, end)`` lines for *func_name* inside *cls_name*.

    Returns ``None`` if the function is not found.
    """
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ClassDef) and (cls_name is None or node.name == cls_name):
            for item in node.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == func_name
                ):
                    return (item.lineno, getattr(item, "end_lineno", item.lineno))
    return None


# ── Amendment A: relaxed_scan thinness assertion ──────────────────────────────

_THIN_BANNED_NODE_TYPES = (ast.For, ast.While, ast.AsyncFor)
_THIN_BANNED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)


def _assert_relaxed_scan_thin(worktree_src: str) -> list[str]:
    """AST-thinness assertion for ``ORCABackend.relaxed_scan``.

    Returns a list of violation strings (empty = thin / compliant).

    Thin means:
    * No ``for``/``while`` loops (no iteration over frames).
    * No arithmetic with numeric literals (no energy math).
    * No ``re.*`` calls (no parsing regex).
    * Must delegate to ``self._interface.relaxed_scan(...)``.
    """
    violations: list[str] = []
    tree = ast.parse(worktree_src)

    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ORCABackend":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "relaxed_scan":
                    target = item
                    break
    if target is None:
        return ["  relaxed_scan method not found in ORCABackend"]

    has_delegation = False
    for node in ast.walk(target):
        if isinstance(node, _THIN_BANNED_NODE_TYPES):
            violations.append(f"  relaxed_scan: loop at line {node.lineno}")
        if isinstance(node, ast.BinOp) and isinstance(node.op, _THIN_BANNED_BINOPS):
            left_num = isinstance(getattr(node, "left", None), ast.Constant) and isinstance(
                node.left.value, (int, float)
            )
            right_num = isinstance(getattr(node, "right", None), ast.Constant) and isinstance(
                node.right.value, (int, float)
            )
            if left_num or right_num:
                violations.append(f"  relaxed_scan: numeric arithmetic at line {node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "re":
                violations.append(f"  relaxed_scan: regex call at line {node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                node.func.attr == "relaxed_scan"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "_interface"
            ):
                has_delegation = True
    if not has_delegation:
        violations.append("  relaxed_scan: no delegation to self._interface.relaxed_scan()")

    return violations


# ── Amendment B: optfreq-removal mechanical modification check ────────────────


def _is_optfreq_removal_line(
    added_ln: int,
    added_txt: str,
    deleted: list[tuple[int, str]],
    build_range: tuple[int, int] | None,
) -> bool:
    """Check if *added_txt* at *added_ln* is a permissible optfreq-removal edit.

    Criteria:
    * Line falls within ``_build_input_blocks`` in the worktree.
    * Line does NOT contain ``"Opt Freq"``.
    * There exists a deleted line whose content contains ``"Opt Freq"``
      or ``"optfreq"`` (the removed calc_type_map entry or its usage).
    """
    stripped = added_txt.strip()
    if not stripped or stripped.startswith("#"):
        return True  # comments/blanks always ok
    if "Opt Freq" in added_txt or "optfreq" in added_txt.lower():
        return False  # must not re-introduce
    if build_range is None:
        return False
    bs, be = build_range
    if not (bs <= added_ln <= be):
        return False
    # Must have at least one deleted line containing "Opt Freq"
    return any("Opt Freq" in d_txt or "optfreq" in d_txt.lower() for _, d_txt in deleted)


# ── Amendment C: trajectory streaming + multi-coordinate scan (2026-09) ─────

_AMENDMENT_C_ORCA_FUNCS = (
    "_run_orca",
    "optimize",
    "transition_state_opt",
    "relaxed_scan",
    "_run_synchronous_relaxed_scan",
)
_AMENDMENT_C_IMPORT_MARKERS = ("threading", "Callable", "ReactionCoordinatePlan")


def _is_amendment_c_addition(added_ln: int, added_txt: str, worktree_src: str) -> bool:
    """Allowlisted ``orca.py`` additions for the trajectory/scan wave.

    * Import lines introducing ``threading`` / ``Callable`` /
      ``ReactionCoordinatePlan``.
    * Lines inside the sanctioned function scopes in the worktree.
    """
    stripped = added_txt.strip()
    is_import_line = (
        stripped.startswith(("import ", "from "))
        or stripped.rstrip(",") in _AMENDMENT_C_IMPORT_MARKERS
    )
    if is_import_line and any(marker in stripped for marker in _AMENDMENT_C_IMPORT_MARKERS):
        return True
    ranges = _func_ranges(worktree_src)
    return any(
        (fr := ranges.get(name)) is not None and fr[0] <= added_ln <= fr[1]
        for name in _AMENDMENT_C_ORCA_FUNCS
    )


def _is_amendment_c_deletion(ln: int, txt: str, baseline_src: str) -> bool:
    """Sanctioned ``orca.py`` deletions for the trajectory/scan wave.

    * The old synchronous-only ``_run_orca`` body and ``relaxed_scan``
      single-coordinate signature (baseline function ranges).
    * The two ``success = self._run_orca(input_file, output_file)`` call
      sites in ``optimize``/``transition_state_opt``.
    * The ``from collections.abc import Sequence`` import line (gains
      ``Callable``).
    """
    stripped = txt.strip()
    if stripped == "success = self._run_orca(input_file, output_file)":
        return True
    if stripped == "from collections.abc import Sequence":
        return True
    ranges = _func_ranges(baseline_src)
    return any(
        (fr := ranges.get(name)) is not None and fr[0] <= ln <= fr[1]
        for name in ("_run_orca", "relaxed_scan")
    )


_E_METHOD_SCOPES = (
    "_build_input_blocks",
    "_write_input",
    "optimize",
    "constrained_optimize",
    "single_point",
    "frequency",
    "casscf",
)


def _amendment_e_orca_block(worktree_src: str) -> tuple[int, int] | None:
    """Contiguous module-level E block: ``_SCF_OPTIONS_KEYS``..``parse_casscf_output``."""
    lines = worktree_src.splitlines()
    start = None
    for idx, line in enumerate(lines, start=1):
        if line.startswith("_SCF_OPTIONS_KEYS"):
            start = idx
            break
    end_range = _func_ranges(worktree_src).get("parse_casscf_output")
    if start is None or end_range is None:
        return None
    return (start, end_range[1])


def _is_amendment_e_addition(ln: int, txt: str, worktree_src: str, class_name: str | None) -> bool:
    """Sanctioned additions for the electronic-state & CASSCF wave.

    ``orca.py``: the contiguous module-level renderer/parsing block plus the
    ``scf_options``-threading lines inside the six existing input methods
    (and the new ``casscf`` method).  ``backends/orca.py``: the
    ``casscf`` thin forwarder only.
    """
    if class_name is None:
        block = _amendment_e_orca_block(worktree_src)
        if block is not None and block[0] <= ln <= block[1]:
            return True
        method_names = _E_METHOD_SCOPES[:-1]
        ranges = _func_ranges(worktree_src)
        if any(
            (fr := ranges.get(name)) is not None and fr[0] <= ln <= fr[1]
            for name in method_names
        ):
            return True
        casscf_method = _func_range(worktree_src, "casscf", "ORCAInterface")
        return casscf_method is not None and casscf_method[0] <= ln <= casscf_method[1]
    scope = _func_range(worktree_src, "casscf", class_name)
    return scope is not None and scope[0] <= ln <= scope[1]


def _is_amendment_e_deletion(ln: int, baseline_src: str) -> bool:
    """Sanctioned E deletions: docstring reflow inside the six input methods."""
    ranges = _func_ranges(baseline_src)
    return any(
        (fr := ranges.get(name)) is not None and fr[0] <= ln <= fr[1]
        for name in _E_METHOD_SCOPES[:-1]
    )


# ── Amendment F: IRC path capture (2026-09) ──────────────────────────────────

_F_ORCA_TS_SYMBOLS = (
    "IrcPathPoint",
    "discover_irc_trajectory_files",
    "parse_irc_trajectory_xyz",
    "parse_irc_iteration_energies",
    "parse_irc_ts_energy",
    "resolve_irc_ts_energy",
    "HARTREE_TO_KCAL",
    "irc_energy_from_comment",
)


def _amendment_f_orca_ts_block(src: str) -> tuple[int, int] | None:
    """Contiguous ``orca_ts.py`` F block: first IRC regex constant..TS resolver."""
    lines = src.splitlines()
    start = None
    for idx, line in enumerate(lines, start=1):
        if line.startswith("_IRC_TRJ_FILE_RE"):
            start = idx
            break
    end_range = _func_ranges(src).get("resolve_irc_ts_energy")
    if start is None or end_range is None:
        return None
    return (start, end_range[1])


def _is_amendment_f_orca_ts_addition(ln: int, txt: str, worktree_src: str) -> bool:
    """Allowlisted ``orca_ts.py`` additions for the IRC path-capture wave."""
    block = _amendment_f_orca_ts_block(worktree_src)
    if block is not None and block[0] <= ln <= block[1]:
        return True
    stripped = txt.strip()
    if stripped == "import os":
        return True
    if stripped == "trajectory_files: dict[str, Path] | None = None":
        return True
    return any(symbol in stripped for symbol in _F_ORCA_TS_SYMBOLS)


def _amendment_f_orca_ts_teeth(worktree: str) -> list[str]:
    """Teeth: the IRC parsers landed and the endpoint parser survives."""
    issues: list[str] = []
    ranges = _func_ranges(worktree)
    for name in (
        "parse_irc_trajectory_xyz",
        "parse_irc_iteration_energies",
        "parse_irc_ts_energy",
        "resolve_irc_ts_energy",
        "discover_irc_trajectory_files",
    ):
        if name not in ranges:
            issues.append(f"  Amendment F scope missing {name}")
    if "class IrcPathPoint" not in worktree:
        issues.append("  Amendment F scope missing IrcPathPoint")
    if "parse_irc_endpoints" not in ranges:
        issues.append("  Amendment F must preserve parse_irc_endpoints")
    return issues


def _is_amendment_f_orca_addition(ln: int, txt: str, worktree_src: str) -> bool:
    """Allowlisted ``orca.py`` additions for the IRC path-capture wave."""
    stripped = txt.strip()
    if "discover_irc_trajectory_files" in stripped:
        return True
    irc_range = _func_range(worktree_src, "irc", "ORCAInterface")
    return irc_range is not None and irc_range[0] <= ln <= irc_range[1]


def _is_amendment_f_orca_deletion(ln: int, txt: str, baseline_src: str) -> bool:
    """Sanctioned ``orca.py`` deletions live inside ``ORCAInterface.irc``."""
    irc_range = _func_range(baseline_src, "irc", "ORCAInterface")
    return irc_range is not None and irc_range[0] <= ln <= irc_range[1]


def _amendment_f_orca_teeth(worktree: str) -> list[str]:
    """Teeth: ``ORCAInterface.irc`` gained the streaming callback."""
    issues: list[str] = []
    irc_range = _func_range(worktree, "irc", "ORCAInterface")
    if irc_range is None:
        issues.append("  Amendment F scope ORCAInterface.irc missing")
    else:
        head = "\n".join(worktree.splitlines()[irc_range[0] - 1 : irc_range[0] + 24])
        if "output_callback" not in head:
            issues.append("  Amendment F: ORCAInterface.irc lacks output_callback")
    if "discover_irc_trajectory_files" not in worktree:
        issues.append("  Amendment F: discover_irc_trajectory_files wiring missing")
    return issues


def _target_orca(src: str) -> set[int]:
    """Target-region line numbers for baseline ``orca.py``."""
    lines = src.splitlines()
    fr = _func_ranges(src)
    tgt: set[int] = set()

    # 1. ORCAInterface.opt_freq method + trailing blank-line buffer
    if "opt_freq" in fr:
        s, e = fr["opt_freq"]
        tgt.update(range(s, min(e + 4, len(lines) + 1)))

    # 2. _build_input_blocks — lines referencing optfreq / Opt Freq + context
    if "_build_input_blocks" in fr:
        bs, be = fr["_build_input_blocks"]
        for i in range(bs, be + 1):
            txt = lines[i - 1]
            if "optfreq" in txt or "Opt Freq" in txt:
                for j in range(max(bs, i - 5), min(be + 1, i + 10)):
                    tgt.add(j)

    # 3. Module-level optfreq constants
    for i, ln in enumerate(lines, 1):
        if not ln[:1].strip():
            low = ln.lower()
            if ("optfreq" in low or "opt_freq" in low) and not ln.lstrip().startswith("#"):
                tgt.add(i)

    return tgt


# ── Amendment G: TS Mode wave (2026-09-22) ───────────────────────────────────
#
# ``docs/ACP_TSMode_Optimization_Implementation_Plan.md`` §9/§13: the TS Mode
# directed-OptTS wave adds read-Hessian input generation and .hess parsing:
#
# * ``orca_ts.py``: ``ts_geom_block`` gains ``hess_file_name`` (``InHess Read``
#   + ``InHessName``), the read/calculate guard, and an int type check on the
#   TS_Mode selector; ``TsOptResult`` gains read-only ``energy`` /
#   ``frequencies`` aliases for ``to_qc_result`` normalization; the M-index
#   docstring semantics are corrected (M 0 = lowest eigenvalue).
# * ``orca.py``: ``ORCAInterface.transition_state_opt`` stages ``hess_file``
#   as ``<name>.hess`` and refuses to combine it with ``'calculate'``.
# * ``hess_file.py``: NEW pure parser for ORCA ``.hess`` files (no subprocess).

_G_ORCA_TS_SYMBOLS = (
    "InHess",
    "hess_file_name",
    "np.integer",
    "def energy",
    "def frequencies",
    "Alias so",
    "resolve frequency-output indices",
)


def _is_amendment_g_orca_ts_addition(ln: int, txt: str, worktree_src: str) -> bool:
    """Allowlisted ``orca_ts.py`` additions for the TS Mode wave."""
    geom_range = _func_ranges(worktree_src).get("ts_geom_block")
    if geom_range is not None and geom_range[0] <= ln <= geom_range[1]:
        return True
    stripped = txt.strip()
    return any(symbol in stripped for symbol in _G_ORCA_TS_SYMBOLS)


def _amendment_g_orca_ts_teeth(worktree: str) -> list[str]:
    """Teeth: the read-Hessian input generation actually landed."""
    issues: list[str] = []
    geom_range = _func_ranges(worktree).get("ts_geom_block")
    if geom_range is None:
        issues.append("  Amendment G scope ts_geom_block missing")
    else:
        body = "\n".join(worktree.splitlines()[geom_range[0] - 1 : geom_range[1]])
        if "InHess Read" not in body:
            issues.append("  Amendment G: ts_geom_block lacks InHess Read")
        if "requires initial_hessian='read'" not in body:
            issues.append("  Amendment G: ts_geom_block lacks read/calculate guard")
    if "parse_ts_mode_vectors" not in _func_ranges(worktree):
        issues.append("  Amendment G must preserve parse_ts_mode_vectors")
    return issues


def _is_amendment_g_orca_addition(ln: int, txt: str, worktree_src: str) -> bool:
    """Allowlisted ``orca.py`` additions for the TS Mode wave."""
    ts_range = _func_range(worktree_src, "transition_state_opt", "ORCAInterface")
    return ts_range is not None and ts_range[0] <= ln <= ts_range[1]


def _is_amendment_g_orca_deletion(ln: int, baseline_src: str) -> bool:
    """Sanctioned ``orca.py`` deletions live inside ``transition_state_opt``."""
    ts_range = _func_range(baseline_src, "transition_state_opt", "ORCAInterface")
    return ts_range is not None and ts_range[0] <= ln <= ts_range[1]


def _amendment_g_orca_teeth(worktree: str) -> list[str]:
    """Teeth: ``transition_state_opt`` gained Hessian staging."""
    issues: list[str] = []
    ts_range = _func_range(worktree, "transition_state_opt", "ORCAInterface")
    if ts_range is None:
        issues.append("  Amendment G scope ORCAInterface.transition_state_opt missing")
        return issues
    body = "\n".join(worktree.splitlines()[ts_range[0] - 1 : ts_range[1]])
    if '"hess_file"' not in body:
        issues.append("  Amendment G: transition_state_opt lacks hess_file staging")
    if "cannot be combined with initial_hessian='calculate'" not in body:
        issues.append("  Amendment G: transition_state_opt lacks calculate guard")
    return issues


# ── Amendment H: Hessian policy relocation (2026-10-04, plan todo 6) ───────
#
# The ``Recalc_Hess`` graded-default policy moved verbatim to
# ``cccp.qc.hessian_policy``; ``orca.py`` now imports it directly and
# ``_get_resolver`` dropped the lazy ``acp`` import while keeping its
# module-level cache.  Sanctioned scopes: the ``resolve_recalc_hess`` import
# line plus the contiguous ``# --- Hessian resolver`` comment-block through
# ``_resolve_recalc_hess_lazy`` in both baseline and worktree.


def _amendment_h_block(src: str) -> tuple[int, int] | None:
    """Contiguous H block: Hessian-resolver comment .. `_resolve_recalc_hess_lazy` end."""
    lines = src.splitlines()
    start = None
    for idx, line in enumerate(lines, start=1):
        if line.startswith("# --- Hessian resolver"):
            start = idx
            break
    end_range = _func_ranges(src).get("_resolve_recalc_hess_lazy")
    if start is None or end_range is None:
        return None
    return (start, end_range[1])


def _is_amendment_h_addition(ln: int, txt: str, worktree_src: str) -> bool:
    """Allowlisted ``orca.py`` additions for the Hessian-policy relocation."""
    stripped = txt.strip()
    if stripped == "from cccp.qc.hessian_policy import resolve_recalc_hess":
        return True
    block = _amendment_h_block(worktree_src)
    return block is not None and block[0] <= ln <= block[1]


def _is_amendment_h_deletion(ln: int, baseline_src: str) -> bool:
    """Sanctioned ``orca.py`` deletions stay inside the baseline H block."""
    block = _amendment_h_block(baseline_src)
    return block is not None and block[0] <= ln <= block[1]


def _amendment_h_orca_teeth(worktree: str) -> list[str]:
    """Teeth: the resolver comes from cccp and the lazy acp import is gone."""
    issues: list[str] = []
    if "from cccp.qc.hessian_policy import resolve_recalc_hess" not in worktree:
        issues.append("  Amendment H: direct cccp.qc.hessian_policy import missing")
    resolver_range = _func_ranges(worktree).get("_get_resolver")
    if resolver_range is None:
        issues.append("  Amendment H scope missing _get_resolver")
    else:
        body = "\n".join(worktree.splitlines()[resolver_range[0] - 1 : resolver_range[1]])
        if "from acp" in body or "import acp" in body:
            issues.append("  Amendment H: _get_resolver still reaches for acp")
        if "_RESOLVER = resolve_recalc_hess" not in body:
            issues.append("  Amendment H: _get_resolver lost its cached cccp resolver assignment")
    if "_resolve_recalc_hess_lazy" not in _func_ranges(worktree):
        issues.append("  Amendment H must preserve _resolve_recalc_hess_lazy")
    return issues


# ── Amendment I: METHOD_META query relocation (2026-10-04, plan todo 7) ──
#
# ``_resolve_method_meta`` dropped its lazy ``acp.catalog`` import and now
# queries ``cccp.qc.method_meta.method_meta`` (single-source METHOD_META).
# Sanctioned scopes: the ``from cccp.qc.method_meta import method_meta``
# import line plus the whole ``_resolve_method_meta`` function (baseline and
# worktree ranges).


def _is_amendment_i_addition(ln: int, txt: str, worktree_src: str) -> bool:
    """Allowlisted ``orca.py`` additions for the METHOD_META relocation."""
    stripped = txt.strip()
    if stripped == "from cccp.qc.method_meta import method_meta":
        return True
    meta_range = _func_ranges(worktree_src).get("_resolve_method_meta")
    return meta_range is not None and meta_range[0] <= ln <= meta_range[1]


def _is_amendment_i_deletion(ln: int, baseline_src: str) -> bool:
    """Sanctioned ``orca.py`` deletions stay inside baseline ``_resolve_method_meta``."""
    meta_range = _func_ranges(baseline_src).get("_resolve_method_meta")
    return meta_range is not None and meta_range[0] <= ln <= meta_range[1]


def _amendment_i_orca_teeth(worktree: str) -> list[str]:
    """Teeth: the meta lookup comes from cccp and the acp import is gone."""
    issues: list[str] = []
    if "from cccp.qc.method_meta import method_meta" not in worktree:
        issues.append("  Amendment I: direct cccp.qc.method_meta import missing")
    meta_range = _func_ranges(worktree).get("_resolve_method_meta")
    if meta_range is None:
        issues.append("  Amendment I scope missing _resolve_method_meta")
        return issues
    body = "\n".join(worktree.splitlines()[meta_range[0] - 1 : meta_range[1]])
    if "from acp" in body or "import acp" in body:
        issues.append("  Amendment I: _resolve_method_meta still reaches for acp")
    if "method_meta(" not in body:
        issues.append("  Amendment I: _resolve_method_meta lost its cccp method_meta() call")
    return issues



def test_amendment_i_predicates_confined_and_negatives() -> None:
    """Amendment I is confined to the meta lookup — negative injection."""
    worktree = _worktree_content("src/cccp/qc/interfaces/orca.py")
    baseline_src = _baseline_content("src/cccp/qc/interfaces/orca.py")
    meta_range = _func_ranges(worktree).get("_resolve_method_meta")
    assert meta_range is not None

    assert not _is_amendment_i_addition(meta_range[0] - 1, "x = 1", worktree)
    assert not _is_amendment_i_addition(meta_range[0] - 1, "from acp.catalog import x", worktree)
    assert not _is_amendment_i_addition(meta_range[1] + 1, "    total += energy", worktree)
    baseline_meta = _func_ranges(baseline_src).get("_resolve_method_meta")
    assert baseline_meta is not None
    assert not _is_amendment_i_deletion(baseline_meta[0] - 1, baseline_src)
    assert not _is_amendment_i_deletion(baseline_meta[1] + 1, baseline_src)

    injected = worktree.replace(
        "    return method_meta(method)",
        "    from acp.catalog import METHOD_META\n    return method_meta(method)",
    )
    issues = _amendment_i_orca_teeth(injected)
    assert any("still reaches for acp" in issue for issue in issues)
    stripped = worktree.replace("from cccp.qc.method_meta import method_meta\n", "")
    issues = _amendment_i_orca_teeth(stripped)
    assert any("direct cccp.qc.method_meta import missing" in issue for issue in issues)


# ── Amendment J: backends move (plan todo 12) ───────────────────────────────

_QCRESULT_FIELDS = frozenset(
    {
        "success",
        "energy",
        "coordinates",
        "symbols",
        "converged",
        "output_file",
        "log_file",
        "freq_log_file",
        "error_message",
        "frequencies",
        "has_frequencies",
        "zpe",
        "enthalpy",
        "gibbs",
        "entropy",
        "metadata",
    }
)


def _is_pure_reexport_shim(src: str) -> bool:
    """True when *src* is a pure re-export shim (docstring/imports/__all__)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if targets == ["__all__"] and isinstance(node.value, (ast.List, ast.Tuple)):
                continue
            return False
        return False
    return True


def _amendment_j_base_teeth(worktree: str) -> list[str]:
    """Teeth for the QCResult merge in ``cccp/qc/interfaces/base.py``."""
    issues: list[str] = []
    try:
        tree = ast.parse(worktree)
    except SyntaxError as exc:
        return [f"  Amendment J: base.py does not parse: {exc}"]
    qcresult_classes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "QCResult"
    ]
    if len(qcresult_classes) != 1:
        issues.append(
            f"  Amendment J: QCResult must have exactly one definition, found {len(qcresult_classes)}"
        )
    else:
        fields = {
            item.target.id
            for item in qcresult_classes[0].body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
        }
        missing = _QCRESULT_FIELDS - fields
        if missing:
            issues.append(f"  Amendment J: QCResult missing fields {sorted(missing)}")
    if _func_ranges(worktree).get("to_qc_result") is None:
        issues.append("  Amendment J: to_qc_result missing from interfaces/base.py")
    if "from acp" in worktree or "import acp" in worktree:
        issues.append("  Amendment J: interfaces/base.py must not import acp")
    return issues


def _amendment_j_backend_orca_teeth() -> list[str]:
    """Teeth for the ``acp/backends/orca.py`` shim conversion.

    The legacy body must have moved verbatim to ``cccp/backends/orca.py``
    (same class, same capability methods), the shim must be a pure re-export,
    and the moved ``ORCABackend`` must keep the Amendment A thinness and the
    Amendment E ``casscf`` delegation at the new station.
    """
    issues: list[str] = []
    shim = _worktree_content("src/acp/backends/orca.py")
    if not _is_pure_reexport_shim(shim):
        issues.append("  Amendment J: acp/backends/orca.py is not a pure re-export shim")
    moved_path = ROOT / "src/cccp/backends/orca.py"
    if not moved_path.is_file():
        return issues + ["  Amendment J: moved implementation cccp/backends/orca.py missing"]
    moved = moved_path.read_text(encoding="utf-8")
    backend_range = None
    for node in ast.walk(ast.parse(moved)):
        if isinstance(node, ast.ClassDef) and node.name == "ORCABackend":
            backend_range = (node.lineno, getattr(node, "end_lineno", node.lineno))
            methods = {
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            missing = {"relaxed_scan", "casscf", "single_point", "optimize", "frequency"} - methods
            if missing:
                issues.append(f"  Amendment J: moved ORCABackend missing methods {sorted(missing)}")
            break
    if backend_range is None:
        issues.append("  Amendment J: moved ORCABackend class missing")
    else:
        issues.extend(_assert_relaxed_scan_thin(moved))
        casscf_range = _func_range(moved, "casscf", "ORCABackend")
        if casscf_range is None:
            issues.append("  Amendment J: moved ORCABackend.casscf missing")
        else:
            body = "\n".join(moved.splitlines()[casscf_range[0] - 1 : casscf_range[1]])
            if "_interface.casscf" not in body:
                issues.append(
                    "  Amendment J: moved ORCABackend.casscf lost its _interface delegation"
                )
    return issues


def _amendment_j_identity_issues() -> list[str]:
    """Runtime ``A is B`` identity between shim and moved implementation."""
    probe = (
        "import acp.backends.orca as shim\n"
        "import cccp.backends.orca as moved\n"
        "assert shim.ORCABackend is moved.ORCABackend\n"
        "print('J_IDENTITY_OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False, cwd=ROOT
    )
    if result.returncode != 0 or "J_IDENTITY_OK" not in result.stdout:
        return [f"  Amendment J: shim/moved identity failed: {result.stderr.strip()[:200]}"]
    return []


def _target_backend(src: str) -> set[int]:
    """Target-region line numbers for baseline ``backends/orca.py``."""
    lines = src.splitlines()
    fr = _func_ranges(src)
    tgt: set[int] = set()
    if "opt_freq" in fr:
        s, e = fr["opt_freq"]
        tgt.update(range(s, min(e + 4, len(lines) + 1)))
    return tgt


# ── Amendment K: constraint plan serialization (2026-10-04, plan todo 18) ──
#
# ``OptimizeOptions.constraints`` carries a ``ReactionCoordinatePlan``; the
# JSON-style ``to_dict()`` counterparts of the existing ``from_dict()``
# parsers are added on ``CoordinateSpec`` / ``ReactionCoordinatePlan``.
# Pure data projection (no algorithm change); sanctioned scope = the two
# ``to_dict`` method bodies only.


def _is_amendment_k_constraints_addition(ln: int, txt: str, worktree_src: str) -> bool:
    """Allowlisted ``constraints.py`` additions: the ``to_dict`` serializers."""
    for class_name in ("CoordinateSpec", "ReactionCoordinatePlan"):
        span = _func_range(worktree_src, "to_dict", class_name)
        if span is not None and span[0] <= ln <= span[1]:
            return True
    return False


def _amendment_k_constraints_teeth(worktree: str) -> list[str]:
    """Teeth: both serializers exist and project the from_dict input keys."""
    issues: list[str] = []
    coord = _func_range(worktree, "to_dict", "CoordinateSpec")
    if coord is None:
        issues.append("  Amendment K scope missing CoordinateSpec.to_dict")
    else:
        body = "\n".join(worktree.splitlines()[coord[0] - 1 : coord[1]])
        for key in ('"atoms"', '"kind"', '"role"'):
            if key not in body:
                issues.append(f"  Amendment K: CoordinateSpec.to_dict must emit {key}")
    plan = _func_range(worktree, "to_dict", "ReactionCoordinatePlan")
    if plan is None:
        issues.append("  Amendment K scope missing ReactionCoordinatePlan.to_dict")
    else:
        body = "\n".join(worktree.splitlines()[plan[0] - 1 : plan[1]])
        if "coordinate.to_dict()" not in body:
            issues.append(
                "  Amendment K: ReactionCoordinatePlan.to_dict must delegate to coordinates"
            )
    return issues


def test_amendment_k_predicates_confined_and_negatives() -> None:
    """Amendment K is confined to the two serializers — negative injection."""
    worktree = _worktree_content("src/cccp/qc/interfaces/constraints.py")
    plan_range = _func_range(worktree, "to_dict", "ReactionCoordinatePlan")
    assert plan_range is not None

    assert not _is_amendment_k_constraints_addition(plan_range[0] - 1, "x = 1", worktree)
    assert not _is_amendment_k_constraints_addition(plan_range[1] + 1, "    total += 1", worktree)

    injected = worktree.replace(
        '            "points": self.points,',
        '            "points": self.points + 1,\n            "smuggled": algorithm_change(),',
    )
    issues = _amendment_k_constraints_teeth(injected)
    assert not issues, "in-body edits stay inside the sanctioned scope"
    delegation = "[coordinate.to_dict() for coordinate in self.coordinates]"
    stripped = worktree.replace(delegation, "[]")
    issues = _amendment_k_constraints_teeth(stripped)
    assert any("must delegate to coordinates" in issue for issue in issues)
    removed = worktree.replace(
        "    def to_dict(self) -> dict[str, object]:", "    def _gone(self):", 1
    )
    issues = _amendment_k_constraints_teeth(removed)
    assert issues, "removing a serializer scope must fire the teeth"


# ── Amendment L: translation single-source render extraction (2026-10-05,
#    plan todo 25) ──────────────────────────────────────────────────────────
#
# The ``%geom`` body rendering delegates to
# ``cccp.qc.translation.render_opt_geom_lines`` (shared with the batch
# effective-config summary so display == execution) and ``_write_nmr_input``
# splits its construction half into ``_build_nmr_input_lines`` (F7 boundary:
# input construction vs file write).  Rendered output is byte-identical
# (goldens/route tests decide).  Sanctioned scopes: the
# ``from cccp.qc.translation import render_opt_geom_lines`` import line plus
# the ``_build_input_blocks`` / ``_write_input`` / ``_write_nmr_input`` /
# ``_build_nmr_input_lines`` bodies (worktree ranges for additions, baseline
# ranges for deletions).  Teeth: both delegations must actually land.

_AMENDMENT_L_FUNCS = (
    "_build_input_blocks",
    "_write_input",
    "_write_nmr_input",
    "_build_nmr_input_lines",
)


def _amendment_l_ranges(src: str) -> list[tuple[int, int]]:
    ranges = _func_ranges(src)
    return [ranges[name] for name in _AMENDMENT_L_FUNCS if name in ranges]


def _is_amendment_l_addition(ln: int, txt: str, worktree_src: str) -> bool:
    """Allowlisted ``orca.py`` additions for the render extraction."""
    stripped = txt.strip()
    if stripped == "from cccp.qc.translation import render_opt_geom_lines":
        return True
    return any(start <= ln <= end for start, end in _amendment_l_ranges(worktree_src))


def _is_amendment_l_deletion(ln: int, baseline_src: str) -> bool:
    """Sanctioned ``orca.py`` deletions stay inside the baseline L scopes."""
    return any(start <= ln <= end for start, end in _amendment_l_ranges(baseline_src))


def _amendment_l_orca_teeth(worktree: str) -> list[str]:
    """Teeth: the %geom and NMR-construction delegations must land."""
    issues: list[str] = []
    if "from cccp.qc.translation import render_opt_geom_lines" not in worktree:
        issues.append("  Amendment L: direct cccp.qc.translation import missing")
    ranges = _func_ranges(worktree)
    build_range = ranges.get("_build_input_blocks")
    if build_range is None:
        issues.append("  Amendment L scope missing _build_input_blocks")
    else:
        body = "\n".join(worktree.splitlines()[build_range[0] - 1 : build_range[1]])
        if "render_opt_geom_lines(" not in body:
            issues.append(
                "  Amendment L: _build_input_blocks must delegate %geom to render_opt_geom_lines"
            )
    if "_build_nmr_input_lines" not in ranges:
        issues.append("  Amendment L scope missing _build_nmr_input_lines")
    write_range = ranges.get("_write_nmr_input")
    if write_range is None:
        issues.append("  Amendment L scope missing _write_nmr_input")
    else:
        body = "\n".join(worktree.splitlines()[write_range[0] - 1 : write_range[1]])
        if "_build_nmr_input_lines(" not in body:
            issues.append(
                "  Amendment L: _write_nmr_input must delegate construction "
                "to _build_nmr_input_lines"
            )
    return issues


def test_amendment_l_predicates_confined_and_negatives() -> None:
    """Amendment L is confined to the render-extraction scopes — negative injection."""
    fp = "src/cccp/qc/interfaces/orca.py"
    worktree = _worktree_content(fp)
    baseline_src = _baseline_content(fp)
    build_range = _func_range(worktree, "_build_input_blocks")
    assert build_range is not None
    other_range = _func_range(worktree, "_run_orca", "ORCAInterface")
    assert other_range is not None

    assert _is_amendment_l_addition(build_range[0], "x = 1", worktree)
    assert _is_amendment_l_addition(
        1, "from cccp.qc.translation import render_opt_geom_lines", worktree
    )
    assert not _is_amendment_l_addition(other_range[0], "x = 1", worktree)
    assert not _is_amendment_l_addition(1, "from acp import x", worktree)

    base_build = _func_range(baseline_src, "_build_input_blocks")
    assert base_build is not None
    base_other = _func_range(baseline_src, "_run_orca", "ORCAInterface")
    assert base_other is not None
    assert _is_amendment_l_deletion(base_build[0], baseline_src)
    assert not _is_amendment_l_deletion(base_other[0], baseline_src)

    assert not _amendment_l_orca_teeth(worktree)
    stripped = worktree.replace("render_opt_geom_lines(", "_renamed_render(")
    issues = _amendment_l_orca_teeth(stripped)
    assert any("render_opt_geom_lines" in issue for issue in issues)
    removed = worktree.replace("_build_nmr_input_lines(", "_renamed_nmr(")
    issues = _amendment_l_orca_teeth(removed)
    assert any("_build_nmr_input_lines" in issue for issue in issues)


# ── Amendment M: plan full-fidelity round-trip (2026-10-05, plan todo 28) ──
#
# ``build_scan_plan``'s raw ``scan_plan`` channel round-trips a
# ``ReactionCoordinatePlan`` through ``from_dict``; the full-fidelity fields
# (``lambda_values`` / ``reference_geometries`` / ``fixed_endpoints`` /
# ``xtb_scc_max_iterations``) were silently dropped there, losing PES
# path_plan semantics.  The additions parse those OPTIONAL keys with the
# pre-extension defaults preserved (absent keys parse exactly as before);
# ``to_dict`` (Amendment K scope) emits them only when non-default, so the
# round-trip pair cannot drift.  Sanctioned scope: the
# ``ReactionCoordinatePlan.from_dict`` body only.

_AMENDMENT_M_ROUNDTRIP_KEYS = (
    "lambda_values",
    "reference_geometries",
    "fixed_endpoints",
    "xtb_scc_max_iterations",
)


def _is_amendment_m_constraints_addition(ln: int, txt: str, worktree_src: str) -> bool:
    """Allowlisted ``constraints.py`` additions: the from_dict round-trip."""
    span = _func_range(worktree_src, "from_dict", "ReactionCoordinatePlan")
    return span is not None and span[0] <= ln <= span[1]


def _amendment_m_constraints_teeth(worktree: str) -> list[str]:
    """Teeth: from_dict parses the extended keys and feeds them to cls()."""
    issues: list[str] = []
    span = _func_range(worktree, "from_dict", "ReactionCoordinatePlan")
    if span is None:
        issues.append("  Amendment M scope missing ReactionCoordinatePlan.from_dict")
        return issues
    body = "\n".join(worktree.splitlines()[span[0] - 1 : span[1]])
    for key in _AMENDMENT_M_ROUNDTRIP_KEYS:
        if f'data.get("{key}")' not in body:
            issues.append(f"  Amendment M: from_dict must parse {key}")
        if f"{key}={key}" not in body:
            issues.append(f"  Amendment M: from_dict must pass {key} to cls()")
    to_dict_span = _func_range(worktree, "to_dict", "ReactionCoordinatePlan")
    if to_dict_span is None:
        issues.append("  Amendment M round-trip pair missing ReactionCoordinatePlan.to_dict")
    else:
        to_dict_body = "\n".join(worktree.splitlines()[to_dict_span[0] - 1 : to_dict_span[1]])
        for key in _AMENDMENT_M_ROUNDTRIP_KEYS:
            if f'"{key}"' not in to_dict_body:
                issues.append(f"  Amendment M: to_dict must emit {key} for the round-trip")
    return issues


def test_amendment_m_predicates_confined_and_negatives() -> None:
    """Amendment M is confined to the from_dict round-trip — negative injection."""
    worktree = _worktree_content("src/cccp/qc/interfaces/constraints.py")
    plan_range = _func_range(worktree, "from_dict", "ReactionCoordinatePlan")
    assert plan_range is not None

    assert not _is_amendment_m_constraints_addition(plan_range[0] - 1, "x = 1", worktree)
    assert not _is_amendment_m_constraints_addition(plan_range[1] + 1, "    total += 1", worktree)

    injected = worktree.replace(
        '        raw_lambda = data.get("lambda_values") or ()',
        '        raw_lambda = data.get("lambda_values") or ()\n        smuggled = algorithm_change()',
    )
    issues = _amendment_m_constraints_teeth(injected)
    assert not issues, "in-body edits stay inside the sanctioned scope"
    stripped = worktree.replace("            fixed_endpoints=fixed_endpoints,\n", "")
    issues = _amendment_m_constraints_teeth(stripped)
    assert any("fixed_endpoints" in issue for issue in issues)
    unpaired = worktree.replace('        fixed_endpoints = bool(data.get("fixed_endpoints") or False)', "")
    issues = _amendment_m_constraints_teeth(unpaired)
    assert any("from_dict must parse fixed_endpoints" in issue for issue in issues)
    removed = worktree.replace(
        "    def from_dict(cls, data: dict[str, object]) -> ReactionCoordinatePlan:",
        "    def _gone(cls, data: dict[str, object]) -> ReactionCoordinatePlan:",
    )
    issues = _amendment_m_constraints_teeth(removed)
    assert issues, "removing the round-trip scope must fire the teeth"


# ── ① AST function-scope audit ──────────────────────────────────────────────

# The F4 refactor wave closed in 2026-05; these scope audits whitelist the
# files that wave was allowed to touch.  They were skipped because the
# whitelist fossilized 2026-05 boundaries and tripped on later QC-interface
# work.  Wave 0 of the acp→cccp architecture remediation (todo 1) re-based
# the audit on main@2a23b93 — the skip's documented exit condition — so the
# checks run again: any change to the QC interface layer from that point on
# must register a new sanctioned amendment here.  The scope redirect onto the
# migration files is tier ⑥ (todo 16).


def test_diff_only_allowed_py_files() -> None:
    """① Only ``.py`` files in the allowed set appear in the diff."""
    py = {f for f in _changed_files() if f.endswith(".py")}
    assert not (py - ALLOWED_PY), f"Unexpected .py files changed: {py - ALLOWED_PY}"


def test_algorithm_body_untouched() -> None:
    """① Every added line must be pure comment/blank — no algorithm-body changes.

    Two plan-sanctioned exceptions are encoded with teeth:

    **Amendment A** — ``backends/orca.py``: the ``relaxed_scan()`` thin wrapper
    (24 lines) + 2 imports from ``cccp.qc.interfaces.{constraints,xtb_scan}``
    are permitted.  AST-thinness assertion enforces: no loops, no numeric
    arithmetic, no regex calls, must delegate to ``self._interface``.

    **Amendment B** — ``orca.py``: 2 modified lines in ``_build_input_blocks``
    are the mechanical consequence of deleting the ``"optfreq": "Opt Freq"``
    entry.  Each must fall within ``_build_input_blocks``, must not contain
    ``"Opt Freq"``, and must pair with a deleted line that does.

    Per plan: any other non-comment added/modified line = FAIL.
    """
    violations: list[str] = []

    for fp in ALLOWED_PY:
        added, deleted = _diff_hunks(fp)

        # ── orca_ts.py: Amendment F + G ───────────────────────────────────
        if fp == "src/cccp/qc/interfaces/orca_ts.py":
            worktree = _worktree_content(fp)
            for ln, txt in added:
                stripped = txt.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if _is_amendment_f_orca_ts_addition(ln, txt, worktree):
                    continue
                if _is_amendment_g_orca_ts_addition(ln, txt, worktree):
                    continue
                violations.append(f"  {fp}:{ln}: {txt!r}")
            violations.extend(_amendment_f_orca_ts_teeth(worktree))
            violations.extend(_amendment_g_orca_ts_teeth(worktree))
            continue

        # ── acp/backends/orca.py: Amendment J (shim) or A + E ────────────
        if fp == "src/acp/backends/orca.py":
            worktree = _worktree_content(fp)
            if _is_pure_reexport_shim(worktree):
                violations.extend(_amendment_j_backend_orca_teeth())
                violations.extend(_amendment_j_identity_issues())
                continue
            method_range = _func_range(worktree, "relaxed_scan", "ORCABackend")
            thin_checked = False
            for ln, txt in added:
                stripped = txt.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                # Allow: import of ReactionCoordinatePlan / RelaxedScanResult
                if (
                    "ReactionCoordinatePlan" in txt or "RelaxedScanResult" in txt
                ) and txt.lstrip().startswith("from "):
                    continue
                # Allow: lines within relaxed_scan method body
                if method_range and method_range[0] <= ln <= method_range[1]:
                    if not thin_checked:
                        violations.extend(_assert_relaxed_scan_thin(worktree))
                        thin_checked = True
                    continue
                # Amendment E: the casscf thin forwarder (delegates to _interface)
                if _is_amendment_e_addition(ln, txt, worktree, "ORCABackend"):
                    continue
                # Anything else is a violation
                violations.append(f"  {fp}:{ln}: {txt!r}")
            continue

        # ── constraints.py: Amendment D ─────────────────────────────────
        if fp == "src/cccp/qc/interfaces/constraints.py":
            worktree = _worktree_content(fp)
            wt_ranges = _func_ranges(worktree)
            allowed_ranges = [
                wt_ranges[name]
                for name in ("orca_constraint_block", "constraint_at")
                if name in wt_ranges
            ]
            for ln, txt in added:
                stripped = txt.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if any(start <= ln <= end for start, end in allowed_ranges):
                    continue
                if _is_amendment_k_constraints_addition(ln, txt, worktree):
                    continue
                if _is_amendment_m_constraints_addition(ln, txt, worktree):
                    continue
                violations.append(f"  {fp}:{ln}: {txt!r}")
            violations.extend(_amendment_k_constraints_teeth(worktree))
            violations.extend(_amendment_m_constraints_teeth(worktree))
            # Teeth: 0-based writer, target-before-C syntax.
            writer = wt_ranges.get("orca_constraint_block")
            if writer is None:
                violations.append(f"  {fp}: Amendment D scope missing orca_constraint_block")
            else:
                body = "\n".join(worktree.splitlines()[writer[0] - 1 : writer[1]])
                if "+ 1" in body or "atom + 1" in body:
                    violations.append(f"  {fp}: orca_constraint_block still applies +1 indexing")
                if "{constraint.target:.8f} C" not in body:
                    violations.append(f"  {fp}: orca_constraint_block must emit target before C")
            continue

        # ── orca.py: Amendment B + C + E ──────────────────────────────────
        if fp == "src/cccp/qc/interfaces/orca.py":
            worktree = _worktree_content(fp)
            build_range = _func_range(worktree, "_build_input_blocks")
            optfreq_added_count = 0
            for ln, txt in added:
                stripped = txt.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if _is_amendment_c_addition(ln, txt, worktree):
                    continue
                if _is_amendment_e_addition(ln, txt, worktree, None):
                    continue
                if _is_amendment_f_orca_addition(ln, txt, worktree):
                    continue
                if _is_amendment_g_orca_addition(ln, txt, worktree):
                    continue
                if _is_amendment_h_addition(ln, txt, worktree):
                    continue
                if _is_amendment_i_addition(ln, txt, worktree):
                    continue
                if _is_amendment_l_addition(ln, txt, worktree):
                    continue
                if _is_optfreq_removal_line(ln, txt, deleted, build_range):
                    optfreq_added_count += 1
                    continue
                violations.append(f"  {fp}:{ln}: {txt!r}")
            # Teeth: at most 2 optfreq-removal lines permitted
            if optfreq_added_count > 2:
                violations.append(
                    f"  {fp}: {optfreq_added_count} optfreq-removal lines (max 2 expected)"
                )
            # Amendment C teeth: the sanctioned scopes must exist and deliver
            # the wave — otherwise the allowance is masking scope drift.
            wt_ranges = _func_ranges(worktree)
            if "_run_synchronous_relaxed_scan" not in wt_ranges:
                violations.append(
                    f"  {fp}: Amendment C scope missing _run_synchronous_relaxed_scan"
                )
            run_orca_range = _func_range(worktree, "_run_orca", "ORCAInterface")
            if run_orca_range is None or "output_callback" not in "\n".join(
                worktree.splitlines()[run_orca_range[0] - 1 : run_orca_range[0] + 8]
            ):
                violations.append(f"  {fp}: Amendment C scope _run_orca lacks output_callback")
            # Amendment E teeth: the electronic-state wave must actually land.
            wt_func_ranges = _func_ranges(worktree)
            scf_range = wt_func_ranges.get("render_scf_block")
            if scf_range is None or "FlipSpin" not in "\n".join(
                worktree.splitlines()[scf_range[0] - 1 : scf_range[1]]
            ):
                violations.append(f"  {fp}: Amendment E scope render_scf_block missing FlipSpin")
            casscf_range = wt_func_ranges.get("parse_casscf_output")
            if casscf_range is None or "_parse_nevpt2_roots" not in "\n".join(
                worktree.splitlines()[casscf_range[0] - 1 : casscf_range[1]]
            ):
                violations.append(
                    f"  {fp}: Amendment E scope parse_casscf_output missing NEVPT2 parsing"
                )
            if _func_range(worktree, "casscf", "ORCAInterface") is None:
                violations.append(f"  {fp}: Amendment E scope ORCAInterface.casscf missing")
            violations.extend(_amendment_f_orca_teeth(worktree))
            violations.extend(_amendment_g_orca_teeth(worktree))
            violations.extend(_amendment_h_orca_teeth(worktree))
            violations.extend(_amendment_i_orca_teeth(worktree))
            violations.extend(_amendment_l_orca_teeth(worktree))
            continue

        # ── interfaces/base.py: Amendment J (QCResult merge) ─────────────
        if fp == "src/cccp/qc/interfaces/base.py":
            worktree = _worktree_content(fp)
            func_range = _func_ranges(worktree).get("to_qc_result")
            for ln, txt in added:
                stripped = txt.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if func_range is not None and func_range[0] <= ln <= func_range[1]:
                    continue
                violations.append(f"  {fp}:{ln}: {txt!r}")
            violations.extend(_amendment_j_base_teeth(worktree))
            continue

        # ── hess_file.py: Amendment G (new pure-parser module) ───────────
        if fp == "src/cccp/qc/interfaces/hess_file.py":
            worktree = _worktree_content(fp)
            if "def parse_orca_hess_file" not in worktree:
                violations.append("  Amendment G scope parse_orca_hess_file missing")
            if "import subprocess" in worktree:
                violations.append("  Amendment G: hess_file.py must not use subprocess")
            continue

    assert not violations, "Non-comment added lines detected:\n" + "\n".join(violations)


def test_orca_ts_no_changes() -> None:
    """① ``orca_ts.py`` additions are confined to Amendments F (IRC path) + G (TS Mode)."""
    fp = "src/cccp/qc/interfaces/orca_ts.py"
    if fp not in _changed_files():
        return
    worktree = _worktree_content(fp)
    added, _ = _diff_hunks(fp)
    violations = [
        f"  {fp}:{ln}: {txt!r}"
        for ln, txt in added
        if txt.strip()
        and not txt.strip().startswith("#")
        and not _is_amendment_f_orca_ts_addition(ln, txt, worktree)
        and not _is_amendment_g_orca_ts_addition(ln, txt, worktree)
    ]
    violations.extend(_amendment_f_orca_ts_teeth(worktree))
    violations.extend(_amendment_g_orca_ts_teeth(worktree))
    assert not violations, "orca_ts.py additions outside Amendments F/G:\n" + "\n".join(violations)


def test_deleted_lines_in_target_regions() -> None:
    """① Every deleted line falls inside a declared target region."""
    checks = [
        ("src/cccp/qc/interfaces/orca.py", _target_orca),
        ("src/acp/backends/orca.py", _target_backend),
    ]
    for fp, builder in checks:
        tgt = builder(_baseline_content(fp))
        _, deleted = _diff_hunks(fp)
        bad_entries = [(ln, t) for ln, t in deleted if ln not in tgt]
        if fp == "src/cccp/qc/interfaces/orca.py":
            baseline_src = _baseline_content(fp)
            bad_entries = [
                (ln, t)
                for ln, t in bad_entries
                if not _is_amendment_e_deletion(ln, baseline_src)
            ]
            bad_entries = [
                (ln, t)
                for ln, t in bad_entries
                if not _is_amendment_f_orca_deletion(ln, t, baseline_src)
            ]
            bad_entries = [
                (ln, t)
                for ln, t in bad_entries
                if not _is_amendment_e_deletion(ln, baseline_src)
            ]
            bad_entries = [
                (ln, t)
                for ln, t in bad_entries
                if not _is_amendment_g_orca_deletion(ln, baseline_src)
            ]
            bad_entries = [
                (ln, t)
                for ln, t in bad_entries
                if not _is_amendment_h_deletion(ln, baseline_src)
            ]
            bad_entries = [
                (ln, t)
                for ln, t in bad_entries
                if not _is_amendment_i_deletion(ln, baseline_src)
            ]
            bad_entries = [
                (ln, t)
                for ln, t in bad_entries
                if not _is_amendment_l_deletion(ln, baseline_src)
            ]
        elif fp == "src/acp/backends/orca.py":
            if _is_pure_reexport_shim(_worktree_content(fp)):
                # Amendment J: the legacy body moved verbatim to
                # cccp/backends/orca.py — deletions are sanctioned once the
                # moved implementation and shim identity teeth pass.
                teeth = _amendment_j_backend_orca_teeth() + _amendment_j_identity_issues()
                assert not teeth, "Amendment J move teeth failed:\n" + "\n".join(teeth)
                bad_entries = []
            else:
                # Amendment C: relaxed_scan multi-coordinate rewrite lives in the
                # method body (baseline range); Amendment A covers its additions.
                baseline_src = _baseline_content(fp)
                scan_range = _func_range(baseline_src, "relaxed_scan", "ORCABackend")
                if scan_range is not None:
                    bad_entries = [
                        (ln, t)
                        for ln, t in bad_entries
                        if not scan_range[0] <= ln <= scan_range[1]
                    ]
        bad = [f"  {fp}:{ln}: {t!r}" for ln, t in bad_entries]
        assert not bad, "Deleted lines outside target regions:\n" + "\n".join(bad)


def test_constraints_deletions_in_amendment_d_regions() -> None:
    """① ``constraints.py`` deletions stay inside the Amendment D scopes."""
    fp = "src/cccp/qc/interfaces/constraints.py"
    baseline_src = _baseline_content(fp)
    ranges = _func_ranges(baseline_src)
    allowed = {
        line
        for name in ("orca_constraint_block", "constraint_at")
        if name in ranges
        for line in range(ranges[name][0], ranges[name][1] + 1)
    }
    _, deleted = _diff_hunks(fp)
    bad = [f"  {fp}:{ln}: {t!r}" for ln, t in deleted if ln not in allowed]
    assert not bad, "Deleted lines outside Amendment D regions:\n" + "\n".join(bad)


def test_worktree_opt_freq_absent() -> None:
    """① Target regions must not reappear in the working tree."""
    # Class methods
    for cls, fp in [
        ("ORCAInterface", "src/cccp/qc/interfaces/orca.py"),
        ("ORCABackend", "src/acp/backends/orca.py"),
    ]:
        tree = ast.parse(_worktree_content(fp))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == cls:
                names = {
                    d.name
                    for d in node.body
                    if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                assert "opt_freq" not in names, f"{cls}.opt_freq still present"

    # Module-level optfreq constants
    for fp in ("src/cccp/qc/interfaces/orca.py", "src/acp/backends/orca.py"):
        for i, ln in enumerate(_worktree_content(fp).splitlines(), 1):
            if not ln[:1].strip():
                low = ln.lower()
                if ("optfreq" in low or "opt_freq" in low) and not ln.lstrip().startswith("#"):
                    pytest.fail(f"{fp}:{i}: module-level optfreq reference: {ln!r}")


# ── ② Scheduler DB mechanism tables ─────────────────────────────────────────


def test_scheduler_db_mechanism_tables() -> None:
    """② ``mechanism_studies`` / ``decision_points`` / ``mechanism_projects`` exist.

    Built in-test via ``acp.scheduler.migrations.migrate``; fixture rows
    inserted and verified.
    """
    from acp.scheduler.migrations import migrate

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = Path(f.name)
    try:
        migrate(db)
        conn = sqlite3.connect(str(db))
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for t in ("mechanism_studies", "decision_points", "mechanism_projects"):
            assert t in tables, f"{t} table missing"

        # Fixture rows
        conn.execute(
            "INSERT INTO mechanism_studies "
            "(id, job_id, status, created_at, updated_at) "
            "VALUES ('s1', 'j1', 'active', '2026-01-01', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO decision_points (id, study_id, status, created_at) "
            "VALUES ('dp1', 's1', 'pending', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO mechanism_projects "
            "(project_id, name, created_at, updated_at) "
            "VALUES ('p1', 'test', '2026-01-01', '2026-01-01')"
        )
        conn.commit()

        assert conn.execute("SELECT count(*) FROM mechanism_studies").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM decision_points").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM mechanism_projects").fetchone()[0] == 1
        conn.close()
    finally:
        db.unlink(missing_ok=True)


def test_scheduler_db_jobs_node_columns() -> None:
    """② Migration 013 adds ``jobs.node_id`` / ``jobs.host`` (no backfill).

    JobStore init creates the ``jobs`` table then runs migrations; the 013
    ALTER must leave both columns present so dispatch can persist the
    chosen execution target (plan todo W3-T8).  Columns stay NULL for rows
    inserted before the migration — the read side falls back to
    ``result["node"]`` / ``spec.target_node``.
    """
    from acp.scheduler.store import JobStore

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = Path(f.name)
    try:
        JobStore(db)
        conn = sqlite3.connect(str(db))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "node_id" in columns, "migration 013 missing jobs.node_id"
        assert "host" in columns, "migration 013 missing jobs.host"

        conn.execute(
            "INSERT INTO jobs (id, workflow, name, status, work_dir, spec_json, "
            "created_at, updated_at) VALUES ('legacy-1', 'fake', 'legacy', "
            "'completed', '/tmp/x', '{}', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT node_id, host FROM jobs WHERE id='legacy-1'"
        ).fetchone()
        assert (row[0], row[1]) == (None, None), "migration 013 must not backfill"
        conn.close()
    finally:
        db.unlink(missing_ok=True)


# ── ③ .omo/ no new files ────────────────────────────────────────────────────


def test_omo_no_new_files() -> None:
    """③ ``.omo/`` must not be tracked by git.

    Checks the current index instead of ``--diff-filter=A`` history: past
    branches did commit ``.omo/`` artifacts before the ignore rule existed,
    and rewriting that history is not worth a force-push.
    """
    assert not _git("ls-files", "--", ".omo/").strip()


# ── ④ Catalog retired-ID final-state audit ──────────────────────────────────


def test_catalog_retired_ids() -> None:
    """④ ``final == baseline ∪ {optfreq, optfreqsp, Lowconfirm, Highconfirm}``.

    Also asserts ``baseline ⊆ final`` (zero drift on original ten).
    """
    base_path = ROOT / "tests/baseline/refactor-evidence/catalog-retired-ids.txt"
    final_path = ROOT / "tests/baseline/refactor-evidence/catalog-retired-ids-final.txt"
    base = set(base_path.read_text().splitlines())
    final = set(final_path.read_text().splitlines())
    expected = base | {"optfreq", "optfreqsp", "Lowconfirm", "Highconfirm"}
    assert final == expected, (
        f"Retired-ID mismatch: missing={expected - final}, extra={final - expected}"
    )
    assert base <= final, "Baseline IDs not subset of final"


# ── ⑤ Must-NOT-Have ────────────────────────────────────────────────────────


def test_grep_gate_final_forbidden_symbols() -> None:
    """⑤ ``check_grep_gates --gate final_forbidden_symbols src/acp`` exit 0."""
    r = subprocess.run(
        [
            sys.executable,
            "scripts/check_grep_gates.py",
            "--gate",
            "final_forbidden_symbols",
            "src/acp",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert r.returncode == 0, f"Gate failed:\n{r.stdout}\n{r.stderr}"


def test_batch_no_irc_invariants() -> None:
    """⑤ BatchOptimize engine source must not reference IRC."""
    from acp.calculations.batch import engine as batch_engine

    src = inspect.getsource(batch_engine).casefold()
    assert "irc" not in src, "BatchOptimize engine contains IRC references"


# ── ⑥ Migration station audit (todo 16) ─────────────────────────────────────

# The wave-2 scope redirect onto the migration files.  Checks here are
# worktree-structural and deliberately never read *BASELINE* content: files
# created after the baseline (the new cccp stations) are audited in full — a
# new path must not skip any key check.  ``_baseline_content``'s skip applies
# only to the ① deletion-region checks on baseline-present files.

MIGRATION_SCOPE = ("src/cccp/", "src/acp/")
MIGRATION_STATION_PREFIXES = (
    "src/cccp/",
    "src/acp/calculations/",
    "src/acp/backends/",
    "src/acp/chem/composition.py",
    "src/acp/core/registry.py",
    "src/acp/catalog.py",
    # todo 19: the frequency-science delegation target (single parse in cccp).
    "src/acp/results/orca_parser.py",
    # todo 25: CENSO template-line construction points (translation-layer consumers).
    "src/acp/workflows/energy_shared.py",
    "src/acp/confsearch/shared/helpers.py",
    # todo 28: PES/路径工作流任务化改线（XtbPathSearch/OrcaGradient + PES 扫描接线）。
    "src/acp/workflows/xtb_path.py",
    "src/acp/workflows/orca_gradient.py",
    # todos 26/27: Confsearch 能量/ensemble/xtbmd 协议与 NMR 任务化改线站点。
    "src/acp/workflows/energy.py",
    "src/acp/workflows/ensemble.py",
    "src/acp/workflows/nmr.py",
    "src/acp/workflows/xtbmd_censo_energy.py",
    "src/acp/workflows/xtbmd_md.py",
    "src/acp/confsearch/protocols/xtb_md.py",
    # acp-execution-integrity todos 1-19: scheduler/store/api 迁移驻点。
    "src/acp/scheduler/",
    "src/acp/storage/",
    "src/acp/api/",
)
MIGRATION_WORKFLOW_STATIONS = frozenset(
    {
        "src/acp/workflows/xtb_path.py",
        "src/acp/workflows/orca_gradient.py",
        "src/acp/workflows/energy.py",
        "src/acp/workflows/ensemble.py",
        "src/acp/workflows/nmr.py",
        "src/acp/workflows/xtbmd_censo_energy.py",
        "src/acp/workflows/xtbmd_md.py",
        "src/acp/confsearch/protocols/xtb_md.py",
    }
)
MIGRATION_SHIM_PY = frozenset(
    {
        "src/acp/backends/__init__.py",
        "src/acp/backends/base.py",
        "src/acp/backends/capabilities.py",
        "src/acp/backends/censo_backend.py",
        "src/acp/backends/crest.py",
        "src/acp/backends/external.py",
        "src/acp/backends/external_backend.py",
        "src/acp/backends/isostat_backend.py",
        "src/acp/backends/matrix.py",
        "src/acp/backends/molclus_backend.py",
        "src/acp/backends/orca.py",
        "src/acp/backends/registry.py",
        "src/acp/backends/xtb.py",
        "src/acp/calculations/primitives/_thermochemistry_input.py",
        "src/acp/calculations/primitives/_thermochemistry_support.py",
        "src/acp/chem/composition.py",
        "src/acp/core/registry.py",
    }
)


def _is_migration_shim(src: str) -> bool:
    """True when *src* is a compat re-export shell: docstring / imports /
    ``__all__`` and optional provenance aliases ``NAME = module.ATTR`` only."""
    if _is_pure_reexport_shim(src):
        return True
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if targets == ["__all__"] and isinstance(node.value, (ast.List, ast.Tuple)):
                continue
            if isinstance(node.value, ast.Attribute):
                continue
            return False
        return False
    return True


def _changed_migration_files() -> set[str]:
    """``.py`` files changed (or newly added, tracked or not) since *BASELINE*
    in the migration scope — untracked smuggled modules must not escape."""
    out = _git("diff", "--name-only", BASELINE, "--", *MIGRATION_SCOPE)
    untracked = _git("ls-files", "--others", "--exclude-standard", "--", *MIGRATION_SCOPE)
    combined = "\n".join(part for part in (out, untracked) if part.strip())
    return {line for line in combined.splitlines() if line.endswith(".py")}


def _migration_audit_issues(
    files: Iterable[str], contents: Mapping[str, str] | None = None
) -> list[str]:
    """Structural teeth over changed migration files (worktree only).

    * R1 station boundary — a change outside a registered migration station is
      an unaudited change and FAILS;
    * R2 new-station isolation — changed ``src/cccp/**`` files must not import
      the acp package (AST scan: covers TYPE_CHECKING and lazy imports);
    * R3 shim purity — declared compat shims stay pure re-export shells.
    * R4 workflow-station purity — the todo-28 workflow stations execute via
      the cccp task cores only; a ``get_backend``/``require_backend`` token
      re-opens the forbidden backend-direct path.
    """
    issues: list[str] = []
    for path in sorted(files):
        if not path.startswith(MIGRATION_STATION_PREFIXES):
            issues.append(f"  {path}: changed outside registered migration stations (unaudited)")
            continue
        src = (contents or {}).get(path)
        if src is None:
            src = (ROOT / path).read_text(encoding="utf-8")
        if path.startswith("src/cccp/"):
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "acp" or alias.name.startswith("acp."):
                            issues.append(f"  {path}:{node.lineno}: imports {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module == "acp" or module.startswith("acp."):
                        issues.append(f"  {path}:{node.lineno}: from {module} import names")
        if path in MIGRATION_SHIM_PY and not _is_migration_shim(src):
            issues.append(f"  {path}: compat shim gained a non-re-export body")
        if path in MIGRATION_WORKFLOW_STATIONS and re.search(
            r"\b(?:get_backend|require_backend)\b", src
        ):
            issues.append(f"  {path}: workflow station acquires a backend directly")
    return issues


def _in_baseline(path: str) -> bool:
    return (
        subprocess.run(
            ["git", "cat-file", "-e", f"{BASELINE}:{path}"],
            capture_output=True,
            cwd=ROOT,
        ).returncode
        == 0
    )


def test_migration_scope_audit_reaches_migration_files() -> None:
    """⑥ The audit actually processes the migration files — no vacuous skip.

    Key migration files changed since *BASELINE* and several of them do NOT
    exist at the baseline at all (created by the migration); the checks must
    still run for them.
    """
    changed = _changed_migration_files()
    expected = {
        "src/cccp/calculation/errors.py",
        "src/cccp/backends/orca.py",
        "src/acp/calculations/contracts.py",
        "src/acp/calculations/result_publication.py",
    }
    assert expected <= changed, f"migration audit scope lost files: {sorted(expected - changed)}"
    post_baseline = {path for path in expected if not _in_baseline(path)}
    assert post_baseline, "expected post-baseline files in the audited set (coverage proof)"
    assert _migration_audit_issues(changed) == [], "migration stations must be clean"


def test_migration_scope_catches_unaudited_change() -> None:
    """⑥ Negative ('deliberately missed audit'): a change outside the
    registered migration stations is flagged, not silently ignored."""
    issues = _migration_audit_issues(["src/acp/nmr/smuggled.py"])
    assert issues, "unaudited change outside migration stations was not detected"
    assert "unaudited" in issues[0]
    unregistered = _migration_audit_issues(["src/acp/scheduler_utils/rogue.py"])
    assert unregistered, "unregistered path under a migration prefix was not detected"
    assert "unaudited" in unregistered[0]


def test_migration_scope_catches_workflow_backend_direct() -> None:
    """⑥ Negative: a todo-28 workflow station re-acquiring a backend is
    flagged — the workflow stations execute via the cccp task cores only."""
    target = "src/acp/workflows/xtb_path.py"
    issues = _migration_audit_issues(
        [target], {target: "from acp.backends.registry import get_backend\n"}
    )
    assert issues, "workflow station backend-direct acquisition was not detected"
    assert "backend directly" in issues[0]
    assert not _migration_audit_issues([target], {target: "value = 1\n"})


def test_migration_scope_catches_acp_import_in_cccp() -> None:
    """⑥ Negative: a cccp file acquiring an acp import is flagged — and the
    file used here is absent from *BASELINE*, proving new paths never skip."""
    target = "src/cccp/calculation/errors.py"
    assert not _in_baseline(target), f"{target} unexpectedly present at {BASELINE}"
    issues = _migration_audit_issues(
        [target], {target: "from acp.calculations import contracts\n"}
    )
    assert issues and ("imports" in issues[0] or "from acp" in issues[0]), (
        f"acp import in a new-station file not detected: {issues}"
    )


def test_migration_scope_catches_smuggled_shim_body() -> None:
    """⑥ Negative: a declared compat shim gaining a real body is flagged."""
    target = "src/acp/core/registry.py"
    issues = _migration_audit_issues(
        [target],
        {
            target: (
                '"""Shim."""\n'
                "from cccp.core.registry import Registry\n"
                "\n"
                "def helper():\n"
                "    return 1\n"
            )
        },
    )
    assert issues and "shim" in issues[0], f"shim body not detected: {issues}"
