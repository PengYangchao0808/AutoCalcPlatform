/*
 * ACP 3.0 — `acp.view.v1` contract + source adapter tests (Track A / A-00).
 *
 * Pure Node vm tests: no browser, no backend. They pin the frozen envelope,
 * the four data states, warnings-without-zero-fill, fixture visibility, the
 * live/fixture/v3 mode rules, and the exact legacy structure URLs.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;
  const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

  function makeStorage(initial) {
    const data = Object.assign({}, initial || {});
    return {
      getItem: (key) => (key in data ? data[key] : null),
      setItem: (key, value) => { data[key] = String(value); },
    };
  }

  function setup(options) {
    options = options || {};
    const warnings = [];
    const sandbox = makeSandbox({
      console: {
        log() {},
        warn(message) { warnings.push(String(message)); },
        error() {},
      },
      location: options.location || { search: "" },
      localStorage: options.storage || null,
      fetch: options.fetch,
    });
    loadScript("js/visualization/view_model.js", sandbox);
    loadScript("js/visualization/view_source.js", sandbox);
    if (options.fixtures !== false) {
      loadScript("js/visualization/fixtures/views/structure.js", sandbox);
      loadScript("js/visualization/fixtures/views/energy.js", sandbox);
      loadScript("js/visualization/fixtures/views/catalog.js", sandbox);
    }
    return {
      sandbox,
      model: sandbox.ACPView.model,
      source: sandbox.ACPView.source,
      fixtures: sandbox.ACPViewFixtures || null,
      warnings,
    };
  }

  const codes = (view) => (view.warnings || []).map((item) => item.code);

  function makeFetch() {
    const calls = [];
    const queue = [];
    function fetchMock(url, opts) {
      calls.push({ url, opts });
      return new Promise((resolve, reject) => { queue.push({ url, resolve, reject }); });
    }
    fetchMock.calls = calls;
    fetchMock.queue = queue;
    fetchMock.respond = (index, body) => { queue[index].resolve(body); };
    return fetchMock;
  }

  await test("normalize: empty input yields the complete acp.view.v1 envelope, never undefined fields", () => {
    const { model } = setup();
    const view = model.normalize(null, { family: "structure", source: "live", viewId: "structure:empty" });
    assertEq(view.schema, "acp.view.v1");
    assertEq(view.viewId, "structure:empty");
    assertEq(view.family, "structure");
    assertEq(view.source, "live");
    assertEq(view.availability, "empty");
    assertEq(view.fixture, false);
    assertEq(view.provenance, {});
    assertEq(view.axes, null);
    assertEq(view.series, []);
    assertEq(view.frames, []);
    assertEq(view.annotations, []);
    assertEq(view.objects, {});
    assertEq(view.groups, []);
    assertEq(view.entries, []);
    assertEq(view.overlay, null);
    assertEq(view.modes, []);
    assertEq(view.vibration, null);
    assertEq(view.warnings, []);
  });

  await test("normalize: ready envelope preserves objects, frames, series, axes and annotation payload", () => {
    const { model } = setup();
    const view = model.normalize({
      availability: "ready",
      provenance: { jobId: "job-1", attempt: 2 },
      objects: { "job-1:opt.xyz": { format: "xyz", text: "1\n\nH 0 0 0\n" } },
      frames: [{ index: 0, label: "opt", energy: -1.5, geometryRef: "job-1:opt.xyz" }],
      axes: { x: { label: "帧", unit: "" }, y: { label: "能量", unit: "kcal/mol" } },
      series: [{
        id: "relative_energy", label: "相对能量", unit: "kcal/mol",
        points: [{ x: 0, y: 0, frameIndex: 0 }],
      }],
      annotations: [{ id: "ts", label: "TS", kind: "ts", x: 1, y: 2.5, payload: { frame_index: 1, selected: true } }],
    }, { family: "energy", source: "live", viewId: "energy:1" });

    assertEq(view.availability, "ready");
    assertEq(view.provenance, { jobId: "job-1", attempt: 2 });
    assertEq(view.frames[0].energy, -1.5);
    assertEq(view.frames[0].geometryRef, "job-1:opt.xyz");
    assertEq(view.objects["job-1:opt.xyz"].format, "xyz");
    assertEq(view.series[0].points[0].frameIndex, 0);
    assertEq(view.axes.y.unit, "kcal/mol");
    assertEq(view.annotations[0].kind, "ts");
    assertEq(view.annotations[0].payload.selected, true);
  });

  await test("normalize: missing point values stay null (never 0-filled) and are declared in warnings", () => {
    const { model } = setup();
    const view = model.normalize({
      series: [{
        id: "s", label: "S",
        points: [{ x: 0, y: null }, { x: null, y: 1 }, { x: 2, y: 3 }],
      }],
    }, { family: "energy", source: "live" });
    const points = view.series[0].points;
    assertEq(points[0].y, null, "missing y is null, not 0");
    assertEq(points[1].x, null, "missing x is null, not 0");
    assertEq(points[2], { x: 2, y: 3 }, "valid point preserved");
    assert(codes(view).indexOf("W_POINT_MISSING") !== -1, "missing point warning present");
    assert(codes(view).indexOf("W_NO_DRAWABLE_POINTS") === -1, "one drawable point is enough");
  });

  await test("normalize: ready without data is downgraded to empty with an explicit warning", () => {
    const { model } = setup();
    const view = model.normalize({ availability: "ready" }, { family: "structure", source: "live" });
    assertEq(view.availability, "empty");
    assert(codes(view).indexOf("W_READY_WITHOUT_DATA") !== -1);
  });

  await test("normalize: error/denied states carry a reason and denied maps to the panel error state", () => {
    const { model } = setup();
    const errorView = model.errorView({ family: "energy", source: "live" }, "E_X", "boom");
    assertEq(errorView.availability, "error");
    assertEq(errorView.warnings[0].code, "E_X");
    assertEq(errorView.warnings[0].message, "boom");

    const denied = model.normalize({ availability: "denied" }, { family: "structure", source: "live" });
    assertEq(denied.availability, "denied");
    assertEq(denied.warnings[0].code, "E_DENIED");
    assertEq(model.stateOf(denied), "error", "denied merges into the four-state error surface");

    const bareError = model.normalize({ availability: "error" }, { family: "structure", source: "live" });
    assertEq(bareError.warnings[0].code, "E_UNKNOWN");
  });

  await test("normalize: unknown family/source and malformed collections are explicit warnings, not silent drops", () => {
    const { model } = setup();
    const view = model.normalize({
      availability: "ready",
      warnings: "not-an-array",
      frames: "not-an-array",
      series: {},
      axes: [],
      objects: { ok: { format: "xyz", text: "1\n\nH 0 0 0\n" } },
    }, { family: "nope", source: "weird" });
    const got = codes(view);
    assert(got.indexOf("E_FAMILY") !== -1, "unknown family");
    assert(got.indexOf("W_SOURCE") !== -1, "unknown source");
    assertEq(view.source, "live", "unknown source falls back to live explicitly");
    assert(got.indexOf("W_WARNINGS_SHAPE") !== -1, "warnings shape");
    assert(got.indexOf("W_FRAMES_SHAPE") !== -1, "frames shape");
    assert(got.indexOf("W_SERIES_SHAPE") !== -1, "series shape");
    assert(got.indexOf("W_AXES_SHAPE") !== -1, "axes shape");
    assertEq(view.availability, "ready", "valid object still renders");
  });

  await test("normalize: idempotent for an already-normalized envelope", () => {
    const { model } = setup();
    const raw = {
      availability: "ready",
      provenance: { jobId: "job-1" },
      objects: { k: { format: "xyz", text: "1\n\nH 0 0 0\n" } },
      frames: [{ index: 0, label: "f", energy: -1, geometryRef: "k" }],
      series: [{ id: "s", label: "S", unit: "kcal/mol", points: [{ x: 0, y: 1, frameIndex: 0 }] }],
      annotations: [{ id: "a", kind: "ts", x: 0, y: 1, payload: { frame_index: 0 } }],
    };
    const once = model.normalize(raw, { family: "energy", source: "live", viewId: "v" });
    const twice = model.normalize(once, { family: "energy", source: "live", viewId: "v" });
    assertEq(twice, once);
  });

  await test("fixture marker is derived from source and cannot be hidden by a raw field", () => {
    const { model } = setup();
    const fixture = model.normalize({ objects: { k: "1\n\nH 0 0 0\n" } }, { family: "structure", source: "fixture" });
    assertEq(fixture.source, "fixture");
    assertEq(fixture.fixture, true);
    const live = model.normalize({ fixture: true, objects: { k: "1\n\nH 0 0 0\n" } }, { family: "structure", source: "live" });
    assertEq(live.source, "live");
    assertEq(live.fixture, false, "raw.fixture cannot override the adapter source");
  });

  await test("structure helpers: ref normalization, stable keys and multi-frame XYZ parsing", () => {
    const { model } = setup();
    const req = model.normalizeStructureRef(JSON.stringify({
      kind: "job-file", jobId: "job-1", path: "WORK/a.xyz", frameIndex: 2.9,
    }));
    assertEq(req.kind, "job-file");
    assertEq(req.frameIndex, 2, "frameIndex is floored");
    assertEq(req.entryId, "job-1:WORK/a.xyz");
    assertEq(model.normalizeStructureRef("not json").kind, "invalid");
    assertEq(model.normalizeStructureRef({ kind: "job-file", jobId: "j" }).kind, "invalid");
    assertEq(model.normalizeStructureRef({ kind: "nope" }).kind, "invalid");
    assertEq(
      model.structureRefKey({ kind: "job-file", jobId: "j", path: "p" }),
      model.structureRefKey({ path: "p", jobId: "j", kind: "job-file" }),
      "ref key is field-order stable"
    );
    const frames = model.parseXyzFrames("2\nenergy -1.0\nC 0 0 0\nO 1.2 0 0\n\n1\nenergy -2.0\nH 0 0 0\n");
    assertEq(frames.length, 2);
    assertEq(frames[0].energy, -1.0);
    assertEq(frames[1].atoms[0].elem, "H");
    assertEq(frames[1].index, 1);
  });

  await test("source fixture: structure view is ready, two frames, fixture-marked, and offline", async () => {
    const { source, fixtures } = setup();
    const adapter = source.create({ mode: "fixture", fixtures });
    assertEq(adapter.mode, "fixture");
    assertEq(adapter.health().ok, true);
    const view = await adapter.load("structure:fixture", { family: "structure" });
    assertEq(view.source, "fixture");
    assertEq(view.fixture, true);
    assertEq(view.availability, "ready");
    assertEq(view.frames.length, 2);
    assertEq(view.frames[0].energy, -76.4);
    assertEq(view.objects["fixture:water"].format, "xyz");
    assertEq(Object.keys(view.objects).length, 1);
  });

  await test("source fixture: energy view maps series, frames and annotations without faking points", async () => {
    const { source, fixtures } = setup();
    const adapter = source.create({ mode: "fixture", fixtures });
    const view = await adapter.load("energy:fixture", { family: "energy" });
    assertEq(view.source, "fixture");
    assertEq(view.availability, "ready");
    assertEq(view.series.length, 1);
    assertEq(view.series[0].points.length, 5);
    assertEq(view.frames.length, 5);
    assertEq(view.annotations.length, 2);
    assertEq(view.annotations[0].kind, "ts");
    assertEq(view.axes.y.unit, "kcal/mol");
    assertEq(view.warnings.length, 0);
  });

  await test("source mode: URL wins over storage, storage wins over default, v3 falls back with a warning", () => {
    const { source, warnings } = setup();
    const storage = makeStorage({ "acp.view.source": "fixture" });
    assertEq(source.modeFromSearch("?x=1&view=fixture"), "fixture");
    assertEq(source.resolveMode({ location: { search: "?view=live" }, storage }), "live");
    assertEq(source.resolveMode({ location: { search: "" }, storage }), "fixture");
    assertEq(source.resolveMode({ location: { search: "" }, storage: makeStorage({}) }), "live");
    assertEq(source.resolveMode({ location: { search: "?view=v3" }, storage }), "live");
    assert(warnings.some((line) => line.indexOf("v3 adapter is not implemented") !== -1),
      "v3 fallback is logged, not silent");
  });

  await test("source v3: explicit adapter reports not-implemented and load rejects with a typed error", async () => {
    const { source } = setup();
    const adapter = source.create({ mode: "v3" });
    assertEq(adapter.mode, "v3");
    assertEq(adapter.health(), { mode: "v3", ok: false, reason: "not-implemented" });
    let caught = null;
    try { await adapter.load("structure:1", { family: "structure" }); } catch (error) { caught = error; }
    assert(caught && caught.code === "E_NOT_IMPLEMENTED", "typed E_NOT_IMPLEMENTED");
  });

  await test("source live: structure uses the exact legacy URLs and reports HTTP failures as error views", async () => {
    const fetchMock = makeFetch();
    const { source } = setup({ fetch: fetchMock });
    const adapter = source.create({ mode: "live", baseUrl: "/api/v1", fetch: fetchMock });
    assertEq(adapter.health().ok, true);

    const pending = adapter.load("structure:1", {
      family: "structure",
      structureRef: { kind: "job-file", jobId: "a b", path: "x dir/f.xyz" },
    });
    await tick();
    assertEq(fetchMock.calls[0].url, "/api/v1/jobs/a%20b/files/x%20dir/f.xyz");
    fetchMock.respond(0, { ok: false, status: 500, statusText: "Server exploded" });
    const failed = await pending;
    assertEq(failed.availability, "error");
    assertEq(failed.warnings[0].code, "E_FETCH");
    assert(/HTTP 500 Server exploded/.test(failed.warnings[0].message));

    const remote = adapter.load("structure:2", {
      family: "structure",
      structureRef: { kind: "job-remote-file", jobId: "job-r", path: "WORK/a.xyz" },
    });
    await tick();
    assertEq(fetchMock.calls[1].url,
      "/api/v1/jobs/job-r/remote-files/WORK/a.xyz/preview?mode=structure");
    fetchMock.respond(1, { content: "1\n\nH 0 0 0\n" });
    const remoteView = await remote;
    assertEq(remoteView.availability, "ready");
  });

  await test("source live: energy-graph maps to points/frames/annotations and keeps nulls null", async () => {
    const fetchMock = makeFetch();
    const { source } = setup({ fetch: fetchMock });
    const adapter = source.create({ mode: "live", baseUrl: "/api/v1", fetch: fetchMock });
    const pending = adapter.load("energy:job-1", { family: "energy", jobId: "job-1" });
    await tick();
    assertEq(fetchMock.calls[0].url, "/api/v1/jobs/job-1/energy-graph");
    fetchMock.respond(0, {
      job_id: "job-1",
      view_type: "scan",
      x_axis: { label: "扫描坐标", unit: "Å" },
      series: [{ id: "relative_energy", label: "相对能量", unit: "kcal/mol", values: [-1, null, -3], x_values: [0, 1, 2] }],
      nodes: [
        { id: "n0", frame_index: 0, energy: -1, status: "ok" },
        { id: "n1", frame_index: 1, energy: null, status: "failed" },
        { id: "n2", frame_index: 2, energy: -3, status: "ok" },
      ],
      annotations: [{ id: "ts", type: "ts", frame_index: 1, x: 1, y: null, selected: true }],
    });
    const view = await pending;
    assertEq(view.source, "live");
    assertEq(view.availability, "ready");
    assertEq(view.axes.x.label, "扫描坐标");
    assertEq(view.axes.y.unit, "kcal/mol");
    assertEq(view.series[0].points.length, 3);
    assertEq(view.series[0].points[1].y, null, "null energy stays null");
    assert(codes(view).indexOf("W_POINT_MISSING") !== -1);
    assertEq(view.frames.length, 3);
    assertEq(view.frames[1].status, "failed");
    assertEq(view.annotations[0].kind, "ts");
    assertEq(view.annotations[0].payload.selected, true);
  });

  await test("source live: an unsupported family is an explicit error, not an empty success", async () => {
    const { source } = setup();
    const adapter = source.create({ mode: "live", baseUrl: "/api/v1", fetch: makeFetch() });
    const view = await adapter.load("vibration:1", { family: "vibration" });
    assertEq(view.availability, "error");
    assertEq(view.warnings[0].code, "E_FAMILY_NOT_IMPLEMENTED");
  });

  await test("normalize overlay: unproven mapping is a ready view with a blocking warning", () => {
    const { model } = setup();
    const view = model.normalize({
      overlay: {
        ok: false, entryA: "a", entryB: "b", mapping: null, rmsd: null,
        maxDisplacement: null, nMapped: 0, reason: "unproven",
      },
      objects: { b: { format: "xyz", text: "1\n\nH 0 0 0\n" } },
    }, { family: "overlay", source: "live", viewId: "overlay:a:b" });
    assertEq(view.availability, "ready");
    assertEq(view.overlay.ok, false);
    assertEq(view.overlay.entryB, "b");
    assertEq(view.overlay.reason, "unproven");
    assert(codes(view).indexOf("W_OVERLAY_UNPROVEN") !== -1);
  });

  await test("normalize thermo: finite ensemble values survive; an empty thermo object warns", () => {
    const { model } = setup();
    const view = model.normalize({
      availability: "ready",
      entries: [{ id: "e", group_id: "g", label: "E", source: { geometry_ref: "x.xyz" } }],
      thermo: {
        total_gibbs_kcal_mol: -12.5, total_gibbs_hartree: -0.004,
        rank1_weight: 0.7, t_s_mix_kcal_per_mol: 1.2,
        population_coverage: 0.95, temperature_k: 298.15,
      },
    }, { family: "catalog", source: "live" });
    assertEq(view.thermo.totalGibbsKcalMol, -12.5);
    assertEq(view.thermo.totalGibbsHartree, -0.004);
    assertEq(view.thermo.rank1Weight, 0.7);
    assertEq(view.thermo.temperatureK, 298.15);

    const empty = model.normalize({ thermo: {} }, { family: "catalog", source: "live" });
    assertEq(empty.thermo, null);
    assert(codes(empty).indexOf("W_THERMO_EMPTY") !== -1);
  });

  await test("normalize vibration: modes keep native indices/vectors; unavailable is a warning, not fake rows", () => {
    const { model } = setup();
    const view = model.normalize({
      modes: [
        { mode_index: 7, frequency_cm1: -120.5, imaginary: true, ir_intensity: 12.5,
          vectors: [[0, 0, 1], [0, 1, 0]] },
        { mode_index: 9, frequency_cm1: 88.0, imaginary: false, ir_intensity: null,
          vectors: [[1, 0, 0]] },
      ],
      vibration: { available: true, atom_count: 2, imaginary_count: 1, source: "normal_modes" },
      availability: "ready",
    }, { family: "vibration", source: "live" });
    assertEq(view.modes.length, 2);
    assertEq(view.modes[0].modeIndex, 7);
    assertEq(view.modes[0].frequencyCm1, -120.5);
    assertEq(view.modes[0].imaginary, true);
    assertEq(view.modes[0].vectors[0], [0, 0, 1]);
    assertEq(view.vibration.available, true);
    assertEq(view.vibration.imaginaryCount, 1);

    const unavailable = model.normalize({
      modes: [],
      vibration: { available: false, reason: "no_normal_modes" },
    }, { family: "vibration", source: "live" });
    assertEq(unavailable.availability, "empty");
    assertEq(unavailable.modes, []);
    assert(codes(unavailable).indexOf("W_VIBRATION_UNAVAILABLE") !== -1);
  });

  await test("normalize catalog: groups and entries keep identity, geometry refs and energy metadata", () => {
    const { model } = setup();
    const view = model.normalize({
      availability: "ready",
      provenance: { jobId: "job-1", revision: "r1" },
      groups: [{ id: "formal", label: "正式结果", kind: "formal_result" }],
      entries: [{
        id: "e-1",
        group_id: "formal",
        label: "优化结果",
        role: "minimum",
        status: "completed",
        relative_energy_kcal: 0,
        energy: { value: -76.4, unit: "hartree", kind: "electronic", temperatureK: 298.15 },
        badges: ["opt", "converged"],
        source: { kind: "formal_result", geometry_ref: "RESULT/opt.xyz", frame_index: 2 },
      }],
    }, { family: "catalog", source: "live", viewId: "catalog:job-1" });
    assertEq(view.availability, "ready");
    assertEq(view.groups[0], { id: "formal", label: "正式结果", kind: "formal_result" });
    const entry = view.entries[0];
    assertEq(entry.id, "e-1");
    assertEq(entry.groupId, "formal");
    assertEq(entry.label, "优化结果");
    assertEq(entry.role, "minimum");
    assertEq(entry.geometryRef, "RESULT/opt.xyz");
    assertEq(entry.frameIndex, 2);
    assertEq(entry.sourceKind, "formal_result");
    assertEq(entry.energy.value, -76.4);
    assertEq(entry.energy.temperatureK, 298.15);
    assertEq(entry.badges, ["opt", "converged"]);
  });

  await test("normalize catalog: missing ids and malformed groups/entries are explicit warnings", () => {
    const { model } = setup();
    const view = model.normalize({
      availability: "ready",
      groups: [{ label: "missing id" }, { id: "g", label: "ok" }],
      entries: [{ label: "missing id" }, { id: "e", label: "ok", group_id: "g" }],
    }, { family: "catalog", source: "live" });
    assertEq(view.groups.length, 1);
    assertEq(view.entries.length, 1);
    assert(codes(view).indexOf("W_GROUP_ID") !== -1, "missing group id warned");
    assert(codes(view).indexOf("W_ENTRY_ID") !== -1, "missing entry id warned");
  });

  await test("source fixture catalog: two groups / three entries, fixture-marked, offline", async () => {
    const { source, fixtures } = setup();
    const adapter = source.create({ mode: "fixture", fixtures });
    const view = await adapter.load("catalog:fixture", { family: "catalog", jobId: "fixture-structure" });
    assertEq(view.source, "fixture");
    assertEq(view.fixture, true);
    assertEq(view.availability, "ready");
    assertEq(view.groups.length, 2);
    assertEq(view.entries.length, 3);
    assertEq(view.entries[0].geometryRef, "RESULT/optimized.xyz");
    assertEq(view.entries[0].relativeEnergyKcal, 0);
    assertEq(view.entries[1].role, "transition_state");
  });

  await test("source mapStructureCatalog: pending_fetch is empty+warned, unavailable is an error", () => {
    const { source } = setup();
    const meta = { family: "catalog", source: "live", jobId: "job-1" };
    const pending = source.mapStructureCatalog({
      availability: "pending_fetch", entries: [], groups: [], warnings: ["pending_fetch"],
    }, meta);
    assertEq(pending.availability, "empty");
    assert(codes(pending).indexOf("W_PENDING_FETCH") !== -1);

    const unavailable = source.mapStructureCatalog({
      availability: "unavailable", entries: [], groups: [], warnings: ["remote catalog missing"],
    }, meta);
    assertEq(unavailable.availability, "error");
    assertEq(unavailable.warnings[0].code, "E_CATALOG_UNAVAILABLE");
    assert(/remote catalog missing/.test(unavailable.warnings[0].message));
  });

  await test("source live catalog: exact endpoint and mapped entries", async () => {
    const fetchMock = makeFetch();
    const { source } = setup({ fetch: fetchMock });
    const adapter = source.create({ mode: "live", baseUrl: "/api/v1", fetch: fetchMock });
    const pending = adapter.load("catalog:job-1", { family: "catalog", jobId: "job-1" });
    await tick();
    assertEq(fetchMock.calls[0].url, "/api/v1/jobs/job-1/structure-viewer");
    fetchMock.respond(0, {
      job_id: "job-1", workflow: "optimize", job_status: "COMPLETED",
      availability: "ready", revision: "r2",
      groups: [{ id: "formal", label: "正式结果" }],
      entries: [{
        id: "e-1", group_id: "formal", label: "优化结果", role: "minimum",
        energy: { value: -76.4, unit: "hartree", kind: "electronic", temperature_k: 298.15 },
        source: { kind: "formal_result", geometry_ref: "RESULT/opt.xyz", frame_index: 0 },
      }],
    });
    const view = await pending;
    assertEq(view.availability, "ready");
    assertEq(view.provenance.jobId, "job-1");
    assertEq(view.provenance.revision, "r2");
    assertEq(view.entries[0].id, "e-1");
    assertEq(view.entries[0].groupId, "formal");
    assertEq(view.entries[0].energy.temperatureK, 298.15);
  });

  await test("structure-entry ref: catalog entry geometry URL and namespaced store identity", () => {
    const { model, source } = setup();
    const req = model.normalizeStructureRef({
      kind: "structure-entry", jobId: "job-1", entryId: "e 1", frameIndex: 1.9,
    });
    assertEq(req.kind, "structure-entry");
    assertEq(req.entryId, "e 1");
    assertEq(req.frameIndex, 1);
    assertEq(req.storeEntryId, "job-1:entry:e 1");
    assertEq(source.buildStructureUrl(req, "/api/v1"),
      "/api/v1/jobs/job-1/structure-viewer/entries/e%201/geometry");
  });

  await test("source: missing fixtures and missing fetch are explicit error views", async () => {
    const { source } = setup({ fixtures: false });
    const fixtureAdapter = source.create({ mode: "fixture", fixtures: {} });
    assertEq(fixtureAdapter.health().ok, false);
    const missing = await fixtureAdapter.load("structure:1", { family: "structure" });
    assertEq(missing.availability, "error");
    assertEq(missing.warnings[0].code, "E_FIXTURE_MISSING");

    const liveAdapter = source.create({ mode: "live", baseUrl: "/api/v1" });
    assertEq(liveAdapter.health().ok, false);
    const noFetch = await liveAdapter.load("structure:1", {
      family: "structure",
      structureRef: { kind: "job-file", jobId: "j", path: "a.xyz" },
    });
    assertEq(noFetch.availability, "error");
    assertEq(noFetch.warnings[0].code, "E_NO_FETCH");
  });

  await test("next entry loads the contract, adapter, fixtures and viewer in dependency order", () => {
    const fs = require("fs");
    const nodePath = require("path");
    const html = fs.readFileSync(nodePath.resolve(__dirname, "../../next/index.html"), "utf8");
    const order = [
      "/js/visualization/energy_chart_renderer.js",
      "/js/visualization/view_model.js",
      "/js/visualization/view_source.js",
      "/js/visualization/fixtures/views/structure.js",
      "/js/visualization/fixtures/views/energy.js",
      "/js/visualization/fixtures/views/catalog.js",
      "/js/modules/molecule-viewer/module.js",
      "/js/modules/energy-profile/module.js",
    ];
    let previous = -1;
    order.forEach((asset) => {
      const index = html.indexOf(asset);
      assert(index !== -1, "next entry must load " + asset);
      assert(index > previous, asset + " must load after its dependency");
      previous = index;
    });
  });
};
