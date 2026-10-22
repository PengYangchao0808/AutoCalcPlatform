# fix-multiframe-playback-bar - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** 打开包含多帧结构的文件（例如 all_conformer.xyz）后，底部那条播放控制条不会再在按下播放后消失；播放、上一帧/下一帧、进度条拖动都能正常驱动结构动画，切到其他标签页或单帧文件时依然隐藏。

**Why this approach:** 唯一的病根是页签改名后遗留的一处旧页签标识判断，使"该显示播放条"的条件永远为假，于是任何刷新可见性的动作（播放走帧、翻帧、拖进度条）都会把播放条关掉；因此修复就是把这处判断改为现行页签标识，并用两层测试锁死：默认 CI 每次运行的行为测试 + 真实浏览器端到端测试。

**What it will NOT do:** 不修复 IRC 播放链路与超大体系降级模式下的另一个已知缺陷（另案处理）；不改播放速度、帧顺序、停止语义，也不改页签、样式或后端。

**Effort:** Short
**Risk:** Low - 单行逻辑修复且被两层测试覆盖；唯一驱动因素是行为测试靠字符串抽取函数源码，若未来函数被移动或改名需同步调整测试。
**Decisions to sanity-check:** 1) IRC/降级大体系下同一元素的空指针缺陷明确排除在本次范围外（已在计划中标注为 known gap）；2) 新增一个浏览器端到端测试文件并接入 CI，而不只做静态断言——你已选择此项。

Your next move: 选择「现在开始执行」或「先做一次高精度复核」，我不会自行开始执行。完整执行细节见下方。

---

> TL;DR (machine): Effort Short, Risk Low — fix the stale `data-tab === "3d"` visibility gate so the multi-frame playback bar stays visible during playback; locked by a default-CI Node behavioral contract test plus a CI-wired Playwright E2E, with a one-paragraph dev-doc contract note.

## Scope
### Must have
- `frontend/ACP_Workbench_v2.html`: the single condition in `updateFrameController()` (line 17948) must gate the multi-frame playback controller on the LIVE structure tab id (`data-tab="structure"`), not the retired `"3d"`, so Play / prev / next / slider keep the bar visible while frames advance.
- `tests/test_frontend_sync.py`: one red-first regression test that (a) statically proves the gate references a rendered `data-tab` value and that the exact stale pattern `getAttribute("data-tab") === "3d"` occurs 0 times, and (b) executes the extracted `updateFrameController()` in Node with DOM stubs, asserting `flex` for the structure tab with >1 frames and `none` for `energy`/`path`/`wavefunction` tabs, a single frame, or a missing active tab.
- New `tests/test_frontend_multiframe_playback_browser.py`: a Playwright E2E test that reproduces the reported flow (multi-frame XYZ load → bar visible → click Play → wait past one tick → bar STILL visible, frame advanced, Stop restores the idle state, no uncaught page errors).
- `.github/workflows/ci.yml`: the new browser test file is added to the py3.11 browser leg (`--run-slow`).
- `docs/ACP_Structure_Viewer_DevDoc.md`: one short contract note so the tab-id invariant is discoverable (frame-controller visibility is owned by `updateFrameController()` and its gate must reference a live `data-tab` value).

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Do NOT touch the intentional `setViewerTab` compat shim at `frontend/ACP_Workbench_v2.html:12630` (`if (tab === "3d" || tab === "conformers") tab = "structure";`) or the tab-merge contract test at `tests/test_frontend_sync.py:103-116`.
- Do NOT change tab ids/names, i18n keys, CSS/`.frame-controller` layout, or `#frame-controller`'s child element ids/classes.
- Do NOT change `toggleFramePlayback`'s 900 ms interval, frame order, stop semantics, or add new playback controls/speed UI.
- Do NOT touch the IRC playback path (`playIrcPath`, `stopIrcPlayback`, `_renderPlaybackBar`, `_renderIrcFrameController`) or the degraded large-system wireframe path; the known null-deref when IRC repurposes `#frame-controller` (`frontend/js/structure_viewer.js:1250-1355`) is explicitly OUT of scope.
- Do NOT add stop-playback-on-tab-switch behavior; do NOT refactor or rename `updateFrameController`.
- Do NOT edit `frontend/ACP_Workbench.html` (v1 — it never had this control), any backend/API file, or any unrelated frontend module.
- Do NOT add new dependencies; do NOT require the browser E2E in the default `-m "not slow"` suite.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **TDD (red-first)**. Framework: pytest. The contract/behavior test lives in `tests/test_frontend_sync.py` (string assertions + a Node-executed behavioral test using the existing `shutil.which("node")` + `subprocess.run(["node", "-e", ...])` pattern at `tests/test_frontend_sync.py:8248-8290`). The end-to-end test is Playwright, following `tests/test_frontend_measure_edit_browser.py` (module-level `pytest.mark.slow`, `shutil`-free browser skip, `pageerror` capture).
- Evidence: `.omo/evidence/task-<N>-fix-multiframe-playback-bar.log` for every todo (attemptDir = `.omo/evidence/` because this plan runs outside ulw-loop). Each log must contain the literal command, stdout/stderr, and exit code.
- Red-first proof: the new `tests/test_frontend_sync.py` test MUST fail against the unfixed HTML (captured), then pass after the one-line fix. The browser E2E MUST be shown failing with the pre-fix condition (temporary one-line revert), then passing once restored — both runs captured.
- Regression commands: `pytest tests/test_frontend_sync.py -q` (default CI), `pytest tests/ -m "not slow" -q` (full default suite), `pytest tests/test_frontend_multiframe_playback_browser.py --run-slow -q` (browser; skips cleanly without playwright/Chromium/CDN).
- Manual QA (agent-executed, F3): start the app (`acp run serve --port 8765`), open the Workbench with a job containing a multi-frame `RESULT/structures/all_conformers.xyz` (or load a multi-frame XYZ via `window._svLoadXyzToViewer` in the browser console), press Play, confirm the bar stays and the frame counter advances; take a screenshot artifact under `.omo/evidence/`.

