/*
 * ACP Client — frame-candidate CRUD tests (A-40/A-41).
 * Pure Node vm tests over the injected v1 transport; no network.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function setup() {
    const calls = [];
    let responder = null;
    const sandbox = makeSandbox({ console: { log() {}, warn() {}, error() {} } });
    loadScript("js/client/frame-candidates.js", sandbox);
    const client = sandbox.ACPClient.frameCandidates;
    client.init({
      v1(path, opts) {
        calls.push({ path, opts });
        return Promise.resolve(responder === null ? {} : responder);
      },
    });
    return {
      client,
      calls,
      respond(value) { responder = value; },
    };
  }

  await test("list: exact GET path and parsed payload", async () => {
    const env = setup();
    env.respond({ revision: 3, candidates: [{ candidate_id: "c1" }] });
    const payload = await env.client.list("job 1");
    assertEq(env.calls[0].path, "/jobs/job%201/frame-candidates");
    assertEq(env.calls[0].opts, undefined);
    assertEq(payload.revision, 3);
    assertEq(payload.candidates[0].candidate_id, "c1");
  });

  await test("save: exact POST path/body/headers", async () => {
    const env = setup();
    env.respond({ revision: 4, candidate: { candidate_id: "c2" }, candidates: [] });
    const payload = await env.client.save("job-1", {
      view_type: "scan", frame_index: 2, role: "TS", expected_revision: 3,
    });
    assertEq(env.calls[0].path, "/jobs/job-1/frame-candidate");
    assertEq(env.calls[0].opts.method, "POST");
    assertEq(env.calls[0].opts.headers, { "Content-Type": "application/json" });
    assertEq(JSON.parse(env.calls[0].opts.body), {
      view_type: "scan", frame_index: 2, role: "TS", expected_revision: 3,
    });
    assertEq(payload.candidate.candidate_id, "c2");
  });

  await test("remove: exact DELETE path and optional expected_revision", async () => {
    const env = setup();
    env.respond({ revision: 5, candidates: [] });
    await env.client.remove("job-1", "c 1", 4);
    assertEq(env.calls[0].path, "/jobs/job-1/frame-candidate/c%201?expected_revision=4");
    assertEq(env.calls[0].opts.method, "DELETE");
  });

  await test("errors and pre-init calls are explicit, never silent success", async () => {
    const sandbox = makeSandbox({ console: { log() {}, warn() {}, error() {} } });
    loadScript("js/client/frame-candidates.js", sandbox);
    let caught = null;
    try { await sandbox.ACPClient.frameCandidates.list("job-1"); } catch (error) { caught = error; }
    assert(caught && /not initialized/.test(caught.message));

    const failing = makeSandbox({ console: { log() {}, warn() {}, error() {} } });
    loadScript("js/client/frame-candidates.js", failing);
    failing.ACPClient.frameCandidates.init({
      v1() { throw new Error("transport exploded"); },
    });
    caught = null;
    try { await failing.ACPClient.frameCandidates.list("job-1"); } catch (error) { caught = error; }
    assert(caught && /transport exploded/.test(caught.message), "transport errors propagate");
  });
};
