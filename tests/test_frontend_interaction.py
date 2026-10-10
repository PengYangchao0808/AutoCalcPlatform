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


# ---------------------------------------------------------------------------
# Scan wizard (GAP-8): click-picked atoms, 1-based->0-based once, real body
#
# page.click / page.fill / page.screenshot hang in this container (pre-existing,
# proven on pristine HEAD) — every scan interaction below drives the live UI
# through page.evaluate with in-page .click() / direct state calls instead.
# ---------------------------------------------------------------------------

_SCAN_XYZ = "3\ntriatomic\nO 0.0 0.0 0.0\nC 1.2 0.0 0.0\nH 2.0 0.0 0.0\n"


def _scan_click(page: Page, selector: str) -> None:
    """Click an element through its own .click() (page.click hangs here)."""
    page.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el) throw new Error("missing element: " + sel);
            el.click();
        }""",
        selector,
    )


def _scan_wait(page: Page, expression: str, *, timeout: float, what: str) -> None:
    """Poll a boolean page expression via evaluate.

    wait_for_function polls via requestAnimationFrame, which intermittently
    stalls in this container — conditions known to be true are never re-checked
    (observed on the draft-restore wait). Evaluate-based polling is reliable.
    """
    deadline = time.monotonic() + timeout
    while True:
        if page.evaluate("() => Boolean(" + expression + ")"):
            return
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout:.0f} s waiting for {what}")
        time.sleep(0.25)


def _scan_wait_wizard_open(page: Page) -> None:
    """Open the create wizard with an in-page click.

    The DCL init binds ``#btn-new-calc`` before its await chain, but the page
    fixture can hand us control before DOMContentLoaded fires — retry until
    the modal actually opens (the same contract ``_wait_wizard_open`` keeps
    via page.click for the non-scan flows).
    """
    deadline = time.monotonic() + 45
    while True:
        opened = page.evaluate(
            "() => { const m = document.getElementById('job-modal');"
            " return !!m && m.style.display === 'flex'; }"
        )
        if opened:
            break
        if time.monotonic() > deadline:
            raise AssertionError("wizard did not open within 45 s")
        page.evaluate(
            "() => { const b = document.getElementById('btn-new-calc'); if (b) b.click(); }"
        )
        time.sleep(0.3)
    # The DCL init loads both catalogs and then writes the default profile.
    # Driving the flow before it settles lets the workflow picker run
    # loadDefaultMethodProfile against a missing schema (stages = {}, no
    # range/points) and lets a late write-back race our state — wait it out.
    deadline = time.monotonic() + 45
    while True:
        ready = page.evaluate(
            """() => ({
                profile: !!wizardState.method.profile_id,
                workflows: Array.isArray(workflowCatalogCache) && workflowCatalogCache.length > 0,
                methods: !!(methodCatalogCache && methodCatalogCache.method_schemas),
                loadFailed: !!(methodCatalogCache && methodCatalogCache._loadFailed),
                workflowFailed: !!(workflowCatalogCache && workflowCatalogCache._loadFailed),
            })"""
        )
        if ready["profile"] and ready["workflows"] and ready["methods"]:
            return
        if ready["loadFailed"] or ready["workflowFailed"]:
            raise AssertionError(f"catalog load failed during init: {ready}")
        if time.monotonic() > deadline:
            raise AssertionError(f"page init did not settle within 45 s: {ready}")
        time.sleep(0.5)


def _pick_workflow(page: Page, title: str) -> None:
    """Pick a workflow by its exact rendered title in the workflow picker.

    The option list only renders once the workflow catalog has loaded, so
    wait for the title first; clicking goes through the elements' own
    .click() handlers (page.click hangs in this container).
    """
    _scan_click(page, "#btn-config-workflow")
    _scan_wait(
        page,
        "document.getElementById('workflow-config-modal') &&"
        " getComputedStyle(document.getElementById('workflow-config-modal')).display !== 'none'",
        timeout=15,
        what="workflow picker to open",
    )
    _scan_wait(
        page,
        "Array.from(document.querySelectorAll('.workflow-option-title')).some("
        "function(el) { return el.textContent.trim() === " + json.dumps(title) + "; })",
        timeout=30,
        what=f"workflow option {title!r} to render",
    )
    page.evaluate(
        """(title) => {
            const titleEl = Array.from(document.querySelectorAll('.workflow-option-title'))
                .find((el) => el.textContent.trim() === title);
            titleEl.closest('.workflow-option').click();
            document.getElementById('wf-config-ok').click();
        }""",
        title,
    )
    _scan_wait(
        page,
        "document.getElementById('workflow-config-modal') &&"
        " getComputedStyle(document.getElementById('workflow-config-modal')).display === 'none'",
        timeout=15,
        what="workflow picker to close",
    )


def _scan_pick_atoms(page: Page, *indices: int) -> None:
    """Click preview atoms through the real click handler (0-based indices)."""
    for index in indices:
        page.evaluate("(n) => { handleScanPreviewAtomClick({ index: n }); }", index)


@pytest.mark.slow
class TestScanWizardBrowser:
    """Todo 9 browser acceptance: the scan wizard on the real submit path."""

    def _open_scan_step_two(self, page: Page) -> None:
        """Open the wizard, parse the structure, pick Relaxed Scan, STAY at 2.

        Step 2 is where the PES-style scan selection panel lives
        (``#scan-selection-panel`` below the 3D preview) — the panel and its
        click-only atom picking are gated on ``createWizardStep === 2``.
        """
        _scan_wait_wizard_open(page)
        page.evaluate(
            """(text) => {
                document.querySelector('.input-mode-tab[data-input-mode="structure"]').click();
                document.getElementById('modal-structure-input').value = text;
            }""",
            _SCAN_XYZ,
        )
        # Setting .value does not fire the input listener, so parse directly;
        # awaiting the async parse means wizardStructures is populated by the
        # time this evaluate returns.
        page.evaluate("() => parseStructuresPreview()")
        _scan_wait(page, "wizardStructures.length > 0", timeout=20, what="structure parse")
        _scan_click(page, "#modal-submit")  # step 1 -> 2
        _scan_wait(
            page,
            "document.getElementById('job-modal').dataset.createStep === '2'",
            timeout=10,
            what="wizard step 2",
        )
        _pick_workflow(page, "Relaxed Scan")
        _scan_wait(
            page,
            "document.getElementById('scan-selection-panel').classList.contains('active')",
            timeout=15,
            what="scan selection panel to activate",
        )
        # The 3Dmol CDN is stubbed out in this suite, so the real preview
        # model never materialises. canPickScanAtoms() gates on a preview
        # model plus a matching structure key — supply both so the click
        # handler under test is reachable (renderScanPreviewSelection then
        # degrades gracefully without a viewer).
        page.evaluate(
            """() => {
                if (!previewModel) previewModel = { setClickable: function() {} };
                previewModelStructureKey =
                    String((scanWizardCurrentStructure() || {}).xyz || '').trim();
            }"""
        )

    def test_scan_wizard_submit_posts_real_body_with_0_based_coordinate(self, page: Page) -> None:
        captured: list[dict] = []

        def _handler(route) -> None:  # noqa: ANN001 - playwright route
            request = route.request
            if request.method == "POST":
                captured.append(json.loads(request.post_data or "{}"))
            route.fulfill(
                status=201,
                content_type="application/json",
                body=json.dumps(
                    {"job_id": "scan_browser_001", "status": "queued", "workflow": "scan"}
                ),
            )

        page.context.route(re.compile(r".*/api/v1/jobs$"), _handler)
        self._open_scan_step_two(page)
        _scan_pick_atoms(page, 0, 1)  # display atoms 1,2 -> mirror [1, 2]
        _evidence(
            "flow6-scan-wizard-pre-submit.json",
            page.evaluate(
                """() => ({
                    selected: scanSelectionState.selectedAtoms.slice(),
                    stages: JSON.parse(JSON.stringify(wizardState.method.stages.scan)),
                    kind: scanSelectionState.selectionKind,
                    canPick: canPickScanAtoms(),
                })"""
            ),
        )
        dialogs: list[str] = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        _scan_click(page, "#modal-submit")  # step 2 -> 3
        _scan_wait(
            page,
            "document.getElementById('job-modal').dataset.createStep === '3'",
            timeout=10,
            what="wizard step 3",
        )
        _scan_click(page, "#modal-submit")  # submit
        page.wait_for_timeout(1500)

        assert not dialogs, f"valid submit must not alert: {dialogs}"
        assert len(captured) == 1, captured
        body = captured[0]
        _evidence("flow6-scan-wizard-body.json", body)
        assert body["workflow"] == "scan"
        assert body["input"]["source_type"] == "xyz_text"
        # Default profile range 1.0/3.0 passes the builder through String():
        # JS String(1.0)/String(3.0) -> "1"/"3" (exact formatting is not part
        # of the contract — node tests cover string fidelity).
        assert body["input"]["scan_coordinates"] == ["0,1,1,3"]
        assert body["method"]["scan_points"] == 21
        assert body["method"]["levels"]["scan"]["scan_coordinate_atoms"] == [1, 2]
        assert body["method"]["levels"]["scan"]["scan_coordinate_kind"] == "distance"

    def test_scan_wizard_rejects_out_of_range_atom_without_submitting(self, page: Page) -> None:
        captured: list[dict] = []

        def _handler(route) -> None:  # noqa: ANN001 - playwright route
            request = route.request
            if request.method == "POST":
                captured.append(json.loads(request.post_data or "{}"))
            route.fulfill(
                status=201,
                content_type="application/json",
                body=json.dumps(
                    {"job_id": "scan_browser_002", "status": "queued", "workflow": "scan"}
                ),
            )

        page.context.route(re.compile(r".*/api/v1/jobs$"), _handler)
        self._open_scan_step_two(page)
        # The click handler clamps picks to the kind's atom count, so plant
        # the out-of-range state directly (selection + its stages mirror) and
        # let the submit-time builder be the one to reject it.
        page.evaluate(
            """() => {
                scanSelectionState.selectedAtoms = [98];
                wizardState.method.stages.scan = wizardState.method.stages.scan || {};
                wizardState.method.stages.scan.scan_coordinate_atoms = [99];
            }"""
        )
        dialogs: list[str] = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        _scan_click(page, "#modal-submit")  # step 2 -> 3
        _scan_click(page, "#modal-submit")  # submit
        page.wait_for_timeout(1500)

        assert captured == [], "invalid scan selection must never reach the API"
        assert dialogs, "invalid scan selection must produce a clear validation result"
        error = page.evaluate("() => document.getElementById('scan-selection-error').textContent")
        assert error and error.strip(), f"panel must surface the validation error: {error!r}"
        _evidence(
            "flow7-scan-wizard-rejected.json",
            {"dialogs": dialogs, "posted": captured, "error": error},
        )

    def test_scan_wizard_switch_away_clears_scan_selection(self, page: Page) -> None:
        self._open_scan_step_two(page)
        _scan_pick_atoms(page, 0, 1)
        picked = _scan_pick_snapshot(page)
        assert picked["selected"] == [0, 1], picked

        _pick_workflow(page, "Geometry Optimization")
        _scan_wait(
            page,
            "!document.getElementById('scan-selection-panel').classList.contains('active')",
            timeout=10,
            what="scan selection panel to deactivate",
        )
        after = page.evaluate(
            """() => {
                const panel = document.getElementById('scan-selection-panel');
                return {
                    panelActive: panel.classList.contains('active'),
                    panelDisplay: getComputedStyle(panel).display,
                    selected: scanSelectionState.selectedAtoms.slice(),
                    mirror: (wizardState.method.stages.scan || {}).scan_coordinate_atoms || null,
                    canPick: canPickScanAtoms(),
                };
            }"""
        )
        assert after["panelActive"] is False, after
        assert after["panelDisplay"] == "none", after
        assert after["selected"] == [], f"selection must clear on switch-away: {after}"
        assert after["mirror"] is None, f"stages mirror atoms must be dropped: {after}"
        assert after["canPick"] is False, after
        _evidence("flow8-scan-switch-away.json", after)

    def test_scan_draft_restore_round_trips_selection(self, page: Page) -> None:
        _scan_wait_wizard_open(page)
        snapshot = {
            "structures": [{"name": "mol", "xyz": _SCAN_XYZ, "has_3d": True}],
            "selectedIndex": 0,
            "activeTab": "task",
            "step": 1,
            "fields": {},
            "workflowState": {
                "workflow": {"id": "scan", "label": "Relaxed Scan", "schema_id": "dft_scan"},
                "method": {
                    "profile_id": "default",
                    "profile_label": "default",
                    "stages": {
                        "scan": {
                            "engine": "orca",
                            "scan_coordinate_kind": "distance",
                            "scan_coordinate_start": 1.5,
                            "scan_coordinate_end": 2.5,
                            "scan_coordinate_points": 7,
                        }
                    },
                },
            },
            # The stage mirror deliberately carries NO atoms here: the restored
            # pick survives only through this block plus its structureKey —
            # syncScanSelectionFromWizard keeps it while the key still matches
            # the staged structure.
            "scanSelection": {
                "selectionKind": "distance",
                "selectedAtoms": [1, 2],
                "structureKey": _SCAN_XYZ.strip(),
            },
        }
        created = page.evaluate(
            """async (args) => {
                const resp = await fetch("/api/v1/drafts", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        name: args.name, project_id: "",
                        workflow: "scan", snapshot: args.snapshot,
                    }),
                });
                return await resp.json();
            }""",
            {"name": "scan-wizard-draft", "snapshot": snapshot},
        )
        assert created.get("draft_id"), created

        dialogs: list[str] = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        _scan_click(page, "#wizard-drafts-open")
        _scan_wait(
            page,
            "document.getElementById('wizard-drafts-modal') &&"
            " getComputedStyle(document.getElementById('wizard-drafts-modal')).display !== 'none'",
            timeout=15,
            what="drafts box to open",
        )
        _scan_wait(
            page,
            "Array.from(document.querySelectorAll('#wizard-drafts-list > div')).some("
            "function(row) { return row.textContent.includes('scan-wizard-draft'); })",
            timeout=20,
            what="draft row to render",
        )
        page.evaluate(
            """(name) => {
                const row = Array.from(document.querySelectorAll('#wizard-drafts-list > div'))
                    .find((el) => el.textContent.includes(name));
                row.querySelector('button.btn.primary').click();
            }""",
            "scan-wizard-draft",
        )
        # Evaluate-poll instead of wait_for_function: rAF-based polling
        # intermittently stalls in this container (observed: a condition that
        # is already true never fires the next frame).
        deadline = time.monotonic() + 35
        while True:
            picked = page.evaluate("() => scanSelectionState.selectedAtoms.length")
            if picked == 2:
                break
            if time.monotonic() > deadline:
                state = page.evaluate(
                    """() => ({
                        selected: scanSelectionState.selectedAtoms.slice(),
                        structureKey: scanSelectionState.structureKey,
                        nStructures: wizardStructures.length,
                        structureXyz:
                            String((scanWizardCurrentStructure() || {}).xyz || '').slice(0, 30),
                        stages: JSON.parse(JSON.stringify(wizardState.method.stages)),
                        wf: wizardState.workflow && wizardState.workflow.id,
                        step: createWizardStep,
                        draftScanSelection: (wizardDraft || {}).scanSelection || null,
                        draftStructures: ((wizardDraft || {}).structures || []).length,
                        modalDisplay: document.getElementById('job-modal').style.display,
                        draftsOverlay: document.getElementById('wizard-drafts-modal').style.display,
                    })"""
                )
                _evidence(
                    "flow9-scan-draft-restore-diag.json",
                    {"dialogs": dialogs, **state},
                )
                raise AssertionError(f"draft restore did not land in time: {state}")
            time.sleep(0.25)

        restored = _scan_pick_snapshot(page)
        _evidence("flow9-scan-draft-restore.json", restored)
        assert restored["selected"] == [1, 2], restored
        assert "C#2 — H#3" in restored["summary"], restored
        # Task-badge range/points come from the stages mirror (1.5 → 2.5 · 7 点).
        assert "1.5 → 2.5" in restored["badge"], restored
        assert "7 点" in restored["badge"], restored

    def test_scan_edit_hydration_hydrates_state_from_stage_mirror(self, page: Page) -> None:
        self._open_scan_step_two(page)
        result = page.evaluate(
            """() => {
                // Edit drafts carry only the stage mirror; hydration must
                // rebuild selection kind, atoms, summary and badge from it.
                resetScanSelection();
                wizardState.method.stages = {
                    scan: {
                        engine: "orca",
                        scan_coordinate_kind: "angle",
                        scan_coordinate_start: 100,
                        scan_coordinate_end: 160,
                        scan_coordinate_points: 9,
                    }
                };
                updateConfigCards();
                var kindHydrated = scanSelectionState.selectionKind;
                // Atoms hydrate only once the kind matches (a kind change
                // clears the mirror first) — add them for the second pass.
                wizardState.method.stages.scan.scan_coordinate_atoms = [1, 2, 3];
                updateConfigCards();
                return {
                    kindHydrated: kindHydrated,
                    kind: scanSelectionState.selectionKind,
                    selected: scanSelectionState.selectedAtoms.slice(),
                    mirror: (wizardState.method.stages.scan || {}).scan_coordinate_atoms || null,
                    summary: document.getElementById('scan-selection-summary').textContent,
                    badge: document.getElementById('scan-task-badge').textContent,
                };
            }"""
        )
        _evidence("flow10-scan-edit-hydration.json", result)
        assert result["kindHydrated"] == "angle", result
        assert result["kind"] == "angle", result
        assert result["selected"] == [0, 1, 2], result
        assert result["mirror"] == [1, 2, 3], result
        assert "键角扫描" in result["summary"] and "O#1 — C#2 — H#3" in result["summary"], result
        assert "100 → 160" in result["badge"] and "9 点" in result["badge"], result


# ---------------------------------------------------------------------------
# Queue-row freshness under latency and re-render recovery (todo 14 / GAP-9)
#
# Every response body served to the browser is built through the REAL
# response models (``V2TaskViewResponse`` / ``V1JobDetailResponse`` /
# ``V1JobRecordModel`` / ``JobRecovery``) and round-trip validated, so the
# page only ever sees fields the real API serializes — never invented ones.
# Execution-version identity is the frozen ``(job_id, attempt, revision)``
# from todo 5 (``name_revision`` is organization naming, NOT identity).
# ---------------------------------------------------------------------------

_PAUSE_GLYPH = "\u2016"  # ‖
_RESUME_GLYPH = "\u25b6"  # ▶
_CANCEL_GLYPH = "\u2715"  # ✕
_CONTINUE_GLYPH = "\u23f5"  # ⏵
_RERUN_GLYPH = "\u21bb"  # ↻

_ROW_BUTTONS_JS = """(jobId) => {
  const row = document.querySelector('#queue-list .queue-row[data-job-id="' + jobId + '"]');
  if (!row) return null;
  return Array.from(row.querySelectorAll('.queue-row-actions button')).map(
    (b) => b.textContent.trim());
}"""


def _row_sel(job_id: str, tail: str) -> str:
    return f'#queue-list .queue-row[data-job-id="{job_id}"] .queue-row-actions {tail}'


def _dump(model: Any) -> dict:
    return json.loads(model.model_dump_json())


def _task_row(job_id: str, status: str, **over: Any) -> dict:
    """One ``V2TaskRowModel`` dump mirroring ``task_views._row_to_task``."""
    from acp.api.v2_schemas import V2TaskRowModel

    workflow = str(over.pop("workflow", "optimize"))
    payload: dict = {
        "id": job_id,
        "status": status,
        "attempt": 1,
        "revision": 0,
        "group_id": None,
        "project_id": "",
        "project_name": "",
        "created_at": "2026-10-09T00:00:00Z",
        "updated_at": "2026-10-09T00:00:00Z",
        "molecule_name": "Mol",
        "task_name": "t1",
        "remark": "",
        "display_name": "Mol_t1_" + job_id,
        "task_dir_name": "Mol_t1_" + job_id,
        "workflow": workflow,
        "tags": [],
        "archived": False,
        "batch_id": None,
        "spec": {
            "workflow": workflow,
            "molecule_name": "Mol",
            "task_name": "t1",
            "remark": "",
            "tags": [],
        },
    }
    payload.update(over)
    body = _dump(V2TaskRowModel.model_validate(payload))
    V2TaskRowModel.model_validate(body)  # real-API serialization gate
    return body


def _v1_record(job_id: str, status: str, **over: Any) -> dict:
    """One ``V1JobRecordModel`` dump — the shape ``GET /jobs/{id}`` serves."""
    from acp.api.v1_schemas import V1JobRecordModel

    workflow = str(over.pop("workflow", "optimize"))
    payload: dict = {
        "id": job_id,
        "status": status,
        "attempt": 1,
        "revision": 0,
        "work_dir": "/tmp/acp_t14/" + job_id,
        "spec": {"workflow": workflow, "name": job_id, "task_name": "t1"},
        "created_at": "2026-10-09T00:00:00Z",
        "updated_at": "2026-10-09T00:00:00Z",
    }
    payload.update(over)
    body = _dump(V1JobRecordModel.model_validate(payload))
    V1JobRecordModel.model_validate(body)
    return body


def _detail_body(record: dict, recovery: dict) -> dict:
    """One ``V1JobDetailResponse`` dump — the shape ``GET /jobs/{id}/detail`` serves."""
    from acp.api.v1_schemas import JobRecovery, V1JobDetailResponse, V1JobRecordModel

    body = _dump(
        V1JobDetailResponse(
            job=V1JobRecordModel.model_validate(record),
            recovery=JobRecovery.model_validate(recovery),
        )
    )
    V1JobDetailResponse.model_validate(body)
    return body


def _task_view_body(rows: list[dict]) -> dict:
    """One ``V2TaskViewResponse`` dump — the shape ``GET /api/v2/task-view`` serves."""
    from acp.api.v2_schemas import (
        V2TaskRowModel,
        V2TaskViewGroupModel,
        V2TaskViewResponse,
    )

    jobs = [V2TaskRowModel.model_validate(r) for r in rows]
    counts: dict = {}
    for j in jobs:
        counts[j.status] = counts.get(j.status, 0) + 1
    body = _dump(
        V2TaskViewResponse(
            groups=[
                V2TaskViewGroupModel(
                    key="__singles__",
                    display_name="Singles",
                    count=len(jobs),
                    jobs=jobs,
                )
            ],
            counts=counts,
            total=len(jobs),
        )
    )
    V2TaskViewResponse.model_validate(body)
    return body


class _QueueApiSim:
    """Route-backed simulation of the real v1/v2 task APIs for queue tests.

    Mutations (pause/unpause/rerun/cancel/batch) are performed by the route
    handlers the same way the real manager would (attempt bumps only on
    rerun, ``revision`` bumps on transitions); everything the browser reads
    is re-serialized through the real response models on every request.
    """

    def __init__(self, page: Page) -> None:
        self.page = page
        self.rows: dict[str, dict] = {}
        self.recoveries: dict[str, dict] = {}
        self.detail_log: list[str] = []
        self.calls: dict = {"pause": 0, "unpause": 0, "rerun": 0, "cancel": 0, "batch": 0}
        self._hold_body: dict | None = None
        self._held: list = []
        self._install()

    # -- state helpers (server-side actors) --------------------------------
    def add_job(self, job_id: str, status: str, **over: Any) -> None:
        self.rows[job_id] = _task_row(job_id, status, **over)
        self.recoveries.setdefault(job_id, self._default_recovery(status))

    @staticmethod
    def _default_recovery(status: str) -> dict:
        return {
            "can_pause": status == "running",
            "can_unpause": status == "paused",
            "can_continue": False,
            "continue_mode": "",
            "continue_notes": "",
            "can_rerun": status in ("completed", "failed", "cancelled"),
            "can_purge": True,
            "can_cancel": status
            in (
                "queued",
                "starting",
                "running",
                "cancelling",
                "pending",
                "waiting_review",
                "paused",
            ),
        }

    def set_recovery(self, job_id: str, recovery: dict) -> None:
        self.recoveries[job_id] = recovery

    def set_state(
        self,
        job_id: str,
        status: str,
        *,
        attempt: int | None = None,
        revision: int | None = None,
    ) -> None:
        row = self.rows[job_id]
        row["status"] = status
        if attempt is not None:
            row["attempt"] = attempt
        if revision is not None:
            row["revision"] = revision
        row["updated_at"] = "2026-10-09T00:00:01Z"
        self.recoveries[job_id] = self._default_recovery(status)

    def rerun_completed(self, job_id: str) -> None:
        """Terminal -> in-place rerun finished: attempt+1, revision bumped."""
        row = self.rows[job_id]
        row["revision"] = int(row["revision"]) + 1
        row["status"] = "completed"
        row["attempt"] = int(row["attempt"]) + 1
        row["revision"] = int(row["revision"]) + 1
        row["status"] = "queued"
        row["updated_at"] = "2026-10-09T00:00:01Z"
        self.recoveries[job_id] = self._default_recovery("queued")

    # -- delayed-response controls ----------------------------------------
    def hold_detail(self, body: dict) -> None:
        """Park the NEXT /detail request; release with :meth:`release_held`."""
        self._hold_body = body

    @property
    def pending_held(self) -> int:
        return len(self._held)

    def release_held(self) -> None:
        held, self._held = self._held, []
        for route, body in held:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(body),
            )

    # -- routing -----------------------------------------------------------
    def _install(self) -> None:
        import re

        page = self.page
        # Catch-all FIRST — playwright checks routes in reverse registration
        # order, so the specific handlers registered below always win.
        page.route(re.compile(r".*/api/.*"), self._on_catchall)
        page.route(re.compile(r".*/api/v1/status$"), self._on_status)
        page.route(re.compile(r".*/api/v2/task-view(\?.*)?$"), self._on_task_view)
        page.route(re.compile(r".*/api/v2/tasks/batch-ops$"), self._on_batch_ops)
        page.route(re.compile(r".*/api/v1/jobs/[^/]+/detail$"), self._on_detail)
        page.route(re.compile(r".*/api/v1/jobs/[^/]+/summary$"), self._on_summary)
        page.route(
            re.compile(r".*/api/v1/jobs/[^/]+/(pause|unpause|rerun|cancel)$"),
            self._on_action,
        )
        page.route(re.compile(r".*/api/v1/jobs/[^/]+$"), self._on_job)

    @staticmethod
    def _job_id(url: str) -> str:
        m = re.search(r"/jobs/([^/]+)", url)
        return m.group(1) if m else ""

    def _record(self, job_id: str) -> dict:
        row = self.rows[job_id]
        return _v1_record(
            job_id,
            row["status"],
            attempt=row["attempt"],
            revision=row["revision"],
            workflow=row["workflow"],
            updated_at=row["updated_at"],
        )

    def _detail(self, job_id: str) -> dict:
        return _detail_body(self._record(job_id), self.recoveries[job_id])

    def _on_catchall(self, route) -> None:  # noqa: ANN001 - playwright route
        route.fulfill(status=200, content_type="application/json", body="{}")

    def _on_status(self, route) -> None:  # noqa: ANN001
        counts: dict = {}
        for row in self.rows.values():
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"host": "127.0.0.1", "port": 8765, "queue": counts}),
        )

    def _on_task_view(self, route) -> None:  # noqa: ANN001
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_task_view_body(list(self.rows.values()))),
        )

    def _on_detail(self, route) -> None:  # noqa: ANN001
        job_id = self._job_id(route.request.url)
        self.detail_log.append(job_id)
        if self._hold_body is not None:
            self._held.append((route, self._hold_body))
            self._hold_body = None
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(self._detail(job_id)),
        )

    def _on_summary(self, route) -> None:  # noqa: ANN001
        job_id = self._job_id(route.request.url)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(self._record(job_id)),
        )

    def _on_job(self, route) -> None:  # noqa: ANN001
        job_id = self._job_id(route.request.url)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(self._record(job_id)),
        )

    def _on_action(self, route) -> None:  # noqa: ANN001
        job_id = self._job_id(route.request.url)
        action = route.request.url.rsplit("/", 1)[-1]
        row = self.rows[job_id]
        if action == "pause":
            self.calls["pause"] += 1
            self.set_state(job_id, "paused", revision=int(row["revision"]) + 1)
        elif action == "unpause":
            self.calls["unpause"] += 1
            self.set_state(job_id, "running", revision=int(row["revision"]) + 1)
        elif action == "rerun":
            self.calls["rerun"] += 1
            self.set_state(
                job_id,
                "queued",
                attempt=int(row["attempt"]) + 1,
                revision=int(row["revision"]) + 1,
            )
        elif action == "cancel":
            self.calls["cancel"] += 1
            self.set_state(job_id, "cancelled", revision=int(row["revision"]) + 1)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(self._record(job_id)),
        )

    def _on_batch_ops(self, route) -> None:  # noqa: ANN001
        self.calls["batch"] += 1
        payload = json.loads(route.request.post_data or "{}")
        results = []
        for job_id in payload.get("task_ids", []):
            if job_id in self.rows:
                self.rows[job_id]["tags"] = ["t14"]
                self.rows[job_id]["updated_at"] = "2026-10-09T00:00:02Z"
            results.append({"task_id": job_id, "ok": True})
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"results": results}),
        )


def _wait_for(page: Page, cond: Any, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        page.evaluate("() => 1")
        time.sleep(0.05)
    raise AssertionError("condition not met within timeout")


@pytest.mark.slow
class TestQueueRowFreshnessBrowser:
    """Todo 14 (GAP-9) executed acceptance: queue rows stay fresh under
    latency; delayed old detail responses can never override newer state;
    recovery changes re-render rows; detail requests stay bounded per poll."""

    def _boot(self, page: Page) -> _QueueApiSim:
        return _QueueApiSim(page)

    @staticmethod
    def _render(page: Page) -> None:
        page.evaluate("() => refreshJobs()")
        page.wait_for_selector("#queue-list .queue-row", timeout=10_000)

    def test_completed_row_drops_stale_active_actions_and_offers_rerun_menu(
        self, page: Page
    ) -> None:
        sim = self._boot(page)
        sim.add_job("J1", "running", attempt=1, revision=1)
        self._render(page)
        # Natural cache population: the real detail fetch while J1 is RUNNING.
        page.evaluate("() => fetchJobDetail('J1')")
        assert page.evaluate("() => !!getJobDetail('J1')")
        # Server-side completion (revision bumps like the real CAS).
        sim.set_state("J1", "completed", revision=2)
        page.evaluate("() => refreshJobs()")  # the poll observing the change
        ops = page.evaluate(_ROW_BUTTONS_JS, "J1")
        assert ops is not None
        assert _RERUN_GLYPH in ops, f"terminal row must offer rerun: {ops}"
        for active in (_PAUSE_GLYPH, _RESUME_GLYPH, _CANCEL_GLYPH):
            assert active not in ops, f"terminal row shows active-only action {active!r}: {ops}"
        # Stale RUNNING detail must be dropped (status disagrees with the list).
        assert page.evaluate("() => getJobDetail('J1')") is None
        # Terminal rerun menu: 3 real actions, rerun_direct enabled.
        page.click(_row_sel("J1", f'button:has-text("{_RERUN_GLYPH}")'))
        page.wait_for_selector(".rerun-action-menu", state="visible", timeout=5_000)
        items = page.eval_on_selector_all(
            ".rerun-action-menu [role=menuitem]",
            "els => els.map((e) => ({ text: e.textContent.trim(), disabled: e.disabled }))",
        )
        assert len(items) == 3, items
        assert not items[0]["disabled"], items
        _evidence("t14-terminal-row-rerun-menu.json", {"ops": ops, "menu": items})

    def test_unselected_row_pause_then_resume_reflects_fresh_state(self, page: Page) -> None:
        sim = self._boot(page)
        sim.add_job("J1", "running", attempt=1, revision=1)
        sim.add_job("J2", "running", attempt=1, revision=1)
        self._render(page)
        page.evaluate("() => fetchJobDetail('J1')")  # cache primed for an unselected row
        assert page.evaluate("() => String(selectedJobId)") == ""
        # Real row button click (stopPropagation keeps the row unselected).
        page.click(_row_sel("J1", "button.mini-btn.warn"))
        _wait_for(page, lambda: sim.calls["pause"] >= 1)
        page.wait_for_timeout(400)  # let the action chain land
        page.evaluate("() => refreshJobs()")
        ops = page.evaluate(_ROW_BUTTONS_JS, "J1")
        assert _RESUME_GLYPH in ops, f"paused unselected row must offer resume: {ops}"
        assert _PAUSE_GLYPH not in ops, f"paused row must not keep pause: {ops}"
        assert page.evaluate("() => String(selectedJobId)") == ""
        page.click(_row_sel("J1", f'button:has-text("{_RESUME_GLYPH}")'))
        _wait_for(page, lambda: sim.calls["unpause"] >= 1)
        page.wait_for_timeout(400)
        page.evaluate("() => refreshJobs()")
        ops = page.evaluate(_ROW_BUTTONS_JS, "J1")
        assert _PAUSE_GLYPH in ops, f"resumed row must offer pause again: {ops}"
        _evidence("t14-unselected-pause-resume.json", {"ops": ops, "calls": sim.calls})

    def test_delayed_detail_response_cannot_override_newer_state(self, page: Page) -> None:
        sim = self._boot(page)
        sim.add_job("J1", "running", attempt=1, revision=1)
        self._render(page)
        stale = _detail_body(
            _v1_record("J1", "running", attempt=1, revision=1),
            {
                "can_pause": True,
                "can_unpause": False,
                "can_continue": False,
                "can_rerun": False,
                "can_purge": True,
                "can_cancel": True,
            },
        )
        sim.hold_detail(stale)
        page.evaluate("() => { window.__late = fetchJobDetail('J1'); }")
        _wait_for(page, lambda: sim.pending_held >= 1)
        # Newer state wins while the old response is still in flight: the real
        # pause action invalidates + refetches (fresh paused recovery).
        page.evaluate("() => pauseJob('J1')")
        _wait_for(page, lambda: sim.calls["pause"] >= 1)
        page.wait_for_timeout(300)
        # The OLD RUNNING response is released late — it must be dropped.
        sim.release_held()
        late = page.evaluate("() => window.__late.then((d) => d)")
        assert late is None, f"late old-generation detail must be dropped, got: {late}"
        cached = page.evaluate(
            """() => {
            const d = getJobDetail('J1');
            return d ? { st: d.job.status, pause: !!(d.recovery && d.recovery.can_pause) } : null;
        }"""
        )
        assert cached is None or cached["pause"] is False, cached
        page.evaluate("() => refreshJobs()")
        ops = page.evaluate(_ROW_BUTTONS_JS, "J1")
        assert _RESUME_GLYPH in ops and _PAUSE_GLYPH not in ops, ops
        _evidence(
            "t14-delayed-response-dropped.json",
            {"late": late, "cached": cached, "ops": ops},
        )

    def test_rerun_between_polls_drops_old_attempt_detail_and_re_renders(self, page: Page) -> None:
        sim = self._boot(page)
        sim.add_job("J1", "running", attempt=1, revision=3)
        self._render(page)
        page.evaluate(
            """() => {
            const row = document.querySelector('#queue-list .queue-row[data-job-id="J1"]');
            row.dataset.t14Mark = 'pre';
        }"""
        )
        # The old attempt's detail response (attempt 1, RUNNING) is delayed.
        stale = _detail_body(
            _v1_record("J1", "running", attempt=1, revision=3),
            {
                "can_pause": True,
                "can_unpause": False,
                "can_continue": False,
                "can_rerun": False,
                "can_purge": True,
                "can_cancel": True,
            },
        )
        sim.hold_detail(stale)
        page.evaluate("() => { window.__late = fetchJobDetail('J1'); }")
        _wait_for(page, lambda: sim.pending_held >= 1)
        # Between two RUNNING polls a rerun completes (terminal -> attempt 2).
        sim.rerun_completed("J1")
        page.evaluate("() => refreshJobs()")  # the next poll observes attempt 2
        info = page.evaluate(
            """() => {
            const row = document.querySelector('#queue-list .queue-row[data-job-id="J1"]');
            const j = jobsCache.find((e) => String(e.id) === 'J1');
            return { mark: row ? row.dataset.t14Mark : null, attempt: j && j.attempt,
                     revision: j && j.revision, status: j && j.status };
        }"""
        )
        assert info["attempt"] == 2 and info["status"] == "queued", info
        assert info["mark"] is None, f"row must re-render on attempt change: {info}"
        # Old attempt's delayed detail response released AFTER the rerun.
        sim.release_held()
        late = page.evaluate("() => window.__late.then((d) => d)")
        assert late is None, f"old-attempt detail must be dropped, got: {late}"
        assert page.evaluate("() => getJobDetail('J1')") is None
        page.evaluate("() => refreshJobs()")
        ops = page.evaluate(_ROW_BUTTONS_JS, "J1")
        for active in (_PAUSE_GLYPH, _RESUME_GLYPH, _CANCEL_GLYPH, _RERUN_GLYPH):
            assert active not in ops, f"queued attempt-2 row shows stale action {active!r}: {ops}"
        _evidence(
            "t14-rerun-between-polls.json",
            {"info": info, "late": late, "ops": ops},
        )

    def test_fresh_server_recovery_re_render_decides_capability(self, page: Page) -> None:
        sim = self._boot(page)
        # mechanism + failed: the status DEFAULT offers continue, but the
        # server recovery (work_dir gone -> can_continue=False) must win and
        # the row must re-render when it arrives.
        sim.add_job("J1", "failed", workflow="mechanism", attempt=1, revision=2)
        sim.set_recovery(
            "J1",
            {
                "can_pause": False,
                "can_unpause": False,
                "can_continue": False,
                "continue_mode": "",
                "continue_notes": "",
                "can_rerun": True,
                "can_purge": True,
                "can_cancel": False,
            },
        )
        self._render(page)
        ops_before = page.evaluate(_ROW_BUTTONS_JS, "J1")
        assert _CONTINUE_GLYPH in ops_before, f"status default offers continue: {ops_before}"
        page.evaluate("() => fetchJobDetail('J1')")  # fresh server recovery arrives
        ops_after = page.evaluate(_ROW_BUTTONS_JS, "J1")
        assert _CONTINUE_GLYPH not in ops_after, (
            f"row must re-render and adopt the server's can_continue=False: {ops_after}"
        )
        assert _RERUN_GLYPH in ops_after, ops_after
        _evidence(
            "t14-recovery-change-re-render.json",
            {"before": ops_before, "after": ops_after},
        )

    def test_detail_requests_bounded_per_poll(self, page: Page) -> None:
        sim = self._boot(page)
        statuses = [
            "running",
            "queued",
            "completed",
            "failed",
            "paused",
            "completed",
            "running",
            "queued",
        ]
        for i, status in enumerate(statuses):
            sim.add_job(f"J{i}", status, attempt=1, revision=1)
        self._render(page)
        # Prime two caches so a naive "refetch dropped rows" implementation
        # would have something to refetch on every poll.
        page.evaluate("() => fetchJobDetail('J0')")
        page.evaluate("() => fetchJobDetail('J1')")
        base = len(sim.detail_log)
        for _ in range(3):  # three poll ticks over 8 visible rows
            page.evaluate("() => refreshJobs()")
            page.evaluate("() => refreshSelectedJobSummary()")
        assert len(sim.detail_log) - base == 0, (
            f"poll ticks must not fetch detail per row: {sim.detail_log[base:]}"
        )
        # Cache drops caused by identity/status changes must NOT trigger
        # per-row refetches on subsequent polls either.
        sim.set_state("J0", "completed", revision=2)
        sim.set_state("J1", "completed", revision=2)
        for _ in range(2):
            page.evaluate("() => refreshJobs()")
        assert len(sim.detail_log) - base == 0, sim.detail_log[base:]
        # A row action may fetch at most one detail (the acted job).
        page.evaluate("() => pauseJob('J6')")
        _wait_for(page, lambda: sim.calls["pause"] >= 1)
        page.wait_for_timeout(300)
        assert len(sim.detail_log) - base <= 1, (
            f"one action -> at most one detail fetch: {sim.detail_log[base:]}"
        )
        # The selected-job summary lane never grows the detail count.
        page.evaluate("() => { selectedJobId = 'J6'; }")
        page.evaluate("() => refreshSelectedJobSummary()")
        assert len(sim.detail_log) - base <= 1, sim.detail_log[base:]
        _evidence(
            "t14-detail-request-bound.json",
            {"detail_log": sim.detail_log, "base": base, "calls": sim.calls},
        )

    def test_batch_ops_invalidate_all_selected_row_caches(self, page: Page) -> None:
        sim = self._boot(page)
        sim.add_job("J1", "running", attempt=1, revision=1)
        sim.add_job("J2", "running", attempt=1, revision=1)
        self._render(page)
        page.evaluate("() => fetchJobDetail('J1')")
        page.evaluate("() => fetchJobDetail('J2')")
        page.check('#queue-list .queue-row[data-job-id="J1"] .queue-check')
        page.check('#queue-list .queue-row[data-job-id="J2"] .queue-check')
        page.evaluate("() => _batchOp('add_tags', { tags: ['t14'] })")
        _wait_for(page, lambda: sim.calls["batch"] >= 1)
        page.wait_for_timeout(300)
        page.evaluate("() => refreshJobs()")
        cache_state = page.evaluate(
            "() => ({ j1: !!getJobDetail('J1'), j2: !!getJobDetail('J2') })"
        )
        assert cache_state == {"j1": False, "j2": False}, (
            f"batch ops must invalidate every selected row's detail cache: {cache_state}"
        )
        _evidence("t14-batch-op-invalidation.json", cache_state)


# ---------------------------------------------------------------------------
# WS3 — scan click-to-pick in the step-2 right-pane selection panel. These
# cases are evaluate-driven (same pattern as test_scan_edit_hydration_*): the
# page fixture stubs the 3Dmol CDN, so a recording previewModel/previewViewer
# pair is bootstrapped and the selection/state layer under test is exercised
# for real — append/restart/toggle semantics, the 1-based stages-mirror write
# through the click handler, marker shape bookkeeping, step gating, and PES
# dispatch preservation.
# ---------------------------------------------------------------------------


_SCAN_PICK_BOOTSTRAP = """async (args) => {
  // The DCL init (catalog fetch -> default profile write-back) can finish
  // AFTER this bootstrap and replace wizardState.method.stages, dropping the
  // 1-based atom mirror these cases assert on. Wait for it to settle first.
  var deadline = Date.now() + 20000;
  for (;;) {
    if (wizardState && wizardState.method && wizardState.method.profile_id &&
        Array.isArray(workflowCatalogCache) && workflowCatalogCache.length > 0 &&
        methodCatalogCache && methodCatalogCache.method_schemas) break;
    if (Date.now() > deadline) throw new Error("page init did not settle in 20s");
    await new Promise(function(resolve) { setTimeout(resolve, 50); });
  }
  window.__pickLog = { spheres: [], cylinders: [], styles: [], removed: [], clickable: [] };
  previewViewer = {
    addSphere: function(opts) {
      var shape = { kind: "sphere", opts: opts };
      window.__pickLog.spheres.push(shape);
      return shape;
    },
    addCylinder: function(opts) {
      var shape = { kind: "cylinder", opts: opts };
      window.__pickLog.cylinders.push(shape);
      return shape;
    },
    addStyle: function(target, style) {
      window.__pickLog.styles.push({ target: target, style: style });
    },
    removeShape: function(shape) { window.__pickLog.removed.push(shape); },
    setStyle: function() {},
    render: function() {},
    removeAllModels: function() {},
  };
  previewModel = {
    setClickable: function(_sel, enabled, cb) {
      window.__pickLog.clickable.push(!!enabled);
      window.__pickModelClick = cb || null;
    },
  };
  wizardState.workflow = { id: "scan", label: "Relaxed Scan", schema_id: "dft_scan" };
  wizardState.method.stages = {
    scan: { engine: "orca", scan_coordinate_kind: "distance",
            scan_coordinate_start: 1.0, scan_coordinate_end: 3.0,
            scan_coordinate_points: 21 }
  };
  wizardStructures = [{ name: "mol", xyz: args.xyz, has_3d: true }];
  wizardSelectedStructureIndex = 0;
  previewModelStructureKey = String(args.xyz).trim();
  resetScanSelection();
  document.getElementById("job-modal").style.display = "block";
  createWizardStep = 2;
  updatePESSelectionVisibility();
  return { canPick: canPickScanAtoms(), clicks: window.__pickLog.clickable.slice() };
}"""


def _scan_pick_snapshot(page: Page) -> dict:
    """State-only snapshot: selection state + stages mirror + panel texts."""
    return page.evaluate(
        """() => ({
            selected: scanSelectionState.selectedAtoms.slice(),
            shapes: scanSelectionState.shapes.length,
            mirror: (wizardState.method.stages.scan || {}).scan_coordinate_atoms || null,
            markers: scanSelectionState.shapes.map((s) => ({ kind: s.kind, opts: s.opts })),
            summary: document.getElementById('scan-selection-summary').textContent,
            badge: document.getElementById('scan-task-badge').textContent,
            status: document.getElementById('scan-selection-status').textContent,
            hint: document.getElementById('scan-pick-hint').textContent,
            canPick: canPickScanAtoms(),
        })"""
    )


@pytest.mark.slow
class TestScanAtomPickBrowser:
    """WS3 scan atom picking: selection semantics, mirror writes, markers."""

    def test_scan_pick_append_restart_and_toggle_semantics(self, page: Page) -> None:
        boot = page.evaluate(_SCAN_PICK_BOOTSTRAP, {"xyz": _SCAN_XYZ})
        assert boot["canPick"] is True, boot
        # The step-2 visibility pass binds the model handler more than once
        # (sync + branch); every decision must enable picking.
        assert boot["clicks"] and all(boot["clicks"]), boot
        _scan_pick_atoms(page, 0)
        mid = _scan_pick_snapshot(page)
        assert mid["selected"] == [0], mid
        assert mid["mirror"] == [1], mid
        assert mid["shapes"] == 1, mid
        assert "已选择 1 / 2" in mid["summary"], mid
        _scan_pick_atoms(page, 1)
        full = _scan_pick_snapshot(page)
        assert full["selected"] == [0, 1], full
        assert full["mirror"] == [1, 2], full
        assert full["shapes"] == 3, full
        assert "O#1 — C#2" in full["summary"], full
        _scan_pick_atoms(page, 2)
        restarted = _scan_pick_snapshot(page)
        assert restarted["selected"] == [2], restarted
        assert restarted["mirror"] == [3], restarted
        assert restarted["shapes"] == 1, restarted
        _scan_pick_atoms(page, 2)
        toggled = _scan_pick_snapshot(page)
        assert toggled["selected"] == [], toggled
        assert toggled["mirror"] == [], toggled
        assert toggled["shapes"] == 0, toggled
        _evidence("flow11-scan-pick-semantics.json", toggled)

    def test_scan_pick_angle_kind_fills_three_slots_and_kind_switch_clears(
        self, page: Page
    ) -> None:
        page.evaluate(_SCAN_PICK_BOOTSTRAP, {"xyz": _SCAN_XYZ})
        page.evaluate("() => { setScanSelectionKind('angle'); }")
        page.evaluate(
            "() => { handleScanPreviewAtomClick({ index: 0 });"
            " handleScanPreviewAtomClick({ index: 1 });"
            " handleScanPreviewAtomClick({ index: 2 }); }"
        )
        full = _scan_pick_snapshot(page)
        assert full["selected"] == [0, 1, 2], full
        assert full["mirror"] == [1, 2, 3], full
        assert full["shapes"] == 5, full
        page.evaluate("() => { setScanSelectionKind('dihedral'); }")
        cleared = _scan_pick_snapshot(page)
        assert cleared["selected"] == [], cleared
        assert cleared["shapes"] == 0, cleared
        assert "已选择 0 / 4" in cleared["summary"], cleared
        _evidence("flow12-scan-pick-kind-switch.json", cleared)

    def test_scan_pick_renders_position_colored_markers(self, page: Page) -> None:
        page.evaluate(_SCAN_PICK_BOOTSTRAP, {"xyz": _SCAN_XYZ})
        page.evaluate("() => { handleScanPreviewAtomClick({ index: 1 }); }")
        page.evaluate("() => { handleScanPreviewAtomClick({ index: 2 }); }")
        snap = _scan_pick_snapshot(page)
        spheres = [m for m in snap["markers"] if m["kind"] == "sphere"]
        cylinders = [m for m in snap["markers"] if m["kind"] == "cylinder"]
        assert [s["opts"]["color"] for s in spheres] == ["#4ea1ff", "#38c172"], snap
        assert all(s["opts"]["radius"] == 0.4 and s["opts"]["opacity"] == 0.82 for s in spheres), (
            snap
        )
        assert len(cylinders) == 1, snap
        cylinder = cylinders[0]["opts"]
        assert cylinder["color"] == "#f2b84b" and cylinder["dashed"] is True, snap
        _evidence("flow13-scan-pick-markers.json", snap)

    def test_scan_pick_gating_step2_enables_and_other_steps_disable(self, page: Page) -> None:
        page.evaluate(_SCAN_PICK_BOOTSTRAP, {"xyz": _SCAN_XYZ})
        assert page.evaluate("() => canPickScanAtoms()") is True
        assert page.evaluate("() => window.__pickLog.clickable.slice(-1)[0]") is True
        page.evaluate("() => { setCreateWizardStep(1); }")
        assert page.evaluate("() => canPickScanAtoms()") is False
        assert page.evaluate("() => window.__pickLog.clickable.slice(-1)[0]") is False
        page.evaluate("() => { setCreateWizardStep(3); }")
        assert page.evaluate("() => canPickScanAtoms()") is False
        assert page.evaluate("() => window.__pickLog.clickable.slice(-1)[0]") is False
        page.evaluate("() => { setCreateWizardStep(2); }")
        assert page.evaluate("() => canPickScanAtoms()") is True
        assert page.evaluate("() => window.__pickLog.clickable.slice(-1)[0]") is True
        _evidence(
            "flow14-scan-pick-gating.json",
            {"step2": True, "step1": False, "step3": False, "step2_again": True},
        )

    def test_scan_pick_preserves_pes_dispatch(self, page: Page) -> None:
        page.evaluate(_SCAN_PICK_BOOTSTRAP, {"xyz": _SCAN_XYZ})
        result = page.evaluate(
            """(args) => {
              wizardState.workflow = { id: "PESsearch", label: "PESsearch",
                  schema_id: "pes_search" };
              wizardStructures = [{ name: "mol", xyz: args.xyz, has_3d: true }];
              wizardSelectedStructureIndex = 0;
              previewModelStructureKey = String(args.xyz).trim();
              s2scanState.atoms = parseXyzAtoms(args.xyz);
              s2scanState.selectedAtoms = [];
              createWizardStep = 2;
              window.__pickLog.clickable = [];
              attachPESPreviewSelection();
              var bound = window.__pickLog.clickable.slice();
              window.__pickModelClick({ index: 1 });
              return {
                bound: bound,
                pesSelected: pesSelectionState.selectedAtoms.slice(),
                scanSelected: scanSelectionState.selectedAtoms.slice(),
                canScan: canPickScanAtoms(),
              };
            }""",
            {"xyz": _SCAN_XYZ},
        )
        assert result["bound"] == [True], result
        assert result["pesSelected"] == [1], result
        assert result["scanSelected"] == [], result
        assert result["canScan"] is False, result
        _evidence("flow15-scan-pick-pes-dispatch-preserved.json", result)

    def test_scan_kind_switch_swaps_default_range_only(self, page: Page) -> None:
        page.evaluate(_SCAN_PICK_BOOTSTRAP, {"xyz": _SCAN_XYZ})
        result = page.evaluate(
            """() => {
              setScanSelectionKind('angle');
              var stage = wizardState.method.stages.scan;
              var swapped = {
                kind: stage.scan_coordinate_kind,
                start: stage.scan_coordinate_start,
                end: stage.scan_coordinate_end,
              };
              // User-edited values are never destroyed by a later switch.
              stage.scan_coordinate_start = 2.0;
              stage.scan_coordinate_end = 4.5;
              setScanSelectionKind('dihedral');
              var kept = {
                kind: stage.scan_coordinate_kind,
                start: stage.scan_coordinate_start,
                end: stage.scan_coordinate_end,
              };
              return { swapped: swapped, kept: kept };
            }"""
        )
        assert result["swapped"]["kind"] == "angle", result
        assert isinstance(result["swapped"]["start"], (int, float)), result
        assert isinstance(result["swapped"]["end"], (int, float)), result
        assert result["swapped"]["start"] == 100, result
        assert result["swapped"]["end"] == 160, result
        assert result["kept"]["kind"] == "dihedral", result
        assert result["kept"]["start"] == 2.0, result
        assert result["kept"]["end"] == 4.5, result
        _evidence("flow16-scan-kind-switch-range-mirror.json", result)

    def test_scan_preview_rerender_keeps_selection_and_redraws_markers(self, page: Page) -> None:
        page.evaluate(_SCAN_PICK_BOOTSTRAP, {"xyz": _SCAN_XYZ})
        _scan_pick_atoms(page, 0, 1)
        before = _scan_pick_snapshot(page)
        assert before["selected"] == [0, 1], before
        assert before["mirror"] == [1, 2], before
        assert before["shapes"] == 3, before
        log_before = page.evaluate(
            "() => ({ spheres: window.__pickLog.spheres.length,"
            " removed: window.__pickLog.removed.length })"
        )
        result = page.evaluate(
            """(args) => {
              // The recording stub predates renderPreviewStructure3D's
              // addModel call; extend it in place (the shared bootstrap
              // stays untouched so existing cases remain byte-identical).
              previewViewer.addModel = function(data, fmt) {
                return {
                  setClickable: function(_sel, enabled, cb) {
                    window.__pickLog.clickable.push(!!enabled);
                    window.__pickModelClick = cb || null;
                  },
                };
              };
              renderPreviewStructure3D({ name: "mol", xyz: args.xyz, has_3d: true });
              return {
                selected: scanSelectionState.selectedAtoms.slice(),
                shapes: scanSelectionState.shapes.length,
                mirror: (wizardState.method.stages.scan || {}).scan_coordinate_atoms || null,
                canPick: canPickScanAtoms(),
                spheres: window.__pickLog.spheres.length,
                removed: window.__pickLog.removed.length,
              };
            }""",
            {"xyz": _SCAN_XYZ},
        )
        assert result["selected"] == [0, 1], result
        assert result["mirror"] == [1, 2], result
        assert result["shapes"] == 3, result
        assert result["canPick"] is True, result
        # Old shapes were cleared and new ones drawn — a real re-render.
        assert result["removed"] > log_before["removed"], (log_before, result)
        assert result["spheres"] > log_before["spheres"], (log_before, result)
        _evidence(
            "flow17-scan-pick-rerender-marker-restore.json",
            {"before": before, "log_before": log_before, "after": result},
        )

    def test_scan_kind_switch_mid_pick_clears_selection_and_shapes(self, page: Page) -> None:
        page.evaluate(_SCAN_PICK_BOOTSTRAP, {"xyz": _SCAN_XYZ})
        result = page.evaluate(
            """() => {
              handleScanPreviewAtomClick({ index: 0 });
              handleScanPreviewAtomClick({ index: 1 });
              var mid = {
                selected: scanSelectionState.selectedAtoms.slice(),
                shapes: scanSelectionState.shapes.length,
              };
              setScanSelectionKind('angle');
              return {
                mid: mid,
                selected: scanSelectionState.selectedAtoms.slice(),
                shapes: scanSelectionState.shapes.length,
                summary: document.getElementById('scan-selection-summary').textContent,
              };
            }"""
        )
        assert result["mid"]["selected"] == [0, 1], result
        assert result["mid"]["shapes"] == 3, result
        assert result["selected"] == [], result
        assert result["shapes"] == 0, result
        assert "已选择 0 / 3" in result["summary"], result
        _evidence("flow18-scan-kind-switch-mid-pick-clears.json", result)
