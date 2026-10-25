"""Frontend sync page tests — nmr area.

Split from tests/test_frontend_sync.py (T29, 2026-10-18). Every test keeps
its original name and body; the old -> new mapping is
tests/test_frontend_sync.py::<name> -> tests/test_frontend_sync_nmr.py
::<name>. Shared helpers live in tests/_frontend_sync_shared.py.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap

import pytest

from tests._frontend_sync_shared import (
    _EN_BLOCK_RE,
    _ZH_BLOCK_RE,
    read_workbench_content,
)


def test_nmr_report_reader_unified_local_remote() -> None:
    """fetchNmrReportJson probes the todo-22 layout first and unwraps remote previews."""
    html = read_workbench_content()
    region = _nmr_panel_region(html)

    assert "NMR_REPORT_PATHS" in region
    new_layout = region.index('"reports/nmr_report.json"')
    disk_layout = region.index('"RESULT/reports/nmr_report.json"')
    legacy_root = region.index('"nmr_report.json"')
    assert new_layout < disk_layout < legacy_root, (
        "probe order: manifest spelling, then disk path, then legacy root"
    )

    assert "fetchNmrReportFromPath" in region
    assert "unwrapNmrPreviewReport" in region
    assert "unwrapNmrFileReport" in region

    assert "?mode=report" in region, "remote reads must use the deterministic report mode"
    assert "apiRemote(" in region
    assert 'content.type === "json_report"' in region
    assert "content.report" in region


def test_nmr_null_render_em_dash_not_zero() -> None:
    """Missing winner/regression/boltzmann numerics render an em dash, never 0."""
    html = read_workbench_content()
    region = _nmr_panel_region(html)

    assert "nmrDisplayNumber" in region
    assert "nmrProbability" in region
    assert 'NMR_NULL_DISPLAY = "—"' in region

    assert "Number(cand.dp4_probability) || 0" not in region
    assert "Number(cand.dp5_probability) || 0" not in region
    assert "Number(winner.dp4).toFixed" not in region
    assert "Number(r.slope).toFixed" not in region
    assert "Number(c.boltzmann_weight).toFixed" not in region

    assert "|| 0" not in region, "NMR panel must not coerce null to 0"
    assert "?? 0" not in region, "NMR panel must not coerce null to 0"
    assert "NaN" not in region.replace("isFinite", ""), "null/NaN must never print as NaN"


def test_nmr_panel_linkage_hooks_present() -> None:
    """T40: atom chips/3D atoms ↔ assignment rows ↔ experimental peaks linked.

    The three surfaces share one selection state + one applier, exposed through
    stable data attributes.  The structure surface reuses the shared
    structure-viewer conventions (``registerCanvasLoader`` +
    ``sharedLoadGeometry`` + ``scheduleViewerFraming``) instead of inventing a
    parallel framing mechanism, and the panel never hand-rolls a trajectory /
    view projection (root AGENTS ANTI-PATTERN 21).
    """
    html = read_workbench_content()
    region = _nmr_panel_region(html)

    # Shared linkage state + single selection applier.
    assert "var nmrPanelState = {" in region
    assert "selectedAtom:" in region
    assert "selectedObservation:" in region
    assert "function nmrSelectLink(" in region
    assert "function nmrSelectAtom(" in region
    assert "function nmrSelectObservation(" in region
    assert "function nmrPanelApplySelection(" in region

    # Stable hooks on all three surfaces: structure atoms (chips + 3D pick),
    # assignment rows, experimental peak rows.
    for hook in (
        "data-nmr-atom",
        "data-nmr-observation",
        "data-nmr-candidate",
        "data-nmr-assign-row",
        "data-nmr-peak-row",
        "data-nmr-structure-viewer",
        "data-nmr-claim-atoms",
        "data-nmr-linkage-clear",
    ):
        assert hook in region, f"missing linkage hook {hook!r}"

    # Structure surface reuses the structure-viewer load/framing convention.
    assert 'registerCanvasLoader("nmr-structure"' in region
    assert "sharedLoadGeometry(" in region
    assert "scheduleViewerFraming(" in region
    assert ".center({}, 0)" not in region, "framing must use scheduleViewerFraming"
    assert ".zoomTo({}, 0)" not in region, "framing must use scheduleViewerFraming"

    # Geometry probing stays read-only through the existing file endpoints.
    assert "function nmrFetchCandidateGeometry(" in region
    assert "conformers/input.xyz" in region

    # Frames contract (ANTI-PATTERN 21): no hand-rolled projection payload.
    assert "view_type" not in region
    assert "series:" not in region


def test_nmr_panel_quality_and_risk_namespaces_separated() -> None:
    """T40: per-candidate quality surfaces T39 risk indicators without mixing
    them into the calibrated probability namespace, and legacy reports render
    the T24 note (schema_version absent)."""
    html = read_workbench_content()
    region = _nmr_panel_region(html)

    assert "function renderNmrCandidateQuality(" in region
    assert "function renderNmrPeaks(" in region
    assert "function nmrRiskSummary(" in region
    assert 't("nmr.quality.calibrated")' in region
    assert 't("nmr.quality.risk")' in region
    assert 't("nmr.quality.risk_note")' in region

    # Risk summary reads the diagnostics (risk) namespace ONLY.
    risk = region.split("function nmrRiskSummary(", 1)[1].split("\nfunction ", 1)[0]
    assert "signals" in risk
    assert "atom_support" in risk
    assert "leave_one_signal_out" in risk
    assert "probability" not in risk, "risk summary must not touch calibrated probability"
    assert "dp4_probability" not in risk

    # The calibrated line reads cand.probability typed state.
    quality = region.split("function renderNmrCandidateQuality(", 1)[1].split("\nfunction ", 1)[0]
    assert "cand.probability" in quality
    assert "nmrRiskSummary(" in quality

    # T24 handoff: legacy reports (no schema_version) render the explicit note.
    assert "report.schema_version == null" in region
    assert 't("nmr.legacy_note")' in region


def test_nmr_i18n_keys_complete_across_locales() -> None:
    """T40: every new nmr.* key exists in both zh-CN and en-US, paired."""
    html = read_workbench_content()

    zh_keys = _extract_nmr_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_nmr_keys(html, _EN_BLOCK_RE)

    assert zh_keys, "No nmr.* keys found in zh-CN block"
    assert en_keys, "No nmr.* keys found in en-US block"
    missing = _NMR_REQUIRED_T40_KEYS - zh_keys
    assert not missing, f"T40 keys missing from zh-CN: {sorted(missing)}"

    only_zh = zh_keys - en_keys
    only_en = en_keys - zh_keys
    assert not only_zh, f"Keys in zh-CN but missing from en-US: {sorted(only_zh)}"
    assert not only_en, f"Keys in en-US but missing from zh-CN: {sorted(only_en)}"


def test_nmr_panel_pure_helpers_behavior() -> None:
    """T40: the derived risk summary and label/claim helpers are correct.

    Behavior lock (node, source-extracted like the PES revision test): risk
    summary takes max |z| over finite z-scores only, counts out-of-domain atom
    support records and winner flips; labels follow the server's
    ``element+per-element-count`` convention; claim aggregation dedupes atoms
    and candidate indices per observation id.
    """
    html = read_workbench_content()
    region = _nmr_panel_region(html)

    def fn_source(name: str) -> str:
        body = region.split(f"function {name}(", 1)[1].split("\nfunction ", 1)[0]
        return f"function {name}(" + body

    script = "\n".join(
        (
            fn_source("nmrRiskSummary"),
            fn_source("nmrAtomLabelsFromSymbols"),
            fn_source("nmrPeakClaimsFromAssignments"),
        )
    ) + textwrap.dedent(
        """
        var diagnostics = {
          signals: [
            { z_score: 1.25 }, { z_score: -3.5 }, { z_score: null }, { z_score: "bad" }
          ],
          atom_support: [
            { atom_label: "C1", out_of_domain: true },
            { atom_label: "C2", out_of_domain: false }
          ],
          leave_one_signal_out: [
            { winner_changed: true }, { winner_changed: false }
          ]
        };
        var risk = nmrRiskSummary(diagnostics);
        var labels = nmrAtomLabelsFromSymbols(["C", "C", "H", "H", "H"]);
        var claims = nmrPeakClaimsFromAssignments({ candidates: [
          { index: 0, assignment: [
            { atom: "C1", element: "C", exp_ppm: 12.5, observation_id: "C:0" } ] },
          { index: 1, assignment: [
            { atom: "C2", element: "C", exp_ppm: 12.5, observation_id: "C:0" },
            { atom: "C1", element: "C", exp_ppm: 30.1, observation_id: "C:1" } ] }
        ]});
        console.log(JSON.stringify({
          risk: risk, labels: labels, nClaims: claims.length, firstClaim: claims[0]
        }));
        """
    )
    if not shutil.which("node"):
        pytest.skip("node is not available")
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["risk"] == {"nSignals": 4, "maxAbsZ": 3.5, "oodAtoms": 1, "looFlips": 1}
    assert payload["labels"] == ["C1", "C2", "H1", "H2", "H3"]
    assert payload["nClaims"] == 2
    assert payload["firstClaim"]["observation_id"] == "C:0"
    assert payload["firstClaim"]["candidate_indices"] == [0, 1]
    assert payload["firstClaim"]["atom_labels"] == ["C1", "C2"]


# ---------------------------------------------------------------------------
# T23 — unified NMR report retrieval + null rendering (gap G13)
# ---------------------------------------------------------------------------


def _nmr_panel_region(html: str) -> str:
    start = html.index("// ── NMR report viewer")
    # T18: the S1/S2 wizard slice modules now sit between nmr.js and wizard.js in
    # the concatenated page; bound the region at the first slice module banner so
    # it stays nmr.js-only.
    end = html.index(" * ACP Workbench — wizard slice S1", start)
    return html[start:end]


# ---------------------------------------------------------------------------
# T40 — NMR panel: structure atom ↔ assignment row ↔ experimental peak linkage
# ---------------------------------------------------------------------------

_NMR_I18N_KEY_RE = re.compile(r'"(nmr\.[^"]+)":')


#: Every user-facing string added by T40 must exist in BOTH locales.
_NMR_REQUIRED_T40_KEYS = {
    "nmr.legacy_note",
    "nmr.linkage.hint",
    "nmr.linkage.clear",
    "nmr.structure.title",
    "nmr.structure.loading",
    "nmr.structure.unavailable",
    "nmr.atoms.title",
    "nmr.atoms.hint",
    "nmr.atoms.no_atoms",
    "nmr.quality.title",
    "nmr.quality.evidence",
    "nmr.quality.coverage",
    "nmr.quality.calibrated",
    "nmr.quality.risk",
    "nmr.quality.risk_note",
    "nmr.quality.status.valid",
    "nmr.quality.status.invalid",
    "nmr.quality.status.evidence_insufficient",
    "nmr.quality.status.unavailable",
    "nmr.quality.status.not_applicable",
    "nmr.quality.status.placeholder",
    "nmr.quality.calibration.weighted",
    "nmr.quality.calibration.unweighted",
    "nmr.quality.calibration.out_of_domain",
    "nmr.quality.max_z",
    "nmr.quality.ood_atoms",
    "nmr.quality.loo_flips",
    "nmr.quality.no_diagnostics",
    "nmr.peaks.title",
    "nmr.peaks.no_data",
    "nmr.peaks.conflict",
    "nmr.peaks.summary",
    "nmr.col_obs",
    "nmr.col_nucleus",
    "nmr.col_claims",
}


def _extract_nmr_keys(html: str, block_re: re.Pattern[str]) -> set[str]:  # type: ignore[type-arg]
    """Extract nmr.* i18n keys from a single locale block."""
    m = block_re.search(html)
    if not m:
        return set()
    return set(_NMR_I18N_KEY_RE.findall(m.group(1)))
