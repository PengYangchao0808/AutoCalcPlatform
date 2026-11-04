/* NEXT 2026-11 central-view recipes. D/G are global regions, never route panels. */
window.ACPShell = window.ACPShell || {};
ACPShell.recipes = (function () {
  "use strict";
  var VIEWER = { instanceId: "shared-viewer", moduleId: "molecule-viewer", titleKey: "shell.module.molecule_viewer", loading: "canvas", binding: "follow-focus", flush: true };
  function recipe(id, panels) { return { id: id, workspaceId: id, panels: panels }; }
  var RECIPES = {
    research: recipe("research", [{ instanceId: "research-network", moduleId: "reaction-network", slot: "main", titleKey: "shell.module.reaction_network", loading: "canvas", binding: "follow-focus", flush: true }]),
    drawing: recipe("drawing", [{ instanceId: "structure-import", moduleId: "structure-import", slot: "main", titleKey: "shell.module.structure_import", loading: "card", binding: "follow-focus", flush: true }]),
    molecular: recipe("molecular", [Object.assign({}, VIEWER, { slot: "main" }), { instanceId: "mol-energy", moduleId: "energy-profile", slot: "bottom", titleKey: "shell.module.energy_profile", loading: "canvas", binding: "follow-focus", flush: true }]),
    config: recipe("config", [{ instanceId: "task-config-wizard", moduleId: "config-workspace", slot: "main", titleKey: "shell.module.task_wizard", loading: "card", binding: "follow-focus", flush: true }]),
    execution: recipe("execution", [{ instanceId: "exec-results", moduleId: "results-workspace", slot: "main", titleKey: "shell.module.results_workspace", loading: "canvas", binding: "follow-focus", flush: true }, Object.assign({}, VIEWER, { slot: "side" })]),
    files: recipe("files", [{ instanceId: "files-workspace", moduleId: "files-workspace", slot: "main", titleKey: "shell.module.files_workspace", loading: "list", binding: "follow-focus", flush: true }])
  };
  function getDemoRecipe() { return { id: "demo", workspaceId: "research", panels: [{ instanceId: "demo-alpha", moduleId: "mock-alpha", slot: "main", titleKey: "shell.module.agent_workspace", loading: "card", binding: "follow-focus" }, { instanceId: "demo-beta", moduleId: "mock-beta", slot: "side", titleKey: "shell.module.molecule_viewer", loading: "canvas", binding: "follow-focus", flush: true }] }; }
  function get(id) { if (id === "demo") return getDemoRecipe(); var base = RECIPES[id]; return base && window.ACPShell.preferences ? ACPShell.preferences.recipe(base) : base || null; }
  function list() { return Object.keys(RECIPES); }
  function forWorkspace(id) { return get(id); }
  return { RECIPES: RECIPES, get: get, list: list, forWorkspace: forWorkspace, getDemoRecipe: getDemoRecipe };
})();
