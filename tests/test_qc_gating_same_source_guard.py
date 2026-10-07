"""BUG-4 guard — QC binary gating must not use ``shutil.which``.

The conftest ``RealQCSnapshot`` (``cccp.config.load_config`` +
``cccp.software.resolve_executable``, resolved once at collection) is the
single source of truth for QC-binary gating AND for the test bodies that
consume it.  A ``shutil.which`` reintroduced in any file of the frozen set
below re-creates the BUG-4 split-brain: the marker opens on ``~/.cccp.yaml``
while the body probes ``PATH`` — failing for configured-but-not-on-PATH
binaries, or silently matching a decoy such as the Python-script
``/usr/bin/orca``.

Scope is deliberately BOUNDED to the QC interface/backend gating files so
legitimate ``shutil.which`` uses elsewhere (node availability probes, CENSO
mock targets, ``resolve_executable``'s own internals) never false-positive.
Violations fail with the exact ``file:line``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent

# Frozen QC gating file set (BUG-4 deviation surface + conftest + smoke docs).
# A rename must fail the existence test below rather than silently drop the
# file from the scan; adding a file here is a deliberate act.
QC_GATING_FILES: tuple[str, ...] = (
    "conftest.py",
    "test_conftest_real_qc_gating.py",
    "test_qc_interfaces_crest.py",
    "test_qc_interfaces_xtb.py",
    "test_qc_interfaces_orca.py",
    "test_acp_backends.py",
    "test_orca_keyword_smoke.py",
    "test_cccp_real_qc_smoke.py",
    "test_acp_nmr_qc_smoke.py",
)


def shutil_which_lines(source: str) -> list[int]:
    """Line numbers of every ``shutil.which(...)`` call in *source*.

    Detects both spellings: ``import shutil`` + ``shutil.which(...)`` and
    ``from shutil import which`` + ``which(...)``.  Docstring mentions are
    string constants, not calls, and never match.
    """
    tree = ast.parse(source)
    module_aliases: set[str] = set()
    direct_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == "shutil"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "shutil":
            direct_names.update(
                alias.asname or alias.name for alias in node.names if alias.name in {"which", "*"}
            )
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "which":
            if isinstance(func.value, ast.Name) and func.value.id in module_aliases:
                lines.append(node.lineno)
        elif isinstance(func, ast.Name) and func.id in direct_names:
            lines.append(node.lineno)
    return sorted(set(lines))


@pytest.mark.parametrize("filename", QC_GATING_FILES)
def test_frozen_gating_file_exists(filename: str) -> None:
    assert (_TESTS_DIR / filename).is_file(), f"frozen QC gating file missing: {filename}"


@pytest.mark.parametrize("filename", QC_GATING_FILES)
def test_no_shutil_which_in_qc_gating_file(filename: str) -> None:
    source = (_TESTS_DIR / filename).read_text(encoding="utf-8")
    violations = [f"{filename}:{line}" for line in shutil_which_lines(source)]
    assert not violations, (
        "QC gating must use the conftest production resolver "
        "(cccp.config.load_config + cccp.software.resolve_executable), "
        "never shutil.which -> " + ", ".join(violations)
    )


def test_detector_is_not_vacuous() -> None:
    """Negative control: the AST scan must flag both which spellings."""
    assert shutil_which_lines("import shutil\nshutil.which('crest')\n") == [2]
    assert shutil_which_lines("from shutil import which\nwhich('x')\n") == [2]
    assert shutil_which_lines("import shutil2 as s\ns.which('x')\n") == []
    assert shutil_which_lines("from pathlib import Path\nPath('x').is_file()\n") == []
