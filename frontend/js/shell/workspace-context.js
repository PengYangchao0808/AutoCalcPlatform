/*
 * ACP Next Shell — WorkspaceContext (UI-01).
 * Authority: ACP_Frontend_UI01_Shell_Design_20261030.md + UI-00 module recipe
 * contract (embedded).
 *
 * The single source of truth for "what the user is looking at": projectId,
 * researchRef, taskRef {jobId, attempt}, structureRef, networkNodeId plus a
 * monotonic `revision` and the `source` of the last write.
 *
 * Rules (pinned by acp2_shell_context.test.js):
 *   - getSnapshot() returns a FROZEN shallow copy (taskRef cloned) so module
 *     code cannot mutate the store;
 *   - subscribe(fn) → unsubscribe function; a throwing subscriber never
 *     breaks the others;
 *   - setFocus(patch, source) merges atomically and bumps revision exactly
 *     once per effective change;
 *   - a projectId change CLEARS taskRef / structureRef / researchRef /
 *     networkNodeId (cross-project references are never carried over);
 *   - a setFocus whose values all equal the current snapshot does NOT bump
 *     revision and does NOT notify (prevents write-back feedback loops when a
 *     panel echoes the context it was handed).
 */
window.ACPShell = window.ACPShell || {};
ACPShell.context = (function () {
  "use strict";

  var FOCUS_KEYS = ["researchRef", "taskRef", "structureRef", "networkNodeId"];

  var state = {
    projectId: null,
    researchRef: null,
    taskRef: null,
    structureRef: null,
    networkNodeId: null,
    revision: 0,
    source: "init"
  };

  var subscribers = [];

  function cloneTaskRef(ref) {
    return ref && typeof ref === "object" ? Object.assign({}, ref) : ref;
  }

  function freezeSnapshot(s) {
    return Object.freeze({
      projectId: s.projectId,
      researchRef: s.researchRef,
      taskRef: cloneTaskRef(s.taskRef),
      structureRef: s.structureRef,
      networkNodeId: s.networkNodeId,
      revision: s.revision,
      source: s.source
    });
  }

  function getSnapshot() {
    return freezeSnapshot(state);
  }

  function subscribe(fn) {
    if (typeof fn !== "function") return function () {};
    subscribers.push(fn);
    return function unsubscribe() {
      var idx = subscribers.indexOf(fn);
      if (idx >= 0) subscribers.splice(idx, 1);
    };
  }

  function sameValue(a, b) {
    if (a === b) return true;
    if (a == null || b == null) return a == b; // eslint-disable-line eqeqeq
    if (typeof a !== "object" || typeof b !== "object") return false;
    try {
      return JSON.stringify(a) === JSON.stringify(b);
    } catch (e) {
      return false;
    }
  }

  function notify(snapshot) {
    // Copy: a subscriber may unsubscribe (itself or others) mid-notify.
    subscribers.slice().forEach(function (fn) {
      try {
        fn(snapshot);
      } catch (error) {
        if (typeof console !== "undefined" && console.error) {
          console.error("WorkspaceContext subscriber failed", error);
        }
      }
    });
  }

  /**
   * Atomically merge `patch` into the focus snapshot.
   * Returns the new frozen snapshot (even when unchanged). Notifies only on
   * an effective change; revision increments exactly once per change.
   */
  function setFocus(patch, source) {
    patch = patch || {};
    var next = {
      projectId: state.projectId,
      researchRef: state.researchRef,
      taskRef: state.taskRef,
      structureRef: state.structureRef,
      networkNodeId: state.networkNodeId,
      revision: state.revision,
      source: state.source
    };
    var changed = false;

    if ("projectId" in patch && !sameValue(patch.projectId, next.projectId)) {
      next.projectId = patch.projectId == null ? null : patch.projectId;
      // Cross-project refs are never carried over.
      next.researchRef = null;
      next.taskRef = null;
      next.structureRef = null;
      next.networkNodeId = null;
      changed = true;
    }

    FOCUS_KEYS.forEach(function (key) {
      if (!(key in patch)) return;
      var value = patch[key] == null ? null : patch[key];
      if (sameValue(value, next[key])) return;
      next[key] = key === "taskRef" ? cloneTaskRef(value) : value;
      changed = true;
    });

    if (!changed) return freezeSnapshot(state);

    next.revision = state.revision + 1;
    next.source = typeof source === "string" && source ? source : "setFocus";
    state = next;
    var snapshot = freezeSnapshot(state);
    notify(snapshot);
    return snapshot;
  }

  return {
    getSnapshot: getSnapshot,
    subscribe: subscribe,
    setFocus: setFocus
  };
})();
