/* Preflight concurrency guard + artifact-read digest (plan B1-3 N2 / B1-8).
 *
 * Runs the REAL client layer inside the zero-dependency vm harness with stub
 * adapters that can settle out of order. No network, no fetch.
 *
 * Pinned contracts:
 *   a. an edit while preflight A is in flight makes A unable to overwrite the
 *      newer B result (out-of-order completion);
 *   b. a late A completion after an edit never restores submit eligibility or
 *      the old fingerprint;
 *   c. same revision + different content => different cache keys (the local
 *      client_request_digest changes);
 *   d. artifact read without sha256 rejects without an adapter call; with
 *      sha256 it sends digest=sha256:<64hex>;
 *   e. submit carries the preflight fingerprint and is otherwise byte-equal to
 *      the preflight payload.
 */
"use strict";

const SCRIPTS = [
  "js/client/resource-store.js",
  "js/workbench/contracts/experience_contract.js",
  "js/client/science-query.js",
  "js/client/wizard-endpoints.js",
  "js/workbench/features/wizard/submit-edit.js",
  "js/workbench/features/science-question.js",
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

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function preflightBody(marker, fingerprint) {
  return {
    executed: false,
    metering_consumed: false,
    transfers_attempted: false,
    plan: { workflow: "singlepoint", marker: marker, plan_digest: fingerprint },
    preflight_fingerprint: fingerprint,
    estimate: { feasible: true, queue_seconds: 60 },
  };
}

function scienceHarness(h) {
  const sandbox = h.makeSandbox({
    fetch: function () {
      throw new Error("fetch is forbidden in the preflight concurrency harness");
    },
    document: makeDom(),
  });
  SCRIPTS.forEach((rel) => h.loadScript(rel, sandbox));
  const W = sandbox.window;
  const Q = W.ACPScienceQuestion;
  const preflightCalls = [];
  const capabilities = clone(Q.FIXTURES.capabilities);
  const view = clone(Q.FIXTURES.view);
  const v2 = (url, opts) => {
    const target = String(url);
    if (target.indexOf("/science/capabilities") === 0) {
      return Promise.resolve(clone(capabilities));
    }
    if (target.indexOf("/science/preflight") === 0) {
      const call = {
        url: target,
        body: JSON.parse((opts && opts.body) || "{}"),
        signal: opts && opts.signal,
        deferred: deferred(),
      };
      preflightCalls.push(call);
      return call.deferred.promise;
    }
    if (target.indexOf("/view") !== -1) return Promise.resolve(clone(view));
    return Promise.reject(new Error("unexpected v2 url: " + target));
  };
  W.ACPClient.science.init({ v2 });
  const submitCalls = [];
  Q.init({
    science: W.ACPClient.science,
    buildJobBody: W.ACPWizardSubmitEdit.buildStructureJobBody,
    // M5-03 F6: fixture harness binds its project explicitly.
    projectId: "demo-project",
    submit: function (body) {
      submitCalls.push(body);
      return Promise.resolve({ job_id: "concurrency_job", attempt: 1 });
    },
  });
  Q.mount(sandbox.document, { autoDiscover: false, fixtureMode: true });
  return { sandbox, W, Q, preflightCalls, submitCalls };
}

function eventHarness(h) {
  const sandbox = h.makeSandbox({
    fetch: function () {
      throw new Error("fetch is forbidden in the artifact read harness");
    },
  });
  h.loadScript("js/client/resource-store.js", sandbox);
  h.loadScript("js/client/event-invalidation.js", sandbox);
  return { sandbox, E: sandbox.window.ACPClient.eventInvalidation };
}

function settled(promise) {
  return promise.then(() => "resolved", (error) => (error && error.name) || "rejected");
}

function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

module.exports.register = async function (h) {
  const { test, assert, assertEq } = h;

  await test("out-of-order preflight: B wins and the late A result is discarded", async () => {
    const H = scienceHarness(h);
    await H.Q.discover();
    await H.Q.loadDemoStructure();
    const pA = settled(H.Q.runPreflight());
    await flush();
    assertEq(H.preflightCalls.length, 1, "A issued");
    H.Q.setValue("charge", "1");
    const pB = settled(H.Q.runPreflight());
    await flush();
    assertEq(H.preflightCalls.length, 2, "B issued after the edit");
    assertEq(H.preflightCalls[0].body.input.charge, 0, "A carries the pre-edit input");
    assertEq(H.preflightCalls[1].body.input.charge, 1, "B carries the edited input");

    const fingerprintB = "sha256:" + "b".repeat(64);
    H.preflightCalls[1].deferred.resolve(preflightBody("B", fingerprintB));
    await pB;
    assertEq(H.Q.getState().preflight.ok, true, "B is accepted");
    assertEq(H.Q.getState().preflight.fingerprint, fingerprintB, "B's fingerprint stored");
    assertEq(H.Q.canSubmit(), true, "B makes submit eligible");

    const fingerprintA = "sha256:" + "a".repeat(64);
    H.preflightCalls[0].deferred.resolve(preflightBody("A", fingerprintA));
    await pA;
    await new Promise((resolve) => setTimeout(resolve, 0));
    assertEq(H.Q.getState().preflight.fingerprint, fingerprintB,
      "the late A response never replaces B");
    assertEq(H.Q.canSubmit(), true, "B remains the eligible preflight");
  });

  await test("late preflight after an edit never restores submit eligibility", async () => {
    const H = scienceHarness(h);
    await H.Q.discover();
    await H.Q.loadDemoStructure();
    const pA = settled(H.Q.runPreflight());
    await flush();
    assertEq(H.preflightCalls.length, 1, "A issued");
    H.Q.setValue("multiplicity", "3");
    assertEq(H.Q.getState().preflight.ok, false, "edit clears the accepted preflight");
    assertEq(H.Q.getState().preflight.fingerprint, null, "edit clears the fingerprint");
    assertEq(H.Q.canSubmit(), false, "edit disables submit");

    H.preflightCalls[0].deferred.resolve(preflightBody("A", "sha256:" + "a".repeat(64)));
    await pA;
    await new Promise((resolve) => setTimeout(resolve, 0));
    assertEq(H.Q.getState().preflight.ok, false, "the old success is not restored");
    assertEq(H.Q.getState().preflight.fingerprint, null, "the old fingerprint stays cleared");
    assertEq(H.Q.getState().submitEnabled, false, "submit stays disabled");
    let rejected = false;
    try {
      await H.Q.submit();
    } catch (_error) {
      rejected = true;
    }
    assert(rejected, "submit refuses without a current preflight");
    assertEq(H.submitCalls.length, 0, "zero submit calls");
  });

  await test("same revision, different content: cache keys and digests differ", async () => {
    const H = scienceHarness(h);
    await H.Q.discover();
    await H.Q.loadDemoStructure();
    const requestA = H.Q.preflightPlan();
    const requestB = clone(requestA);
    requestB.input.charge = 5;
    const science = H.W.ACPClient.science;
    const digestA = science.clientRequestDigest(requestA);
    const digestB = science.clientRequestDigest(requestB);
    assert(digestA !== digestB, "canonical request digest changes with content");
    assert(/^[0-9a-f]{8}$/.test(digestA), "local digest is 8 hex chars");
    const scope = { projectId: "demo-project", permission: "read" };
    const opts = { identity: "manual-single_point_energy", generation: 3 };
    const keyA = science.preflightKeyFor(scope, requestA, opts);
    const keyB = science.preflightKeyFor(scope, requestB, opts);
    assert(keyA !== keyB, "same revision + different content => different keys");
    assertEq(science.preflightKeyFor(scope, clone(requestA), opts), keyA,
      "the same content maps to the same key deterministically");
    assert(keyA.indexOf(digestA) !== -1, "key embeds the local request digest");
    assert(keyB.indexOf(digestB) !== -1, "key embeds the local request digest");
  });

  await test("artifact read requires sha256 and sends digest=sha256:<64hex>", async () => {
    const H = eventHarness(h);
    const calls = [];
    H.E.init({
      v2: (path, opts) => {
        calls.push({ path: String(path), opts: opts });
        return Promise.resolve({ ok: true });
      },
    });
    const scope = { projectId: "proj-a", permission: "read" };
    const taskRef = { jobId: "job-1", attempt: 1 };

    let missing = null;
    try {
      await H.E.readArtifact({
        scope,
        taskRef,
        artifact: { artifact_id: "art-1", availability: "readable", generation: 2 },
      });
    } catch (error) {
      missing = error;
    }
    assert(missing && missing.name === "ArtifactNotReadable", "missing sha256 rejects");
    assert(/sha256 missing/.test(missing.message), "the refusal names the missing sha256");
    assertEq(calls.length, 0, "no digest-less request is ever sent");

    const hex = "ab".repeat(32);
    await H.E.readArtifact({
      scope,
      taskRef,
      artifact: { artifact_id: "art-1", availability: "readable", generation: 2, sha256: hex },
    });
    assertEq(calls.length, 1, "readable + sha256 issues one GET");
    assertEq(calls[0].opts.method, "GET");
    assert(calls[0].path.indexOf("digest=sha256%3A" + hex) !== -1,
      "the path carries the normalized digest");

    await H.E.readArtifact({
      scope,
      taskRef,
      artifact: {
        artifact_id: "art-1",
        availability: "readable",
        generation: 2,
        sha256: "sha256:" + hex,
      },
    });
    assertEq(calls.length, 2);
    assertEq((calls[1].path.match(/digest=/g) || []).length, 1, "one digest parameter");
    assert(calls[1].path.indexOf("sha256%3Asha256") === -1, "a sha256: prefix is not doubled");
  });

  await test("submit carries the preflight fingerprint and the same normalized payload", async () => {
    const H = scienceHarness(h);
    await H.Q.discover();
    await H.Q.loadDemoStructure();
    const pending = H.Q.runPreflight();
    await flush();
    const fingerprint = "sha256:" + "c".repeat(64);
    H.preflightCalls[0].deferred.resolve(preflightBody("A", fingerprint));
    await pending;
    await H.Q.submit();
    assertEq(H.submitCalls.length, 1, "submit called once");
    assertEq(H.submitCalls[0].preflight_fingerprint, fingerprint,
      "the fingerprint travels with the submit body");
    const submitted = clone(H.submitCalls[0]);
    delete submitted.preflight_fingerprint;
    assertEq(submitted, H.preflightCalls[0].body,
      "preflight and submit payloads are identical by construction");
  });
};
