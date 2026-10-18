// ── S2 Bond-Length Scan Modal ───────────────────────────────────────────────
var s2scanState = {
  step: 1,
  sourceType: "task_artifact",
  sourcePreview: null,
  xyzText: "",
  coordinateKind: "distance",
  coordinateUnit: "angstrom",
  selectedAtoms: [],
  coordinateIntent: "bond_breaking",
  charge: 0,
  mult: 1,
  atoms: [],
  selA: null,
  selB: null,
  model: null,
  lineShape: null,
  selSpheres: [],
  selLines: [],
  intent: "bond_breaking",
  startDist: 1.0,
  endDist: 3.0,
  nPoints: 21,
  // HISTORICAL-COMPAT (T23g): legacy S2 scan panel defaults — method literals
  // live ONLY in this legacy branch; they mirror
  // FIELD_DEFINITIONS.scan_optimizer_method.default ("GFN2-xTB") and the
  // historical single-point default "B97-3c" (parity-locked by
  // tests/test_frontend_sync.py).
  scanMethod: "GFN2-xTB", // HISTORICAL-COMPAT (T23g)
  spEnabled: true,
  spMethod: "B97-3c", // HISTORICAL-COMPAT (T23g)
  maxIter: 200,
  projectId: "",
  activeCount: 0,
  sourceXyz: "",
  previewFraming: { pending: false, settled: false, observer: null, timer: null, lastW: 0, lastH: 0, epoch: 0 },
  bondFraming: { pending: false, settled: false, observer: null, timer: null, lastW: 0, lastH: 0, epoch: 0 },
  resultFraming: { pending: false, settled: false, observer: null, timer: null, lastW: 0, lastH: 0, epoch: 0 },
};

function s2scanCloseReport() {
  if (activeViewerTab === "energy") setViewerTab("structure");
}

function s2scanResetState() {
  disposeViewerFraming(s2scanState.previewFraming);
  disposeViewerFraming(s2scanState.bondFraming);
  disposeViewerFraming(s2scanState.resultFraming);
  s2scanState.step = 1;
  s2scanState.sourceType = "task_artifact";
  s2scanState.sourcePreview = null;
  s2scanState.xyzText = "";
  s2scanState.coordinateKind = "distance";
  s2scanState.coordinateUnit = "angstrom";
  s2scanState.selectedAtoms = [];
  s2scanState.coordinateIntent = "bond_breaking";
  s2scanState.charge = 0;
  s2scanState.mult = 1;
  s2scanState.atoms = [];
  s2scanState.selA = null;
  s2scanState.selB = null;
  s2scanState.model = null;
  s2scanState.lineShape = null;
  s2scanState.selSpheres = [];
  s2scanState.selLines = [];
  s2scanState.intent = "bond_breaking";
  s2scanState.startDist = 1.0;
  s2scanState.endDist = 3.0;
  s2scanState.nPoints = 21;
  s2scanState.scanMethod = "GFN2-xTB"; // HISTORICAL-COMPAT (T23g) legacy S2 panel default
  s2scanState.spEnabled = true;
  s2scanState.spMethod = "B97-3c"; // HISTORICAL-COMPAT (T23g) legacy S2 panel default
  s2scanState.maxIter = 200;
  s2scanState.projectId = "";
  /* R4: candidate-review state is owned by candidateReview — reset through
     the controller, same point in the flow as the former inline field
     resets (those s2scanState review fields no longer exist). */
  if (window.ACPWorkbench && ACPWorkbench.controllers && ACPWorkbench.controllers.candidateReview) {
    ACPWorkbench.controllers.candidateReview.reset();
  }
  s2scanState.sourceXyz = "";
}

function s2scanDist(a, b) {
  var dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
  return Math.sqrt(dx*dx + dy*dy + dz*dz);
}

function s2scanAngle(a, b, c) {
  var ab = [a.x - b.x, a.y - b.y, a.z - b.z];
  var cb = [c.x - b.x, c.y - b.y, c.z - b.z];
  var dot = ab[0] * cb[0] + ab[1] * cb[1] + ab[2] * cb[2];
  var norm = Math.sqrt(ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2) * Math.sqrt(cb[0] ** 2 + cb[1] ** 2 + cb[2] ** 2);
  return Math.acos(Math.max(-1, Math.min(1, dot / norm))) * 180 / Math.PI;
}

function s2scanDihedral(a, b, c, d) {
  var b1 = [b.x - a.x, b.y - a.y, b.z - a.z];
  var b2 = [c.x - b.x, c.y - b.y, c.z - b.z];
  var b3 = [d.x - c.x, d.y - c.y, d.z - c.z];
  var norm = Math.sqrt(b2[0] ** 2 + b2[1] ** 2 + b2[2] ** 2);
  var u = b2.map(function(value) { return value / norm; });
  var dot1 = b1[0] * u[0] + b1[1] * u[1] + b1[2] * u[2];
  var dot2 = b3[0] * u[0] + b3[1] * u[1] + b3[2] * u[2];
  var v = b1.map(function(value, i) { return value - dot1 * u[i]; });
  var w = b3.map(function(value, i) { return value - dot2 * u[i]; });
  var cross = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
  var x = v[0] * w[0] + v[1] * w[1] + v[2] * w[2];
  var y = cross[0] * w[0] + cross[1] * w[1] + cross[2] * w[2];
  return Math.atan2(y, x) * 180 / Math.PI;
}
