/*
 * ACP 3.0 fixture — energy profile view (Track A / A-00).
 *
 * A small deterministic profile (5 frames, one TS annotation) for the
 * `acp.view.v1` contract and the upcoming energy-profile panel. It is demo
 * data, never a substitute for a live energy-graph response.
 */
(function () {
  "use strict";

  window.ACPViewFixtures = window.ACPViewFixtures || {};

  window.ACPViewFixtures.energy = function () {
    var frames = [
      { index: 0, label: "Reactant", energy: -76.40, geometryRef: "RESULT/optimized.xyz" },
      { index: 1, label: "Approach", energy: -75.10, geometryRef: "" },
      { index: 2, label: "TS", energy: -72.80, geometryRef: "RESULT/ts.xyz" },
      { index: 3, label: "Intermediate", energy: -77.90, geometryRef: "" },
      { index: 4, label: "Product", energy: -81.20, geometryRef: "" }
    ];
    return {
      availability: "ready",
      provenance: {
        jobId: "fixture-energy",
        digest: "fixture",
        generatedAt: "2026-10-31T00:00:00Z",
        viewType: "energy",
        xAxisMode: "frame_index"
      },
      axes: {
        x: { label: "帧", unit: "" },
        y: { label: "相对能量", unit: "kcal/mol" }
      },
      series: [
        {
          id: "relative_energy",
          label: "相对能量",
          unit: "kcal/mol",
          points: [
            { x: 0, y: 0.00, frameIndex: 0 },
            { x: 1, y: 1.30, frameIndex: 1 },
            { x: 2, y: 3.60, frameIndex: 2 },
            { x: 3, y: -1.50, frameIndex: 3 },
            { x: 4, y: -4.80, frameIndex: 4 }
          ]
        }
      ],
      frames: frames,
      annotations: [
        {
          id: "fixture-ts",
          label: "TS",
          kind: "ts",
          x: 2,
          y: 3.60,
          payload: { frame_index: 2, selected: true }
        },
        {
          id: "fixture-int",
          label: "Intermediate",
          kind: "intermediate",
          x: 3,
          y: -1.50,
          payload: { frame_index: 3 }
        }
      ]
    };
  };
})();
