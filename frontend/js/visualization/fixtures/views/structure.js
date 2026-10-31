/*
 * ACP 3.0 fixture — structure view (Track A / A-00).
 *
 * Deterministic two-frame water geometry used by `?view=fixture` and the
 * view-model contract tests. Values are demo data: the envelope source is
 * forced to "fixture" by view_source.js, so the viewer shows the demo badge.
 */
(function () {
  "use strict";

  window.ACPViewFixtures = window.ACPViewFixtures || {};

  var XYZ = [
    "3",
    "water fixture frame 0 energy -76.40",
    "O 0.000000 0.000000 0.000000",
    "H 0.960000 0.000000 0.000000",
    "H -0.240000 0.930000 0.000000",
    "",
    "3",
    "water fixture frame 1 energy -76.30",
    "O 0.000000 0.000000 0.000000",
    "H 1.000000 0.000000 0.000000",
    "H -0.300000 0.950000 0.000000"
  ].join("\n") + "\n";

  window.ACPViewFixtures.structure = function () {
    return {
      availability: "ready",
      provenance: {
        jobId: "fixture-structure",
        entryId: "fixture:water",
        digest: "fixture",
        generatedAt: "2026-10-31T00:00:00Z"
      },
      objects: {
        "fixture:water": { format: "xyz", text: XYZ }
      },
      frames: [
        { index: 0, label: "Frame 0", energy: -76.4, geometryRef: "fixture:water" },
        { index: 1, label: "Frame 1", energy: -76.3, geometryRef: "fixture:water" }
      ]
    };
  };
})();
