/* T12 + D08: NMR per-candidate structure viewers are bounded scene instances
 * (nmrPanelState.viewers holds instances, never raw 3Dmol handles), and the
 * canvas parent keeps its canvas child across loads — loading/empty/error text
 * lives in a dedicated .nmr-viewer-status node.
 * Stack under test: framing.js + mol3d_adapter.js + scene_instance.js +
 * workbench/features/nmr.js against a recording $3Dmol stub plus a mini-DOM
 * with REAL textContent/innerHTML child-clearing semantics (setting textContent
 * detaches children exactly like a browser), so creation, the status-node
 * lifecycle, post-create failure disposal and the candidate-switch registry
 * run through the REAL instance code. Failure case: sharedLoadGeometry
 * {loaded:false} after creation must dispose + deregister BEFORE the canvas is
 * removed — the stub counters catch every leaked viewer and every registered
 * detached instance. */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;
  const fs = require("fs");
  const path = require("path");

  function makeDomNode(tagName) {
    const node = {
      __tag: String(tagName).toLowerCase(),
      __attrs: {},
      __onRemoveChild: null,
      className: "",
      style: {},
      children: [],
      parentNode: null,
      isConnected: true,
      clientWidth: 0,
      clientHeight: 0,
      _text: "",
      _rawHtml: null,
    };
    node.__clearChildren = () => {
      node.children.forEach((c) => { c.parentNode = null; });
      node.children = [];
      node._rawHtml = null;
    };
    Object.defineProperty(node, "textContent", {
      get() {
        if (node._rawHtml != null) return node._text;
        if (node.children.length === 0) return node._text;
        return node.children.map((c) => c.textContent).join("");
      },
      set(value) {
        node.__clearChildren();
        node._text = value == null ? "" : String(value);
      },
    });
    Object.defineProperty(node, "innerHTML", {
      get() {
        if (node._rawHtml != null) return node._rawHtml;
        return node.children.map((c) => c.outerHTML).join("") || node._text;
      },
      set(value) {
        node.__clearChildren();
        node._text = "";
        const html = value == null ? "" : String(value);
        node._rawHtml = html === "" ? null : html;
      },
    });
    Object.defineProperty(node, "outerHTML", {
      get() {
        return "<" + node.__tag + (node.className ? ' class="' + node.className + '"' : "") + ">";
      },
    });
    node.appendChild = (child) => {
      if (child.parentNode) child.parentNode.removeChild(child);
      child.parentNode = node;
      node.children.push(child);
      node._rawHtml = null;
      return child;
    };
    node.removeChild = (child) => {
      const idx = node.children.indexOf(child);
      if (idx < 0) throw new Error("removeChild: not a child");
      node.children.splice(idx, 1);
      child.parentNode = null;
      if (typeof node.__onRemoveChild === "function") node.__onRemoveChild(child);
      return child;
    };
    node.setAttribute = (name, value) => { node.__attrs[name] = String(value); };
    node.getAttribute = (name) => (name in node.__attrs ? node.__attrs[name] : null);
    node.addEventListener = () => {};
    const matches = (n, sel) => {
      if (sel === "canvas") return n.__tag === "canvas";
      if (sel.charAt(0) === ".") return (" " + n.className + " ").indexOf(" " + sel.slice(1) + " ") >= 0;
      return false;
    };
    const walk = (n, out) => {
      n.children.forEach((c) => {
        if (matches(c, out.sel)) out.hits.push(c);
        walk(c, out);
      });
    };
    node.querySelector = (sel) => {
      const out = { sel, hits: [] };
      walk(node, out);
      return out.hits[0] || null;
    };
    node.querySelectorAll = (sel) => {
      const out = { sel, hits: [] };
      walk(node, out);
      return out.hits;
    };
    return node;
  }

  function makeNativeViewer(calls, id) {
    const model = {
      __id: id + "/model",
      setStyle: (sel, style, add) => calls.push(["model.setStyle", sel, style, add]),
      setClickable: (sel, val, cb) => { calls.push(["model.setClickable", sel, val]); model.__click = cb; },
      selectedAtoms: (sel) => { calls.push(["model.selectedAtoms", sel]); return model.__atoms; },
    };
    const viewer = {
      __id: id,
      __clearCount: 0,
      __model: model,
      clear: () => { viewer.__clearCount += 1; calls.push(["clear", id]); },
      addModel: (data, spec) => { calls.push(["addModel", data, spec]); return model; },
      removeAllModels: () => calls.push(["removeAllModels"]),
      setStyle: (...a) => { calls.push(["setStyle", ...a]); return viewer; },
      addSphere: (spec) => { calls.push(["addSphere", spec]); return { __shape: true }; },
      removeAllShapes: () => calls.push(["removeAllShapes"]),
      selectedAtoms: (sel) => calls.push(["viewer.selectedAtoms", sel]),
      render: () => calls.push(["render"]),
      resize: () => calls.push(["resize"]),
      center: (...a) => calls.push(["center", ...a]),
      zoomTo: (...a) => calls.push(["zoomTo", ...a]),
      zoom: (...a) => calls.push(["zoom", ...a]),
    };
    return viewer;
  }

  function loadNmr(opts) {
    opts = opts || {};
    const created = [];
    const nativeCalls = [];
    const registered = {};
    const sandbox = makeSandbox({
      requestAnimationFrame: (fn) => { setImmediate(fn); return 1; },
    });
    sandbox._containers = {};
    sandbox.document = {
      getElementById: () => null,
      createElement: (tag) => makeDomNode(tag),
      querySelector: (sel) => {
        const m = /\[data-nmr-structure-viewer="(\d+)"\]/.exec(sel);
        return m ? (sandbox._containers[m[1]] || null) : null;
      },
    };
    function makeContainer(key) {
      const c = makeDomNode("div");
      c.className = "nmr-structure-viewer empty";
      c.key = key;
      c.isConnected = true;
      c.clientWidth = 300;
      c.clientHeight = 200;
      sandbox._containers[key] = c;
      return c;
    }
    makeContainer("0");
    makeContainer("1");
    sandbox.t = (k) => k;
    sandbox.escapeHtml = (s) => String(s);
    if (opts.with3Dmol !== false) {
      sandbox.$3Dmol = {
        createViewer: (container, options) => {
          const v = makeNativeViewer(nativeCalls, "v" + created.length);
          created.push({ container, options, viewer: v });
          container.appendChild(makeDomNode("canvas"));
          return v;
        },
      };
    }
    loadScript("js/visualization/framing.js", sandbox);
    loadScript("js/visualization/mol3d_adapter.js", sandbox);
    if (!opts.skipSceneModule) {
      loadScript("js/visualization/scene_instance.js", sandbox);
    }
    sandbox.scheduleViewerFraming = sandbox.ACPVisualization.framing.scheduleViewerFraming;
    sandbox.disposeViewerFraming = sandbox.ACPVisualization.framing.disposeViewerFraming;
    sandbox.ACPStructureViewer = {
      registerCanvasLoader: (id, fn) => { registered[id] = fn; },
      sharedLoadGeometry: (xyz, o) => {
        const fn = registered[o && o.canvasId];
        if (typeof fn !== "function") return null;
        return { loaded: !!fn(xyz, { sphere: { scale: 0.3 } }) };
      },
    };
    loadScript("js/workbench/features/nmr.js", sandbox);
    return { sandbox, registered, created, nativeCalls };
  }

  const canvasCount = (c) => c.querySelectorAll("canvas").length;
  const statusCount = (c) => c.querySelectorAll(".nmr-viewer-status").length;

  await test("nmr viewers are scene instances: exact creation options; registry holds the instance", () => {
    const { sandbox, created } = loadNmr({});
    assertEq(sandbox.NMR_STRUCTURE_VIEWER_LIMIT, 8, "per-candidate viewer cap unchanged");
    const container = sandbox._containers["0"];
    container.appendChild(sandbox.document.createElement("stale"));
    sandbox.nmrSetViewerStatus(container, "loading");
    const info = sandbox.nmrEnsureViewer(0, container);
    assert(info && info.instance, "registry entry holds an instance");
    assertEq(created.length, 1, "exactly one viewer creation");
    assert(created[0].container === container, "creation container");
    assertEq(created[0].options, { backgroundColor: "0x0e1419", antialias: true });
    assertEq(container.children.length, 1, "pre-create clear ran before the scene existed");
    assertEq(canvasCount(container), 1, "only the new canvas remains after the pre-create clear");
    assertEq(statusCount(container), 0, "stale status node cleared with the pre-create wipe");
    for (const m of ["dispose", "isDisposed", "selectedAtoms", "addModel", "removeAllModels",
      "addSphere", "removeAllShapes", "render", "setClickable"]) {
      assert(typeof info.instance[m] === "function", "instance." + m + " must exist for consumers");
    }
    assert(!("viewer" in info.instance), "the instance never exposes a raw viewer handle");
    assert(sandbox.nmrPanelState.viewers[0] === info, "nmrPanelState.viewers holds the entry");
  });

  await test("registry reuse: same container + canvas keeps the same instance (no re-creation)", () => {
    const { sandbox, created } = loadNmr({});
    const first = sandbox.nmrEnsureViewer(0, sandbox._containers["0"]);
    const again = sandbox.nmrEnsureViewer(0, sandbox._containers["0"]);
    assertEq(created.length, 1, "no second creation on reuse");
    assert(again === first, "same entry returned");
    assert(first.instance.isDisposed() === false, "instance stays live on reuse");
  });

  await test("failure: candidate switch disposes the OLD instance — stub counter catches leaks", () => {
    const { sandbox, created } = loadNmr({});
    const container = sandbox._containers["0"];
    const info1 = sandbox.nmrEnsureViewer(0, container);
    const inst1 = info1.instance;
    const v1 = created[0].viewer;
    // Canvas loss (e.g. a host re-render) makes the reuse check fail; the
    // stale entry must be replaced with its instance disposed.
    container.removeChild(container.querySelector("canvas"));
    const info2 = sandbox.nmrEnsureViewer(0, container);
    const inst2 = info2.instance;
    const v2 = created[1].viewer;

    assert(inst1 !== inst2, "a fresh instance replaces the old one");
    assert(inst1.isDisposed() === true, "OLD instance disposed on switch");
    assert(inst2.isDisposed() === false, "new instance live");
    assertEq(v1.__clearCount, 1, "old native viewer released exactly once (leak counter)");
    assertEq(v2.__clearCount, 0, "new native viewer untouched");
    assertEq(created.length, 2, "stub creation counter: 2 created, 1 disposed, 1 live");
    assertEq(Object.keys(sandbox.nmrPanelState.viewers).length, 1, "registry holds one entry per candidate");
    assert(sandbox.nmrPanelState.viewers[0].instance === inst2, "registry points at the live instance");
    const leak = created.reduce((n, c) => n + c.viewer.__clearCount, 0);
    assertEq(leak, created.length - 1, "every replaced instance is released (only the live one remains)");
  });

  await test("nmrPanelDispose disposes every instance exactly once and empties the registry", () => {
    const { sandbox, created } = loadNmr({});
    sandbox.nmrEnsureViewer(0, sandbox._containers["0"]);
    sandbox.nmrEnsureViewer(1, sandbox._containers["1"]);
    assertEq(created.length, 2);
    const token = sandbox.nmrPanelDispose();
    assertEq(token, sandbox.nmrPanelState.token, "dispose bumps the load token");
    assertEq(created[0].viewer.__clearCount, 1, "candidate 0 released exactly once");
    assertEq(created[1].viewer.__clearCount, 1, "candidate 1 released exactly once");
    assertEq(sandbox.nmrPanelState.viewers, {}, "registry emptied");
    sandbox.nmrPanelDispose();
    assertEq(created[0].viewer.__clearCount, 1, "idempotent re-entry: no double release");
    assertEq(created[1].viewer.__clearCount, 1, "idempotent re-entry: no double release");
  });

  await test("load path through the instance: clear/addModel/style + 3D pick on the model token", async () => {
    const { sandbox, registered, created, nativeCalls } = loadNmr({});
    const info = sandbox.nmrEnsureViewer(0, sandbox._containers["0"]);
    info.labels = ["C1", "C2", "H1"];
    sandbox.nmrPanelState.loadingCandidate = 0;
    const ok = registered["nmr-structure"]("3\nx\nC 0 0 0\nC 1 0 0\nH 0 1 0\n", { sphere: { scale: 0.3 } });
    sandbox.nmrPanelState.loadingCandidate = null;
    assertEq(ok, true);
    const kinds = nativeCalls.map((c) => c[0]);
    assertEq(kinds[0], "removeAllModels", "reuse load clears through the instance");
    assertEq(kinds[1], "addModel", "then adds the geometry model");
    assertEq(nativeCalls[1][2], "xyz", "xyz spec kept");
    assertEq(kinds[2], "model.setStyle", "style applied to the current model");
    const v = created[0].viewer;
    assert(typeof v.__model.__click === "function", "3D pick bound on the live model token");
    v.__model.__click({ index: 1, elem: "C", serial: 2, x: 1, y: 0, z: 0 });
    assertEq(sandbox.nmrPanelState.selectedAtom, "C2", "pick resolves label via the shared linkage state");
    assertEq(sandbox.nmrPanelState.selectedCandidate, 0);
  });

  await test("load flow: selectedAtoms snapshot fills labels; failure falls back to []", async () => {
    const { sandbox, created } = loadNmr({});
    const container = sandbox._containers["0"];
    sandbox.nmrFetchCandidateGeometry = async () => "3\nx\nC 0 0 0\nC 1 0 0\nH 0 1 0\n";
    created.length = 0;
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 0 }, 0);
    let info = sandbox.nmrPanelState.viewers[0];
    assert(info && info.instance, "viewer instance created through the load flow");
    created[0].viewer.__model.__atoms = [
      { serial: 1, index: 0, elem: "C", x: 0, y: 0, z: 0 },
      { serial: 2, index: 1, elem: "C", x: 1, y: 0, z: 0 },
      { serial: 3, index: 2, elem: "H", x: 0, y: 1, z: 0 },
    ];
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 0 }, 0);
    info = sandbox.nmrPanelState.viewers[0];
    assertEq(info.atoms.length, 3, "atom snapshot through instance.selectedAtoms");
    assertEq(info.labels, ["C1", "C2", "H1"], "element+count labels kept");
    // Failure contract: restricted selectedAtoms returns null -> [] fallback.
    created[0].viewer.__model.__atoms = null;
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 0 }, 0);
    info = sandbox.nmrPanelState.viewers[0];
    assertEq(info.atoms, [], "null selectedAtoms falls back to []");
    assertEq(info.labels, [], "labels derived from the [] fallback");
  });

  await test("framing goes through the instance; highlight spheres are instance shape tokens", async () => {
    const { sandbox, nativeCalls, created } = loadNmr({});
    const container = sandbox._containers["0"];
    sandbox.nmrFetchCandidateGeometry = async () => "1\nx\nC 0 0 0\n";
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 0 }, 0);
    created[0].viewer.__model.__atoms = [{ serial: 1, index: 0, elem: "C", x: 0, y: 0, z: 0 }];
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 0 }, 0);
    await new Promise((r) => setTimeout(r, 30));
    const kinds = nativeCalls.map((c) => c[0]);
    for (const k of ["resize", "center", "zoomTo", "render"]) {
      assert(kinds.includes(k), "framing pass must drive instance." + k);
    }
    nativeCalls.length = 0;
    sandbox.nmrHighlightAtoms({ C1: true }, "C1");
    assertEq(nativeCalls[0][0], "removeAllShapes", "highlights reset through the instance");
    const sphere = nativeCalls.find((c) => c[0] === "addSphere");
    assert(sphere, "highlight sphere drawn");
    assertEq(sphere[1].center, { x: 0, y: 0, z: 0 }, "sphere centered on the snapshot atom");
    assertEq(sphere[1].color, "#4ea1ff", "active atom color");
    assertEq(nativeCalls[nativeCalls.length - 1][0], "render", "scene refreshed");
    assertEq(canvasCount(container), 1, "canvas still attached on the reuse path");
  });

  await test("failure: missing 3Dmol / missing scene module / failed create degrade to null without throwing", () => {
    const noLib = loadNmr({ with3Dmol: false });
    let threw = null;
    let info = "unset";
    try { info = noLib.sandbox.nmrEnsureViewer(0, noLib.sandbox._containers["0"]); } catch (e) { threw = e; }
    assertEq(threw, null, "must not throw when 3Dmol is missing");
    assertEq(info, null, "ensure reports unavailable");
    assertEq(noLib.sandbox.nmrPanelState.viewers, {}, "no stale registry entry");

    const noScene = loadNmr({ skipSceneModule: true });
    threw = null;
    try { info = noScene.sandbox.nmrEnsureViewer(0, noScene.sandbox._containers["0"]); } catch (e) { threw = e; }
    assertEq(threw, null, "must not throw when the scene module is missing");
    assertEq(info, null);
    assertEq(noScene.sandbox.nmrPanelState.viewers, {}, "no stale registry entry");
  });

  // ── D08: canvas parent vs. status node ──────────────────────────────────

  await test("D08 success: canvas stays a child; only the status node is removed; atoms/labels populated", async () => {
    const { sandbox, created } = loadNmr({});
    const container = sandbox._containers["0"];
    sandbox.nmrFetchCandidateGeometry = async () => "3\nx\nC 0 0 0\nC 1 0 0\nH 0 1 0\n";
    created.length = 0;
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 0 }, 0);
    assertEq(canvasCount(container), 1, "canvas count stays 1 after the first success");
    assert(container.querySelector("canvas").parentNode === container, "canvas is a child of the container");
    assertEq(statusCount(container), 0, "status node removed on success");
    assertEq(container.className, "nmr-structure-viewer", "success path marks the container loaded");

    created[0].viewer.__model.__atoms = [
      { serial: 1, index: 0, elem: "C", x: 0, y: 0, z: 0 },
      { serial: 2, index: 1, elem: "C", x: 1, y: 0, z: 0 },
      { serial: 3, index: 2, elem: "H", x: 0, y: 1, z: 0 },
    ];
    const canvasBefore = container.querySelector("canvas");
    let midStatus = -1;
    let midCanvas = -1;
    const orig = sandbox.ACPStructureViewer.sharedLoadGeometry;
    sandbox.ACPStructureViewer.sharedLoadGeometry = (xyz, o) => {
      midStatus = statusCount(container);
      midCanvas = canvasCount(container);
      return orig(xyz, o);
    };
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 0 }, 0);
    assertEq(midStatus, 1, "in-flight loading feedback lives in the status node");
    assertEq(midCanvas, 1, "status node sits beside the live canvas, never replaces it");
    assertEq(statusCount(container), 0, "success removes ONLY the status node");
    assertEq(canvasCount(container), 1, "canvas count stays 1 after the reload");
    assert(container.querySelector("canvas") === canvasBefore, "the SAME canvas node survives the reload");
    assert(canvasBefore.parentNode === container, "canvas still a child after success");
    const info = sandbox.nmrPanelState.viewers[0];
    assertEq(info.atoms.length, 3, "atom snapshot populated");
    assertEq(info.labels, ["C1", "C2", "H1"], "labels populated");
    assertEq(created.length, 1, "reuse guard: no second creation on reload");
  });

  await test("D08 post-create failure: {loaded:false} disposes+deregisters BEFORE the canvas is removed", async () => {
    const { sandbox, created } = loadNmr({});
    const container = sandbox._containers["0"];
    sandbox.nmrFetchCandidateGeometry = async () => "3\nx\nC 0 0 0\nC 1 0 0\nH 0 1 0\n";
    sandbox.ACPStructureViewer.sharedLoadGeometry = () => ({ loaded: false });
    const seenAtRemoval = [];
    container.__onRemoveChild = (node) => {
      if (node.__tag !== "canvas") return;
      seenAtRemoval.push({
        registered: sandbox.nmrPanelState.viewers[0] !== undefined,
        clearCount: created[0].viewer.__clearCount,
      });
    };
    created.length = 0;
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 0 }, 0);
    assertEq(created.length, 1, "the scene was created before the failure");
    assertEq(seenAtRemoval.length, 1, "the canvas is removed exactly once");
    assertEq(seenAtRemoval[0].clearCount, 1, "instance disposed BEFORE the canvas removal");
    assertEq(seenAtRemoval[0].registered, false, "instance deregistered BEFORE the canvas removal");
    assertEq(canvasCount(container), 0, "dead canvas removed together with the disposed instance");
    assertEq(sandbox.nmrPanelState.viewers[0], undefined, "no registered detached instance survives");
    assertEq(Object.keys(sandbox.nmrPanelState.viewers).length, 0, "registry empty after the failure");
    assertEq(created[0].viewer.__clearCount, 1, "instance released exactly once");
    assertEq(statusCount(container), 1, "failure status node shown");
    assertEq(container.querySelector(".nmr-viewer-status").textContent, "nmr.structure.unavailable",
      "status carries the unavailable text");
    assertEq(container.className, "nmr-structure-viewer empty", "container marked empty");
  });

  await test("D08 missing geometry after create: instance discarded before the status, no leak", async () => {
    const { sandbox, created } = loadNmr({});
    const container = sandbox._containers["0"];
    sandbox.nmrFetchCandidateGeometry = async () => null;
    created.length = 0;
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 0 }, 0);
    assertEq(created.length, 1, "ensure still created the scene (existing call order)");
    assertEq(created[0].viewer.__clearCount, 1, "post-create failure releases the instance");
    assertEq(Object.keys(sandbox.nmrPanelState.viewers).length, 0, "registry empty — no detached entry");
    assertEq(canvasCount(container), 0, "dead canvas removed with the disposed instance");
    assertEq(container.querySelector(".nmr-viewer-status").textContent, "nmr.structure.unavailable",
      "status node carries the unavailable text");
  });

  await test("D08 switch/reopen/dispose matrix: registry never leaks an instance", async () => {
    const { sandbox, created } = loadNmr({});
    const c0 = sandbox._containers["0"];
    sandbox.nmrFetchCandidateGeometry = async () => "1\nx\nC 0 0 0\n";
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 0 }, 0);
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 1 }, 0);
    assertEq(Object.keys(sandbox.nmrPanelState.viewers).length, 2, "two candidates registered");
    assertEq(created.length, 2);

    const inst0 = sandbox.nmrPanelState.viewers[0].instance;
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 0 }, 0);
    assert(sandbox.nmrPanelState.viewers[0].instance === inst0, "reopen keeps the same instance");
    assertEq(created.length, 2, "reopen does not create a second viewer");

    const c0b = sandbox.document.createElement("div");
    c0b.className = "nmr-structure-viewer empty";
    c0b.isConnected = true;
    c0b.clientWidth = 300;
    c0b.clientHeight = 200;
    sandbox._containers["0"] = c0b;
    sandbox.nmrEnsureViewer(0, c0b);
    assert(sandbox.nmrPanelState.viewers[0].instance !== inst0, "container switch replaces the instance");
    assert(inst0.isDisposed() === true, "replaced instance disposed");
    assertEq(Object.keys(sandbox.nmrPanelState.viewers).length, 2, "registry still one entry per candidate");

    sandbox.ACPStructureViewer.sharedLoadGeometry = () => ({ loaded: false });
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 1 }, 0);
    assertEq(Object.keys(sandbox.nmrPanelState.viewers).length, 1, "failed candidate deregistered");

    sandbox.ACPStructureViewer.sharedLoadGeometry = () => ({ loaded: true });
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 1 }, 0);
    assertEq(Object.keys(sandbox.nmrPanelState.viewers).length, 2, "retry re-registers exactly one entry");

    sandbox.nmrPanelDispose();
    assertEq(sandbox.nmrPanelState.viewers, {}, "dispose empties the registry");
    for (const c of created) {
      assertEq(c.viewer.__clearCount, 1, "every created native viewer released exactly once");
    }
  });

  await test("D08 old-bug demo (monkeypatch): container.textContent wipe detaches the canvas", async () => {
    const { sandbox } = loadNmr({});
    const container = sandbox._containers["0"];
    sandbox.nmrFetchCandidateGeometry = async () => "1\nx\nC 0 0 0\n";
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 0 }, 0);
    assertEq(canvasCount(container), 1, "fixed success path keeps the canvas");
    assert(container.querySelector("canvas").parentNode === container, "canvas connected after success");
    assertEq(container.textContent, "", "text is already empty WITHOUT wiping children");

    // The pre-fix success line, replayed as a monkeypatch (no production edit):
    // real-DOM textContent='' removes every child — the canvas included.
    container.textContent = "";
    assertEq(canvasCount(container), 0, "the old line would detach the canvas (bug reproduced)");
    assertEq(container.querySelector("canvas"), null, "old bug: no canvas child remains");
    assert(sandbox.nmrPanelState.viewers[0] !== undefined,
      "old bug: the registry entry would survive as a registered detached instance");
  });

  await test("D08 refreshNmrReport: dispose runs before the body rewrite (failed re-fetch leaks nothing)", async () => {
    const { sandbox, created } = loadNmr({});
    const panel = sandbox.document.createElement("div");
    const body = sandbox.document.createElement("div");
    sandbox.document.getElementById = (id) => (id === "nmr-report-panel" ? panel : id === "nmr-report-body" ? body : null);
    const container = sandbox._containers["0"];
    body.appendChild(container);
    sandbox.nmrEnsureViewer(0, container);
    assertEq(created.length, 1);
    sandbox.fetchNmrReportJson = async () => null;
    await sandbox.refreshNmrReport({ spec: { workflow: "nmr" } });
    assertEq(created[0].viewer.__clearCount, 1, "old instance released on re-render");
    assertEq(sandbox.nmrPanelState.viewers, {}, "registry empty even when the re-fetch finds no report");
  });

  await test("source pin: nmr.js holds no raw viewer handles or second adapter", () => {
    const src = fs.readFileSync(path.join(__dirname, "..", "workbench", "features", "nmr.js"), "utf8");
    for (const banned of ["ACPVisualization.mol3d.create(", "ACPVisualization.mol3d.destroy(", "$3Dmol", "getModel(", "info.viewer"]) {
      assert(src.indexOf(banned) < 0, "nmr.js must not reference " + banned);
    }
    assert(src.indexOf("ACPVisualization.scene") >= 0, "creation goes through the scene namespace");
    assert(src.indexOf(".dispose()") >= 0, "per-candidate cleanup goes through dispose()");
    assert(src.indexOf("selectedAtoms({})") >= 0, "atom reads use the restricted selectedAtoms call");

    // D08 pins: the canvas parent is never wiped after scene creation — the
    // single container.innerHTML write is the pre-create clear in
    // nmrEnsureViewer, and the load path uses the status-node helper.
    assert(src.indexOf('nmrSetViewerStatus(viewer, t("nmr.structure.loading"))') >= 0,
      "renderNmrStructure seeds loading text via the status node");
    assert(src.indexOf(".nmr-viewer-status") >= 0, "status node helper present");
    const slice = src.slice(src.indexOf("function nmrEnsureViewer("));
    assert(slice.indexOf("container.textContent") < 0, "viewer load path never writes container.textContent");
    assertEq((slice.match(/container\.innerHTML/g) || []).length, 1,
      "exactly one container.innerHTML write — the pre-create clear");
    assert(slice.indexOf("nmrRemoveViewerStatus(container)") >= 0, "success removes only the status node");
    assert(slice.indexOf("nmrDiscardViewer(cand.index, info)") >= 0, "failure disposes+deregisters first");
  });
};