Pre-verified by exploration (do NOT re-investigate):
- Baseline: branch `main`, `git status --porcelain` empty at plan time; `.omo/` is not tracked.
- The IRC path cannot be made worse by the fix: `_renderIrcFrameController` (`frontend/js/structure_viewer.js:1266-1355`, sole caller `_renderPlaybackBar:1263`, degraded/large systems only) clears the controller's children and sets `display:flex` itself; any later `updateFrameController()` throws at the child derefs (~17942) BEFORE reaching the gate (~17948), so the changed literal is unreachable in that state.
- No existing test locks the stale literal or these DOM ids: `tests/test_frontend_sync.py:8242` uses the string `function updateFrameController` only as an extraction boundary; the `"3d"` assertions at `tests/test_frontend_sync.py:103-116` (and the duplicate at ~3163) are unaffected; no test references `frame-controller`/`frame-play`.
- `#frame-play` carries `data-i18n="frame.play"` and there is no `frame.stop` key ("Stop" is hardcoded at ~17979): a language switch during playback resets the label while the timer keeps running. Pre-existing cosmetic quirk — do NOT fix in this plan (out of scope); the E2E must not assert locale-dependent idle text.

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.
- **Wave 1** — Todo 1 (core fix + Node contract/behavior test). Serial; everything else depends on the corrected gate for its green run.
- **Wave 2** — Todo 2 (browser E2E + CI wiring). Runs after the fix; its red-proof uses a controlled temporary revert of the single line.
- **Wave 3** — Todo 3 (dev-doc contract note). Independent of Todos 1-2's code; can run in parallel with Wave 2 once Todo 1's wording is settled.
- **Final verification wave** — F1-F4 in parallel after all todos.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 (fix + Node test) | — | 2 (green run), 3 (doc wording) | — |
| 2 (E2E + CI) | 1 | — | 3 |
| 3 (dev-doc note) | 1 | — | 2 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. Fix the `updateFrameController` visibility gate + add the Node behavioral/contract regression test
  What to do / Must NOT do:
  - In `frontend/ACP_Workbench_v2.html` line 17948, change `activeTab.getAttribute("data-tab") === "3d"` to `activeTab.getAttribute("data-tab") === "structure"` and add a short inline comment ("frame controller is visible only on the structure tab; tab ids live at lines 3613-3616").
  - In `tests/test_frontend_sync.py`, add one test (suggested name `test_frame_controller_visibility_gate_uses_live_tab_id`) that does BOTH: (i) STATIC: assert `html.count('getAttribute("data-tab") === "3d"') == 0` (exact stale pattern — verified present exactly once pre-fix at line 17948), and that the single `getAttribute("data-tab") === "X"` literal inside the extracted function has `X` in the rendered `data-tab="..."` set (note: the shim at 12630 compares the `setViewerTab` argument, not `getAttribute("data-tab")`, so it is not matched by this pattern; its lock stays at `tests/test_frontend_sync.py:115-116`). (ii) BEHAVIORAL: skip if `shutil.which("node")` is falsy, else build a Node script (same style as `tests/test_frontend_sync.py:8248-8290`) that evals the extracted function with stubs — `document.getElementById(id)` -> `{style:{}, textContent:"", max:"", value:""}` per id, `document.querySelector(".canvas-tab.active")` -> `{getAttribute: () => tabId}`, `molDoc = {frames: [{energy:null},{energy:null}], currentFrame: 0}`, `currentFrame = () => ({energy: null})` — then assert `#frame-controller.style.display`: `"flex"` for `tabId="structure"` with 2 frames, `"none"` for `tabId` in `{"energy","path","wavefunction"}` with 2 frames, `"none"` for `tabId="structure"` with 1 frame, and `"none"` when `tabId === null` (querySelector miss); exit 0 + print PASS.
  - RED-FIRST: write the test, run it against the UNFIXED HTML first and capture the failure, then apply the HTML fix and re-run to green.
  Must NOT do: touch the shim at 12630, tab ids, i18n, CSS, or add null-guards/refactors to the function (the IRC-repurposed-controller deref is out of scope); change anything else in the HTML.
  Parallelization: Wave 1 | Blocked by: none | Blocks: Todo 2, Todo 3 | Can parallelize with: none
  References (executor has NO interview context - be exhaustive):
  - `frontend/ACP_Workbench_v2.html:17937-17949` (function body; 17948 is the only edit site)
  - `frontend/ACP_Workbench_v2.html:3610-3617` (canvas tab buttons: `data-tab="structure" | "path" | "energy" | "wavefunction"`)
  - `frontend/ACP_Workbench_v2.html:12629-12697` (`setViewerTab`; compat shim 12630; controller shown 12669; hidden 12654/12685)
  - `frontend/ACP_Workbench_v2.html:17951-17983` (`setFrame` -> `updateFrameController`; `stopFramePlayback`; `toggleFramePlayback` 900 ms)
  - `frontend/ACP_Workbench_v2.html:15266-15288`, `15312-15335`, `23796-23799` (other `updateFrameController` callers and button wiring)
  - `tests/test_frontend_sync.py:17-31` (`FRONTEND` path), `103-116` (tab-merge locks that must keep passing), `8237-8245` (function-body extraction precedent), `8248-8290` (Node execution pattern)
  Acceptance criteria (agent-executable):
  - `pytest tests/test_frontend_sync.py -q` exits 0 (including the existing tab-merge assertions at lines 103-116).
  - The new test fails on the unfixed HTML (captured) and passes after the fix; `git diff frontend/ACP_Workbench_v2.html` shows exactly one changed line plus a comment.
  QA scenarios (name the exact tool + invocation): happy = `pytest tests/test_frontend_sync.py -k frame_controller -q` after the fix -> PASS; failure = revert the literal to `"3d"`, rerun -> FAIL naming the tab id, then restore. Evidence `.omo/evidence/task-1-fix-multiframe-playback-bar.log`
  Commit: Y | fix(frontend): keep multi-frame playback bar visible on the structure tab
