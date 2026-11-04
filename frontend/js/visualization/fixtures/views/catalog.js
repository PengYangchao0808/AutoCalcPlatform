/*
 * ACP 3.0 fixture — structure catalog view (Track A / A-32).
 *
 * Two groups / three entries mirror the `structure_viewer_v1` catalog shape.
 * Geometry is deliberately referenced, not embedded: selecting an entry goes
 * through the same structure-entry endpoint as live data.
 */
(function () {
  "use strict";

  window.ACPViewFixtures = window.ACPViewFixtures || {};

  window.ACPViewFixtures.catalog = function () {
    return {
      availability: "ready",
      job_id: "fixture-structure",
      workflow: "optimize",
      job_status: "COMPLETED",
      revision: "fixture",
      provenance: {
        jobId: "fixture-structure",
        revision: "fixture",
        workflow: "optimize",
        jobStatus: "COMPLETED"
      },
      groups: [
        { id: "formal", label: "正式结果", kind: "formal_result" },
        { id: "input", label: "输入结构", kind: "calculation_input" }
      ],
      entries: [
        {
          id: "fixture-opt",
          group_id: "formal",
          label: "优化结果",
          role: "minimum",
          status: "completed",
          energy: { value: -76.4, unit: "hartree", kind: "electronic" },
          relative_energy_kcal: 0,
          badges: ["opt"],
          source: { kind: "formal_result", geometry_ref: "RESULT/optimized.xyz", frame_index: 0 }
        },
        {
          id: "fixture-ts",
          group_id: "formal",
          label: "TS 候选",
          role: "transition_state",
          status: "completed",
          energy: { value: -75.9, unit: "hartree", kind: "electronic" },
          relative_energy_kcal: 12.4,
          badges: ["ts", "imaginary"],
          source: { kind: "formal_result", geometry_ref: "RESULT/ts.xyz", frame_index: 0 }
        },
        {
          id: "fixture-input",
          group_id: "input",
          label: "输入结构",
          role: "input",
          status: "completed",
          energy: null,
          relative_energy_kcal: null,
          badges: [],
          source: { kind: "calculation_input", geometry_ref: "input.xyz", frame_index: 0 }
        }
      ]
    };
  };
})();
