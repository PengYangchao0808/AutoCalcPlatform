/*
 * Pure fixture samples for the task-view contract (synthetic data only —
 * no real user/project data). Loaded by module tests and usable by Antigravity
 * for page-level mock verification. Shape matches S0 (captured 2026-10-16).
 *
 * Samples: normal / empty / partial-truncated / facet-less (cached-facet
 * refresh) / active-heavy. Error + stale cases are described in comments of
 * the corresponding client tests (they are exception/staleness behaviors, not
 * payload shapes).
 */
window.ACPWorkbench = window.ACPWorkbench || {};
ACPWorkbench.contracts = ACPWorkbench.contracts || {};
ACPWorkbench.contracts.taskViewSamples = (function () {
  "use strict";

  function row(over) {
    var base = {
      id: "20261016_001_singlepoint", status: "completed", group_id: "g1",
      project_id: null, project_name: null,
      created_at: "2026-10-16T09:00:00", updated_at: "2026-10-16T09:05:00",
      started_at: "2026-10-16T09:00:05", completed_at: "2026-10-16T09:04:55",
      last_activity_at: "2026-10-16T09:05:00", current_stage: "done", progress: 100,
      molecule_name: "ethanol", task_name: "sp", remark: "fixture",
      display_name: "ethanol_sp_fixture", task_dir_name: "ethanol_singlepoint_fixture",
      workflow: "singlepoint", tags: [], archived: false, batch_id: null,
      spec: {}, custom_name: null, resolved_name: "ethanol_sp_fixture",
      default_name: "ethanol_sp_fixture", name_revision: 1, name_updated_at: null,
      attempt: 1, revision: 7, stage_index: 1, stage_total: 1,
      stage_detail: null, progress_state: null, live_status: null,
      display_method: "wB97X-D4/def2-TZVPPD",
    };
    return Object.assign(base, over || {});
  }

  var normal = {
    groups: [
      { key: "ethanol", display_name: "ethanol", unassigned: false, retired: false,
        count: 2, truncated: false, min_created_at: "2026-10-16T09:00:00",
        jobs: [
          row({ id: "20261016_001_singlepoint", status: "completed" }),
          row({ id: "20261016_002_optimize", task_name: "opt", status: "running",
            workflow: "optimize", attempt: 1, revision: 12, progress: 40,
            current_stage: "opt", completed_at: null }),
        ] },
      { key: "benzene", display_name: "benzene", unassigned: false, retired: false,
        count: 1, truncated: false, min_created_at: "2026-10-16T08:00:00",
        jobs: [ row({ id: "20261016_003_frequency", molecule_name: "benzene",
          task_name: "freq", status: "failed", workflow: "frequency",
          display_name: "benzene_freq_fixture", attempt: 2, revision: 20 }) ] },
    ],
    facets: {
      statuses: { completed: 1, running: 1, failed: 1 },
      workflows: [{ key: "singlepoint", count: 1 }, { key: "optimize", count: 1 }, { key: "frequency", count: 1 }],
      molecules: [{ key: "ethanol", name: "ethanol", count: 2 }, { key: "benzene", name: "benzene", count: 1 }],
      tags: [], batches: [],
    },
    total: 3, truncated: false,
    counts: { completed: 1, running: 1, failed: 1 },
    query: { project_id: null, group_by: "molecule", sort: "created_desc", statuses: [],
      workflows: [], molecule_keys: [], tags: [], batch_ids: [], remarks: [],
      search: "", archived: "exclude", running_first: false },
    offset: 0, limit: 200, next_offset: null,
  };

  var empty = {
    groups: [], facets: { statuses: {}, workflows: [], molecules: [], tags: [], batches: [] },
    total: 0, truncated: false, counts: {},
    query: { project_id: null, group_by: "molecule", sort: "created_desc", statuses: [],
      workflows: [], molecule_keys: [], tags: [], batch_ids: [], remarks: [],
      search: "no-such-molecule", archived: "exclude", running_first: false },
    offset: 0, limit: 200, next_offset: null,
  };

  // include_facets=false refresh path: server omits facets; client reuses the
  // 30s cached facets (legacy taskFacetCache behavior preserved).
  var partialNoFacets = {
    groups: normal.groups, total: 3, truncated: false, counts: normal.counts,
    query: normal.query, offset: 0, limit: 200, next_offset: null,
  };

  // truncated:true + next_offset — pagination and "result may be incomplete"
  // states must stay distinguishable from empty-success.
  var truncatedPage = {
    groups: normal.groups, total: 403, truncated: true, counts: { running: 3, completed: 400 },
    query: normal.query, offset: 0, limit: 200, next_offset: 200,
  };

  return { row: row, normal: normal, empty: empty, partialNoFacets: partialNoFacets, truncatedPage: truncatedPage };
})();
