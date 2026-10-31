/* Versioned presentation preferences only: never persist task refs or credentials. */
window.ACPShell = window.ACPShell || {};
ACPShell.preferences = (function () {
  "use strict";
  var KEY = "acp-next-layout-v1", memory = {}, loaded = false, active = "research";
  var slots = ["main", "side", "bottom"];
  // Modules that are registered but not in a default recipe are still legal
  // layout choices (V7 assembly will pin defaults). The Shell registry is the
  // authority: an absent module never becomes an option.
  var EXTRA_MODULES = {
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
  function copy(value) { return JSON.parse(JSON.stringify(value)); }
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
  function load() {
    if (loaded) return;
    loaded = true;
    try {
      var saved = JSON.parse(window.localStorage.getItem(KEY) || "null");
      if (!saved || saved.version !== 1) return;
      if (Object.prototype.hasOwnProperty.call(ACPShell.recipes.RECIPES, saved.active)) active = saved.active;
      Object.keys(ACPShell.recipes.RECIPES).forEach(function (id) {
        // UI-04 migration: a layout saved before the execution workspace had a
        // bottom slot has no way to express "reaction-network". Re-derive the
        // execution defaults once instead of silently hiding the new panel.
        if (id === "execution" && saved.workspaces && saved.workspaces.execution &&
            JSON.stringify(saved.workspaces.execution).indexOf("reaction-network") === -1) {
          return;
        }
        try { if (saved.workspaces && saved.workspaces[id]) memory[id] = validate(id, saved.workspaces[id]); } catch (e) { /* Invalid workspace falls back independently. */ }
      });
    } catch (e) { /* Unavailable or corrupt storage: defaults remain usable. */ }
  }
  function get(id) { load(); return copy(memory[id] || defaults(id)); }
  function save(id, input) {
    var value = validate(id, input);
    load(); memory[id] = value;
    return persist();
  }
  function persist() {
    try {
      window.localStorage.setItem(KEY, JSON.stringify({ version: 1, workspaces: memory, active: active }));
      return { persisted: true };
    } catch (e) { return { persisted: false }; }
  }
  function recipe(base) {
    var value = get(base.workspaceId), known = modules();
    var result = Object.assign({}, base, { panels: [], dimensions: value });
    slots.forEach(function (slot) {
      var moduleId = value[slot];
      if (!moduleId) return;
      var original = base.panels.find(function (p) { return p.moduleId === moduleId; }) || known[moduleId];
      result.panels.push(Object.assign({}, original, { slot: slot }));
    });
    return result;
  }
  return { get: get, save: save, defaults: defaults, validate: validate, modules: modules, recipe: recipe,
    active: function () { load(); return active; },
    setActive: function (id) { load(); if (Object.prototype.hasOwnProperty.call(ACPShell.recipes.RECIPES, id)) { active = id; persist(); } }
  };
})();
