"""Shared helpers/constants for the split frontend sync test suite (T29).

Split from tests/test_frontend_sync.py (2026-10-18). Prologue helpers and the
cross-area helpers/constants live here so the per-area split files keep the
original test bodies byte-identical. Owned by the T29 slice; see
.omo/evidence/acp-parallel-plan-1-frontend/task-29-acp-parallel-plan-1-frontend.md.
"""
from __future__ import annotations

import re
from pathlib import Path

from typing_extensions import TypedDict

REPO_ROOT = Path(__file__).parents[1]
FRONTEND = REPO_ROOT / "frontend" / "ACP_Workbench_v2.html"
SERVER = REPO_ROOT / "src" / "acp" / "api" / "server.py"

# Structure-viewer extracted modules (todo 13)
FRONTEND_JS_DIR = REPO_ROOT / "frontend" / "js"
FRONTEND_CSS_DIR = REPO_ROOT / "frontend" / "css"
FRONTEND_WORKBENCH_CSS_DIR = FRONTEND_CSS_DIR / "workbench"
FRONTEND_WORKBENCH_JS_DIR = FRONTEND_JS_DIR / "workbench"

def read_workbench_scripts() -> str:
    """Read all decomposed workbench script files referenced by ACP_Workbench_v2.html in document order."""
    html_raw = FRONTEND.read_text(encoding="utf-8")
    chunks: list[str] = []
    for src in re.findall(r'<script[^>]+src=["\']([^"\']+\.js[^"\']*)["\']', html_raw):
        clean = src.split("?")[0]
        if clean.startswith(("js/workbench/", "js/client/", "js/visualization/")):
            p = REPO_ROOT / "frontend" / clean
            if p.is_file():
                chunks.append(p.read_text(encoding="utf-8"))
    return "\n".join(chunks)


_CACHED_SCRIPTS: str | None = None
_CACHED_CONTENT: str | None = None


def get_cached_workbench_scripts() -> str:
    global _CACHED_SCRIPTS
    if _CACHED_SCRIPTS is None:
        _CACHED_SCRIPTS = read_workbench_scripts()
    return _CACHED_SCRIPTS


def read_workbench_content() -> str:
    """Read the full workbench page representation (HTML skeleton + referenced workbench scripts).
    Used by sync assertions verifying cross-layer contracts between DOM structure and classic scripts."""
    global _CACHED_CONTENT
    if _CACHED_CONTENT is None:
        _CACHED_CONTENT = FRONTEND.read_text(encoding="utf-8") + "\n" + get_cached_workbench_scripts()
    return _CACHED_CONTENT


def read_workbench_css() -> str:
    """Read all stylesheets linked by ACP_Workbench_v2.html in cascade order."""
    html = read_workbench_content()
    css_chunks: list[str] = []
    for href in re.findall(r'<link[^>]+href=["\']([^"\']+\.css[^"\']*)["\']', html):
        clean = href.split("?")[0]
        path = REPO_ROOT / "frontend" / clean
        if path.is_file():
            css_chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(css_chunks)

FRONTEND_FILES: list[Path] = [
    FRONTEND,
    FRONTEND_JS_DIR / "geometry_store.js",
    FRONTEND_JS_DIR / "structure_viewer.js",
    FRONTEND_JS_DIR / "structure_editor.js",
    FRONTEND_JS_DIR / "vibration_viewer.js",
    FRONTEND_CSS_DIR / "structure_viewer.css",
]
if FRONTEND_WORKBENCH_CSS_DIR.is_dir():
    FRONTEND_FILES.extend(sorted(FRONTEND_WORKBENCH_CSS_DIR.glob("*.css")))

