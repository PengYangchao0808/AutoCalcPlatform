/*
 * ACP Workbench controller — task selection session (R1).
 *
 * Owner: frontend/js/workbench/controllers/** (OpenCode).
 * Version: v20261017.1
 *
 * Owns the lifecycle of ONE task selection. Every select(jobId) mints a
 * monotonic session token — even for the same job — so a superseded
 * session's late detail/files/failure can never touch a newer session's
 * panels (A→B→A races and same-job reselect/rerun races are both covered;
 * the raw `selectedJobId` string compares this replaces could not tell the
 * two A sessions apart). The page global `selectedJobId` stays the single
 * page-authoritative selection: this controller never writes it — the
 * page's resetForSelection hook does, exactly as before.
 *
 * Pure controller: no DOM, no window globals, no network of its own. The
 * page (features/reaction-builder.js::selectJob) registers its hooks once
 * per selection via init({hooks}) and then calls select(jobId). Hooks are
 * captured at select() entry — a later init() never redirects an in-flight
 * older session. Every awaited hook is followed by an isCurrent(session)
 * check; a superseded session stops silently (return null) and its failures
 * are swallowed, never surfaced onto the newer session.
 *
 * Hook mapping (former inline 20-step sequence in selectJob → hook):
 *   1-8  selectedJobId write + folder/mechanism/energy/s2scan resets,
 *        energyGraphDestroyViewer, renderMechanismPlaceholder,
 *        candidateReview.reset          → resetForSelection(jobId, session)
 *   9    fetchJobDetail (GAP-9 generation/stale guards inside) → loadDetail(jobId, session)  [awaited]
 *   9b   job = detail.job || findJobRowInCache; lastRemoteNode/
 *        selectedJobIsRemote writes      → applyDetail(detail, session) → job|null
 *   10   local /files or remote /remote-files (60s budget) + shape remap
 *                                        → loadFiles(jobId, job, session)    [awaited]
 *   11   folderCache[""] + lastPinnedItems → applyFiles(filesResp, session)
 *   12   updateInfoPanel + renderFileTree + refreshNmrReport (order kept)
 *                                        → afterPanels(session)
 *   13   setViewerTab(path|energy)       → reloadViewerTab(session)          [awaited]
 *   14   highlightQueueRow               → highlight(jobId, session)
 *   15   close old SSE + openSSE         → openEvents(jobId, session)
 *   16   pollLogTail (Q3-P0 stdout tail) → primeLogs(jobId, session)
 *   17   await refreshJobs               → refreshList(session)              [awaited]
 *   18   ACPStructureViewer.onJobSelected → onStructureCatalog(jobId, session)
 *   19+20 mechanism WORK/study auto-expand + v2 RESULT auto-expand
 *                                        → afterSelection(session)
 *   (optional) onSelectionError(err, jobId, session) — current-session
 *        failures only; the error is rethrown after the hook, matching the
 *        former inline behavior where fetch errors propagated to the caller.
 */
window.ACPWorkbench = window.ACPWorkbench || {};
ACPWorkbench.controllers = ACPWorkbench.controllers || {};
ACPWorkbench.controllers.taskSession = (function () {
  "use strict";

  // Monotonic for the module lifetime. init() deliberately NEVER resets it:
  // the page re-registers hooks on every selection, and same-job reselect
  // must still mint a fresh token so the old session dies.
  var tokenSeq = 0;
  var currentSession = null; // { jobId, token }
  var hooks = null;

  function init(injected) {
    hooks = (injected && injected.hooks) || null;
  }

  /** Snapshot of the live selection, or null when cleared. */
  function current() {
    return currentSession
      ? { jobId: currentSession.jobId, token: currentSession.token }
      : null;
  }

  /** True only for the session object minted by the latest select()/clear(). */
  function isCurrent(session) {
    return !!(session && currentSession && session.token === currentSession.token);
  }

  /** Selection cleared (row stale / drawer reset). Bumps the token so any
   * in-flight session stops at its next isCurrent check. */
  function clear() {
    tokenSeq += 1;
    currentSession = null;
  }

  /** Page teardown: clear + detach hooks. */
  function dispose() {
    clear();
    hooks = null;
  }

  async function select(jobId) {
    if (!hooks) throw new Error("taskSession controller: init({hooks}) required");
    var h = hooks; // captured: a later init() never redirects this session
    var session = { jobId: String(jobId == null ? "" : jobId), token: ++tokenSeq };
    currentSession = session;
    try {
      if (h.resetForSelection) h.resetForSelection(session.jobId, session);
      if (!session.jobId) return null;

      // 9: rich detail (or legacy fallback). Superseded sessions release
      // their late response without touching any panel.
      var detail = h.loadDetail ? await h.loadDetail(session.jobId, session) : null;
      if (!isCurrent(session)) return null;

      // 9b: derive the job row; null job (gone from server AND list) ends
      // the session exactly like the former inline `if (!job) return`.
      var job = h.applyDetail ? await h.applyDetail(detail, session) : null;
      if (!isCurrent(session)) return null;
      if (!job) return null;

      // 10: file listing (local vs remote).
      var filesResp = h.loadFiles ? await h.loadFiles(session.jobId, job, session) : null;
      if (!isCurrent(session)) return null;

      // 11-12: projections + panel renders (page owns ordering).
      if (h.applyFiles) h.applyFiles(filesResp, session);
      if (h.afterPanels) h.afterPanels(session);
      if (!isCurrent(session)) return null;

      // 13: restore/refresh the active path|energy viewer tab.
      if (h.reloadViewerTab) await h.reloadViewerTab(session);
      if (!isCurrent(session)) return null;

      // 14-16: queue highlight, event stream, stdout tail.
      if (h.highlight) h.highlight(session.jobId, session);
      if (h.openEvents) h.openEvents(session.jobId, session);
      if (h.primeLogs) h.primeLogs(session.jobId, session);

      // 17: list refresh (row badges/recovery converge with the selection).
      if (h.refreshList) await h.refreshList(session);
      if (!isCurrent(session)) return null;

      // 18: structure catalog + default entry geometry.
      if (h.onStructureCatalog) h.onStructureCatalog(session.jobId, session);

      // 19-20: mechanism / v2 RESULT auto-expand.
      if (h.afterSelection) h.afterSelection(session);

      return current();
    } catch (err) {
      // A superseded session's failure must not clear or touch the newer
      // session's panels — swallow it (the newer session owns the UI now).
      if (!isCurrent(session)) return null;
      if (h.onSelectionError) h.onSelectionError(err, session.jobId, session);
      throw err;
    }
  }

  return {
    init: init,
    select: select,
    current: current,
    isCurrent: isCurrent,
    clear: clear,
    dispose: dispose,
  };
})();
