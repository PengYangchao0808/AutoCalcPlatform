/* ACP 3.0 module test — s2-scan (Track A / A-48 · P16 S2 bond-length scan).
 *
 * Loads the module as the classic script the Shell loader consumes
 * (window.ACPNextModules["s2-scan"] = { descriptor, factory, _internals })
 * inside the zero-dependency node vm harness with a fake DOM. Every request
 * goes through an injected request seam, so no test can reach the network
 * (the sandbox has no fetch/XMLHttpRequest at all).
 *
 * Pinned contracts:
 *  1. exactly one ACPNextModules key, Shell-valid descriptor, no extra global;
 *  2. four states (empty/loading/ready/error) + a separate busy attribute,
 *     ScanTS unchecked/off by default;
 *  3. validation matrix: start > 0, end > start, points >= 2 and bounded,
 *     atom indices 1-based/distinct/in range, charge/multiplicity parity;
 *  4. preview is the existing zero-side-effect `POST /preflight`; submit is the
 *     existing `POST /jobs` with preflight_fingerprint + Idempotency-Key;
 *     cannot submit while invalid, before preflight, or twice for one plan;
 *  5. busy -> receipt -> error surfaces (403 / 422 / preflight_stale);
 *  6. source structure: inline XYZ, task structure (injected loader AND the
 *     canonical ACPView.source URL), uploaded text read locally;
 *  7. generation/dispose guards: late structure/preflight results never paint;
 *  8. no second viewer, no direct network primitive, no task mutation.
 */
"use strict";

