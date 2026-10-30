/* Versioned presentation preferences only: never persist task refs or credentials. */
window.ACPShell = window.ACPShell || {};
ACPShell.preferences = (function () {
  "use strict";
  var KEY = "acp-next-layout-v1", memory = {}, loaded = false, active = "research";
  var slots = ["main", "side", "bottom"];
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
