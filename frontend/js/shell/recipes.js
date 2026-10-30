/*
 * ACP Next Shell — workspace recipes (UI-01).
 * Authority: Shell Design §3.1 (slot allocation) + UI-00 recipe contract.
 *
 * A recipe is the declarative panel layout of ONE workspace:
 *   { id, workspaceId, panels: [ { instanceId, moduleId, slot, titleKey,
 *       loading, binding, flush?, pendingPhase? } ] }
 *
 * Viewer single-active-instance decision (design §3.1, 未决项): the viewer is
 * pinned to the ONE instanceId `shared-viewer` in every recipe that shows it —
 * layout-manager migrates the same live panel element between slots, so there
 * is never a second viewer and never a remount flash.
 *
 * Three-recipe × single-viewer matrix (2026-10-30 修订, design §3.1): the
 * viewer only ever lives in research (side) and molecular (main). Execution
 * NEVER hosts a viewer — its side slot is the exec-detail placeholder
 * (task/artifact/config per upstream plan §5) until UI-03 fills it. The
 * migration path is therefore only ever triggered by the research↔molecular
 * pair; execution recipes never move the viewer element.
 *
 * `pendingPhase` marks known-but-not-yet-landed modules (placeholder panel);
 * a moduleId absent from the registry entirely also renders as a placeholder
 * without blocking sibling panels (design §3.1 未知模块占位).
 */
window.ACPShell = window.ACPShell || {};
ACPShell.recipes = (function () {
  "use strict";

  var VIEWER = {
    instanceId: "shared-viewer",
    moduleId: "molecule-viewer",
    titleKey: "shell.module.molecule_viewer",
    loading: "canvas",
    binding: "follow-focus",
    flush: true
  };

  var RECIPES = {
    research: {
      id: "research",
      workspaceId: "research",
      panels: [
        {
          instanceId: "research-agent",
          moduleId: "agent-workspace",
          slot: "main",
          titleKey: "shell.module.agent_workspace",
          loading: "card",
          binding: "follow-focus"
        },
        Object.assign({}, VIEWER, { slot: "side" }),
        {
          instanceId: "research-tasks",
          moduleId: "task-explorer",
          slot: "bottom",
          titleKey: "shell.module.task_explorer",
          loading: "list",
          binding: "follow-focus"
        }
      ]
    },
    molecular: {
      id: "molecular",
      workspaceId: "molecular",
      panels: [
        Object.assign({}, VIEWER, { slot: "main" }),
        {
          instanceId: "mol-config",
          moduleId: "task-config",
          slot: "side",
          titleKey: "shell.module.task_config",
          loading: "list"
        }
      ]
    },
    execution: {
      id: "execution",
      workspaceId: "execution",
      panels: [
        {
          instanceId: "exec-tasks",
          moduleId: "task-explorer",
          slot: "main",
          titleKey: "shell.module.task_explorer",
          loading: "list",
          binding: "follow-focus"
        },
        // 2026-10-30 修订 (design §3.1): execution side = exec-detail, NOT the
        // shared viewer. exec-detail is not a registered module yet → the
        // placeholder path renders it ("待接入" until UI-03). The single active
        // viewer stays in research side / molecular main only.
        {
          instanceId: "exec-detail",
          moduleId: "exec-detail",
          slot: "side",
          titleKey: "shell.module.task_detail",
          loading: "list",
          binding: "follow-focus"
        }
      ]
    }
  };

  /** ?demo=1 only: research workspace driven by the two mock modules. */
  function getDemoRecipe() {
    return {
      id: "demo",
      workspaceId: "research",
      panels: [
        {
          instanceId: "demo-alpha",
          moduleId: "mock-alpha",
          slot: "main",
          titleKey: "shell.module.agent_workspace",
          loading: "card",
          binding: "follow-focus"
        },
        {
          instanceId: "demo-beta",
          moduleId: "mock-beta",
          slot: "side",
          titleKey: "shell.module.molecule_viewer",
          loading: "canvas",
          binding: "follow-focus",
          flush: true
        }
      ]
    };
  }

  function get(id) {
    if (id === "demo") return getDemoRecipe();
    var base = RECIPES[id];
    return base && window.ACPShell.preferences ? ACPShell.preferences.recipe(base) : base || null;
  }

  function list() {
    return Object.keys(RECIPES);
  }

  /** recipe id whose workspaceId matches — P0 the two namespaces are equal. */
  function forWorkspace(workspaceId) {
    var ids = Object.keys(RECIPES);
    for (var i = 0; i < ids.length; i++) {
      if (RECIPES[ids[i]].workspaceId === workspaceId) return get(ids[i]);
    }
    return null;
  }

  return {
    RECIPES: RECIPES,
    get: get,
    list: list,
    forWorkspace: forWorkspace,
    getDemoRecipe: getDemoRecipe
  };
})();