_I18N_KEY_RE = re.compile(r'"((?:energy|tab\.energy)\.[^"]+)":')
_NODES_I18N_KEY_RE = re.compile(r'"(nodes\.[^"]+)":')
_ZH_BLOCK_RE = re.compile(r'"zh-CN":\s*\{(.*?)\n\s*"en-US":', re.DOTALL)
_EN_BLOCK_RE = re.compile(r'"en-US":\s*\{(.*?)(?:\n\s*\};)', re.DOTALL)
_SP_JS = FRONTEND_JS_DIR / "structure_source_picker.js"


def viewer_creation_site_count() -> int:
    """Pin that no feature file creates a viewer directly (0 since T10) — all
    scenes go through the bounded ACPVisualization.scene instance, which holds
    the viewer via the shared ACPVisualization.mol3d adapter (FE-09). The regex
    also accepts the legacy raw $3Dmol.createViewer form so a regression would
    still be counted (and fail the == 0 budget pins below)."""
    source = "\n".join(path.read_text(encoding="utf-8")
                       for path in (FRONTEND_WORKBENCH_JS_DIR / "features").glob("*.js"))
    return len(re.findall(r"(?:\$3Dmol\.createViewer|ACPVisualization\.mol3d\.create)\(", source))


class _ProfileRecord(TypedDict, total=False):
    profile_id: str


def _extract_energy_keys(html: str, block_re: re.Pattern[str]) -> set[str]:  # type: ignore[type-arg]
    """Extract energy.* / tab.energy.* i18n keys from a single locale block."""
    m = block_re.search(html)
    if not m:
        return set()
    return set(_I18N_KEY_RE.findall(m.group(1)))


def _extract_nodes_keys(html: str, block_re: re.Pattern[str]) -> set[str]:  # type: ignore[type-arg]
    """Extract nodes.* i18n keys from a single locale block."""
    m = block_re.search(html)
    if not m:
        return set()
    return set(_NODES_I18N_KEY_RE.findall(m.group(1)))


# ---------------------------------------------------------------------------
# UX spec v2.0 P0: single-column workspace + summary bar + strip + drawers
# ---------------------------------------------------------------------------


def _div_span(html: str, id_token: str) -> tuple[int, int]:
    """Return the (start, end) character span of the <div> carrying id_token."""
    token_pos = html.index(id_token)
    start = html.rindex("<div", 0, token_pos)
    depth = 0
    pos = start
    while True:
        next_open = html.find("<div", pos)
        next_close = html.find("</div>", pos)
        if next_close == -1:
            raise AssertionError(f"unbalanced div nesting while scanning {id_token}")
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + len("<div")
        else:
            depth -= 1
            pos = next_close + len("</div>")
            if depth == 0:
                return start, pos


# ---------------------------------------------------------------------------
# Todo 19: Phase-A contract tests + i18n completeness
# ---------------------------------------------------------------------------
# This section consolidates all phase-A structural locks for the structure
# viewer.  Every assertion here guards a contract established in todos 13-18.
# Removing or weakening any assertion turns the corresponding test red.

_STRUCTURE_I18N_KEY_RE = re.compile(r'"(structure\.[^"]+)":')


def _extract_structure_keys(html: str, block_re: re.Pattern[str]) -> set[str]:  # type: ignore[type-arg]
    """Extract structure.* i18n keys from a single locale block."""
    m = block_re.search(html)
    if not m:
        return set()
    return set(_STRUCTURE_I18N_KEY_RE.findall(m.group(1)))


# ---------------------------------------------------------------------------
# Wave 5 / todo 27: vibration frequency inspector (ACPVibrationViewer)
# ---------------------------------------------------------------------------

_VIB_JS = FRONTEND_JS_DIR / "vibration_viewer.js"


_SV_JS_PATH = FRONTEND_JS_DIR / "structure_viewer.js"


# ---------------------------------------------------------------------------
# Wave 5 / todo 30: mutual exclusion + equilibrium restore + teardown
# ---------------------------------------------------------------------------

_EDITOR_JS_PATH = FRONTEND_JS_DIR / "structure_editor.js"
