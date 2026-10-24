/* T-17 / C11 Manual Research entry (model-free science question form).
 *
 * Runs the REAL client layer (resource-store + experience contract +
 * science-query + wizard-endpoints + the wizard payload builder) against stub
 * request adapters inside the zero-dependency vm harness. No network, no
 * fetch: the sandbox `fetch` throws if any module ever reaches for it.
 *
 * Pinned contracts:
 *   1. fixture submit works with NO model config anywhere;
 *   2. failed preflight disables submit and makes zero submit adapter calls;
 *   3. charge/multiplicity are visible and required (never hidden);
 *   4. retired workflow ids never become defaults (ANTI #20);
 *   5. fixture mode shows the 演示数据 badge (and live mode hides it);
 *   6. task detail references the SAME (job_id, attempt) submit returned;
 *   7. no second payload: the submit adapter receives exactly the wizard
 *      envelope builder's output (the wizard payload shape);
 *   8. no fetch() in the feature;
 *   9. discover runs before preflight; preflight sends the normalized request;
 *  10. structure preview goes through the wizard parse endpoint.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const FRONTEND = path.resolve(__dirname, "..", "..");
const REPO = path.resolve(FRONTEND, "..");
const FEATURE_REL = "js/workbench/features/science-question.js";
const FETCH_CALL_RE = /(?<![A-Za-z0-9_$])fetch\s*\(/;

const SCRIPTS = [
  "js/client/resource-store.js",
  "js/workbench/contracts/experience_contract.js",
  "js/client/science-query.js",
  "js/client/wizard-endpoints.js",
  "js/workbench/features/wizard/submit-edit.js",
  FEATURE_REL,
];

function makeDom() {
  const elements = new Map();
  function makeEl(id) {
    return {
      id,
      innerHTML: "",
      textContent: "",
      value: "",
      disabled: false,
      hidden: false,
      attrs: {},
      listeners: {},
      setAttribute(name, value) {
        this.attrs[name] = String(value);
        if (name === "hidden") this.hidden = true;
      },
      removeAttribute(name) {
        delete this.attrs[name];
        if (name === "hidden") this.hidden = false;
      },
      getAttribute(name) {
        return this.attrs[name] === undefined ? null : this.attrs[name];
      },
      addEventListener(type, handler) {
        (this.listeners[type] = this.listeners[type] || []).push(handler);
      },
    };
  }
  return {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, makeEl(id));
      return elements.get(id);
    },
    addEventListener() {},
    querySelectorAll() { return []; },
    createElement(tag) { return makeEl(tag); },
  };
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function routeSet(overrides) {
  return Object.assign({
    capabilities: { body: null },
    preflight: { reject: new Error("no preflight route") },
    view: { body: null },
    parse: { reject: new Error("no parse route") },
  }, overrides || {});
}

function call(spec) {
  if (spec && spec.reject) {
    const err = spec.reject instanceof Error ? spec.reject : new Error(String(spec.reject));
    return Promise.reject(err);
  }
  return Promise.resolve(clone(spec && spec.body != null ? spec.body : {}));
}

function setup(harness, routes, options) {
  options = options || {};
  const fetchCalls = [];
  const sandbox = harness.makeSandbox({
    fetch: function () {
      fetchCalls.push(Array.prototype.slice.call(arguments));
      throw new Error("fetch is forbidden in the manual research feature");
    },
    document: makeDom(),
  });
  SCRIPTS.forEach((rel) => harness.loadScript(rel, sandbox));
  const W = sandbox.window;
  const log = [];
  const v2 = (url, opts) => {
    log.push({
      adapter: "v2",
      url: String(url),
      method: (opts && opts.method) || "GET",
      body: opts && opts.body,
    });
    const path = String(url);
    if (path.indexOf("/science/capabilities") === 0) return call(routes.capabilities);
    if (path.indexOf("/science/preflight") === 0) return call(routes.preflight);
    if (path.indexOf("/view") !== -1) return call(routes.view);
    return Promise.reject(new Error("unexpected v2 url: " + path));
  };
  const v1 = (url, opts) => {
    log.push({ adapter: "v1", url: String(url), method: (opts && opts.method) || "GET" });
    return Promise.reject(new Error("unexpected v1 url: " + url));
  };
  const direct = (url, opts) => {
    log.push({ adapter: "direct", url: String(url), method: (opts && opts.method) || "GET" });
    if (String(url).indexOf("/structures/parse") !== -1) return call(routes.parse);
    return Promise.reject(new Error("unexpected direct url: " + url));
  };
  W.ACPClient.science.init({ v1, v2 });
  W.ACPClient.wizardEndpoints.init({ direct, baseUrl: "/api/v1" });

  const submitCalls = [];
  let builtSpec = null;
  let builtBody = null;
  const realBuilder = W.ACPWizardSubmitEdit.buildStructureJobBody;
  const buildJobBody = (spec) => {
    builtSpec = spec;
    builtBody = realBuilder(spec);
    return builtBody;
  };
  const submit = (body) => {
    submitCalls.push(body);
    return Promise.resolve(clone(options.submitResponse || W.ACPScienceQuestion.FIXTURES.submit));
  };
  const catalog = options.catalog ? () => options.catalog : undefined;
  const Q = W.ACPScienceQuestion;
  const initOptions = { science: W.ACPClient.science, wizardEndpoints: W.ACPClient.wizardEndpoints, submit, buildJobBody };
  if (catalog) initOptions.catalog = catalog;
  if (options.fixtureMode !== undefined) initOptions.fixtureMode = options.fixtureMode;
  Q.init(initOptions);
  const dom = sandbox.document;
  Q.mount(dom, { autoDiscover: false, fixtureMode: options.fixtureMode });
  return {
    sandbox, W, Q, dom, log, fetchCalls, submitCalls,
    getBuiltSpec: () => builtSpec,
    getBuiltBody: () => builtBody,
    bodyHtml: () => dom.getElementById("science-question-body").innerHTML,
    badge: () => dom.getElementById("science-question-demo-badge"),
  };
}

async function happyFixtureFlow(harness) {
  await harness.Q.loadDemoStructure();
  await harness.Q.runPreflight();
  return harness.Q.submit();
}

function realRetiredWorkflowIds() {
  try {
    const candidates = process.env.ACP_PYTHON ? [process.env.ACP_PYTHON] : ["python3.11", "python3", "python"];
    for (const py of candidates) {
      try {
        const out = execFileSync(py, ["-c",
          "import json; from acp.catalog import WORKFLOW_CATALOG; print(json.dumps([w['id'] for w in WORKFLOW_CATALOG if w.get('status') != 'active']))",
        ], { cwd: REPO, stdio: "pipe", timeout: 20000 });
        return JSON.parse(out.toString());
      } catch (_e) { /* next interpreter */ }
    }
  } catch (_e) { /* fall through */ }
  return null;
}

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;
  const QFIX = (() => {
    const sandbox = makeSandbox({ document: makeDom() });
    SCRIPTS.forEach((rel) => loadScript(rel, sandbox));
    return sandbox.window.ACPScienceQuestion.FIXTURES;
  })();

  await test("no model config: fixture discover -> preflight -> submit still works", async () => {
    const h0 = setup(h, routeSet({
      capabilities: { reject: new Error("platform absent") },
      preflight: { body: QFIX.preflight },
      view: { body: QFIX.view },
    }), { fixtureMode: true });
    assert(Object.keys(h0.sandbox).indexOf("modelConfig") === -1, "sandbox must carry no model config");
    await h0.Q.loadDemoStructure();
    const discovered = await h0.Q.discover();
    assertEq(discovered.demo, true, "offline discover adopts the embedded fixture catalog");
    await h0.Q.runPreflight();
    const detail = await h0.Q.submit();
    assertEq(h0.submitCalls.length, 1, "fixture submit adapter called once");
    assertEq(h0.submitCalls[0].workflow, "singlepoint");
    assert(detail, "submit resolves the read-only detail");
    assertEq(h0.fetchCalls.length, 0, "no global fetch during the whole flow");
    assertEq(h0.Q.getState().taskRef.job_id, QFIX.submit.job_id);
  });

  await test("production mode: a capabilities failure sets state.error and never adopts fixtures", async () => {
    const h0 = setup(h, routeSet({
      capabilities: { reject: new Error("HTTP 404") },
      preflight: { body: QFIX.preflight },
    }));
    const state = await h0.Q.discover();
    assertEq(state.demo, false, "production failure is not demo mode");
    assertEq(state.fixtureMode, false);
    assertEq(state.discovered, false, "discovery did not succeed");
    assert(state.error && /HTTP 404/.test(state.error.message), "top-level error carries the failure");
    assertEq(state.error.retry, true, "error exposes a retry action flag");
    assertEq(state.question, null, "no fixture question adopted");
    assertEq(h0.Q.demoBadgeVisible(), false, "no demo badge in production failure");
    assertEq(h0.badge().hidden, true, "badge stays hidden");
    assertEq(h0.Q.getState().submitEnabled, false, "nothing becomes submittable");
  });

  await test("preflight failure: submit disabled, zero submit adapter calls", async () => {
    const h0 = setup(h, routeSet({
      capabilities: { body: QFIX.capabilities },
      preflight: { reject: new Error("HTTP 422") },
    }));
    await h0.Q.discover();
    await h0.Q.loadDemoStructure();
    const preflight = await h0.Q.runPreflight();
    assertEq(preflight.ok, false, "failed preflight must not pass");
    assertEq(h0.Q.canSubmit(), false);
    assertEq(h0.Q.getState().submitEnabled, false);
    assert(/id="sq-submit"[^>]*\bdisabled\b/.test(h0.bodyHtml()), "submit button rendered disabled");
    let rejected = false;
    try {
      await h0.Q.submit();
    } catch (_e) {
      rejected = true;
    }
    assert(rejected, "submit must refuse after a failed preflight");
    assertEq(h0.submitCalls.length, 0, "zero submit adapter calls after failed preflight");
    assertEq(h0.Q.getState().submitCalls, 0);
  });

  await test("charge/multiplicity are visible and required (never hidden)", async () => {
    const h0 = setup(h, routeSet({
      capabilities: { body: QFIX.capabilities },
      preflight: { body: QFIX.preflight },
    }));
    await h0.Q.discover();
    const fields = h0.Q.getState().fields;
    assertEq(fields.charge.required, true);
    assertEq(fields.charge.visible, true);
    assertEq(fields.multiplicity.required, true);
    assertEq(fields.multiplicity.visible, true);
    assert(fields.charge.label && fields.multiplicity.label, "both fields carry visible labels");
    const html = h0.bodyHtml();
    assert(/id="sq-charge"[^>]*\brequired\b/.test(html), "charge input requires a value");
    assert(/id="sq-multiplicity"[^>]*\brequired\b/.test(html), "multiplicity input requires a value");
    assert(html.indexOf("sq-row-charge") !== -1 && html.indexOf("sq-row-multiplicity") !== -1, "rows rendered");
    assert(html.indexOf("display:none") === -1, "no default row is hidden");
    await h0.Q.loadDemoStructure();
    await h0.Q.runPreflight();
    assertEq(h0.Q.canSubmit(), true, "fixture defaults are complete");
    h0.Q.setValue("multiplicity", "1.5");
    assertEq(h0.Q.canSubmit(), false, "non-integer spin blocks submit");
    h0.Q.setValue("multiplicity", "1");
    h0.Q.setValue("charge", "0");
    await h0.Q.runPreflight();
    assertEq(h0.Q.canSubmit(), true);
  });

  await test("ANTI #20: retired workflow ids never become defaults", async () => {
    const hostileCapabilities = clone(QFIX.capabilities);
    hostileCapabilities.capabilities.fixture = true;
    hostileCapabilities.capabilities.questions[0].workflow = "legacy_retired_demo";
    const catalog = [
      { id: "legacy_retired_demo", label: "Legacy", status: "retired", method_schema_id: "legacy_schema" },
      { id: "singlepoint", label: "Singlepoint", status: "active", method_schema_id: "dft_singlepoint" },
    ];
    const h0 = setup(h, routeSet({
      capabilities: { body: hostileCapabilities },
      preflight: { body: QFIX.preflight },
    }), { catalog });
    await h0.Q.discover();
    const resolved = h0.Q.resolveQuestionWorkflow("single_point_energy", catalog);
    assertEq(resolved.id, "singlepoint", "a retired mapping self-heals to the active entry");
    assert(resolved.status === "active");
    assert(resolved.id !== "legacy_retired_demo", "the retired id is never selected");
    await h0.Q.loadDemoStructure();
    await h0.Q.runPreflight();
    await h0.Q.submit();
    assertEq(h0.submitCalls[0].workflow, "singlepoint", "submit carries the active workflow");
    assertEq(h0.submitCalls[0].method.schema_id, "dft_singlepoint", "schema comes from the active entry");

    const retiredReal = realRetiredWorkflowIds();
    const fixtureWorkflows = QFIX.workflowCatalog.map((entry) => entry.id);
    if (retiredReal) {
      fixtureWorkflows.forEach((id) => {
        assert(retiredReal.indexOf(id) === -1, "fixture workflow " + id + " must be an active id");
      });
      QFIX.capabilities.capabilities.questions.forEach((question) => {
        assert(retiredReal.indexOf(question.workflow) === -1,
          "fixture question " + question.question_id + " maps to retired id " + question.workflow);
      });
    }
    QFIX.workflowCatalog.forEach((entry) => {
      assertEq(entry.status, "active", "fixture catalog is active-only");
    });
  });

  await test("fixture mode shows the 演示数据 badge; live capabilities hide it", async () => {
    const offline = setup(h, routeSet({
      capabilities: { reject: new Error("platform absent") },
      preflight: { body: QFIX.preflight },
    }), { fixtureMode: true });
    await offline.Q.discover();
    assertEq(offline.Q.getState().demo, true);
    assertEq(offline.Q.demoBadgeVisible(), true);
    assertEq(offline.badge().textContent, "演示数据");
    assertEq(offline.badge().hidden, false, "badge is visible in fixture mode");

    const live = clone(QFIX.capabilities);
    delete live.capabilities.fixture;
    const liveHarness = setup(h, routeSet({
      capabilities: { body: live },
      preflight: { body: QFIX.preflight },
    }));
    await liveHarness.Q.discover();
    assertEq(liveHarness.Q.getState().demo, false, "non-fixture capabilities are not demo mode");
    assertEq(liveHarness.badge().hidden, true, "badge hidden without fixtures");
  });

  await test("task detail references the SAME job_id/attempt as submit", async () => {
    const submitResponse = { job_id: "manual_test_task", attempt: 2 };
    const viewBody = clone(QFIX.view);
    viewBody.project_id = "demo-project";
    viewBody.task_ref = { job_id: "manual_test_task", attempt: 2 };
    const h0 = setup(h, routeSet({
      capabilities: { body: QFIX.capabilities },
      preflight: { body: QFIX.preflight },
      view: { body: viewBody },
    }), { submitResponse });
    await h0.Q.discover();
    await happyFixtureFlow(h0);
    const viewCalls = h0.log.filter((entry) => entry.url.indexOf("/science/tasks/") !== -1 && entry.url.indexOf("/view") !== -1);
    assertEq(viewCalls.length, 1, "exactly one authoritative view read");
    assert(viewCalls[0].url.indexOf("/science/tasks/manual_test_task/attempts/2/view") !== -1,
      "view read uses the submit (job_id, attempt): " + viewCalls[0].url);
    const detail = h0.Q.getState().detail;
    assertEq(detail.taskRef, { job_id: "manual_test_task", attempt: 2 });
    const html = h0.bodyHtml();
    assert(html.indexOf("manual_test_task") !== -1, "detail shows the same job id");
    assert(html.indexOf("attempt 2") !== -1, "detail shows the same attempt");
    assert(html.indexOf("structure_final.xyz") !== -1, "raw artifact refs listed");
    assert(html.indexOf("sq-detail-limitations") !== -1, "limitations section present");
  });

  await test("no duplicate payload: adapter receives exactly the wizard payload shape", async () => {
    const h0 = setup(h, routeSet({
      capabilities: { body: QFIX.capabilities },
      preflight: { body: QFIX.preflight },
      view: { body: QFIX.view },
    }));
    await h0.Q.discover();
    await happyFixtureFlow(h0);
    const body = h0.submitCalls[0];
    assertEq(h0.submitCalls.length, 1);
    assert(body === h0.getBuiltBody(), "submit adapter must receive the wizard builder output object");
    assertEq(Object.keys(body).sort(),
      ["input", "method", "molecule_name", "project_id", "remark", "resources", "workflow"]);
    assertEq(Object.keys(body.input).sort(), ["charge", "multiplicity", "source", "source_type"]);
    assertEq(Object.keys(body.method).sort(), ["levels", "profile_id", "schema_id"]);
    assertEq(Object.keys(body.resources).sort(),
      ["batch_id", "batch_index", "batch_total", "mem", "nproc", "parallelism"]);
    assertEq(body.input.source_type, "xyz_text");
    assertEq(body.workflow, "singlepoint");
    assertEq(typeof body.molecule_name, "string");
    const featureSource = fs.readFileSync(path.join(FRONTEND, FEATURE_REL), "utf8");
    assert(featureSource.indexOf("buildStructureJobBody") !== -1,
      "the feature reuses the wizard envelope builder");
    assert(!/function\s+buildStructureJobBody\s*\(/.test(featureSource),
      "the feature defines no second envelope builder");
    const spec = h0.getBuiltSpec();
    assert(spec && spec.workflow === "singlepoint" && spec.input && spec.method && spec.resources,
      "the builder spec carries values only");
  });

  await test("no fetch: feature source has zero fetch( calls and the flow never touches fetch", async () => {
    const featureSource = fs.readFileSync(path.join(FRONTEND, FEATURE_REL), "utf8");
    const hits = FETCH_CALL_RE.test(featureSource);
    assert(!hits, "science-question.js must own no fetch() call");
    const h0 = setup(h, routeSet({
      capabilities: { body: QFIX.capabilities },
      preflight: { body: QFIX.preflight },
      view: { body: QFIX.view },
    }));
    await h0.Q.discover();
    await happyFixtureFlow(h0);
    assertEq(h0.fetchCalls.length, 0, "global fetch is never reached");
    assert(h0.log.every((entry) => entry.adapter === "v2" || entry.adapter === "direct"),
      "every platform call goes through the injected Client adapters");
  });

  await test("discover runs before preflight and preflight sends the normalized request", async () => {
    const h0 = setup(h, routeSet({
      capabilities: { body: QFIX.capabilities },
      preflight: { body: QFIX.preflight },
      view: { body: QFIX.view },
    }));
    await h0.Q.discover();
    await h0.Q.loadDemoStructure();
    await h0.Q.runPreflight();
    const order = h0.log.map((entry) => entry.url.indexOf("/science/capabilities") === 0 ? "discover"
      : entry.url.indexOf("/science/preflight") === 0 ? "preflight" : "other");
    assertEq(order[0], "discover");
    assert(order.indexOf("preflight") > order.indexOf("discover"), "preflight follows discover");
    const plan = h0.Q.preflightPlan();
    assertEq(plan.workflow, "singlepoint");
    assert(plan.input && plan.input.source_type === "xyz_text" && plan.input.source.indexOf("O  0.000000") !== -1,
      "the normalized request carries the real XYZ source");
    assert(plan.method && plan.method.levels && plan.method.schema_id === "dft_singlepoint",
      "the normalized request carries method schema/levels");
    assert(plan.resources && plan.resources.nproc === 4 && plan.resources.mem === "4GB",
      "the normalized request carries resources");
    const sent = h0.log.filter((entry) => entry.url.indexOf("/science/preflight") === 0).pop();
    assertEq(JSON.parse(sent.body), plan, "the preflight adapter received exactly the normalized request");
    assertEq(h0.submitCalls.length, 0, "preflight makes no submit call");
  });

  await test("structure preview goes through the wizard parse endpoint (no local parser)", async () => {
    const h0 = setup(h, routeSet({
      capabilities: { body: QFIX.capabilities },
      preflight: { body: QFIX.preflight },
      parse: { body: { ok: true, detected_format: "xyz", errors: [], warnings: [], structures: [{ name: "demo_water", atom_count: 3, charge: 0, multiplicity: 1 }] } },
    }));
    await h0.Q.discover();
    await h0.Q.loadDemoStructure();
    const preview = await h0.Q.previewStructure();
    const parseCalls = h0.log.filter((entry) => entry.url.indexOf("/structures/parse") !== -1);
    assertEq(parseCalls.length, 1, "exactly one wizard parse call");
    assertEq(parseCalls[0].method, "POST");
    assertEq(preview.ok, true);
    assertEq(preview.atomCount, 3);
    assertEq(preview.charge, 0);
    assertEq(preview.multiplicity, 1);
  });

  await test("preflight failure shows the cause and a working retry control", async () => {
    const h0 = setup(h, routeSet({
      capabilities: { body: QFIX.capabilities },
      preflight: {
        body: {
          executed: false,
          metering_consumed: false,
          transfers_attempted: false,
          plan: { question_id: "single_point_energy", workflow: "singlepoint" },
          estimate: { feasible: false, reason: "fixture preflight failure" },
        },
      },
    }));
    await h0.Q.discover();
    await h0.Q.loadDemoStructure();
    const outcome = await h0.Q.runPreflight();
    assertEq(outcome.ok, false, "infeasible preflight fails");
    const html = h0.bodyHtml();
    assert(html.indexOf("预检失败") !== -1, "failure status visible");
    assert(html.indexOf("sq-status-cause") !== -1, "cause line rendered");
    assert(html.indexOf("fixture preflight failure") !== -1, "failure cause shown to the user");
    assert(html.indexOf("sq-preflight-retry") !== -1, "retry control rendered");
    assertEq(h0.Q.getState().preflight.error, "fixture preflight failure", "cause kept in state");
    assertEq(h0.Q.getState().submitEnabled, false, "submit stays disabled");
    const retry = h0.dom.getElementById("sq-preflight-retry");
    const handlers = (retry.listeners && retry.listeners.click) || [];
    assert(handlers.length >= 1, "retry button is wired");
    const count = () => h0.log.filter((e) => e.url.indexOf("/science/preflight") === 0).length;
    const before = count();
    await handlers[0]();
    assertEq(count(), before + 1, "retry re-runs the preflight");
    assertEq(h0.submitCalls.length, 0, "failure never submits");
  });

  await test("user edit refreshes the field provenance (source line never stale)", async () => {
    const h0 = setup(h, routeSet({
      capabilities: { body: QFIX.capabilities },
      preflight: { body: QFIX.preflight },
    }));
    await h0.Q.discover();
    const origin = h0.Q.getState().defaults.sources.method;
    assert(String(origin).indexOf("capability:") === 0, "baseline provenance is the capability default");
    h0.Q.setValue("method", "wB97X-D4");
    const live = h0.dom.getElementById("sq-source-method");
    const liveHtml = String(live.innerHTML || "").replace(/<wbr>/g, "");
    assert(liveHtml.indexOf("用户输入") !== -1, "edited field marked as a user edit");
    assert(liveHtml.indexOf(origin) !== -1, "original provenance preserved in the line");
    const sources = h0.Q.getState().defaults.sources;
    assert(String(sources.method).indexOf("用户输入") !== -1, "state provenance updated");
    assert(String(sources.charge).indexOf("capability:") === 0, "untouched fields keep their provenance");
    h0.Q.setValue("method", "r2SCAN-3c");
    assert(String(h0.Q.getState().defaults.sources.method).indexOf("用户输入") !== -1,
      "further edits do not nest stale sources");
    assert(String(h0.Q.getState().defaults.sources.method.split("原：")[1] || "").indexOf("用户输入") === -1,
      "origin stays the capability source");
    h0.Q.render();
    assert(h0.bodyHtml().indexOf("用户输入") !== -1, "re-render shows the refreshed provenance");
  });

  await test("source provenance breaks only at separators, never inside a token", async () => {
    const h0 = setup(h, routeSet({ capabilities: { body: QFIX.capabilities } }));
    await h0.Q.discover();
    const html = h0.bodyHtml();
    const source = /id="sq-source-method"[^>]*>([\s\S]*?)<\/div>/.exec(html);
    assert(source, "method source line rendered");
    assert(source[1].indexOf("capability:<wbr>single_point_energy.<wbr>defaults.<wbr>method") !== -1,
      "break opportunities sit after ':' and '.', not inside a word");
    assert(source[1].replace(/<wbr>/g, "").indexOf("capability:single_point_energy.defaults.method") !== -1,
      "the source identifier itself is unchanged");
    const css = fs.readFileSync(
      path.join(FRONTEND, "css", "workbench", "features.css"), "utf8");
    assert(/\.science-question-source[^}]*overflow-wrap:\s*normal/s.test(css),
      "source lines never split an unbreakable token to fill the width");
  });
};
