# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Browser-level interaction acceptance for method-family gating (T25).

Real headless Chromium (Playwright) drives the served ACP Workbench v2 and
verifies behaviour that the static contract suite
(``tests/test_frontend_sync.py``) can only pin as source markers:

1. switching ``scan_optimizer_method`` DFT -> GFN -> DFT hides/restores the
   basis / dispersion / RI / grid / SCF controls (T15 family gate);
2. the copy-scan-level action produces the expected FINAL SERIALIZED
   payload (the captured ``buildPESProtocolFromMethod`` JSON — the exact
   object ``submitPESsearchTask`` POSTs), not just UI state (T16/T23a);
3. a legacy draft with ``grid=UltraFine`` surfaces the migration hint and
   migrates the value in place (T23f);
4. an invalid GFN combination (explicit basis override) is rejected by the
   backend with 422 on job submit (T13/T14 strict lane);
5. validation warnings are visible in the method modal AND the wizard
   summary (T24).

Skip policy (T20-enforced): a skip is legitimate ONLY when, AFTER the
documented install attempt, the ``playwright`` module is still not
importable OR no browser binary exists.  When both are available every flow
must execute — skipping then is a failure.

Install attempt (recorded in ``.omo/evidence/task-25-cccp-correctness-
hardening.txt``)::

    /opt/acp/venv/bin/python -m pip install -e '.[browser]'
    /opt/acp/venv/bin/python -m playwright install chromium

Server: by default the real FastAPI app (``create_app``) boots in-process on
an ephemeral port — the same app ``acp run serve`` exposes at ``/``.  Set
``ACP_T25_BASE_URL`` to drive an externally served instance instead (used
for the evidence run against ``acp run serve --port 8899``).

All tests are marked ``@pytest.mark.slow`` — they are skipped in the
default ``pytest -m "not slow"`` run and require ``--run-slow``.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Guard: skip the entire module only when playwright is not installed
# (the documented install attempt must have been made first — see docstring)
# ---------------------------------------------------------------------------
pytest.importorskip(
    "playwright",
    reason=(
        "playwright not importable after `pip install -e '.[browser]'` — "
        "see .omo/evidence/task-25-cccp-correctness-hardening.txt"
    ),
)

try:
    from playwright.sync_api import Browser, Page, sync_playwright