- [x] 2. Add the Playwright E2E regression test + wire it into CI
  What to do / Must NOT do:
  - Create `tests/test_frontend_multiframe_playback_browser.py` modelled on `tests/test_frontend_measure_edit_browser.py`: module docstring, `pytest.importorskip("playwright")`, `pytestmark = pytest.mark.slow`, `_find_free_port`, `_chromium_launchable`, session `server_url` fixture (temp `ACP_RUN_ROOT` + `create_app` + uvicorn thread), `browser_errors` fixture and `page.on("pageerror", ...)`, `browser_page` fixture (headless Chromium, 3Dmol CDN wait with skip, `initViewer()` assert, wait for `window._svLoadXyzToViewer`), and `_assert_no_page_errors`.
  - Add a 2-frame XYZ constant (same atom count in both frames, e.g. the butane coordinates plus a copy with one methyl translated) and a test (suggested name `test_multiframe_playback_bar_stays_visible_during_play`): load via `window._svLoadXyzToViewer(TWO_FRAME_XYZ, "playback_test")`; `await page.evaluate("async () => { await setViewerTab('structure'); }")` (mirrors the file-open flow, `frontend/ACP_Workbench_v2.html:18000`); assert `page.is_visible("#frame-controller")` and `#frame-total` text `"2"`; capture `molDoc.currentFrame` (expect 0); click `#frame-play`; assert `#frame-play` text is `"Stop"`; then WAIT EVENT-DRIVEN — `page.wait_for_function("() => molDoc.currentFrame >= 1", timeout=5000)` (do NOT use a fixed sleep; do NOT assert exact equality with 1); assert `page.is_visible("#frame-controller")` (THE regression assertion), `molDoc.currentFrame >= 1`, `#frame-play` text still `"Stop"`; click `#frame-play` again; assert `page.evaluate("() => framePlayTimer === null")` and the button text is not `"Stop"`; `_assert_no_page_errors(browser_errors)`.
  - The new module MUST carry `pytest.importorskip("playwright")` and a module-level `pytestmark = pytest.mark.slow` (precedent `tests/test_frontend_measure_edit_browser.py:28-31,41`) so the default `-m "not slow"` leg (`/.github/workflows/ci.yml:50`) skips it.
  - Add `tests/test_frontend_multiframe_playback_browser.py` to the explicit file list in the `Run browser tests` step (`/.github/workflows/ci.yml:61`).
  - RED-FIRST: temporarily set `frontend/ACP_Workbench_v2.html:17948` back to `"3d"`, run the E2E -> it must FAIL on the post-play visibility assertion; restore `"structure"`, rerun -> PASS.
  Must NOT do: modify `tests/test_frontend_measure_edit_browser.py` or `tests/test_frontend_viewer_framing_browser.py`; assert locale-dependent button text (`"Play"`/`"播放"`); create real scheduler jobs or require files on disk; make the test fail when playwright/Chromium/CDN are absent (skip instead).
  Parallelization: Wave 2 | Blocked by: Todo 1 | Blocks: none | Can parallelize with: Todo 3
  References (executor has NO interview context - be exhaustive):
  - `tests/test_frontend_measure_edit_browser.py:1-41` (header/importorskip/slow mark), `97-116` (`_find_free_port`, `_chromium_launchable`, `_assert_no_page_errors`), `119-137` (`_load_molecule` bridge usage), `192-303` (fixtures incl. `pageerror` capture at 267), `311-336` (test style)
  - `tests/test_frontend_viewer_framing_browser.py:9-16` (browser prerequisites), `229-275` (slow test style)
  - `.github/workflows/ci.yml:32-61` (install extras; default suite line 50; browser leg 52-61)
  - `frontend/ACP_Workbench_v2.html:17937-17949`, `17965-17983`, `23796-23799`, `12664-12682` (gate, playback, wiring, structure-tab show)
  - `frontend/js/structure_viewer.js:796-803` (main-canvas bridge), `1596-1657` (`loadSelectedGeometry`), `1690-1760` (`injectManualEntry`/`selectEntry`)
  - `frontend/ACP_Workbench_v2.html:15203-15235` (`parseMultiFrameXYZ`), `15312-15335` (`_svLoadXyzToViewer`)
  Acceptance criteria (agent-executable):
  - `pytest tests/test_frontend_multiframe_playback_browser.py --run-slow -q` passes when playwright + Chromium + the 3Dmol CDN are available, and SKIPS with an explicit reason otherwise (never errors).
  - `grep -n "test_frontend_multiframe_playback_browser" .github/workflows/ci.yml` returns the browser-step line.
  - The new module contains both `pytest.importorskip("playwright")` and a module-level `pytestmark = pytest.mark.slow`; `pytest tests/test_frontend_multiframe_playback_browser.py -q` (no `--run-slow`) collects/skips it without running a browser.
  - Regression: `pytest tests/test_frontend_sync.py -q`, `pytest tests/test_frontend_measure_edit_browser.py --run-slow -q`, and `pytest tests/test_frontend_viewer_framing_browser.py --run-slow -q` all pass (or skip as designed) after this todo.
  - Red-proof and green run both captured; `git diff` after restore shows only the intended HTML line.
  QA scenarios (name the exact tool + invocation): happy = the `--run-slow` invocation above after the fix -> PASS; failure = with the temporary `"3d"` revert -> FAIL on `page.is_visible("#frame-controller")` after the tick. Evidence `.omo/evidence/task-2-fix-multiframe-playback-bar.log`
  Commit: Y | test(frontend): lock multi-frame playback bar visibility with a browser regression test
