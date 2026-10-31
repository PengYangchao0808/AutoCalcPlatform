/*
 * ACP 3.0 module: energy-profile (Track A / A-38).
 *
 * Node vm tests. Rendering runs the REAL energy_chart_renderer.js over the
 * REAL acp.view.v1 adapter; only fetch/browser/DOM are stubbed.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const flush = async () => { await sleep(0); await sleep(0); };

  function makeEl(tag) {
    const el = {
      tagName: String(tag).toUpperCase(),
      children: [],
      parentNode: null,
      attributes: {},
      className: "",
      textContent: "",
      innerHTML: "",
      hidden: false,
      type: "",
      clientWidth: 640,
      clientHeight: 320,
      style: {},
      _listeners: {},
      appendChild(child) {
        if (child.parentNode) child.parentNode.removeChild(child);
        child.parentNode = el;
        el.children.push(child);
        return child;
      },
      removeChild(child) {
        const index = el.children.indexOf(child);
        if (index === -1) throw new Error("removeChild: not a child");
        el.children.splice(index, 1);
        child.parentNode = null;
        return child;
      },
      remove() { if (el.parentNode) el.parentNode.removeChild(el); },
      replaceChildren() {
        el.children.slice().forEach((child) => { child.parentNode = null; });
        el.children = [];
      },
      setAttribute(key, value) { el.attributes[key] = String(value); },
      getAttribute(key) { return key in el.attributes ? el.attributes[key] : null; },
      addEventListener(type, fn) { (el._listeners[type] = el._listeners[type] || []).push(fn); },
      removeEventListener(type, fn) {
        const listeners = el._listeners[type] || [];
        const index = listeners.indexOf(fn);
        if (index !== -1) listeners.splice(index, 1);
      },
      dispatch(type, event) { (el._listeners[type] || []).slice().forEach((fn) => fn(event || { type })); },
      click() { el.dispatch("click", { type: "click", target: el }); },
      querySelector() { return null; },
      querySelectorAll() { return []; },
    };
    return el;
  }

  function makeHost() {
    const host = makeEl("div");
    host.__isDocumentRoot = true;
    return host;
  }

  function makeDocument() {
    return {
      createElement: (tag) => makeEl(tag),
      getElementById: () => null,
    };
  }

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

  function makeContext(initial) {
    let snapshot = initial || { revision: 0, source: "test" };
    const listeners = [];
    return {
      getSnapshot: () => snapshot,
      subscribe(fn) {
        listeners.push(fn);
        return () => {
          const index = listeners.indexOf(fn);
          if (index !== -1) listeners.splice(index, 1);
        };
      },
      emit(next) { snapshot = next; listeners.slice().forEach((fn) => fn(next)); },
      listenerCount: () => listeners.length,
    };
  }

  function setup(options) {
    options = options || {};
    const fetchMock = makeFetch();
    const sandbox = makeSandbox({
      document: makeDocument(),
      location: options.location || { search: "" },
      localStorage: options.localStorage || null,
      fetch: fetchMock,
      console: { log() {}, warn() {}, error() {} },
    });
    if (!options.skipRenderer) loadScript("js/visualization/energy_chart_renderer.js", sandbox);
    loadScript("js/visualization/view_model.js", sandbox);
    if (!options.skipSource) loadScript("js/visualization/view_source.js", sandbox);
    loadScript("js/visualization/fixtures/views/energy.js", sandbox);
    loadScript("js/modules/energy-profile/module.js", sandbox);
    return { sandbox, fetchMock, entry: sandbox.window.ACPNextModules["energy-profile"] };
  }

  function makeInstance(options) {
    const env = setup(options);
    const ctx = makeContext(options && options.contextInitial);
    const host = makeHost();
    const instance = env.entry.factory({
      context: ctx,
      commands: (options && options.commands) || {},
      fetch: env.fetchMock,
      viewSource: options && options.viewSource,
      candidateApi: options && options.candidateApi,
    });
    return Object.assign(env, { ctx, host, instance });
  }

  function find(el, className) {
    if (!el) return null;
    if (String(el.className || "").split(/\s+/).indexOf(className) !== -1) return el;
    for (const child of el.children || []) {
      const found = find(child, className);
      if (found) return found;
    }
    return null;
  }

  function findByAttr(el, name, value) {
    if (!el) return null;
    if (typeof el.getAttribute === "function" && el.getAttribute(name) === String(value)) return el;
    for (const child of el.children || []) {
      const found = findByAttr(child, name, value);
      if (found) return found;
    }
    return null;
  }

  function findAll(el, className) {
    const out = [];
    if (!el) return out;
    if (String(el.className || "").split(/\s+/).indexOf(className) !== -1) out.push(el);
    for (const child of el.children || []) out.push(...findAll(child, className));
    return out;
  }

  function textOf(el) {
    if (!el) return "";
    let out = String(el.textContent || "");
    for (const child of el.children || []) out += " " + textOf(child);
    return out;
  }

  function clickFrame(chart, frameIndex) {
    chart.dispatch("click", {
      target: {
        getAttribute(name) { return name === "data-energy-frame" ? String(frameIndex) : null; },
        parentNode: chart,
      },
    });
  }

  const energyPayload = (overrides) => Object.assign({
    job_id: "job-1",
    view_type: "energy",
    x_axis: { label: "扫描坐标", unit: "Å" },
    series: [{
      id: "relative_energy", label: "相对能量", unit: "kcal/mol",
      values: [-1.0, -2.0, -3.0], x_values: [0.0, 1.0, 2.0],
    }],
    nodes: [
      { id: "n0", frame_index: 0, energy: -1.0, status: "ok" },
      { id: "n1", frame_index: 1, energy: -2.0, status: "ok" },
      { id: "n2", frame_index: 2, energy: -3.0, status: "ok" },
    ],
    annotations: [],
  }, overrides || {});

  await test("descriptor + instance surface match the module contract", () => {
    const { entry } = setup();
    const descriptor = entry.descriptor;
    assertEq(descriptor.id, "energy-profile");
    assertEq(descriptor.interfaceVersion, 1);
    assertEq(descriptor.provides, ["energy.profile"]);
    assertEq(descriptor.binds, ["taskRef"]);
    assertEq(descriptor.singleton, undefined);
    const instance = entry.factory({ context: makeContext(), commands: {} });
    ["mount", "updateContext", "resize", "suspend", "resume", "dispose"].forEach((method) => {
      assertEq(typeof instance[method], "function", method + " must be a function");
    });
  });

  await test("mount without a task shows the empty state and performs no fetch", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    const root = t.host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "empty");
    assertEq(t.fetchMock.calls.length, 0);
    const overlay = find(root, "acp-mod-energy-overlay");
    assertEq(overlay.hidden, false, "empty overlay visible");
    assertEq(find(root, "acp-mod-energy-retry").hidden, true);
    t.instance.dispose();
  });

  await test("taskRef loads energy-graph through the adapter and renders the canonical SVG", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-1" }, revision: 1, source: "shell" });
    await flush();
    assertEq(t.fetchMock.calls.length, 1);
    assertEq(t.fetchMock.calls[0].url, "/api/v1/jobs/job-1/energy-graph");
    const root = t.host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "loading");
    t.fetchMock.respond(0, { ok: true, json: () => Promise.resolve(energyPayload()) });
    await flush();
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    const chart = find(root, "acp-mod-energy-chart");
    assert(chart.innerHTML.indexOf("data-energy-chart") !== -1, "canonical renderer emitted its SVG");
    assert(chart.innerHTML.indexOf("kcal/mol") !== -1, "series unit preserved");
    assertEq(root.getAttribute("data-acp-fixture"), "false");
    t.instance.dispose();
  });

  await test("fixture mode renders the fixture energy view offline with a visible badge", async () => {
    const t = makeInstance({ location: { search: "?view=fixture" } });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    await flush();
    const root = t.host.children[0];
    assertEq(t.fetchMock.calls.length, 0, "fixture never calls the network");
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    assertEq(root.getAttribute("data-acp-fixture"), "true");
    assertEq(find(root, "acp-mod-energy-fixture").hidden, false);
    assert(find(root, "acp-mod-energy-chart").innerHTML.indexOf("data-energy-chart") !== -1);
    t.instance.dispose();
  });

  await test("HTTP failure is an explicit error state and retry really re-fetches", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-1" }, revision: 1, source: "shell" });
    await flush();
    t.fetchMock.respond(0, { ok: false, status: 500, statusText: "Server exploded" });
    await flush();
    const root = t.host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "error");
    assert(/HTTP 500 Server exploded/.test(find(root, "acp-mod-energy-detail").textContent));
    const retry = find(root, "acp-mod-energy-retry");
    assertEq(retry.hidden, false);
    retry.click();
    await flush();
    assertEq(t.fetchMock.calls.length, 2, "retry re-fetches");
    t.fetchMock.respond(1, { ok: true, json: () => Promise.resolve(energyPayload()) });
    await flush();
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    t.instance.dispose();
  });

  await test("empty series is a declared empty state, not a fake curve", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-empty" }, revision: 1, source: "shell" });
    await flush();
    t.fetchMock.respond(0, {
      ok: true,
      json: () => Promise.resolve({ job_id: "job-empty", view_type: "energy", series: [], nodes: [] }),
    });
    await flush();
    const root = t.host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "empty");
    assertEq(find(root, "acp-mod-energy-chart").innerHTML, "");
    t.instance.dispose();
  });

  await test("null points stay null (warned) and never reach the SVG as zero/NaN", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-null" }, revision: 1, source: "shell" });
    await flush();
    t.fetchMock.respond(0, {
      ok: true,
      json: () => Promise.resolve(energyPayload({
        job_id: "job-null",
        series: [{ id: "relative_energy", label: "相对能量", unit: "kcal/mol",
          values: [-1.0, null, -3.0], x_values: [0.0, 1.0, 2.0] }],
        nodes: [
          { id: "n0", frame_index: 0, energy: -1.0, status: "ok" },
          { id: "n1", frame_index: 1, energy: null, status: "failed" },
          { id: "n2", frame_index: 2, energy: -3.0, status: "ok" },
        ],
      })),
    });
    await flush();
    const root = t.host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    assertEq(root.getAttribute("data-acp-warning-count"), "1");
    const chart = find(root, "acp-mod-energy-chart");
    assert(chart.innerHTML.indexOf("NaN") === -1, "no NaN in the SVG");
    const groups = (chart.innerHTML.match(/class="energy-point-group"/g) || []).length;
    assertEq(groups, 2, "only the two finite points are rendered");
    t.instance.dispose();
  });

  await test("toRendererData maps frames/series/annotations without leaking the envelope", () => {
    const { entry } = setup();
    const view = {
      series: [{ id: "s", label: "S", unit: "kcal/mol", points: [
        { x: 0, y: 1, frameIndex: 0, nodeId: "n0" },
        { x: 1, y: null, frameIndex: 1, nodeId: "n1" },
      ] }],
      frames: [{ index: 0, energy: 1, status: "ok" }, { index: 1, energy: null, status: "failed" }],
      annotations: [{ id: "a", label: "TS", kind: "ts", x: 1, y: 2, payload: { frame_index: 1, selected: true } }],
      provenance: { jobId: "job-1", viewType: "scan", xAxisMode: "frame_index" },
      axes: { x: { label: "帧", unit: "" }, y: { label: "能量", unit: "kcal/mol" } },
    };
    const data = entry._internals.toRendererData(view);
    assertEq(data.view_type, "scan");
    assertEq(data.x_axis.label, "帧");
    assertEq(data.series[0].values, [1, null]);
    assertEq(data.series[0].x_values, [0, 1]);
    assertEq(data.nodes[1].frame_index, 1);
    assertEq(data.nodes[1].status, "failed");
    assertEq(data.annotations[0].type, "ts");
    assertEq(data.annotations[0].frame_index, 1);
    assertEq(data.annotations[0].selected, true);
  });

  await test("candidate band renders and a point/annotation click locates the structure frame", async () => {
    const calls = [];
    const t = makeInstance({
      commands: {
        openStructure(ref) { calls.push(ref); },
      },
    });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-locate" }, revision: 1, source: "shell" });
    await flush();
    t.fetchMock.respond(0, {
      ok: true,
      json: () => Promise.resolve(energyPayload({
        job_id: "job-locate",
        nodes: [
          { id: "n0", frame_index: 0, energy: -1.0, status: "ok", geometry_ref: "RESULT/f0.xyz" },
          { id: "n1", frame_index: 1, energy: -2.0, status: "ok", geometry_ref: "RESULT/f1.xyz" },
          { id: "n2", frame_index: 2, energy: -3.0, status: "ok", geometry_ref: "RESULT/f2.xyz" },
        ],
        annotations: [
          { id: "ts-1", type: "ts", label: "TS", frame_index: 1, x: 1, y: -2.0, selected: false },
        ],
      })),
    });
    await flush();
    const root = t.host.children[0];
    const chart = find(root, "acp-mod-energy-chart");
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    assert(chart.innerHTML.indexOf("energy-candidate-band") !== -1, "candidate band rendered");
    assert(chart.innerHTML.indexOf('data-energy-candidate-frame="1"') !== -1, "band chip carries its frame");
    assert(chart.innerHTML.indexOf("energy-marker-ts") !== -1, "candidate marker rendered in the plot");

    const pointTarget = {
      getAttribute(name) { return name === "data-energy-frame" ? "1" : null; },
      parentNode: chart,
    };
    chart.dispatch("click", { target: pointTarget });
    await flush();
    assertEq(calls.length, 1, "point click locates one structure");
    assertEq(calls[0].kind, "job-file");
    assertEq(calls[0].path, "RESULT/f1.xyz");
    assertEq(calls[0].frameIndex, 1);
    assertEq(root.getAttribute("data-acp-module-state"), "ready", "selection keeps the chart ready");

    const annotationTarget = {
      getAttribute(name) {
        if (name === "data-energy-annotation") return "ts-1";
        if (name === "data-energy-frame") return "1";
        return null;
      },
      parentNode: chart,
    };
    chart.dispatch("click", { target: annotationTarget });
    await flush();
    assertEq(calls.length, 2, "annotation click locates again");

    const bandTarget = {
      getAttribute(name) { return name === "data-energy-candidate-jump" ? "2" : null; },
      parentNode: chart,
    };
    chart.dispatch("click", { target: bandTarget });
    await flush();
    assertEq(calls.length, 3, "candidate band click locates the chip frame");
    assertEq(calls[2].path, "RESULT/f2.xyz");
    assertEq(typeof t.instance.submit, "undefined", "energy-profile exposes no submit surface");
    t.instance.dispose();
  });

  await test("save candidate: role selection, revision payload, saved state and projected band", async () => {
    const saves = [];
    const lists = [];
    const candidateApi = {
      list(jobId) {
        lists.push(jobId);
        return Promise.resolve({ revision: 0, candidates: [] });
      },
      save(jobId, payload) {
        saves.push({ jobId, payload });
        return Promise.resolve({
          job_id: jobId,
          revision: 1,
          candidate: {
            candidate_id: "cand-1", view_type: "scan", frame_index: 1, role: "TS",
            display_label: "TS-01", name: "candidate",
          },
          candidates: [{
            candidate_id: "cand-1", view_type: "scan", frame_index: 1, role: "TS",
            display_label: "TS-01", name: "candidate",
          }],
        });
      },
    };
    const t = makeInstance({ candidateApi });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-save" }, revision: 1, source: "shell" });
    await flush();
    t.fetchMock.respond(0, {
      ok: true,
      json: () => Promise.resolve(energyPayload({
        job_id: "job-save",
        view_type: "scan",
        metadata: { item_id: "item-1" },
        nodes: [
          { id: "n0", frame_index: 0, energy: -1.0, status: "ok", geometry_ref: "RESULT/f0.xyz" },
          { id: "n1", frame_index: 1, energy: -2.0, status: "ok", geometry_ref: "RESULT/f1.xyz" },
          { id: "n2", frame_index: 2, energy: -3.0, status: "ok", geometry_ref: "RESULT/f2.xyz" },
        ],
      })),
    });
    await flush();
    const root = t.host.children[0];
    const chart = find(root, "acp-mod-energy-chart");
    clickFrame(chart, 1);
    await flush();
    const bar = find(root, "acp-mod-energy-candidate-bar");
    assertEq(bar.hidden, false, "selection opens the candidate bar");
    findByAttr(root, "data-energy-candidate-role", "int").click();
    const save = findByAttr(root, "data-energy-action", "save-candidate");
    assertEq(save.disabled, false);
    save.click();
    await flush();
    assertEq(saves.length, 1, "one candidate save");
    assertEq(saves[0].jobId, "job-save");
    assertEq(saves[0].payload.view_type, "scan");
    assertEq(saves[0].payload.frame_index, 1);
    assertEq(saves[0].payload.role, "INT");
    assertEq(saves[0].payload.expected_revision, 0);
    assertEq(saves[0].payload.frame_id, "n1");
    assertEq(saves[0].payload.item_id, "item-1");
    assertEq(bar.getAttribute("data-acp-candidate-state"), "saved");
    assert(/cand-1/.test(find(root, "acp-mod-energy-candidate-status").textContent));
    assertEq(save.disabled, true, "saved candidate disables duplicate save");
    assert(chart.innerHTML.indexOf("energy-candidate-band") !== -1, "saved candidate projects into the band");
    t.instance.dispose();
  });

  await test("save candidate: 409 conflict refreshes the list and surfaces a retryable error", async () => {
    let listCalls = 0;
    const candidateApi = {
      list() {
        listCalls += 1;
        return Promise.resolve({ revision: listCalls, candidates: [] });
      },
      save() {
        const error = new Error("revision conflict");
        error.status = 409;
        return Promise.reject(error);
      },
    };
    const t = makeInstance({ candidateApi });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-conflict" }, revision: 1, source: "shell" });
    await flush();
    t.fetchMock.respond(0, {
      ok: true,
      json: () => Promise.resolve(energyPayload({
        job_id: "job-conflict",
        view_type: "scan",
        nodes: [{ id: "n0", frame_index: 0, energy: -1.0, status: "ok" }],
      })),
    });
    await flush();
    const root = t.host.children[0];
    clickFrame(find(root, "acp-mod-energy-chart"), 0);
    await flush();
    findByAttr(root, "data-energy-action", "save-candidate").click();
    await flush();
    assert(listCalls >= 2, "conflict triggers a candidate list refresh");
    const bar = find(root, "acp-mod-energy-candidate-bar");
    assertEq(bar.getAttribute("data-acp-candidate-state"), "error");
    assert(/候选已更新/.test(find(root, "acp-mod-energy-candidate-status").textContent));
    assertEq(findByAttr(root, "data-energy-action", "save-candidate").disabled, false,
      "conflict leaves a retryable save control");
    t.instance.dispose();
  });

  await test("save candidate: missing candidate client is explicit and never fabricates success", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-no-client" }, revision: 1, source: "shell" });
    await flush();
    t.fetchMock.respond(0, {
      ok: true,
      json: () => Promise.resolve(energyPayload({
        job_id: "job-no-client", view_type: "scan",
        nodes: [{ id: "n0", frame_index: 0, energy: -1.0, status: "ok" }],
      })),
    });
    await flush();
    const root = t.host.children[0];
    clickFrame(find(root, "acp-mod-energy-chart"), 0);
    await flush();
    const save = findByAttr(root, "data-energy-action", "save-candidate");
    assertEq(save.disabled, true);
    assert(/候选接口未接入/.test(find(root, "acp-mod-energy-candidate-status").textContent));
    t.instance.dispose();
  });

  await test("save candidate: fixture data is visibly non-saveable", async () => {
    const t = makeInstance({ location: { search: "?view=fixture" } });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    await flush();
    const root = t.host.children[0];
    clickFrame(find(root, "acp-mod-energy-chart"), 1);
    await flush();
    const save = findByAttr(root, "data-energy-action", "save-candidate");
    assertEq(save.disabled, true);
    assert(/演示数据不可保存/.test(find(root, "acp-mod-energy-candidate-status").textContent));
    t.instance.dispose();
  });

  await test("candidate drawer: server list, detail fields and row selection", async () => {
    const candidates = [
      { candidate_id: "cand-1", view_type: "scan", frame_index: 1, role: "TS",
        display_label: "TS-01", name: "my ts", saved_at: "2026-10-31T00:00:00Z",
        structure_path: "RESULT/ts.xyz" },
      { candidate_id: "cand-2", view_type: "scan", frame_index: 2, role: "INT",
        display_label: "INT-01", name: "my int", saved_at: "2026-10-31T00:01:00Z",
        structure_path: "RESULT/int.xyz" },
    ];
    const candidateApi = {
      list: () => Promise.resolve({ revision: 2, candidates }),
      save: () => Promise.reject(new Error("not used")),
      remove: () => Promise.resolve({ revision: 3, candidates: [] }),
    };
    const t = makeInstance({ candidateApi });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-drawer" }, revision: 1, source: "shell" });
    await flush();
    t.fetchMock.respond(0, {
      ok: true,
      json: () => Promise.resolve(energyPayload({
        job_id: "job-drawer", view_type: "scan",
        nodes: [
          { id: "n0", frame_index: 0, energy: -1.0, status: "ok" },
          { id: "n1", frame_index: 1, energy: -2.0, status: "ok" },
          { id: "n2", frame_index: 2, energy: -3.0, status: "ok" },
        ],
      })),
    });
    await flush();
    const root = t.host.children[0];
    const toggle = find(root, "acp-mod-energy-candidates-toggle");
    assert(/2/.test(toggle.textContent), "candidate toggle shows the server count");
    toggle.click();
    await flush();
    const drawer = find(root, "acp-mod-energy-candidate-drawer");
    assertEq(drawer.hidden, false);
    assertEq(drawer.getAttribute("data-acp-candidate-list-state"), "ready");
    const rows = findAll(root, "acp-mod-energy-candidate-row");
    assertEq(rows.length, 2, "both server candidates listed");
    rows[0].click();
    await flush();
    const detail = find(root, "acp-mod-energy-candidate-drawer-detail");
    assertEq(detail.hidden, false, "row selection opens details");
    const detailText = textOf(detail);
    assert(/cand-1/.test(detailText));
    assert(/RESULT\/ts\.xyz/.test(detailText));
    assert(/TS-01/.test(detailText));
    assertEq(find(root, "acp-mod-energy-candidate-bar").hidden, false, "candidate row selects the frame");
    t.instance.dispose();
  });

  await test("candidate drawer: remove uses expected_revision and empties the list", async () => {
    const candidates = [{
      candidate_id: "cand-1", view_type: "scan", frame_index: 1, role: "TS",
      display_label: "TS-01", structure_path: "RESULT/ts.xyz",
    }];
    const removes = [];
    const candidateApi = {
      list: () => Promise.resolve({ revision: 2, candidates }),
      remove(jobId, candidateId, revision) {
        removes.push({ jobId, candidateId, revision });
        return Promise.resolve({ revision: 3, candidates: [] });
      },
    };
    const t = makeInstance({ candidateApi });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-remove" }, revision: 1, source: "shell" });
    await flush();
    t.fetchMock.respond(0, {
      ok: true,
      json: () => Promise.resolve(energyPayload({
        job_id: "job-remove", view_type: "scan",
        nodes: [{ id: "n0", frame_index: 0, energy: -1.0, status: "ok" },
          { id: "n1", frame_index: 1, energy: -2.0, status: "ok" }],
      })),
    });
    await flush();
    const root = t.host.children[0];
    find(root, "acp-mod-energy-candidates-toggle").click();
    await flush();
    findAll(root, "acp-mod-energy-candidate-row")[0].click();
    await flush();
    findByAttr(root, "data-energy-action", "remove-candidate").click();
    await flush();
    assertEq(removes.length, 1);
    assertEq(removes[0], { jobId: "job-remove", candidateId: "cand-1", revision: 2 });
    assertEq(find(root, "acp-mod-energy-candidate-drawer").getAttribute("data-acp-candidate-list-state"), "empty");
    assert(/暂无候选/.test(find(root, "acp-mod-energy-candidate-drawer-status").textContent));
    assertEq(findAll(root, "acp-mod-energy-candidate-row").length, 0);
    t.instance.dispose();
  });

  await test("candidate drawer: remove 409 refreshes the server list and surfaces the conflict", async () => {
    let listCalls = 0;
    const candidates = [{
      candidate_id: "cand-1", view_type: "scan", frame_index: 1, role: "TS",
      display_label: "TS-01", structure_path: "RESULT/ts.xyz",
    }];
    const candidateApi = {
      list() {
        listCalls += 1;
        return Promise.resolve({ revision: listCalls, candidates });
      },
      remove() {
        const error = new Error("revision conflict");
        error.status = 409;
        return Promise.reject(error);
      },
    };
    const t = makeInstance({ candidateApi });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-remove-conflict" }, revision: 1, source: "shell" });
    await flush();
    t.fetchMock.respond(0, {
      ok: true,
      json: () => Promise.resolve(energyPayload({
        job_id: "job-remove-conflict", view_type: "scan",
        nodes: [{ id: "n0", frame_index: 0, energy: -1.0, status: "ok" },
          { id: "n1", frame_index: 1, energy: -2.0, status: "ok" }],
      })),
    });
    await flush();
    const root = t.host.children[0];
    find(root, "acp-mod-energy-candidates-toggle").click();
    await flush();
    findAll(root, "acp-mod-energy-candidate-row")[0].click();
    await flush();
    findByAttr(root, "data-energy-action", "remove-candidate").click();
    await flush();
    assert(listCalls >= 2, "conflict refreshes the list");
    assertEq(find(root, "acp-mod-energy-candidate-drawer").getAttribute("data-acp-candidate-list-state"), "error");
    assert(/候选已更新/.test(find(root, "acp-mod-energy-candidate-drawer-status").textContent));
    assertEq(findAll(root, "acp-mod-energy-candidate-row").length, 1, "server list kept after conflict");
    t.instance.dispose();
  });

  await test("candidate drawer: rename reuses the idempotent save path", async () => {
    const candidate = {
      candidate_id: "cand-1", view_type: "scan", frame_index: 1, role: "TS",
      display_label: "TS-01", name: "my ts", structure_path: "RESULT/ts.xyz",
    };
    const saves = [];
    const candidateApi = {
      list: () => Promise.resolve({ revision: 2, candidates: [candidate] }),
      save(jobId, payload) {
        saves.push({ jobId, payload });
        return Promise.resolve({
          revision: 3,
          candidate: Object.assign({}, candidate, { name: payload.name }),
          candidates: [Object.assign({}, candidate, { name: payload.name })],
        });
      },
    };
    const t = makeInstance({ candidateApi });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-rename" }, revision: 1, source: "shell" });
    await flush();
    t.fetchMock.respond(0, {
      ok: true,
      json: () => Promise.resolve(energyPayload({
        job_id: "job-rename", view_type: "scan",
        nodes: [{ id: "n0", frame_index: 0, energy: -1.0, status: "ok" },
          { id: "n1", frame_index: 1, energy: -2.0, status: "ok" }],
      })),
    });
    await flush();
    const root = t.host.children[0];
    find(root, "acp-mod-energy-candidates-toggle").click();
    await flush();
    findAll(root, "acp-mod-energy-candidate-row")[0].click();
    await flush();
    findByAttr(root, "data-energy-candidate-name", "1").value = "renamed";
    findByAttr(root, "data-energy-action", "rename-candidate").click();
    await flush();
    assertEq(saves.length, 1);
    assertEq(saves[0].payload.name, "renamed");
    assertEq(saves[0].payload.candidate_id, undefined, "rename reuses save, not a new id");
    assert(/renamed/.test(textOf(find(root, "acp-mod-energy-candidate-drawer-detail"))));
    t.instance.dispose();
  });

  await test("candidate drawer: empty server list is an explicit empty state", async () => {
    const candidateApi = { list: () => Promise.resolve({ revision: 0, candidates: [] }) };
    const t = makeInstance({ candidateApi });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-no-candidates" }, revision: 1, source: "shell" });
    await flush();
    t.fetchMock.respond(0, {
      ok: true,
      json: () => Promise.resolve(energyPayload({
        job_id: "job-no-candidates", view_type: "scan",
        nodes: [{ id: "n0", frame_index: 0, energy: -1.0, status: "ok" }],
      })),
    });
    await flush();
    const root = t.host.children[0];
    find(root, "acp-mod-energy-candidates-toggle").click();
    await flush();
    assertEq(find(root, "acp-mod-energy-candidate-drawer").getAttribute("data-acp-candidate-list-state"), "empty");
    assert(/暂无候选/.test(find(root, "acp-mod-energy-candidate-drawer-status").textContent));
    t.instance.dispose();
  });

  await test("adapter keeps viewType / xAxisMode / defaultSeries for renderer selection", async () => {
    const { sandbox, fetchMock } = setup();
    const adapter = sandbox.ACPView.source.create({ mode: "live", baseUrl: "/api/v1", fetch: fetchMock });
    const pending = adapter.load("energy:job-irc", { family: "energy", jobId: "job-irc" });
    await flush();
    fetchMock.respond(0, {
      ok: true,
      json: () => Promise.resolve({
        job_id: "job-irc", view_type: "irc", default_series: "irc_path",
        x_axis: { label: "反应进度", unit: "", default_mode: "reaction_progress" },
        series: [{ id: "irc_path", label: "IRC", unit: "kcal/mol", values: [0, 1], x_values: [0, 1] }],
        nodes: [{ id: "n0", frame_index: 0, energy: 0 }, { id: "n1", frame_index: 1, energy: 1 }],
      }),
    });
    const view = await pending;
    assertEq(view.provenance.viewType, "irc");
    assertEq(view.provenance.xAxisMode, "reaction_progress");
    assertEq(view.provenance.defaultSeries, "irc_path");
  });

  await test("structureRef-only context still resolves the owning job's energy profile", async () => {
    const t = makeInstance({
      contextInitial: {
        revision: 1,
        structureRef: { kind: "job-file", jobId: "job-ref", path: "RESULT/opt.xyz" },
      },
    });
    await t.instance.mount(t.host, t.ctx.getSnapshot());
    await flush();
    assertEq(t.fetchMock.calls.length, 1);
    assertEq(t.fetchMock.calls[0].url, "/api/v1/jobs/job-ref/energy-graph");
    t.fetchMock.respond(0, { ok: true, json: () => Promise.resolve(energyPayload({ job_id: "job-ref" })) });
    await flush();
    assertEq(t.host.children[0].getAttribute("data-acp-module-state"), "ready");
    t.instance.dispose();
  });

  await test("resize re-renders the ready chart without dropping the state", async () => {
    const t = makeInstance({ location: { search: "?view=fixture" } });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    await flush();
    const root = t.host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    const chart = find(root, "acp-mod-energy-chart");
    const before = chart.innerHTML;
    t.instance.resize();
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    assertEq(chart.innerHTML, before, "resize keeps the same deterministic fixture SVG");
    t.instance.dispose();
  });

  await test("denied view maps to the error surface with its reason", async () => {
    const env = setup();
    const ctx = makeContext({ revision: 0 });
    const denied = env.sandbox.ACPView.model.normalize({
      availability: "denied",
      warnings: [{ code: "E_DENIED", message: "no access to this project" }],
    }, { family: "energy", source: "live" });
    const host = makeHost();
    const instance = env.entry.factory({
      context: ctx,
      commands: {},
      viewSource: { mode: "live", load: () => Promise.resolve(denied) },
    });
    await instance.mount(host, { revision: 0, source: "test" });
    ctx.emit({ taskRef: { jobId: "job-denied" }, revision: 1, source: "shell" });
    await flush();
    const root = host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "error");
    assert(/no access to this project/.test(find(root, "acp-mod-energy-detail").textContent));
    instance.dispose();
  });

  await test("missing canonical renderer degrades to a local error, never a blank panel", async () => {
    const t = makeInstance({ skipRenderer: true });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    const root = t.host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "error");
    assert(/energyChart/.test(find(root, "acp-mod-energy-detail").textContent));
    assertEq(find(root, "acp-mod-energy-retry").hidden, false);
    t.instance.dispose();
  });

  await test("dispose is idempotent and late responses never write the DOM", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-late" }, revision: 1, source: "shell" });
    await flush();
    const root = t.host.children[0];
    t.instance.dispose();
    t.instance.dispose();
    assertEq(t.host.children.length, 0, "module DOM removed");
    assertEq(t.ctx.listenerCount(), 0, "context subscription released");
    t.fetchMock.respond(0, { ok: true, json: () => Promise.resolve(energyPayload()) });
    await flush();
    assertEq(root.getAttribute("data-acp-module-state"), "loading", "late response must not settle the disposed panel");
  });
};