const fs = require("fs");
const path = require("path");

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const MODULE_PATH = "js/modules/s2-scan/module.js";
  const API = "/api/v1";
  const WATER = "3\nwater\nO 0.000 0.000 0.000\nH 0.758 0.586 0.000\nH -0.758 0.586 0.000\n";
  const DIGEST = "sha256:" + "a".repeat(64);
  const DIGEST_B = "sha256:" + "b".repeat(64);

  function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  const tick = () => new Promise((r) => setTimeout(r, 0));
  async function flush(n = 10) {
    for (let i = 0; i < n; i++) await Promise.resolve();
    await tick();
    for (let i = 0; i < 6; i++) await Promise.resolve();
  }

  // ------------------------------------------------------------ fake DOM --

  function makeDom() {
    const docListeners = new Map();

    function makeEl(tag) {
      const listeners = new Map();
      const node = {
        tagName: String(tag).toUpperCase(),
        children: [],
        parentNode: null,
        className: "",
        textContent: "",
        hidden: false,
        disabled: false,
        value: "",
        checked: false,
        type: "",
        name: "",
        id: "",
        files: null,
        attrs: {},
        listeners,
        setAttribute(key, value) { this.attrs[key] = String(value); },
        getAttribute(key) { return key in this.attrs ? this.attrs[key] : null; },
        removeAttribute(key) { delete this.attrs[key]; },
        appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
        removeChild(child) {
          const index = this.children.indexOf(child);
          if (index >= 0) this.children.splice(index, 1);
          child.parentNode = null;
          return child;
        },
        replaceChildren(...nodes) {
          this.children.forEach((child) => { child.parentNode = null; });
          this.children = nodes.slice();
          nodes.forEach((child) => { child.parentNode = this; });
        },
        addEventListener(type, fn) {
          if (!listeners.has(type)) listeners.set(type, new Set());
          listeners.get(type).add(fn);
        },
        removeEventListener(type, fn) {
          if (listeners.has(type)) listeners.get(type).delete(fn);
        },
        fire(type, event) {
          const set = listeners.get(type);
          if (set) [...set].forEach((fn) => fn(Object.assign({ type, target: node }, event || {})));
        }
      };
      return node;
    }

    const document = {
      createElement: makeEl,
      addEventListener(type, fn) {
        if (!docListeners.has(type)) docListeners.set(type, new Set());
        docListeners.get(type).add(fn);
      },
      removeEventListener(type, fn) {
        if (docListeners.has(type)) docListeners.get(type).delete(fn);
      },
      dispatchEvent(event) {
        const set = docListeners.get(event && event.type);
        if (set) [...set].forEach((fn) => fn(event));
        return true;
      },
      documentElement: makeEl("html"),
      activeDocListeners() {
        let count = 0;
        docListeners.forEach((set) => { count += set.size; });
        return count;
      }
    };
    return { document };
  }

  function walk(node, out = []) {
    (node.children || []).forEach((child) => { out.push(child); walk(child, out); });
    return out;
  }

  function find(root, attr, value) {
    return walk(root).find((node) => node.getAttribute && node.getAttribute(attr) === value) || null;
  }

  function findAll(root, attr, value) {
    return walk(root).filter((node) => node.getAttribute && node.getAttribute(attr) === value);
  }

  function subtreeText(node) {
    let text = node.textContent || "";
    (node.children || []).forEach((child) => { text += " " + subtreeText(child); });
    return text;
  }

  function collectAttr(node, attr) {
    const out = [];
    walk(node).forEach((child) => {
      const value = child.getAttribute && child.getAttribute(attr);
      if (value != null) out.push(value);
    });
    return out;
  }

  // ------------------------------------------------------------- harness --

  function loadModule(sandbox) {
    loadScript(MODULE_PATH, sandbox);
    return sandbox.ACPNextModules["s2-scan"];
  }

  function defaultResponse(pathname) {
    if (pathname === API + "/preflight") {
      return {
        schema_version: 1,
        feasible: true,
        reasons: [],
        advisories: [],
        plan_digest: DIGEST,
        would_create_task: false,
        would_reserve: false
      };
    }
    if (pathname === API + "/jobs") {
      return {
        job_id: "20261031_001_scan_demo",
        status: "queued",
        workflow: "scan",
        attempt: 1,
        plan_digest: DIGEST,
        result: "accepted"
      };
    }
    return WATER;
  }

  function makeClient(responder) {
    const calls = [];
    function request(pathname, opts) {
      calls.push({ path: pathname, opts: opts || {} });
      try {
        return Promise.resolve((responder || defaultResponse)(pathname, opts, calls.length));
      } catch (error) {
        return Promise.reject(error);
      }
    }
    return { calls, request };
  }

  function makeContext(options) {
    options = options || {};
    const listeners = new Set();
    let snapshot = {
      projectId: options.projectId || null,
      taskRef: options.taskRef || (options.structureRef ? { jobId: options.structureRef.jobId } : null),
      structureRef: options.structureRef || null,
      revision: 1
    };
    return {
      getSnapshot() { return snapshot; },
      subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
      setFocus(patch) {
        snapshot = Object.assign({}, snapshot, patch, { revision: snapshot.revision + 1 });
        listeners.forEach((fn) => fn(snapshot));
      },
      listenerCount() { return listeners.size; }
    };
  }

  function makeCommands() {
    const commands = {
      opened: [],
      openTask(payload) { commands.opened.push(payload); }
    };
    return commands;
  }

  function setup(options) {
    options = options || {};
    const dom = makeDom();
    const sandbox = makeSandbox({});
    const mod = loadModule(sandbox);
    const client = options.client || makeClient(options.responder);
    const hasContext = options.context !== null;
    const context = hasContext
      ? (options.context || makeContext({
        structureRef: options.structureRef,
        projectId: options.projectId
      }))
      : null;
    const commands = options.commands || makeCommands();
    const host = dom.document.createElement("div");
    const deps = {
      doc: dom.document,
      request: client.request,
      context,
      commands,
      parseDebounceMs: 0
    };
    if (options.loadStructure) deps.loadStructure = options.loadStructure;
    if (options.readFile) deps.readFile = options.readFile;
    const instance = mod.factory(deps);
    return { dom, sandbox, mod, client, context, commands, host, deps, instance };
  }

  async function mount(ctx) {
    await ctx.instance.mount(ctx.host, { context: ctx.context, commands: ctx.commands });
    await flush();
  }

  function root(ctx) {
    return ctx.host.children[0] || null;
  }

  function stateOf(ctx) {
    return root(ctx).getAttribute("data-acp-module-state");
  }

  function attr(ctx, name) {
    return root(ctx).getAttribute(name);
  }

  function field(ctx, role) {
    return find(ctx.host, "data-role", role);
  }

  function action(ctx, name) {
    return find(ctx.host, "data-action", name);
  }

  async function typeXyz(ctx, text) {
    const area = field(ctx, "xyz-text");
    area.value = text;
    area.fire("input");
    await flush();
  }

  async function fill(ctx, values) {
    Object.keys(values).forEach((role) => {
      const input = field(ctx, role);
      input.value = values[role];
      input.fire("input");
    });
    await flush();
  }

  function setSource(ctx, kind) {
    const radios = findAll(ctx.host, "data-role", "source");
    radios.forEach((radio) => { radio.checked = radio.value === kind; });
    const target = radios.find((radio) => radio.value === kind);
    target.fire("change");
  }

  function preflightBody(ctx, index) {
    const calls = ctx.client.calls.filter((call) => call.path === API + "/preflight");
    const call = calls[index == null ? calls.length - 1 : index];
    assert(!!call, "no preflight request was issued");
    return JSON.parse(call.opts.body);
  }

  function submitBodies(ctx) {
    return ctx.client.calls
      .filter((call) => call.path === API + "/jobs")
      .map((call) => ({ body: JSON.parse(call.opts.body), opts: call.opts }));
  }

  // ------------------------------------------------------------- tests --

  await test("module: one ACPNextModules key, Shell-valid descriptor, no legacy globals", async () => {
    const sandbox = makeSandbox({});
    loadScript("js/shell/module-registry.js", sandbox);
    const mod = loadModule(sandbox);
    assertEq(Object.keys(sandbox.ACPNextModules), ["s2-scan"], "exactly one module key");
    assertEq(typeof mod.factory, "function", "factory present");
    assertEq(sandbox.ACPShell.registry.validateDescriptor(mod.descriptor), null,
      "descriptor must pass the Shell registry validator");
    assertEq(mod.descriptor.id, "s2-scan");
    assertEq(mod.descriptor.interfaceVersion, 1);
    assertEq(mod.descriptor.hosts, ["acp-web"]);
    assertEq(mod.descriptor.error.titleKey, "next.s2scan.errorTitle");
    assert(!("s2scanState" in sandbox), "legacy page global s2scanState must not exist");
    assert(!("s2scanResetState" in sandbox), "legacy helper must not be redefined");
    assertEq(typeof mod._internals.validateConfig, "function", "internals exposed for pure tests");
  });

  await test("mount: empty state with the form rendered, ScanTS off, submit disabled, no request", async () => {
    const ctx = setup();
    await mount(ctx);
    assertEq(root(ctx).className, "acp-s2scan");
    assertEq(stateOf(ctx), "empty", "initial state is empty");
    assertEq(attr(ctx, "data-acp-scants"), "off", "ScanTS off");
    assertEq(field(ctx, "scants").checked, false, "ScanTS checkbox unchecked");
    assertEq(subtreeText(field(ctx, "scants-state")).indexOf("关闭") !== -1, true, "off badge text");
    assertEq(action(ctx, "submit").disabled, true, "submit disabled on an empty dialog");
    assert(!!field(ctx, "atom-a"), "atom A input");
    assert(!!field(ctx, "start"), "start input");
    assert(!!field(ctx, "points"), "points input");
    assertEq(ctx.client.calls.length, 0, "mount performs no network call");
    assertEq(subtreeText(field(ctx, "source-status")).indexOf("尚未载入结构") !== -1, true, "empty hint");
    assertEq(subtreeText(root(ctx)).indexOf("S2 键长扫描") !== -1, true, "zh title fallback");
  });

  await test("inline XYZ: ready state, atom count, bond length and the frozen request envelope", async () => {
    const ctx = setup();
    await mount(ctx);
    await typeXyz(ctx, WATER);

    assertEq(stateOf(ctx), "ready", "ready after a valid structure");
    assertEq(subtreeText(field(ctx, "source-status")).indexOf("3 个原子") !== -1, true, "atom count");
    const bondText = subtreeText(field(ctx, "bond-length"));
    assert(bondText.indexOf("0.958") !== -1, "water O-H bond length rendered: " + bondText);

    const internals = ctx.mod._internals;
    const parsed = internals.parseXyz(WATER);
    assertEq(parsed.ok, true);
    assertEq(parsed.count, 3);
    assertEq(parsed.atoms[0].symbol, "O");

    const request = internals.buildRequest({
      xyz: WATER,
      atomA: "1",
      atomB: "2",
      start: "1.0",
      end: "3.0",
      points: "21",
      charge: "0",
      multiplicity: "1",
      scants: false
    });
    assertEq(request.workflow, "scan");
    assertEq(request.method.schema_id, "dft_scan");
    assertEq(request.method.scan_points, 21);
    assertEq(request.method.scan_use_scants, false, "ScanTS defaults to false in the wire body");
    assertEq(Object.keys(request.method).sort(), ["scan_points", "scan_use_scants", "schema_id"],
      "no invented method fields");
    assertEq(request.input.source_type, "xyz_text");
    assertEq(request.input.source, WATER, "submitted geometry is forwarded verbatim");
    assertEq(request.input.xyz_text, WATER);
    assertEq(request.input.scan_coordinates, ["0,1,1.0,3.0"], "1-based UI -> 0-based wire");
    assertEq("project_id" in request, false, "no project id without a context project");
    assertEq("resources" in request, false, "this dialog owns no resources block");
    assertEq("idempotency_key" in request, false, "idempotency only travels in the header");
  });

  await test("validation matrix: start>0, end>start, points bounded, atoms, parity", async () => {
    const internals = setup().mod._internals;
    const atoms = internals.parseXyz(WATER).atoms;
    function check(overrides) {
      return internals.validateConfig(Object.assign({
        atoms,
        atomA: "1",
        atomB: "2",
        start: "1.0",
        end: "3.0",
        points: "21",
        charge: "0",
        multiplicity: "3"
      }, overrides || {}));
    }
    function codes(result) {
      return result.errors.map((error) => error.code);
    }

    assertEq(check().ok, true, "baseline is valid (triplet water passes the parity rule)");
    assertEq(check().parityChecked, true);
    assertEq(check().electrons, 10, "water: 8 + 1 + 1 electrons");
    assertEq(check().bondLength.toFixed(3), "0.958");

    assertEq(codes(check({ start: "0" })), ["start_not_positive"]);
    assertEq(codes(check({ start: "-1.5" })), ["start_not_positive"]);
    assertEq(codes(check({ start: "abc" })), ["start_invalid"]);
    assertEq(codes(check({ start: "" })), ["start_invalid"]);
    assertEq(codes(check({ end: "1.0" })), ["end_not_greater"], "end must be greater than start");
    assertEq(codes(check({ end: "0.5" })), ["end_not_greater"]);
    assertEq(codes(check({ end: "" })), ["end_invalid"]);

    assertEq(codes(check({ points: "1" })), ["points_min"]);
    assertEq(codes(check({ points: "2" })), [], "2 points is the submission-boundary floor");
    assertEq(codes(check({ points: "100" })), [], "100 points is the catalog ceiling");
    assertEq(codes(check({ points: "101" })), ["points_max"]);
    assertEq(codes(check({ points: "2.5" })), ["points_invalid"]);
    assertEq(codes(check({ points: "" })), ["points_invalid"]);
    assertEq(internals.MIN_POINTS, 2);
    assertEq(internals.MAX_POINTS, 100);

    assertEq(codes(check({ atomA: "0" })), ["atom_out_of_range"]);
    assertEq(codes(check({ atomA: "4" })), ["atom_out_of_range"]);
    assertEq(codes(check({ atomB: "9" })), ["atom_out_of_range"]);
    assertEq(codes(check({ atomA: "x" })), ["atom_invalid"]);
    assertEq(codes(check({ atomA: "2", atomB: "2" })), ["atoms_equal"]);

    assertEq(codes(check({ charge: "1.5" })), ["charge_invalid"]);
    assertEq(codes(check({ multiplicity: "0" })), ["multiplicity_invalid"]);
    assertEq(codes(check({ multiplicity: "2" })), ["multiplicity_parity"],
      "even electron count requires an odd multiplicity");
    assertEq(codes(check({ charge: "-1", multiplicity: "2" })), [],
      "anion: 11 electrons -> even multiplicity is legal");
    assertEq(codes(check({ multiplicity: "4" })), ["multiplicity_parity"]);

    const unknown = internals.parseXyz("Xx 0.0 0.0 0.0\nH 1.0 0.0 0.0\n");
    assertEq(unknown.ok, true, "unknown symbol still parses as a structure");
    const unverifiable = internals.validateConfig({
      atoms: unknown.atoms, atomA: "1", atomB: "2", start: "1.0", end: "2.0",
      points: "5", charge: "0", multiplicity: "2"
    });
    assertEq(unverifiable.ok, true, "unknown element must not fake a parity error");
    assertEq(unverifiable.parityChecked, false);
    assertEq(unverifiable.electrons, null);
  });

  await test("UI validation: invalid fields block preflight and submit without any request", async () => {
    const ctx = setup();
    await mount(ctx);
    await typeXyz(ctx, WATER);
    await fill(ctx, { start: "0" });

    assertEq(attr(ctx, "data-acp-valid"), "false");
    assertEq(action(ctx, "submit").disabled, true);
    const errorItems = findAll(ctx.host, "data-error", "start_not_positive");
    assertEq(errorItems.length, 1, "start_not_positive is rendered");
    assert(errorItems[0].textContent.indexOf("大于 0") !== -1, "readable zh error text");

    action(ctx, "preflight").fire("click");
    await flush();
    assertEq(ctx.client.calls.filter((call) => call.path === API + "/preflight").length, 0,
      "invalid config must not reach /preflight");
    action(ctx, "submit").fire("click");
    await flush();
    assertEq(submitBodies(ctx).length, 0, "invalid config must never submit");
    assertEq(subtreeText(field(ctx, "preflight-status")).indexOf("未发送预检") !== -1, true);
  });

  await test("ScanTS: default off, explicit opt-in only, never enabled by the module", async () => {
    const ctx = setup();
    await mount(ctx);
    await typeXyz(ctx, WATER);

    action(ctx, "preflight").fire("click");
    await flush();
    assertEq(preflightBody(ctx).method.scan_use_scants, false, "default request disables ScanTS");

    const scants = field(ctx, "scants");
    scants.checked = true;
    scants.fire("change");
    await flush();
    assertEq(attr(ctx, "data-acp-scants"), "on", "explicit opt-in is visible");
    assertEq(subtreeText(field(ctx, "scants-state")).indexOf("已启用") !== -1, true);

    action(ctx, "preflight").fire("click");
    await flush();
    assertEq(preflightBody(ctx).method.scan_use_scants, true, "opt-in reaches the wire body");

    const source = fs.readFileSync(path.resolve(__dirname, "..", "modules", "s2-scan", "module.js"), "utf8");
    assert(!/scants\.checked\s*=\s*true/.test(source), "the module must never turn ScanTS on by itself");
    assert(source.indexOf('scants: false') !== -1, "the default form value is false");
  });

  await test("preflight: exact body, busy state, digest + advisories rendered, submit enabled", async () => {
    const client = makeClient((pathname) => {
      if (pathname === API + "/preflight") {
        return {
          schema_version: 1,
          feasible: true,
          reasons: [],
          advisories: [{
            code: "scan_single_coordinate_geometry",
            message: "单坐标扫描只覆盖该键长的变化"
          }],
          plan_digest: DIGEST,
          would_create_task: false,
          would_reserve: false
        };
      }
      return defaultResponse(pathname);
    });
    const ctx = setup({ client });
    await mount(ctx);
    await typeXyz(ctx, WATER);

    action(ctx, "preflight").fire("click");
    await flush();

    const calls = ctx.client.calls.filter((call) => call.path === API + "/preflight");
    assertEq(calls.length, 1, "one preflight request");
    assertEq(calls[0].opts.method, "POST");
    assertEq(calls[0].opts.headers["Content-Type"], "application/json");
    const body = JSON.parse(calls[0].opts.body);
    assertEq(body.workflow, "scan");
    assertEq(body.input.source_type, "xyz_text");
    assertEq(body.input.scan_coordinates, ["0,1,1.0,3.0"]);
    assertEq(body.method.scan_use_scants, false);
    assertEq("preflight_fingerprint" in body, false, "preflight carries no fingerprint");

    assertEq(attr(ctx, "data-acp-busy"), "none", "busy cleared after the response");
    assertEq(subtreeText(field(ctx, "preflight-status")).indexOf("预检通过") !== -1, true);
    assertEq(subtreeText(field(ctx, "preflight-status")).indexOf(DIGEST.slice(0, 23)) !== -1, true,
      "digest preview rendered");
    const advisories = field(ctx, "advisories");
    assertEq(advisories.hidden, false, "advisories are visible");
    assertEq(subtreeText(advisories).indexOf("单坐标扫描") !== -1, true);
    assertEq(action(ctx, "submit").disabled, false, "submit enabled after a feasible preflight");
    assertEq(ctx.client.calls.filter((call) => call.path === API + "/jobs").length, 0,
      "preflight alone never submits");
  });

  await test("preflight: infeasible reasons are listed, submit stays disabled, no /jobs", async () => {
    const client = makeClient((pathname) => {
      if (pathname === API + "/preflight") {
        return {
          schema_version: 1,
          feasible: false,
          reasons: [
            { code: "input_invalid", message: "scan coordinate atoms are out of range", field: "input.scan_coordinates" },
            { code: "input_invalid", message: "second reason" }
          ],
          advisories: [],
          plan_digest: null,
          would_create_task: false,
          would_reserve: false
        };
      }
      return defaultResponse(pathname);
    });
    const ctx = setup({ client });
    await mount(ctx);
    await typeXyz(ctx, WATER);
    action(ctx, "preflight").fire("click");
    await flush();

    assertEq(subtreeText(field(ctx, "preflight-status")).indexOf("预检未通过") !== -1, true);
    const reasons = findAll(ctx.host, "data-preflight-reason", "1");
    assertEq(reasons.length, 2, "both server reasons are rendered");
    assertEq(reasons[0].textContent.indexOf("out of range") !== -1, true);
    assertEq(action(ctx, "submit").disabled, true);
    action(ctx, "submit").fire("click");
    await flush();
    assertEq(submitBodies(ctx).length, 0);
  });

  await test("submit: fingerprint + Idempotency-Key, busy state, receipt and openTask", async () => {
    const ctx = setup();
    await mount(ctx);
    await typeXyz(ctx, WATER);
    action(ctx, "preflight").fire("click");
    await flush();
    const preflightPayload = preflightBody(ctx);

    action(ctx, "submit").fire("click");
    await flush();

    const submits = submitBodies(ctx);
    assertEq(submits.length, 1, "one submit request");
    const call = ctx.client.calls.filter((item) => item.path === API + "/jobs")[0];
    assertEq(call.opts.method, "POST");
    assertEq(call.opts.headers["Content-Type"], "application/json");
    assert(call.opts.headers["Idempotency-Key"].indexOf("next-s2-") === 0, "idempotency key sent");
    assertEq(submits[0].body.preflight_fingerprint, DIGEST, "preflight fingerprint carried");

    const withoutFingerprint = Object.assign({}, submits[0].body);
    delete withoutFingerprint.preflight_fingerprint;
    assertEq(withoutFingerprint, preflightPayload, "submit body is exactly the preflighted plan");

    assertEq(attr(ctx, "data-acp-busy"), "none");
    const receipt = field(ctx, "receipt");
    assertEq(receipt.hidden, false, "receipt visible");
    assertEq(receipt.getAttribute("data-acp-receipt-state"), "submitted");
    const receiptText = subtreeText(receipt);
    assert(receiptText.indexOf("20261031_001_scan_demo") !== -1, "job id in the receipt");
    assert(receiptText.indexOf("queued") !== -1, "status in the receipt");
    assertEq(ctx.commands.opened.length, 1, "openTask dispatched once");
    assertEq(ctx.commands.opened[0].jobId, "20261031_001_scan_demo");
    assertEq(ctx.commands.opened[0].source, "s2-scan");

    // Same plan again: the receipt blocks an accidental duplicate submit.
    action(ctx, "submit").fire("click");
    await flush();
    assertEq(submitBodies(ctx).length, 1, "no duplicate submit for an already-submitted plan");
  });

  await test("submit: blocked before preflight, and a config edit invalidates the preflight", async () => {
    const ctx = setup();
    await mount(ctx);
    await typeXyz(ctx, WATER);

    action(ctx, "submit").fire("click");
    await flush();
    assertEq(submitBodies(ctx).length, 0, "no submit without a preflight");

    action(ctx, "preflight").fire("click");
    await flush();
    assertEq(action(ctx, "submit").disabled, false);

    await fill(ctx, { points: "31" });
    assertEq(action(ctx, "submit").disabled, true, "edit after preflight disables submit");
    assertEq(subtreeText(field(ctx, "preflight-status")).indexOf("尚未预检") !== -1, true);
    action(ctx, "submit").fire("click");
    await flush();
    assertEq(submitBodies(ctx).length, 0, "stale preflight never submits");

    action(ctx, "preflight").fire("click");
    await flush();
    assertEq(preflightBody(ctx).method.scan_points, 31, "re-preflight uses the edited plan");
    assertEq(action(ctx, "submit").disabled, false);

    // Serializing the same edited plan reuses a NEW idempotency key only after
    // the first key was consumed; here the first submit must use the new plan.
    action(ctx, "submit").fire("click");
    await flush();
    assertEq(submitBodies(ctx).length, 1);
    assertEq(submitBodies(ctx)[0].body.method.scan_points, 31);
    assertEq(submitBodies(ctx)[0].body.preflight_fingerprint, DIGEST);
  });

  await test("busy: double clicks never duplicate requests and both buttons disable", async () => {
    const gate = deferred();
    let preflightCalls = 0;
    const client = makeClient((pathname) => {
      if (pathname === API + "/preflight") {
        preflightCalls += 1;
        return gate.promise;
      }
      return defaultResponse(pathname);
    });
    const ctx = setup({ client });
    await mount(ctx);
    await typeXyz(ctx, WATER);

    action(ctx, "preflight").fire("click");
    action(ctx, "preflight").fire("click");
    await flush();
    assertEq(preflightCalls, 1, "second click while busy is ignored");
    assertEq(attr(ctx, "data-acp-busy"), "preflight");
    assertEq(action(ctx, "preflight").disabled, true);
    assertEq(action(ctx, "submit").disabled, true);
    assertEq(subtreeText(field(ctx, "preflight-status")).indexOf("预检中") !== -1, true);

    gate.resolve({ schema_version: 1, feasible: true, reasons: [], advisories: [], plan_digest: DIGEST });
    await flush();
    assertEq(attr(ctx, "data-acp-busy"), "none");
    assertEq(action(ctx, "submit").disabled, false);

    const submitGate = deferred();
    let submitCalls = 0;
    const ctx2 = setup({
      client: makeClient((pathname) => {
        if (pathname === API + "/preflight") return defaultResponse(pathname);
        if (pathname === API + "/jobs") {
          submitCalls += 1;
          return submitGate.promise;
        }
        return WATER;
      })
    });
    await mount(ctx2);
    await typeXyz(ctx2, WATER);
    action(ctx2, "preflight").fire("click");
    await flush();
    action(ctx2, "submit").fire("click");
    action(ctx2, "submit").fire("click");
    await flush();
    assertEq(submitCalls, 1, "second submit click while submitting is ignored");
    assertEq(attr(ctx2, "data-acp-busy"), "submit");
    assertEq(subtreeText(action(ctx2, "submit")).indexOf("提交中") !== -1, true);
    submitGate.resolve({ job_id: "J2", status: "queued", attempt: 1, plan_digest: DIGEST });
    await flush();
    assertEq(attr(ctx2, "data-acp-busy"), "none");
  });

  await test("errors: preflight failure, forbidden submit, and preflight_stale recovery", async () => {
    let mode = "ok";
    const ctx = setup({
      client: makeClient((pathname, opts) => {
        if (pathname === API + "/preflight") {
          if (mode === "preflight-500") {
            const error = new Error("boom");
            error.status = 500;
            error.body = { detail: "boom" };
            throw error;
          }
          return defaultResponse(pathname);
        }
        if (pathname === API + "/jobs") {
          if (mode === "forbidden") {
            const error = new Error("forbidden");
            error.status = 403;
            error.body = { detail: "当前身份无权提交任务" };
            throw error;
          }
          if (mode === "stale") {
            const error = new Error("preflight stale");
            error.status = 400;
            error.body = { detail: { code: "preflight_stale", message: "fingerprint mismatch" } };
            throw error;
          }
          return defaultResponse(pathname);
        }
        return WATER;
      })
    });
    await mount(ctx);
    await typeXyz(ctx, WATER);

    mode = "preflight-500";
    action(ctx, "preflight").fire("click");
    await flush();
    assertEq(subtreeText(field(ctx, "preflight-status")).indexOf("预检请求失败") !== -1, true);
    assertEq(subtreeText(field(ctx, "preflight-status")).indexOf("boom") !== -1, true);
    assertEq(action(ctx, "submit").disabled, true, "a failed preflight leaves submit disabled");
    assertEq(attr(ctx, "data-acp-busy"), "none");

    mode = "forbidden";
    action(ctx, "preflight").fire("click");
    await flush();
    action(ctx, "submit").fire("click");
    await flush();
    assertEq(field(ctx, "receipt").hidden, false, "submit error surface is visible");
    assertEq(field(ctx, "receipt").getAttribute("data-acp-receipt-state"), "error");
    assertEq(subtreeText(field(ctx, "receipt")).indexOf("无权提交") !== -1, true);
    assertEq(ctx.commands.opened.length, 0, "no navigation on a failed submit");
    assertEq(submitBodies(ctx)[0].body.preflight_fingerprint, DIGEST);

    mode = "stale";
    action(ctx, "submit").fire("click");
    await flush();
    assertEq(subtreeText(field(ctx, "receipt")).indexOf("重新预检") !== -1, true);
    assertEq(action(ctx, "submit").disabled, true, "stale fingerprint clears the preflight");
    assertEq(attr(ctx, "data-acp-busy"), "none");

    mode = "ok";
    action(ctx, "preflight").fire("click");
    await flush();
    action(ctx, "submit").fire("click");
    await flush();
    assertEq(field(ctx, "receipt").getAttribute("data-acp-receipt-state"), "submitted");
  });

  await test("task structure: deferred loader drives loading -> ready and error -> retry", async () => {
    const first = deferred();
    let calls = 0;
    const ctx = setup({
      structureRef: { kind: "structure-entry", jobId: "J1", entryId: "e1" },
      loadStructure: () => {
        calls += 1;
        if (calls === 1) return first.promise;
        if (calls === 2) return Promise.reject(new Error("geometry missing"));
        return Promise.resolve(WATER);
      }
    });
    await mount(ctx);
    setSource(ctx, "task");
    await flush();
    assertEq(stateOf(ctx), "empty");
    assertEq(attr(ctx, "data-acp-source"), "task");
    const taskRefText = subtreeText(field(ctx, "task-ref"));
    assert(taskRefText.indexOf("J1") !== -1 && taskRefText.indexOf("e1") !== -1, "ref identity shown");

    action(ctx, "load-task").fire("click");
    await flush();
    assertEq(stateOf(ctx), "loading", "deferred load is a loading state");
    assertEq(field(ctx, "overlay").hidden, false, "overlay visible while loading");

    first.resolve(WATER);
    await flush();
    assertEq(stateOf(ctx), "ready");
    assertEq(subtreeText(field(ctx, "source-status")).indexOf("3 个原子") !== -1, true);
    assertEq(ctx.client.calls.length, 0, "injected loader performs no transport call");

    // error -> retry
    action(ctx, "load-task").fire("click");
    await flush();
    assertEq(stateOf(ctx), "error");
    assertEq(subtreeText(field(ctx, "overlay-detail")).indexOf("geometry missing") !== -1, true);
    assertEq(action(ctx, "retry").hidden, false);
    action(ctx, "retry").fire("click");
    await flush();
    assertEq(stateOf(ctx), "ready");
    assertEq(subtreeText(field(ctx, "source-status")).indexOf("3 个原子") !== -1, true);
  });

  await test("task structure: canonical ACPView.source URL is used, never assembled here", async () => {
    const sandbox = makeSandbox({});
    loadScript("js/visualization/view_source.js", sandbox);
    const mod = loadModule(sandbox);
    const dom = makeDom();
    const calls = [];
    const instance = mod.factory({
      doc: dom.document,
      request: (url) => { calls.push(url); return Promise.resolve(WATER); },
      parseDebounceMs: 0
    });
    const host = dom.document.createElement("div");
    await instance.mount(host, {});
    await flush();

    const radios = findAll(host, "data-role", "source");
    radios.forEach((radio) => { radio.checked = radio.value === "task"; });
    radios.find((radio) => radio.value === "task").fire("change");
    // No context: the module must expose the explicit no-ref state and not call.
    assertEq(host.children[0].getAttribute("data-acp-module-state"), "empty");
    find(host, "data-action", "load-task").fire("click");
    await flush();
    assertEq(calls.length, 0, "no ref -> no request");

    // With a ref through the context, the canonical helper builds the URL.
    const ref = { kind: "structure-entry", jobId: "J1", entryId: "e1" };
    const expected = sandbox.ACPView.source.buildStructureUrl(ref, "/api/v1");
    const context = makeContext({ structureRef: ref });
    instance.updateContext(context.getSnapshot());
    await flush();
    find(host, "data-action", "load-task").fire("click");
    await flush();
    assertEq(calls, [expected], "exactly the canonical structure-viewer geometry URL");
    assertEq(host.children[0].getAttribute("data-acp-module-state"), "ready");
    assertEq(subtreeText(find(host, "data-role", "source-status")).indexOf("3 个原子") !== -1, true);
    instance.dispose();

    // Without ACPView.source and without an injected loader: explicit error,
    // still no invented request.
    const bare = setup({ structureRef: ref });
    await mount(bare);
    setSource(bare, "task");
    await flush();
    action(bare, "load-task").fire("click");
    await flush();
    assertEq(stateOf(bare), "error");
    assertEq(subtreeText(field(bare, "overlay-detail")).indexOf("view_source.js") !== -1, true);
    assertEq(bare.client.calls.length, 0, "no request without the canonical URL helper");
  });

  await test("uploaded text: read locally, parsed, and nothing is uploaded", async () => {
    const ctx = setup();
    await mount(ctx);
    setSource(ctx, "upload");
    await flush();
    const fileInput = field(ctx, "file");
    const file = { name: "water.xyz", text: () => Promise.resolve(WATER) };
    fileInput.files = [file];
    fileInput.fire("change");
    await flush();

    assertEq(stateOf(ctx), "ready");
    assertEq(subtreeText(field(ctx, "source-status")).indexOf("3 个原子") !== -1, true);
    assertEq(ctx.client.calls.length, 0, "uploaded text never touches the network");
    const uploadCalls = ctx.client.calls.filter((call) => /upload/i.test(call.path));
    assertEq(uploadCalls.length, 0, "no upload endpoint is invented");

    // The uploaded text is visible in the editor for inspection.
    assertEq(field(ctx, "xyz-text").value, WATER);
    action(ctx, "preflight").fire("click");
    await flush();
    assertEq(preflightBody(ctx).input.source, WATER);
  });

  await test("generation/dispose: late structure and preflight results never paint", async () => {
    const gate = deferred();
    const ctx = setup({
      structureRef: { kind: "structure-entry", jobId: "J1", entryId: "e1" },
      loadStructure: () => gate.promise
    });
    await mount(ctx);
    setSource(ctx, "task");
    await flush();
    action(ctx, "load-task").fire("click");
    await flush();
    assertEq(stateOf(ctx), "loading");

    ctx.instance.dispose();
    assertEq(ctx.host.children.length, 0, "dispose removes the module DOM");
    ctx.instance.dispose();
    assertEq(ctx.host.children.length, 0, "dispose is idempotent");
    assertEq(ctx.context.listenerCount(), 0, "context subscription removed");

    gate.resolve(WATER);
    await flush();
    assertEq(ctx.host.children.length, 0, "late structure result cannot rebuild the DOM");

    // Late preflight after dispose: no receipt, no navigation.
    const gate2 = deferred();
    let calls = 0;
    const ctx2 = setup({
      client: makeClient((pathname) => {
        if (pathname === API + "/preflight") { calls += 1; return gate2.promise; }
        return defaultResponse(pathname);
      })
    });
    await mount(ctx2);
    await typeXyz(ctx2, WATER);
    action(ctx2, "preflight").fire("click");
    await flush();
    assertEq(calls, 1);
    ctx2.instance.dispose();
    gate2.resolve({ schema_version: 1, feasible: true, reasons: [], advisories: [], plan_digest: DIGEST });
    await flush();
    assertEq(ctx2.host.children.length, 0);
    assertEq(ctx2.commands.opened.length, 0, "no openTask from a disposed instance");
    assertEq(submitBodies(ctx2).length, 0);

    // Remount works on the same instance and restores the safe defaults.
    await mount(ctx2);
    assertEq(stateOf(ctx2), "empty");
    assertEq(attr(ctx2, "data-acp-scants"), "off");
    assertEq(field(ctx2, "scants").checked, false);
    assertEq(action(ctx2, "submit").disabled, true);
    ctx2.instance.dispose();
  });

  await test("updateContext: a new task structure focus resets the loaded task structure", async () => {
    const loads = [];
    const ctx = setup({
      structureRef: { kind: "structure-entry", jobId: "J1", entryId: "e1" },
      loadStructure: (ref) => { loads.push(ref); return Promise.resolve(WATER); }
    });
    await mount(ctx);
    setSource(ctx, "task");
    await flush();
    action(ctx, "load-task").fire("click");
    await flush();
    assertEq(stateOf(ctx), "ready");

    ctx.context.setFocus({ structureRef: { kind: "structure-entry", jobId: "J2", entryId: "e2" } });
    await flush();
    assertEq(stateOf(ctx), "empty", "stale structure is dropped on focus change");
    assertEq(loads.length, 1, "focus change does not auto-load (no surprise request)");
    assertEq(subtreeText(field(ctx, "task-ref")).indexOf("J2") !== -1, true);
    action(ctx, "load-task").fire("click");
    await flush();
    assertEq(loads.length, 2);
    assertEq(loads[1].jobId, "J2");
  });

  await test("project context: project_id rides in both preflight and submit", async () => {
    const ctx = setup({ projectId: "proj-a" });
    await mount(ctx);
    await typeXyz(ctx, WATER);
    action(ctx, "preflight").fire("click");
    await flush();
    assertEq(preflightBody(ctx).project_id, "proj-a");
    action(ctx, "submit").fire("click");
    await flush();
    const body = submitBodies(ctx)[0].body;
    const without = Object.assign({}, body);
    delete without.preflight_fingerprint;
    assertEq(without, preflightBody(ctx), "project id is part of the frozen plan");
  });

  await test("i18n: static chrome carries data-i18n keys and the zh fallback applies", async () => {
    const ctx = setup();
    await mount(ctx);
    const keys = collectAttr(root(ctx), "data-i18n");
    [
      "next.s2scan.title",
      "next.s2scan.subtitle",
      "next.s2scan.source_title",
      "next.s2scan.scan_title",
      "next.s2scan.state_title",
      "next.s2scan.review_title",
      "next.s2scan.scants_hint",
      "next.s2scan.method_note",
      "next.s2scan.submit",
      "next.s2scan.preflight"
    ].forEach((key) => {
      assert(keys.indexOf(key) !== -1, "missing data-i18n key: " + key);
    });
    assertEq(subtreeText(find(ctx.host, "data-i18n", "next.s2scan.title")), "S2 键长扫描");
    assertEq(field(ctx, "xyz-text").getAttribute("data-i18n-ph"), "next.s2scan.xyz_placeholder");

    // A language change re-renders dynamic copy without throwing.
    ctx.dom.document.dispatchEvent({ type: "acp:langchange", detail: { lang: "en-US" } });
    await flush();
    assertEq(stateOf(ctx), "empty");
    assertEq(subtreeText(find(ctx.host, "data-i18n", "next.s2scan.title")), "S2 键长扫描",
      "missing shell keys keep the zh fallback instead of the bare key");
  });

  await test("boundary pins: no second viewer, no direct network primitive, no task mutation", async () => {
    const source = fs.readFileSync(path.resolve(__dirname, "..", "modules", "s2-scan", "module.js"), "utf8");
    [
      "$3Dmol",
      "ACPVisualization",
      "scene_instance",
      "createViewer",
      "new Image(",
      "<canvas",
      "fetch(",
      "XMLHttpRequest",
      "sendBeacon",
      "jobCommands",
      "s2scanState"
    ].forEach((token) => {
      assertEq(source.indexOf(token), -1, "module must not reference " + token);
    });
    assert(source.indexOf("ACPClient.transport.request") !== -1, "uses the existing client seam");
    assert(source.indexOf("/preflight") !== -1, "preview endpoint documented");
    assert(source.indexOf("/jobs") !== -1, "submit endpoint documented");

    const ctx = setup();
    await mount(ctx);
    await typeXyz(ctx, WATER);
    const jobCallsBefore = ctx.client.calls.length;
    action(ctx, "preflight").fire("click");
    await flush();
    const paths = ctx.client.calls.slice(jobCallsBefore).map((call) => call.path);
    assertEq(paths, ["/api/v1/preflight"], "preview goes through the one preflight endpoint");
    assertEq(ctx.client.calls.filter((call) => /cancel|rerun|pause|purge|edit/i.test(call.path)).length, 0,
      "no task mutation command is ever issued");
  });
};