- [x] 3. Record the frame-controller visibility contract in the structure-viewer dev doc
  What to do / Must NOT do:
  - In `docs/ACP_Structure_Viewer_DevDoc.md`, extend the existing "Unified frame controller" bullet (line 46, currently IRC/scan-centric) so it also names multi-frame XYZ file playback as a primary user (the reported case), and record the visibility contract: `#frame-controller` (defined at `frontend/ACP_Workbench_v2.html:3743`) visibility is owned by `updateFrameController()` (~line 17948) for ALL mutation paths (Play/prev/next/slider, file loads, i18n re-apply); its gate MUST reference a live `data-tab` value — the structure tab id is `structure`; `setViewerTab()` also shows/hides it by frame count (~line 12669); legacy `"3d"`/`"conformers"` survive only as `setViewerTab` input aliases (~line 12630). Add one explicit "known gap" sentence: when the IRC controller repurposes the same element on degraded (large) systems, `updateFrameController()` throws at its child derefs (~17942) before reaching the gate, so this gate does NOT guard that state (tracked separately).
  Must NOT do: rewrite other doc sections; add new files; touch code or tests in this todo.
  Parallelization: Wave 3 | Blocked by: Todo 1 | Blocks: none | Can parallelize with: Todo 2
  References (executor has NO interview context - be exhaustive):
  - `docs/ACP_Structure_Viewer_DevDoc.md:7-11` (overview already names `data-tab="structure"`), `29-53` (UX spec, incl. line 46), `224-231` (frontend module table)
  - `frontend/ACP_Workbench_v2.html:12630`, `12669`, `17937-17949`
  Acceptance criteria (agent-executable):
  - `grep -n "updateFrameController" docs/ACP_Structure_Viewer_DevDoc.md` returns the new line; the note names `structure` as the live tab id.
  - `git diff --stat docs/` shows exactly one doc file changed.
  QA scenarios (name the exact tool + invocation): happy = grep finds the note and its `structure` id matches the HTML tab-id set; failure = a note naming `"3d"` as the live id would fail the cross-check. Evidence `.omo/evidence/task-3-fix-multiframe-playback-bar.log`
  Commit: Y | docs(structure-viewer): record the frame-controller visibility contract

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
- [x] F2. Code quality review
- [x] F3. Real manual QA
- [x] F4. Scope fidelity
<!-- 2026-09-28: F1-F4 reviewers ALL returned VERDICT: APPROVE (evidence: .omo/evidence/f1-plan-compliance.md, f2-code-quality.md, f3-manual-qa.md + f3-playback-before.png / f3-playback-during.png, f4-scope-fidelity.md). Rows marked [x] on completion of the autonomous continuation directive; the user retains veto - any rejection triggers fixes + re-running the affected reviewer. -->


