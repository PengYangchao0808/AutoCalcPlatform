/*
 * ACP Workbench controller — candidate review controller.
 *
 * Owner: frontend/js/workbench/controllers/** (OpenCode).
 * Version: v20261017.1
 *
 * Solves:
 *   - Explicit state machine for PES/Scan candidate review (single owner).
 *   - Saving candidates updates review state without creating tasks.
 *   - Dirty tracking, locking, and conflict prevention.
 *
 * R4: this controller is now the SINGLE owner of the candidate-review state
 * that used to live inline in `s2scanState` (features/scan-s2.js) and was
 * mutated directly by features/energy.js.  The page reads via getState()
 * (snapshot copies) and every transition goes through the methods below —
 * `s2scanState` keeps only deprecated write-only aliases for one un-migrated
 * reset site (features/reaction-builder.js, off-limits this slice).
 *
 * Pure state machine: no document.*, no fetch(, no $3Dmol, no page globals.
 */
window.ACPWorkbench = window.ACPWorkbench || {};
ACPWorkbench.controllers = ACPWorkbench.controllers || {};
ACPWorkbench.controllers.candidateReview = (function () {
  "use strict";

  var state = {
    selectedTs: [],
    selectedInt: [],
    reviewConfirmed: false,
    selectionLocked: false,
    selectionEditMode: false,
    candidates: [],
    dirty: false,
    savedAt: null,
    reviewStatus: "",
    revision: 0,
  };

  var listeners = new Set();

  function subscribe(fn) {
    listeners.add(fn);
    return function () {
      listeners.delete(fn);
    };
  }

  function notify() {
    listeners.forEach(function (fn) {
      try {
        fn(getState());
      } catch (e) {
        console.error("Candidate review listener failed", e);
      }
    });
  }

  function getState() {
    return {
      selectedTs: state.selectedTs.slice(),
      selectedInt: state.selectedInt.slice(),
      reviewConfirmed: state.reviewConfirmed,
      selectionLocked: state.selectionLocked,
      selectionEditMode: state.selectionEditMode,
      // Entries are copied too: readers get snapshots and can only mutate the
      // working set through controller methods (single-writer guarantee).
      candidates: state.candidates.map(function (entry) {
        return Object.assign({}, entry);
      }),
      dirty: state.dirty,
      savedAt: state.savedAt,
      reviewStatus: state.reviewStatus,
      revision: state.revision,
    };
  }

  function reset() {
    state.selectedTs = [];
    state.selectedInt = [];
    state.reviewConfirmed = false;
    state.selectionLocked = false;
    state.selectionEditMode = false;
    state.candidates = [];
    state.dirty = false;
    state.savedAt = null;
    state.reviewStatus = "";
    state.revision = 0;
    notify();
  }

  function isDirty() {
    return !!state.dirty;
  }

  function isLocked() {
    return !!state.selectionLocked;
  }

  function findCandidateByFrame(frameIndex) {
    var fi = Number(frameIndex);
    for (var i = 0; i < state.candidates.length; i++) {
      if (Number(state.candidates[i].frame_index) === fi) return state.candidates[i];
    }
    return null;
  }

  /** Exclusive role toggle over the selectedTs/selectedInt role arrays.
   * Selecting an id in one role removes it from the other; toggling an
   * already-selected id removes it. Rejected (returns false) while locked. */
  function toggleCandidate(id, role) {
    if (state.selectionLocked) return false;
    var targetArray = role === "ts" ? state.selectedTs : state.selectedInt;
    var otherArray = role === "ts" ? state.selectedInt : state.selectedTs;

    // Remove from other role if present
    var otherIdx = otherArray.indexOf(id);
    if (otherIdx >= 0) otherArray.splice(otherIdx, 1);

    var idx = targetArray.indexOf(id);
    if (idx >= 0) {
      targetArray.splice(idx, 1);
    } else {
      targetArray.push(id);
    }
    state.dirty = true;
    notify();
    return true;
  }

  function setLock(locked) {
    state.selectionLocked = !!locked;
    notify();
  }

  /** Local edit-mode gate (unlock-to-edit / relock). Entering edit mode does
   * NOT clear dirty — the dirty flag tracks unsaved working changes. */
  function setSelectionMode(editMode) {
    state.selectionEditMode = !!editMode;
    notify();
  }

  function setConfirmed(confirmed) {
    state.reviewConfirmed = !!confirmed;
    state.dirty = true;
    notify();
  }

  function setSavedAt(value) {
    state.savedAt = value || null;
    notify();
  }

  function setReviewStatus(status) {
    state.reviewStatus = String(status == null ? "" : status);
    notify();
  }

  function setRevision(value) {
    state.revision = Number(value) || 0;
    notify();
  }

  /** Replace the whole working candidate list (e.g. from s2scan or PES
   * loads). Entries are shallow-copied on the way in; dirty is untouched —
   * callers pair this with clearDirty()/setConfirmed() per their flow. */
  function setCandidates(list) {
    var src = Array.isArray(list) ? list : [];
    state.candidates = src.map(function (entry) {
      return Object.assign({}, entry);
    });
    notify();
  }

  /** Mark every working entry saved (after a successful server write that
   * did not return the full candidate list). */
  function markAllCandidatesSaved() {
    state.candidates.forEach(function (entry) {
      entry.saved = true;
    });
    notify();
  }

  function clearDirty() {
    state.dirty = false;
    notify();
  }

  /** Rename a working candidate by frame. Returns false (no change) while
   * locked or when no entry exists for the frame — mirrors the former page
   * behavior where only an existing entry could be renamed. */
  function setCandidateName(frameIndex, name) {
    if (state.selectionLocked) return false;
    var entry = findCandidateByFrame(frameIndex);
    if (!entry) return false;
    entry.name = String(name == null ? "" : name);
    entry.saved = false;
    state.dirty = true;
    notify();
    return true;
  }

  /** Former energyGraphUpsertCandidate semantics: role "none" removes the
   * frame's entry (dirty only when one existed); any other patch updates or
   * creates the entry with saved=false and marks dirty. Returns true when
   * the working set changed. */
  function upsertCandidate(frameIndex, patch) {
    if (frameIndex == null || state.selectionLocked) return false;
    var existing = findCandidateByFrame(frameIndex);
    if (patch && patch.role === "none") {
      if (existing) {
        state.candidates.splice(state.candidates.indexOf(existing), 1);
        state.dirty = true;
        notify();
        return true;
      }
      return false;
    }
    if (existing) {
      if (patch && patch.role != null) existing.role = patch.role;
      if (patch && patch.name != null) existing.name = patch.name;
      if (patch && patch.candidate_id != null) existing.candidate_id = patch.candidate_id;
      existing.saved = false;
    } else {
      var role = (patch && patch.role) || "ts";
      state.candidates.push({
        candidate_id: (patch && patch.candidate_id) || null,
        frame_index: Number(frameIndex),
        role: role,
        name: (patch && patch.name) || "",
        recommended_type: (patch && patch.recommended_type) || null,
        selection_source: (patch && patch.selection_source) || "manual",
        saved: false,
      });
    }
    state.dirty = true;
    notify();
    return true;
  }

  /** Remove a frame's working entry. Returns true when an entry was removed. */
  function removeCandidate(frameIndex) {
    if (frameIndex == null || state.selectionLocked) return false;
    var existing = findCandidateByFrame(frameIndex);
    if (!existing) return false;
    state.candidates.splice(state.candidates.indexOf(existing), 1);
    state.dirty = true;
    notify();
    return true;
  }

  /** Former energyGraphSetFrameRole semantics. Returns true when the working
   * set changed (the page re-renders only on true). */
  function setFrameRole(frameIndex, role) {
    if (frameIndex == null || state.selectionLocked) return false;
    var existing = findCandidateByFrame(frameIndex);
    if (role === "none") {
      if (existing) {
        state.candidates.splice(state.candidates.indexOf(existing), 1);
        state.dirty = true;
        notify();
        return true;
      }
      return false;
    }
    if (existing) {
      if (existing.role === role) return false;
      existing.role = role;
      existing.saved = false;
    } else {
      state.candidates.push({
        candidate_id: null,
        frame_index: Number(frameIndex),
        role: role,
        name: "",
        recommended_type: null,
        selection_source: "manual",
        saved: false,
      });
    }
    state.dirty = true;
    notify();
    return true;
  }

  /** Former energyGraphMoveCandidate semantics: `occupied` carries the entry
   * already sitting on the target frame so the page can surface the conflict
   * alert (the controller itself never touches the DOM). */
  function moveCandidate(frameIndexFrom, frameIndexTo) {
    if (state.selectionLocked) return { moved: false, occupied: null };
    frameIndexFrom = Number(frameIndexFrom);
    frameIndexTo = Number(frameIndexTo);
    if (frameIndexFrom === frameIndexTo) return { moved: false, occupied: null };
    var entry = findCandidateByFrame(frameIndexFrom);
    if (!entry) return { moved: false, occupied: null };
    var occupied = findCandidateByFrame(frameIndexTo);
    if (occupied) return { moved: false, occupied: occupied };
    entry.frame_index = frameIndexTo;
    entry.saved = false;
    state.dirty = true;
    notify();
    return { moved: true, occupied: null };
  }

  /** Apply the scan/PES data payload's review snapshot + annotations
   * (former energyGraphSyncS2Selection body, verbatim semantics). The caller
   * keeps the edit-mode/dirty early-return guard — once the user is editing,
   * polling must not silently restore the server snapshot. A confirmed empty
   * review is meaningful: algorithmic recommendations must not repopulate it. */
  function syncFromServerSnapshot(review, annotations) {
    review = review || {};
    state.reviewStatus = String(review.status || "pending");
    state.reviewConfirmed = state.reviewStatus === "confirmed";
    state.selectionLocked = state.reviewConfirmed;
    state.savedAt = review.decided_at || null;
    state.revision = Number(review.revision || 0);
    state.candidates = [];
    (Array.isArray(annotations) ? annotations : []).forEach(function (annotation) {
      if (["ts", "intermediate"].indexOf(String(annotation.type)) < 0) return;
      if (annotation.active === false) return;
      if (annotation.frame_index == null) return;
      if (state.reviewConfirmed && !annotation.saved) return;
      state.candidates.push({
        candidate_id: annotation.candidate_id ? String(annotation.candidate_id) : null,
        frame_index: Number(annotation.frame_index),
        role: String(annotation.type),
        name: annotation.name ? String(annotation.name) : "",
        recommended_type: annotation.recommended_type ? String(annotation.recommended_type) : null,
        selection_source: annotation.selection_source ? String(annotation.selection_source) : "algorithm",
        saved: !!annotation.saved,
      });
    });
    state.dirty = false;
    notify();
  }

  /** Apply a POST /pes/review (or restore) result — former
   * energyGraphApplyReviewResult semantics: the working set becomes the
   * saved server set, the selection locks, dirty clears. */
  function applyReviewResult(result) {
    if (!result) return;
    var roleMap = { TS: "ts", INT: "intermediate" };
    if (Array.isArray(result.candidates)) {
      state.candidates = result.candidates.map(function (row) {
        var rawRole = String(row.role || "ts");
        var role = roleMap[rawRole.toUpperCase()] || rawRole.toLowerCase();
        if (role !== "ts" && role !== "intermediate") role = "intermediate";
        return {
          candidate_id: String(row.candidate_id || "") || null,
          frame_index: Number(row.frame_index),
          role: role,
          name: row.name ? String(row.name) : "",
          recommended_type: row.recommended_type ? String(row.recommended_type) : null,
          selection_source: String(row.selection_source || "manual"),
          saved: true,
        };
      });
    } else {
      state.candidates.forEach(function (entry) { entry.saved = true; });
    }
    state.dirty = false;
    state.reviewConfirmed = true;
    state.selectionLocked = true;
    state.selectionEditMode = false;
    state.reviewStatus = String(result.status || "confirmed");
    state.savedAt = result.confirmed_at || null;
    state.revision = Number(result.revision || 0);
    notify();
  }

  /** GET /pes/review-style snapshot (server field names). Clears dirty —
   * a server snapshot is by definition the saved state. */
  function loadFromSnapshot(snap) {
    if (!snap) return;
    state.selectedTs = Array.isArray(snap.selected_ts) ? snap.selected_ts.slice() : [];
    state.selectedInt = Array.isArray(snap.selected_int) ? snap.selected_int.slice() : [];
    state.reviewConfirmed = !!snap.review_confirmed;
    state.selectionLocked = !!snap.selection_locked;
    state.reviewStatus = snap.review_status || "";
    state.savedAt = snap.saved_at || null;
    state.revision = Number(snap.revision) || 0;
    state.dirty = false;
    notify();
  }

  return {
    getState: getState,
    reset: reset,
    toggleCandidate: toggleCandidate,
    setLock: setLock,
    setConfirmed: setConfirmed,
    loadFromSnapshot: loadFromSnapshot,
    subscribe: subscribe,
    /* R4 additions — single-owner surface for the page */
    setSelectionMode: setSelectionMode,
    setSavedAt: setSavedAt,
    setReviewStatus: setReviewStatus,
    setRevision: setRevision,
    setCandidates: setCandidates,
    markAllCandidatesSaved: markAllCandidatesSaved,
    clearDirty: clearDirty,
    isDirty: isDirty,
    isLocked: isLocked,
    setCandidateName: setCandidateName,
    upsertCandidate: upsertCandidate,
    removeCandidate: removeCandidate,
    setFrameRole: setFrameRole,
    moveCandidate: moveCandidate,
    syncFromServerSnapshot: syncFromServerSnapshot,
    applyReviewResult: applyReviewResult,
  };
})();
