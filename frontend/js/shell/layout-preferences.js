/* Versioned presentation preferences only: never persist task refs or credentials. */
window.ACPShell = window.ACPShell || {};
ACPShell.preferences = (function () {
  "use strict";
  var KEY = "acp-next-layout-v2", memory = {}, loaded = false, active = "research";
  // One-time marker for the 2026-11-03 research-slot migration (network
  // contract §6). Never bumped with KEY's schema version: it records that the
  // migration already ran, so a later user choice is never overridden.
  var NETWORK_MIGRATION_KEY = "acp-next-layout-network-migrated";
  var slots = ["main", "side", "bottom"];
  // Modules that are registered but not in a default recipe are still legal
  // layout choices (V7 assembly will pin defaults). The Shell registry is the
  // authority: an absent module never becomes an option.
  var EXTRA_MODULES = {
    // 2026-11-03 (network contract §6): the research main slot is the project
    // chemistry network, so agent-workspace is no longer in a default recipe.
    // It stays registered and must stay pin-able from the layout editor.
    "results-workspace": {
      instanceId: "results-workspace", moduleId: "results-workspace",
      titleKey: "shell.module.results_workspace", loading: "canvas"
    },
    "task-explorer": {
      instanceId: "task-explorer", moduleId: "task-explorer",
      titleKey: "shell.module.task_explorer", loading: "list"
    },
    "exec-detail": {
      instanceId: "exec-detail", moduleId: "exec-detail",
      titleKey: "shell.module.task_detail", loading: "list"
    },
    "task-config": {
      instanceId: "task-config", moduleId: "task-config",
      titleKey: "shell.module.task_config", loading: "card"
    },
    "structure-import": {
      instanceId: "structure-import", moduleId: "structure-import",
      titleKey: "shell.module.structure_import", loading: "card"
    },
    "agent-workspace": {
      instanceId: "agent-workspace", moduleId: "agent-workspace",
      titleKey: "shell.module.agent_workspace", loading: "card"
    },
    "node-status": {
      instanceId: "node-status", moduleId: "node-status",
      titleKey: "shell.module.node_status", loading: "list"
    },
    "approval-card": {
      instanceId: "approval-card", moduleId: "approval-card",
      titleKey: "shell.module.approval_card", loading: "card"
    },
    "capability-form": {
      instanceId: "capability-form", moduleId: "capability-form",
      titleKey: "shell.module.capability_form", loading: "card"
    },
    "s2-scan": {
      instanceId: "s2-scan", moduleId: "s2-scan",
      titleKey: "shell.module.s2_scan", loading: "card"
    },
    "config-workspace": {
      instanceId: "config-workspace", moduleId: "config-workspace",
      titleKey: "shell.module.task_wizard", loading: "card"
    },
    "task-wizard": {
      instanceId: "task-wizard", moduleId: "task-wizard",
      titleKey: "shell.module.task_wizard", loading: "card"
    },
    "nmr-report": {
      instanceId: "nmr-report", moduleId: "nmr-report",
      titleKey: "shell.module.nmr_report", loading: "card"
    },
    "tsmode-editor": {
      instanceId: "tsmode-editor", moduleId: "tsmode-editor",
      titleKey: "shell.module.tsmode_editor", loading: "card"
    },
    "mechanism-plan": {
      instanceId: "mechanism-plan", moduleId: "mechanism-plan",
      titleKey: "shell.module.mechanism_plan", loading: "card"
    },
    "job-edit": {
      instanceId: "job-edit", moduleId: "job-edit",
      titleKey: "shell.module.job_edit", loading: "card"
    },
    "research-record": {
      instanceId: "research-record", moduleId: "research-record",
      titleKey: "shell.module.research_record", loading: "card"
    },
    "path-view": {
      instanceId: "path-view", moduleId: "path-view",
      titleKey: "shell.module.path_view", loading: "card"
    },
    "pes-handoff": {
      instanceId: "pes-handoff", moduleId: "pes-handoff",
      titleKey: "shell.module.pes_handoff", loading: "card"
    }
  };
  // Kept legal for old v2 layouts, but no longer offered as free-floating panels.
  var HIDDEN_FROM_PICKER = {
    "task-wizard": true, "job-edit": true, "nmr-report": true, "tsmode-editor": true,
    "s2-scan": true, "node-status": true, "approval-card": true, "capability-form": true,
    "path-view": true, "pes-handoff": true, "mechanism-plan": true, "research-record": true,
    "task-config": true
  };
  function copy(value) { return JSON.parse(JSON.stringify(value)); }
  // Host seam (js/shell/host-env.js): env.storage applies env.storageKey(KEY)
  // once, so the raw KEY is passed down — never a pre-prefixed one. Without
  // the seam this is exactly the window.localStorage call it always was.
  function storageGet(key) {
    var env = window.ACPShell && window.ACPShell.env;
    if (env) return env.storage.get(key);
    return window.localStorage.getItem(key);
  }
  function storageSet(key, value) {
    var env = window.ACPShell && window.ACPShell.env;
    if (env) { env.storage.set(key, value); return; }
    window.localStorage.setItem(key, value);
  }
  function defaults(id) {
    var recipe = ACPShell.recipes.RECIPES[id];
    if (!recipe) throw new Error("Unknown workspace");
    var value = { main: "", side: "", bottom: "", sideWidth: 32, bottomHeight: 30 };
    recipe.panels.forEach(function (panel) { value[panel.slot] = panel.moduleId; });
    return value;
  }
  function modules() {
    var result = {};
    Object.keys(ACPShell.recipes.RECIPES).forEach(function (id) {
      ACPShell.recipes.RECIPES[id].panels.forEach(function (p) { if (!result[p.moduleId]) result[p.moduleId] = p; });
    });
    var registry = window.ACPShell && window.ACPShell.registry;
    if (registry && typeof registry.has === "function") {
      Object.keys(EXTRA_MODULES).forEach(function (id) {
        if (!result[id] && registry.has(id)) result[id] = EXTRA_MODULES[id];
      });
    }
    return result;
  }
  function validate(id, input) {
    var value = defaults(id), known = modules(), seen = {};
    if (!input || typeof input !== "object") throw new Error("Invalid layout");
    slots.forEach(function (slot) {
      var moduleId = input[slot];
      if (typeof moduleId !== "string" || (moduleId && !known[moduleId])) throw new Error("Unknown panel");
      if (slot === "main" && !moduleId) throw new Error("Main panel required");
      if (moduleId && seen[moduleId]) throw new Error("Duplicate panel");
      if (moduleId) seen[moduleId] = true;
      value[slot] = moduleId;
    });
    ["sideWidth", "bottomHeight"].forEach(function (key) {
      var number = input[key];
      if (typeof number !== "number" || !Number.isFinite(number) || number < 20 || number > 65) throw new Error("Invalid panel size");
      value[key] = Math.round(number);
    });
    return value;
  }
  /** Marker read: never throws (the seam swallows storage errors; the plain
   *  localStorage fallback is wrapped here). */
  function networkMigrationDone() {
    try { return storageGet(NETWORK_MIGRATION_KEY) === "1"; } catch (e) { return false; }
  }
  /** Marker write: a blocked/sandboxed store must never block the load — the
   *  worst case is that the one-time rule runs again next time. */
  function markNetworkMigrationDone() {
    try { storageSet(NETWORK_MIGRATION_KEY, "1"); } catch (e) { /* session-only */ }
  }
  function load() {
    if (loaded) return;
    loaded = true;
    // Read BEFORE the layout so a first-ever load also records the marker: a
    // user who pins agent-workspace back into `main` afterwards must keep it.
    var networkMigrated = networkMigrationDone();
    try {
      var saved = JSON.parse(storageGet(KEY) || "null");
      if (saved && saved.version === 2) {
        if (Object.prototype.hasOwnProperty.call(ACPShell.recipes.RECIPES, saved.active)) active = saved.active;
        Object.keys(ACPShell.recipes.RECIPES).forEach(function (id) {
          // UI-04 migration: a layout saved before the execution workspace had
          // a bottom slot has no way to express "reaction-network". Re-derive
          // the execution defaults once instead of silently hiding the panel.
          if (id === "execution" && saved.workspaces && saved.workspaces.execution &&
              JSON.stringify(saved.workspaces.execution).indexOf("reaction-network") === -1) {
            return;
          }
          // 2026-11-03 migration (network contract §6), ONE-TIME: a research
          // layout saved before the network canvas moved to the main slot still
          // pins agent-workspace there. The stored shape is unchanged (schema
          // version stays 1), so the marker — not the layout content — decides:
          // absent → discard once and re-derive, present → honour the user's
          // saved choice verbatim (including agent-workspace in main).
          if (!networkMigrated && id === "research" && saved.workspaces && saved.workspaces.research &&
              saved.workspaces.research.main === "agent-workspace") {
            return;
          }
          try { if (saved.workspaces && saved.workspaces[id]) memory[id] = validate(id, saved.workspaces[id]); } catch (e) { /* Invalid workspace falls back independently. */ }
        });
      }
    } catch (e) { /* Unavailable or corrupt storage: defaults remain usable. */ }
    if (!networkMigrated) markNetworkMigrationDone();
  }
  function get(id) { load(); return copy(memory[id] || defaults(id)); }
  function save(id, input) {
    var value = validate(id, input);
    load(); memory[id] = value;
    return persist();
  }
  function persist() {
    try {
      storageSet(KEY, JSON.stringify({ version: 2, workspaces: memory, active: active }));
      return { persisted: true };
    } catch (e) { return { persisted: false }; }
  }
  function recipe(base) {
    var value = get(base.workspaceId), known = modules();
    var result = Object.assign({}, base, { panels: [], dimensions: value });
    slots.forEach(function (slot) {
      var moduleId = value[slot];
      if (!moduleId) return;
      // v2 saved before the config host landed may still name task-wizard as E's main.
      if (base.workspaceId === "config" && slot === "main" && moduleId === "task-wizard") moduleId = "config-workspace";
      var original = base.panels.find(function (p) { return p.moduleId === moduleId; }) || known[moduleId];
      result.panels.push(Object.assign({}, original, { slot: slot }));
    });
    return result;
  }
  function pickerModules() {
    var all = modules(), shown = {};
    Object.keys(all).forEach(function (id) { if (!HIDDEN_FROM_PICKER[id]) shown[id] = all[id]; });
    return shown;
  }
  return { get: get, save: save, defaults: defaults, validate: validate, modules: modules, pickerModules: pickerModules, recipe: recipe,
    active: function () { load(); return active; },
    setActive: function (id) { load(); if (Object.prototype.hasOwnProperty.call(ACPShell.recipes.RECIPES, id)) { active = id; persist(); } }
  };
})();