## Commit strategy
- Work on the current branch `main` (verified clean at plan time: `git status --porcelain` empty; HEAD = `main`). If the user prefers a worktree/PR, run the worker with `$start-work --worktree <path>` / `--make-pr`.
- Three atomic commits, one per todo, in execution order:
  1. `fix(frontend): keep multi-frame playback bar visible on the structure tab` (HTML fix + `tests/test_frontend_sync.py` regression test)
  2. `test(frontend): lock multi-frame playback bar visibility with a browser regression test` (new browser test + `.github/workflows/ci.yml`)
  3. `docs(structure-viewer): record the frame-controller visibility contract` (dev-doc note)
- Do NOT commit `.omo/` artifacts. Do NOT amend or rewrite unrelated history; do NOT push.
- No version bump: `__version__` sites are unrelated to a frontend bug fix (see AGENTS.md anti-pattern #2) — do not touch them.

## Success criteria
- On a multi-frame XYZ (e.g. `RESULT/structures/all_conformers.xyz`), the bottom playback bar (`#frame-controller`) stays visible while Play / prev / next / slider advance frames, and Play toggles to Stop and back.
- The bar is hidden on non-structure tabs (`path`/`energy`/`wavefunction`) and for single-frame documents.
- `pytest tests/test_frontend_sync.py -q` is green, including a Node behavioral assertion that a `structure` tab + >1 frames yields `display:flex`, `none` for `energy`/`path`/`wavefunction` tabs, a single frame, or a missing active tab — plus the static assertion that the exact stale pattern `getAttribute("data-tab") === "3d"` occurs 0 times (the intentional `setViewerTab` argument shim is separately locked and untouched).
- `pytest tests/ -m "not slow" -q` is green; the new browser test passes under `--run-slow` when playwright/Chromium/CDN are present (otherwise skips with a clear reason).
- `git diff` is limited to the four intended files (`frontend/ACP_Workbench_v2.html`, `tests/test_frontend_sync.py`, `tests/test_frontend_multiframe_playback_browser.py`, `.github/workflows/ci.yml`) plus the one dev doc; no other frontend behavior changed.
