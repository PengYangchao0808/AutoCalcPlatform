/**
 * ACP Task Input Workspace — reconciler and entry helpers for the unified new-task candidate workspace.
 * @version 0.2.0
 *
 * Namespace: window.ACPTaskInputWorkspace
 *
 * Pure functions (Node-testable):
 *   - entryKey(entryOrSnapshot)          -> string dedup key
 *   - entryFromSnapshot(snapshot)         -> pending entry
 *   - reconcileSelection(current, sel)    -> new entry array
 *   - derivedSelectedUids(entries)        -> string[] of source_uids
 *
 * Instance API:
 *   - mount(hostEl, config) -> instance
 *   - destroy()
 *   - render()
 */
(function () {
  "use strict";

  var VERSION = "0.2.0";

  /* ── Pure helpers (exported on namespace for Node testing) ────────── */

  /**
   * Build a deduplication key from an entry or picker snapshot.
   * Entries without source_uid get a local-only key that never collides
   * with library entries.
   */
  function entryKey(entryOrSnapshot) {
    if (!entryOrSnapshot) return ":";
    var uid = entryOrSnapshot.source_uid || "";
    if (!uid) {
      // Local entries: use a stable local identifier (index-based or name).
      return ":local:" + (entryOrSnapshot.input_id || entryOrSnapshot.name || "");
    }
    var version = entryOrSnapshot.version_id || entryOrSnapshot.geometry_hash || "";
    return uid + ":" + version;
  }

  /**
   * Create a pending input entry from a picker snapshot.
   * Carries forward all legacy fields that wizardStructures consumers expect.
   */
  function entryFromSnapshot(snapshot) {
    if (!snapshot) return null;
    var s = snapshot;
    return {
      // ── New fields (plan §3) ──
      input_id: _makeInputId(),
      source_kind: _sourceKindFromSnapshot(s),
      source_uid: s.source_uid || null,
      // The v2 picker identity is source_uid, while the v1 geometry endpoint
      // still resolves files by the legacy job_<id>:<path> source_id.
      source_id: s.source_id || "",
      version_id: s.version_id || null,
      geometry_hash: s.geometry_hash || null,
      geometry_state: "pending",
      geometry_error: null,
      usage_status: s.usage_status || "active",
      status_revision: s.status_revision || 0,
      metadata_revision: s.metadata_revision || 0,
      version_changed: false,
      // ── Legacy fields (preserved for ~20 read sites + submitJobModal) ──
      name: s.resolved_name || s.custom_name || s.default_name || s.name || "",
      molecule_name: s.molecule_name || s.resolved_name || "",
      tag: s.role || s.tag || "",
      role: s.role || "",
      charge: s.charge,
      multiplicity: s.multiplicity || 1,
      atom_count: s.atom_count || 0,
      formula: s.formula || "",
      candidate_id: s.candidate_id || "",
      source_ref: {
        source_uid: s.source_uid || "",
        source_id: s.source_id || "",
        job_id: s.job_id || "",
        job_name: s.job_name || s.job_resolved_name || "",
        path: "",
        project_id: s.project_id || "",
      },
      input_modified: false,
      // Geometry will be loaded later; these are placeholders.
      xyz: null,
      smiles: null,
      normalized_path: null,
      warnings: [],
    };
  }

  function _makeInputId() {
    return "inp_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 7);
  }

  function _sourceKindFromSnapshot(s) {
    if (!s) return "manual_input";
    var sk = s.source_kind || "";
    if (sk === "saved_candidate") return "saved_candidate";
    if (sk && sk.indexOf("task") >= 0) return "task_result";
    if (s.source_uid) return "task_result";
    return "manual_input";
  }

  /**
   * Reconcile the current entry list with a new picker selection.
   *
   * Rules (plan §6):
   *  - Add pending entries for selected snapshots not already present (dedupe by source_uid).
   *  - Remove library-sourced entries whose source_uid is no longer selected.
   *  - ALWAYS preserve local entries (source_uid == null) and their order.
   *  - Same source_uid, different version: keep existing entry, set version_changed = true.
   *  - Never mutate inputs.
   */
  function reconcileSelection(currentEntries, selection) {
    var current = Array.isArray(currentEntries) ? currentEntries : [];
    var selected = Array.isArray(selection) ? selection : [];

    // Build lookup: source_uid -> selected snapshot
    var selByUid = {};
    for (var si = 0; si < selected.length; si++) {
      var snap = selected[si];
      var uid = snap && snap.source_uid;
      if (uid) selByUid[uid] = snap;
    }

    // Build lookup: source_uid -> existing entry index (for version check)
    var existByUid = {};
    for (var ei = 0; ei < current.length; ei++) {
      var e = current[ei];
      if (e && e.source_uid) existByUid[e.source_uid] = ei;
    }

    var result = [];
    var processedUids = {};

    // Pass 1: iterate existing entries
    for (var i = 0; i < current.length; i++) {
      var entry = current[i];
      if (!entry) continue;

      if (!entry.source_uid) {
        // Local entry (manual/upload): always preserve
        result.push(entry);
        continue;
      }

      var euid = entry.source_uid;
      if (processedUids[euid]) continue;
      processedUids[euid] = true;

      if (!selByUid[euid]) {
        // Library entry no longer selected → remove (do not push)
        continue;
      }

      // Library entry still selected
      var selSnap = selByUid[euid];
      var selVersion = selSnap.version_id || selSnap.geometry_hash || "";
      var entryVersion = entry.version_id || entry.geometry_hash || "";

      if (selVersion && entryVersion && selVersion !== entryVersion) {
        // Same source_uid but different version → keep existing, flag it
        var flagged = _shallowClone(entry);
        flagged.version_changed = true;
        result.push(flagged);
      } else {
        // Same version or no version info → keep as-is
        result.push(entry);
      }
    }

    // Pass 2: add pending entries for newly selected snapshots
    for (var j = 0; j < selected.length; j++) {
      var s = selected[j];
      var suid = s && s.source_uid;
      if (!suid) continue;
      if (processedUids[suid]) continue;

      // New selection → create pending entry
      var newEntry = entryFromSnapshot(s);
      if (newEntry) result.push(newEntry);
    }

    return result;
  }

  function _shallowClone(obj) {
    var out = {};
    for (var k in obj) {
      if (Object.prototype.hasOwnProperty.call(obj, k)) out[k] = obj[k];
    }
    return out;
  }

  /**
   * Derive the list of source_uids from the current entries
   * for programmatic picker sync (setSelection).
   */
  function derivedSelectedUids(entries) {
    var list = Array.isArray(entries) ? entries : [];
    var uids = [];
    var seen = {};
    for (var i = 0; i < list.length; i++) {
      var e = list[i];
      if (!e || !e.source_uid) continue;
      if (seen[e.source_uid]) continue;
      seen[e.source_uid] = true;
      uids.push(e.source_uid);
    }
    return uids;
  }

  /* ── Instance mount (scaffold — wiring done in HTML) ──────────────── */

  function mount(hostEl, config) {
    var _hostEl = hostEl || null;
    var _config = config || {};
    var _destroyed = false;

    var instance = {
      VERSION: VERSION,
      destroy: function () {
        _destroyed = true;
      },
      render: function () {},
    };

    return instance;
  }

  function destroy() {}

  function render() {}

  /* ── Public namespace ─────────────────────────────────────────────── */

  window.ACPTaskInputWorkspace = {
    VERSION: VERSION,
    mount: mount,
    destroy: destroy,
    render: render,
    // Pure functions exposed for Node testing and HTML wiring
    entryKey: entryKey,
    entryFromSnapshot: entryFromSnapshot,
    reconcileSelection: reconcileSelection,
    derivedSelectedUids: derivedSelectedUids,
  };
})();