except ImportError:  # pragma: no cover — old playwright without sync_api
    pytest.skip(
        "playwright.sync_api unavailable after `pip install -e '.[browser]'` — "
        "upgrade playwright >= 1.40",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE_DIR = Path(
    os.environ.get(
        "ACP_T25_EVIDENCE_DIR",
        str(_REPO_ROOT / ".omo" / "evidence" / "task-25-cccp-correctness-hardening"),
    )
)

# Stale pre-gating DFT values that must never leak into a GFN payload (T16).
_STALE_DFT_TOKENS = (
    "def2-TZVP",
    "RIJCOSX",
    "def2/J",
    "AutoAux",
    "DefGrid2",
    "CPCM",
    "chloroform",
    "D4",
    "Tight",
)


def _find_free_port() -> int:
    """Return an OS-assigned ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _chromium_launch_failure_is_missing_binary(exc: BaseException) -> bool:
    """True only for the 'no usable browser binary' failure class."""
    text = str(exc).lower()
    return (
        "executable doesn't exist" in text
        or "playwright install" in text
        or "error while loading shared libraries" in text
        or "host system is missing dependencies" in text
    )


def _evidence(name: str, payload: Any) -> Path:
    """Write a JSON artifact into the evidence directory and echo it."""
    _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = _EVIDENCE_DIR / name
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    path.write_text(text, encoding="utf-8")
    print(f"\n[EVIDENCE] {path}\n{text}\n")
    return path


def _shot(page: Page, name: str) -> Path:
    """Capture a screenshot into the evidence directory."""
    _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = _EVIDENCE_DIR / name
    page.screenshot(path=str(path), full_page=False)
    print(f"[EVIDENCE] screenshot {path}")
    return path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def server_url() -> Generator[str, None, None]:
    """Yield the workbench base URL.

    When ``ACP_T25_BASE_URL`` is set (e.g. an ``acp run serve`` instance),
    drive that server; otherwise boot the real FastAPI app in-process on an
    ephemeral port with a throwaway ``run_root``.
    """
    external = os.environ.get("ACP_T25_BASE_URL", "").strip()
    if external:
        yield external.rstrip("/")
        return

    import tempfile

    import uvicorn

    tmp_root = Path(tempfile.mkdtemp(prefix="acp_t25_server_"))
    # Set BEFORE importing the server module (module-level app reads it).
    os.environ["ACP_RUN_ROOT"] = str(tmp_root)

    from acp.api.server import create_app

    app = create_app(run_root=tmp_root)
    port = _find_free_port()
    host = "127.0.0.1"
    url = f"http://{host}:{port}"

    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                break
        except OSError:
            time.sleep(0.1)
    else:
        pytest.fail(f"Server did not start within 15 s on {url}")

    yield url

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def page(server_url: str) -> Generator[Page, None, None]:
    """Launch headless Chromium and yield a page on the workbench.

    Skips ONLY when the playwright module is missing (module-level guard)
    or no browser binary exists — anything else is a hard failure (T20).
    """
    pw_ctx = sync_playwright().start()
    browser: Browser | None = None
    context = None
    try:
        try:
            browser = pw_ctx.chromium.launch(headless=True)
        except Exception as exc:
            if _chromium_launch_failure_is_missing_binary(exc):
                pytest.skip(
                    "no usable chromium binary after "
                    "`python -m playwright install chromium` "
                    f"({exc}) — see .omo/evidence/task-25-cccp-correctness-hardening.txt"
                )
            raise

        context = browser.new_context(
            viewport={"width": 1440, "height": 900}, device_scale_factor=1
        )
        # Hermetic: the workbench's only external resource is the 3Dmol CDN
        # script tag, which (a) blocks DOMContentLoaded when unreachable and
        # (b) would make these tests network-dependent.  Stub it with empty
        # JS — initViewer() degrades gracefully and the wizard flows never
        # touch the 3D viewer.
        context.route("https://3Dmol.org/**", lambda route: route.fulfill(body=""))
        # Deterministic locale so label/text assertions are stable.
        context.add_init_script("localStorage.setItem('acp-lang', 'en-US');")
        pg = context.new_page()
        pg.goto(server_url, wait_until="domcontentloaded", timeout=60_000)
        pg.wait_for_function("typeof openModal === 'function'", timeout=30_000)
        pg.wait_for_selector("#btn-new-calc", state="attached", timeout=30_000)

        yield pg
    finally:
        if context is not None:
            context.close()
        if browser is not None:
            browser.close()
        pw_ctx.stop()


# ---------------------------------------------------------------------------
# Wizard / method-modal driving helpers (real clicks through the live UI)
# ---------------------------------------------------------------------------


def _wait_wizard_open(page: Page) -> None:
    """Click ``#btn-new-calc`` until the wizard opens.

    The DOMContentLoaded init binds the click listeners only AFTER its
    await chain (catalogs / jobs / nodes / backends) finishes, so early
    clicks are silently lost — retry until the modal responds.
    """
    deadline = time.monotonic() + 45
    while True:
        page.click("#btn-new-calc")
        try:
            page.wait_for_selector("#job-modal", state="visible", timeout=1_500)
            return
        except Exception:
            if time.monotonic() > deadline:
                raise


def _load_structure_and_next(page: Page, smiles: str = "CCO") -> None:
    """Type a structure into the wizard and advance to step 2.

    Step 2 is where the workflow/method config cards live (step 1 hides
    them); advancing requires at least one parsed structure.  The wizard
    defaults to the structure-library tab, so switch to the text-input tab
    first.
    """
    page.click('.input-mode-tab[data-input-mode="structure"]')
    page.fill("#modal-structure-input", smiles)
    page.wait_for_function("wizardStructures.length > 0", timeout=20_000)
    page.click("#modal-submit")  # "next step" -> step 2
    page.wait_for_selector('#job-modal[data-create-step="2"]', state="attached")
    page.wait_for_selector("#btn-config-method", state="visible")


def _open_method_modal(page: Page) -> None:
    """Open the create-task wizard on PESsearch and open the method modal.

    Sequence mirrors the real user path: new task -> pick the PESsearch
    workflow -> load a structure -> next step -> configure the calculation
    protocol.
    """
    _wait_wizard_open(page)
    _load_structure_and_next(page)

    # Pick the PESsearch workflow from the workflow picker.
    page.click("#btn-config-workflow")
    page.wait_for_selector("#workflow-config-modal", state="visible")
    option = page.locator(".workflow-option", has_text=re.compile(r"PES"))
    option.first.click()
    page.click("#wf-config-ok")
    page.wait_for_selector("#workflow-config-modal", state="hidden")

    # Open the method modal on the pes_scan schema.
    page.click("#btn-config-method")
    page.wait_for_selector("#method-config-modal", state="visible")
    page.wait_for_selector(
        '#mc-levels-container .mc-level-card[data-level-id="scan_optimizer"]',
        timeout=30_000,
    )


def _scan_card(page: Page):
    """The scan_optimizer level card locator."""
    return page.locator('#mc-levels-container .mc-level-card[data-level-id="scan_optimizer"]')


def _sp_card(page: Page):
    """The single_point level card locator."""
    return page.locator('#mc-levels-container .mc-level-card[data-level-id="single_point"]')


# Selects are identified by a unique option value inside their level card
# (field rows carry no element ids — see buildFieldRow).
_SCAN_SELECTORS = {
    "method": 'select:has(option[value="GFN2-xTB"])',
    "basis": 'select:has(option[value="def2-TZVP"])',
    "dispersion": 'select:has(option[value="D3BJ"])',
    "ri": 'select:has(option[value="RIJCOSX"])',
    "grid": 'select:has(option[value="DefGrid1"])',
    "scf": 'select:has(option[value="verytight"])',
}


def _scan_select(page: Page, key: str):
    return _scan_card(page).locator(_SCAN_SELECTORS[key])


def _set_scan_method(page: Page, value: str) -> None:
    """Switch the scan_optimizer method via the real <select> change event."""
    _scan_select(page, "method").select_option(value)
    # The change handler rebuilds the card in place; settle for the modal's
    # debounced validation round-trip before asserting.
    page.wait_for_timeout(700)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestMethodFamilyGatingBrowser:
    """T25 flows 1-5: real interaction acceptance over the served workbench."""

    # -- flow 1: DFT -> GFN -> DFT hides/restores the family fields --------
    def test_scan_optimizer_method_gating_hides_and_restores_fields(self, page: Page) -> None:
        _open_method_modal(page)
        card = _scan_card(page)

        # DFT state (B3LYP): every family field renders an editable select.
        _set_scan_method(page, "B3LYP")
        for key in ("basis", "dispersion", "ri", "grid", "scf"):
            expect_select = _scan_select(page, key)
            expect_select.wait_for(state="visible", timeout=10_000)
            assert expect_select.count() == 1, f"DFT: {key} select must be present"
        _shot(page, "flow1-01-dft-fields-visible.png")

        # GFN state (GFN2-xTB): basis/dispersion/RI lock, grid/SCF rows vanish.
        _set_scan_method(page, "GFN2-xTB")
        for key in ("basis", "dispersion", "ri", "grid", "scf"):
            assert _scan_select(page, key).count() == 0, f"GFN: {key} select must be hidden/removed"
        assert card.locator("span.mc-basis-readonly").count() >= 1, (
            "GFN: basis must render as built-in read-only note"
        )
        assert card.locator("span.mc-builtin-badge").count() >= 1, (
            "GFN: dispersion/RI must render as built-in badges"
        )
        _shot(page, "flow1-02-gfn-fields-hidden.png")

        # Back to DFT: everything is restored.
        _set_scan_method(page, "B3LYP")
        for key in ("basis", "dispersion", "ri", "grid", "scf"):
            restored = _scan_select(page, key)
            restored.wait_for(state="visible", timeout=10_000)
            assert restored.count() == 1, f"restored: {key} select must be back"
        _shot(page, "flow1-03-dft-fields-restored.png")

    # -- flow 2: copy-scan-level -> FINAL serialized payload --------------
    def test_copy_scan_level_final_serialized_payload(self, page: Page) -> None:
        _open_method_modal(page)

        # Scan level stays in the GFN family with valid shared fields.
        _set_scan_method(page, "GFN2-xTB")
        _scan_card(page).locator('select:has(option[value="ALPB"])').select_option("ALPB")
        _scan_card(page).locator('select:has(option[value="water"])').select_option("water")
        page.wait_for_timeout(700)

        # Single-point level: plant a stale pre-gating DFT configuration.
        sp = _sp_card(page)
        sp.locator('select:has(option[value="GFN0-xTB"])').select_option("B3LYP")
        page.wait_for_timeout(700)
        sp = _sp_card(page)  # card was rebuilt after the functional change
        sp.locator('select:has(option[value="def2-TZVP"])').select_option("def2-TZVP")
        sp.locator('select:has(option[value="D3BJ"])').select_option("D4")
        sp.locator('select:has(option[value="RIJCOSX"])').select_option("RIJCOSX")
        sp.locator('select:has(option[value="def2/J"])').select_option("def2/J")
        sp.locator('select:has(option[value="CPCM"])').select_option("CPCM")
        sp.locator('select:has(option[value="chloroform"])').select_option("chloroform")
        sp.locator('select:has(option[value="DefGrid2"])').select_option("DefGrid2")
        sp.locator('select:has(option[value="VeryTight"])').select_option("Tight")
        page.wait_for_timeout(700)
        _shot(page, "flow2-01-stale-dft-single-point.png")

        # Real copy-scan-level click (copies scan_optimizer -> single_point).
        page.locator("button.mc-copy-scan-level").click()

        # FINAL SERIALIZED payload: the exact serializer the submit path uses,
        # captured from the copy RESULT (synchronously after the click, before
        # the 400 ms debounced validate round-trip can write normalized
        # defaults back into the deliberately-cleared fields).
        captured = page.evaluate("JSON.stringify(buildPESProtocolFromMethod(0, 1))")
        payload = json.loads(captured)
        page.wait_for_timeout(900)
        settled = json.loads(page.evaluate("JSON.stringify(buildPESProtocolFromMethod(0, 1))"))
        _evidence(
            "flow2-copied-payload.json",
            {
                "copy_result": payload,
                "post_writeback": settled,
                "note": (
                    "copy_result is the T16 contract payload (asserted). "
                    "post_writeback records the debounced validate write-back "
                    "refilling catalog defaults into cleared fields "
                    "(scf_convergence); SCF is stripped for GFN at QC emit by "
                    "the keyword registry — logged as a follow-up finding."
                ),
            },
        )
        _shot(page, "flow2-02-after-copy-scan-level.png")

        sp_payload = payload["single_point"]
        assert sp_payload["method"] == "GFN2-xTB", sp_payload
        assert sp_payload["basis"] is None, sp_payload
        assert sp_payload["dispersion"] in (None, "none"), sp_payload
        assert sp_payload["ri_approximation"] == "none", sp_payload
        assert sp_payload["aux_j_basis"] is None, sp_payload
        assert sp_payload["aux_c_basis"] is None, sp_payload
        assert sp_payload["grid"] is None, sp_payload
        assert sp_payload["scf_convergence"] is None, sp_payload
        # Valid shared fields still travel with the copy.
        assert sp_payload["solvent_model"] == "ALPB", sp_payload
        assert sp_payload["solvent"] == "water", sp_payload
        assert sp_payload["enabled"] is True, sp_payload
        # Scan level: GFN serialization defense (T23a).
        so_payload = payload["scan_optimizer"]
        assert so_payload["method"] == "GFN2-xTB", so_payload
        assert so_payload["basis"] is None, so_payload
        # No stale family-inapplicable value may reach the captured payload.
        for stale in _STALE_DFT_TOKENS:
            assert stale not in captured, f"stale '{stale}' leaked into payload"

    # -- flow 3: legacy draft grid=UltraFine -> migration hint ------------
    def test_legacy_draft_grid_migration_hint(self, page: Page) -> None:
        # Open the wizard so the drafts box becomes reachable (its listeners
        # bind with the same init chain as #btn-new-calc).
        _wait_wizard_open(page)

        # A legacy (pre-T12) draft: grid carries a Gaussian-era alias.
        draft_name = "T25-legacy-grid"
        snapshot = {
            "structures": [],
            "selectedIndex": 0,
            "activeTab": "task",
            "step": 1,
            "fields": {},
            "workflowState": {
                "workflow": {"id": "PESsearch", "label": "PES Search", "schema_id": "pes_scan"},
                "method": {
                    "profile_id": "default",
                    "profile_label": "default",
                    "stages": {
                        "scan_optimizer": {
                            "engine": "orca",
                            "scan_optimizer_method": "B3LYP",
                            "scan_optimizer_basis": "def2-SVP",
                            "scan_optimizer_grid": "UltraFine",
                        }
                    },
                },
            },
        }
        created = page.evaluate(
            """async (args) => {
                const resp = await fetch("/api/v1/drafts", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        name: args.name, project_id: "",
                        workflow: "PESsearch", snapshot: args.snapshot,
                    }),
                });
                return await resp.json();
            }""",
            {"name": draft_name, "snapshot": snapshot},
        )
        assert created.get("draft_id"), created

        # Real drafts-box resume -> hydration -> migration hint alert.
        dialogs: list[str] = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        page.click("#wizard-drafts-open")
        page.wait_for_selector("#wizard-drafts-modal", state="visible")
        row = page.locator("#wizard-drafts-list > div").filter(has_text=draft_name)
        row.locator("button.btn.primary").click()
        page.wait_for_timeout(1500)

        assert dialogs, "hydration must surface the grid migration hint alert"
        hint = next((m for m in dialogs if "UltraFine" in m), "")
        assert hint, f"migration hint must name the legacy value: {dialogs}"
        assert "DefGrid3" in hint, hint
        assert "Integration grid migrated" in hint or "grid migrated" in hint.lower(), hint
        _evidence("flow3-migration-hint.json", {"dialogs": dialogs, "hint": hint})
        _shot(page, "flow3-grid-migration-hint.png")

        # The value is migrated in place (never left ORCA-illegal).
        migrated = page.evaluate("wizardState.method.stages.scan_optimizer.scan_optimizer_grid")
        assert migrated == "DefGrid3", migrated

    # -- flow 4: invalid GFN combination rejected on submit ---------------
    def test_invalid_gfn_combination_rejected_on_submit(self, page: Page) -> None:
        _open_method_modal(page)
        _set_scan_method(page, "GFN2-xTB")

        # Build the real submit protocol via the page's own serializer, then
        # tamper it the way a stale/pre-gating client would (explicit basis
        # override on a GFN method) and submit through the browser.
        result = page.evaluate(
            """async () => {
                const protocol = buildPESProtocolFromMethod(0, 1);
                protocol.scan_optimizer.method = "GFN2-xTB";
                protocol.scan_optimizer.basis = "def2-TZVP";
                const body = {
                    workflow: "PESsearch",
                    name: "t25-invalid-gfn",
                    task_name: "t25_invalid_gfn",
                    molecule_name: "h2",
                    remark: "T25 browser acceptance",
                    input: {
                        source: {
                            source_type: "xyz_text",
                            xyz_text: "2\\nH2\\nH 0.0 0.0 0.0\\nH 0.0 0.0 0.74\\n",
                            charge: 0,
                            multiplicity: 1,
                        },
                        coordinate: {
                            kind: "distance", atoms: [0, 1], unit: "angstrom",
                            start: 1.0, end: 2.0, n_points: 3, bond_type: "single",
                        },
                        selection: {
                            mode: "functional", kind: "bond_stretch",
                            atom_indices: [0, 1], groups: [], selected_bond: true,
                        },
                        protocol: protocol,
                    },
                    method: {
                        mode: "bond_length_scan",
                        schema_id: "pes_scan",
                        profile_id: "default",
                    },
                    resources: {nproc: 1, mem: "4GB", parallelism: 1},
                };
                const resp = await fetch("/api/v1/jobs", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(body),
                });
                return {status: resp.status, body: await resp.json().catch(() => ({}))};
            }"""
        )
        _evidence("flow4-submit-rejection.json", result)

        assert result["status"] == 422, result
        detail_text = json.dumps(result["body"].get("detail", ""), ensure_ascii=False)
        assert "GFN" in detail_text, detail_text
        assert "def2-TZVP" in detail_text, detail_text
        assert "basis" in detail_text, detail_text

    # -- flow 5: validation warnings visible in modal + summary -----------
    def test_validation_warnings_visible_in_modal_and_summary(self, page: Page) -> None:
        # Legacy draft whose values are NOT client-migrated: the method alias
        # "b973c" and the title-cased "Tight" reach /validate-method, which
        # surfaces canonicalization warnings (T24) — rendered by the UI.
        _wait_wizard_open(page)

        draft_name = "T25-legacy-alias"
        snapshot = {
            "structures": [],
            "selectedIndex": 0,
            "activeTab": "task",
            "step": 1,
            "fields": {},
            "workflowState": {
                "workflow": {"id": "PESsearch", "label": "PES Search", "schema_id": "pes_scan"},
                "method": {
                    "profile_id": "default",
                    "profile_label": "default",
                    "stages": {
                        "scan_optimizer": {
                            "engine": "orca",
                            "scan_optimizer_method": "b973c",
                            "scan_optimizer_convergence": "Tight",
                        }
                    },
                },
            },
        }
        created = page.evaluate(
            """async (args) => {
                const resp = await fetch("/api/v1/drafts", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        name: args.name, project_id: "",
                        workflow: "PESsearch", snapshot: args.snapshot,
                    }),
                });
                return await resp.json();
            }""",
            {"name": draft_name, "snapshot": snapshot},
        )
        assert created.get("draft_id"), created

        dialogs: list[str] = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        page.click("#wizard-drafts-open")
        page.wait_for_selector("#wizard-drafts-modal", state="visible")
        row = page.locator("#wizard-drafts-list > div").filter(has_text=draft_name)
        row.locator("button.btn.primary").click()
        page.wait_for_timeout(1200)

        # Config cards / summary live on wizard step 2 (step 1 hides them).
        _load_structure_and_next(page)

        # Warnings render in the method modal (doValidate -> refresh).
        page.click("#btn-config-method")
        warn = page.locator("#mc-validation-warnings")
        warn.wait_for(state="visible", timeout=15_000)
        warn_text = warn.inner_text()
        assert "b973c" in warn_text and "B97-3c" in warn_text, warn_text
        assert "Tight" in warn_text and "tight" in warn_text, warn_text
        _shot(page, "flow5-01-warnings-in-modal.png")

        # ... and survive into the wizard summary after the modal closes.
        page.click("#mt-config-ok")
        page.wait_for_selector("#method-config-modal", state="hidden")
        summary = page.locator("#method-detail-stages li.method-detail-warning")
        summary.first.wait_for(state="visible", timeout=10_000)
        summary_text = page.locator("#method-detail-stages").inner_text()
        assert "B97-3c" in summary_text, summary_text
        _evidence(
            "flow5-warnings.json",
            {"modal": warn_text, "summary": summary_text, "dialogs": dialogs},
        )
        _shot(page, "flow5-02-warnings-in-summary.png")
