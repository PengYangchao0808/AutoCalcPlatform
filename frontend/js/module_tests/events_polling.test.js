/* Behavior tests for R2: event stream and polling strategy modules. */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function setupEnvironment(overrides) {
    const sandbox = makeSandbox(overrides);
    loadScript("js/client/event-stream.js", sandbox);
    loadScript("js/client/polling-strategy.js", sandbox);
    return sandbox;
  }

  class MockEventSource {
    constructor(url) {
      this.url = url;
      this.listeners = {};
      this.closed = false;
      MockEventSource.instances.push(this);
    }
    addEventListener(type, fn) {
      this.listeners[type] = this.listeners[type] || [];
      this.listeners[type].push(fn);
    }
    emit(type, event) {
      const fns = this.listeners[type] || [];
      fns.forEach((fn) => fn(event));
    }
    close() {
      this.closed = true;
    }
  }
  MockEventSource.instances = [];

  function instancesFor(fragment) {
    return MockEventSource.instances.filter((e) => e.url.indexOf(fragment) !== -1);
  }

  function countingHandlers(names) {
    const calls = {};
    const handlers = {};
    names.forEach((n) => {
      calls[n] = 0;
      handlers[n] = () => { calls[n]++; };
    });
    return { calls, handlers };
  }

  await test("eventStream: subscribes to events and transitions to closed on terminal event", async () => {
    MockEventSource.instances = [];
    const sandbox = setupEnvironment({ EventSource: MockEventSource });
    const W = sandbox.window;

    const terminalEvents = [];
    const logs = [];
    const handle = W.ACPClient.events.openJobStream("job-test-1", {
      onLog: (type, msg) => logs.push({ type, msg }),
      onTerminalEvent: (type, payload, jid) => terminalEvents.push({ type, jid }),
    }, { EventSourceClass: MockEventSource });

    const es = MockEventSource.instances[0];
    assert(es, "EventSource was instantiated");
    assert(es.url.includes("job-test-1"), "url contains jobId");

    // Emit normal progress
    es.emit("stage.progress", { data: JSON.stringify({ stage: "opt", status: "running" }) });
    assertEq(logs.length, 1);
    assertEq(logs[0].type, "stage.progress");

    // Emit terminal completed event
    es.emit("job.completed", { data: JSON.stringify({ status: "completed" }) });
    assertEq(terminalEvents.length, 1);
    assertEq(terminalEvents[0].type, "job.completed");
    assertEq(terminalEvents[0].jid, "job-test-1");
    assertEq(W.ACPClient.events.getState(), "closed");
    assert(es.closed, "EventSource was closed");
  });

  await test("eventStream: repeated openJobStream cleanly closes old stream and isolates generations", async () => {
    MockEventSource.instances = [];
    const sandbox = setupEnvironment({ EventSource: MockEventSource });
    const W = sandbox.window;

    const logs = [];
    W.ACPClient.events.openJobStream("job-old", {
      onLog: (type, msg) => logs.push("old:" + type),
    }, { EventSourceClass: MockEventSource });
    const es1 = MockEventSource.instances[0];

    // Open new stream for job-new
    W.ACPClient.events.openJobStream("job-new", {
      onLog: (type, msg) => logs.push("new:" + type),
    }, { EventSourceClass: MockEventSource });
    const es2 = MockEventSource.instances[1];

    assert(es1.closed, "old stream was closed");

    // Emit on old stream
    es1.emit("job.status", { data: "{}" });
    assert(!logs.includes("old:job.status"), "old stream events are ignored");

    // Emit on new stream
    es2.emit("job.status", { data: "{}" });
    assert(logs.includes("new:job.status"), "new stream received event");
  });

  await test("eventStream: init-injected baseUrl builds exact first-connect and resume URLs (after_seq replaces history)", async () => {
    MockEventSource.instances = [];
    const sandbox = setupEnvironment({ EventSource: MockEventSource });
    const W = sandbox.window;
    W.ACPClient.events.init({ baseUrl: "/TESTBASE", reconnectDelayMs: 10 });

    const states = [];
    W.ACPClient.events.openJobStream("job A/1", {
      onStateChange: (s) => states.push(s),
    }, { EventSourceClass: MockEventSource });

    const es = MockEventSource.instances[0];
    assertEq(states[0], "connecting", "connecting announced synchronously at open (legacy parity)");
    assertEq(es.url, "/TESTBASE/jobs/job%20A%2F1/events?history=200",
      "first connect: {base}/jobs/{enc}/events?history=200");

    // Seen event advances the cursor, then a connection error triggers resume.
    es.emit("job.status", { lastEventId: "41", data: "{}" });
    es.onerror();
    assertEq(W.ACPClient.events.getState(), "reconnecting");
    await sleep(60);

    assertEq(MockEventSource.instances.length, 2, "exactly one reconnect");
    const es2 = MockEventSource.instances[1];
    assertEq(es2.url, "/TESTBASE/jobs/job%20A%2F1/events?after_seq=41",
      "resume: after_seq REPLACES history — never combined");
    assert(!es2.url.includes("history"), "resume URL carries no history param");
  });

  await test("eventStream: init baseUrl wins over opts.baseUrl; opts.baseUrl used when init absent", async () => {
    MockEventSource.instances = [];
    // No init → per-call opts.baseUrl is the fallback.
    const sb1 = setupEnvironment({ EventSource: MockEventSource });
    sb1.window.ACPClient.events.openJobStream("j1", {}, { EventSourceClass: MockEventSource, baseUrl: "/OPTBASE" });
    assertEq(MockEventSource.instances[0].url, "/OPTBASE/jobs/j1/events?history=200");

    // init called → injected base wins over per-call opts.
    const sb2 = setupEnvironment({ EventSource: MockEventSource });
    sb2.window.ACPClient.events.init({ baseUrl: "/INITBASE" });
    sb2.window.ACPClient.events.openJobStream("j2", {}, { EventSourceClass: MockEventSource, baseUrl: "/OPTBASE" });
    assertEq(MockEventSource.instances[1].url, "/INITBASE/jobs/j2/events?history=200");
  });

  await test("eventStream: onStateChange chain mirrors the retired page fallback (connecting/connected/reconnecting/closed)", async () => {
    MockEventSource.instances = [];
    const sandbox = setupEnvironment({ EventSource: MockEventSource });
    const W = sandbox.window;
    W.ACPClient.events.init({ baseUrl: "/T", reconnectDelayMs: 10 });

    const states = [];
    W.ACPClient.events.openJobStream("job-s", {
      onStateChange: (s) => states.push(s),
    }, { EventSourceClass: MockEventSource });
    const es = MockEventSource.instances[0];

    assertEq(states, ["connecting"]);
    es.onopen();
    assertEq(states, ["connecting", "connected"], "onopen → connected");
    es.onerror();
    assertEq(states[states.length - 1], "reconnecting", "onerror → reconnecting");
    await sleep(40);
    const es2 = MockEventSource.instances[1];
    es2.onopen();
    assertEq(states[states.length - 1], "connected", "reconnect open → connected");
    es2.emit("done", { data: "{}" });
    assertEq(states[states.length - 1], "closed", "terminal → closed");
    assertEq(W.ACPClient.events.getState(), "closed");
  });

  await test("eventStream: stale stream cannot close or replace the new one (A→B→A + pending old reconnect)", async () => {
    MockEventSource.instances = [];
    const sandbox = setupEnvironment({ EventSource: MockEventSource });
    const W = sandbox.window;
    W.ACPClient.events.init({ baseUrl: "/T", reconnectDelayMs: 20 });

    const hA = W.ACPClient.events.openJobStream("job-A", {}, { EventSourceClass: MockEventSource });
    const es1 = MockEventSource.instances[0];
    const hB = W.ACPClient.events.openJobStream("job-B", {}, { EventSourceClass: MockEventSource });
    const es2 = MockEventSource.instances[1];
    assert(es1.closed, "A closed when B opens");

    // A stale handle must not close the active stream.
    hA.close();
    assert(!es2.closed, "stale A handle must not close the active B stream");
    assertEq(W.ACPClient.events.getCurrentJobId(), "job-B");

    // A→B→A: B closed, a NEW A stream becomes current.
    W.ACPClient.events.openJobStream("job-A", {}, { EventSourceClass: MockEventSource });
    const es3 = MockEventSource.instances[2];
    assert(es2.closed, "B closed by the new A open");

    // Old A's error fires late — must not spawn a duplicate A stream.
    es1.onerror();
    await sleep(60);
    assertEq(MockEventSource.instances.length, 3, "stale onerror must not create a new stream");
    assertEq(instancesFor("job-A").length, 2, "still exactly stale-es1 + current-es3 for job-A");

    // A pending reconnect of the CURRENT A must be disarmed when a new open supersedes it.
    es3.onerror(); // schedules gen-3 reconnect
    await sleep(5);
    W.ACPClient.events.openJobStream("job-B", {}, { EventSourceClass: MockEventSource });
    const es4 = MockEventSource.instances[3];
    await sleep(60);
    assertEq(MockEventSource.instances.length, 4, "disarmed reconnect must not produce a late extra stream");
    assert(!es4.closed, "newest stream untouched");
    assertEq(W.ACPClient.events.getCurrentJobId(), "job-B");
  });

  await test("eventStream: terminal event closes without reconnect; close() clears pending reconnect; close is generation-guarded", async () => {
    MockEventSource.instances = [];
    const sandbox = setupEnvironment({ EventSource: MockEventSource });
    const W = sandbox.window;
    W.ACPClient.events.init({ baseUrl: "/T", reconnectDelayMs: 20 });

    const terminals = [];
    const h = W.ACPClient.events.openJobStream("job-t", {
      onTerminalEvent: (type) => terminals.push(type),
    }, { EventSourceClass: MockEventSource });
    const es = MockEventSource.instances[0];
    es.emit("job.cancelled", { data: "{}" });
    assertEq(terminals, ["job.cancelled"]);
    assert(es.closed, "terminal closes the EventSource");
    assertEq(W.ACPClient.events.getState(), "closed");

    // Error after terminal must NOT reconnect (closed guard).
    es.onerror();
    await sleep(50);
    assertEq(MockEventSource.instances.length, 1, "no reconnect after terminal close");

    // close() clears a pending reconnect timer.
    const h2 = W.ACPClient.events.openJobStream("job-r", {}, { EventSourceClass: MockEventSource });
    const es2 = MockEventSource.instances[1];
    es2.onerror(); // reconnecting, timer pending
    assertEq(W.ACPClient.events.getState(), "reconnecting");
    h2.close();
    assertEq(W.ACPClient.events.getState(), "closed");
    await sleep(50);
    assertEq(MockEventSource.instances.length, 2, "pending reconnect was cleared by close()");

    // Stale handle close is a no-op (idempotent dispose).
    h2.close();
    assertEq(MockEventSource.instances.length, 2);
    assert(MockEventSource.instances[1].closed, "close() closed the active EventSource before going stale");
  });

  await test("pollingStrategy: lane 2 — summary every tick, viewer/energy/mech every 2nd tick; drawer skips summary only", async () => {
    MockEventSource.instances = [];
    const env1 = countingHandlers(["pollSelectedSummary", "pollStructureViewer", "pollEnergyGraph", "pollMechanismSection"]);
    const sb1 = setupEnvironment({ EventSource: MockEventSource });
    sb1.window.ACPClient.polling.init(env1.handlers, {
      isHidden: () => false,
      getSelectedJobId: () => "job-1",
      isDetailDrawerOpen: () => false,
      getActiveViewerTab: () => "energy",
      getAppSection: () => "mechanism",
      delays: { summaryActive: 20, log: 1000, slow: 1000, jobsActive: 1000, jobsIdle: 1000, jobsHidden: 1000 },
    });
    sb1.window.ACPClient.polling.start(false);
    await sleep(170);
    sb1.window.ACPClient.polling.stop();

    const c = env1.calls;
    assert(c.pollSelectedSummary >= 4, "summary polled repeatedly (got " + c.pollSelectedSummary + ")");
    assert(c.pollStructureViewer >= 2, "structure-viewer refresh runs on the every-2nd-tick path");
    assert(c.pollStructureViewer < c.pollSelectedSummary, "viewer refresh is every 2nd tick, not every tick");
    assert(c.pollSelectedSummary - 2 * c.pollStructureViewer <= 1, "viewer count tracks half the summary ticks");
    assertEq(c.pollEnergyGraph, c.pollStructureViewer, "energy polled on the same every-2nd-tick gate when tab=energy");
    assertEq(c.pollMechanismSection, c.pollStructureViewer, "mech polled on the same every-2nd-tick gate when section=mechanism");

    // Drawer open: summary skipped, the periodic viewer retry keeps running;
    // energy gate inactive on a non-energy tab (compat handler owns the rest).
    const env2 = countingHandlers(["pollSelectedSummary", "pollStructureViewer", "pollEnergyGraph"]);
    const sb2 = setupEnvironment({ EventSource: MockEventSource });
    sb2.window.ACPClient.polling.init(env2.handlers, {
      isHidden: () => false,
      getSelectedJobId: () => "job-1",
      isDetailDrawerOpen: () => true,
      getActiveViewerTab: () => "structure",
      getAppSection: () => "",
      delays: { summaryActive: 20, log: 1000, slow: 1000, jobsActive: 1000, jobsIdle: 1000, jobsHidden: 1000 },
    });
    sb2.window.ACPClient.polling.start(false);
    await sleep(150);
    sb2.window.ACPClient.polling.stop();
    assertEq(env2.calls.pollSelectedSummary, 0, "drawer open must skip the summary fetch");
    assert(env2.calls.pollStructureViewer >= 2, "periodic structure-viewer retry still covers the drawer-open path");
    assertEq(env2.calls.pollEnergyGraph, 0, "energy handler not called when viewer tab is not energy");
  });

  await test("pollingStrategy: hidden page — lanes 2/3/4 skip work, lane 1 keeps polling; stop freezes every lane", async () => {
    const names = ["pollJobsList", "pollSelectedSummary", "pollStructureViewer", "pollLogTail", "pollDiskUsage", "pollFileTree"];
    const env = countingHandlers(names);
    const sb = setupEnvironment();
    sb.window.ACPClient.polling.init(env.handlers, {
      isHidden: () => true,
      getSelectedJobId: () => "job-1",
      isDetailDrawerOpen: () => false,
      delays: { summaryHidden: 20, jobsHidden: 20, log: 20, slow: 20 },
    });
    sb.window.ACPClient.polling.start(false);
    await sleep(150);
    sb.window.ACPClient.polling.stop();
    const frozen = JSON.parse(JSON.stringify(env.calls));

    assert(frozen.pollJobsList >= 3, "lane 1 keeps polling while hidden (only the cadence slows)");
    assertEq(frozen.pollSelectedSummary, 0, "lane 2 skips summary work while hidden");
    assertEq(frozen.pollStructureViewer, 0, "lane 2 skips viewer work while hidden");
    assertEq(frozen.pollLogTail, 0, "lane 3 skips log tail while hidden (still reschedules)");
    assertEq(frozen.pollDiskUsage, 0, "lane 4 skips disk usage while hidden");
    assertEq(frozen.pollFileTree, 0, "lane 4 skips file tree while hidden");

    await sleep(100);
    assertEq(env.calls, frozen, "stop() disarms every lane — no late ticks");
    assert(!sb.window._jobsPollTimer && !sb.window._midPollTimer && !sb.window._logPollTimer && !sb.window._slowPollTimer,
      "legacy timer mirrors cleared after stop");
  });

  await test("pollingStrategy: lane 3 polls the log tail for the selection with a fixed cadence; no selection skips but keeps rescheduling", async () => {
    const sb = setupEnvironment();
    const seen = [];
    sb.window.ACPClient.polling.init({
      pollLogTail: (id) => { seen.push(id); },
    }, {
      isHidden: () => false,
      getSelectedJobId: () => "job-9",
      delays: { log: 20, summaryActive: 1000, summaryHidden: 1000, slow: 1000, jobsActive: 1000, jobsIdle: 1000, jobsHidden: 1000 },
    });
    sb.window.ACPClient.polling.start(false);
    await sleep(130);
    sb.window.ACPClient.polling.stop();
    assert(seen.length >= 3, "log tail polled repeatedly (got " + seen.length + ")");
    assert(seen.every((id) => id === "job-9"), "log tail always targets the current selection");

    // No selection: no tail reads, but the lane must keep its reschedule chain alive.
    const sb2 = setupEnvironment();
    let logCalls = 0;
    sb2.window.ACPClient.polling.init({
      pollLogTail: () => { logCalls++; },
    }, {
      isHidden: () => false,
      getSelectedJobId: () => null,
      delays: { log: 20, summaryActive: 1000, summaryHidden: 1000, slow: 1000, jobsActive: 1000, jobsIdle: 1000, jobsHidden: 1000 },
    });
    sb2.window.ACPClient.polling.start(false);
    await sleep(90);
    sb2.window.ACPClient.polling.stop();
    assertEq(logCalls, 0, "no tail read without a selection");
    assert(sb2.window.ACPClient.polling.getGeneration() > 0);
  });

  await test("pollingStrategy: lane 4 — disk + file tree always when visible; nodes/backends only with sysres drawer open", async () => {
    const names = ["pollDiskUsage", "pollNodes", "pollBackends", "pollFileTree"];
    const closedEnv = countingHandlers(names);
    const sb1 = setupEnvironment();
    sb1.window.ACPClient.polling.init(closedEnv.handlers, {
      isHidden: () => false,
      isSysresDrawerOpen: () => false,
      delays: { slow: 30, log: 1000, summaryActive: 1000, summaryHidden: 1000, jobsActive: 1000, jobsIdle: 1000, jobsHidden: 1000 },
    });
    sb1.window.ACPClient.polling.start(false);
    await sleep(130);
    sb1.window.ACPClient.polling.stop();
    assert(closedEnv.calls.pollDiskUsage >= 2, "disk usage polled on every visible slow tick");
    assert(closedEnv.calls.pollFileTree >= 2, "file tree refresh polled on every visible slow tick");
    assertEq(closedEnv.calls.pollNodes, 0, "nodes skipped while sysres drawer closed");
    assertEq(closedEnv.calls.pollBackends, 0, "backends skipped while sysres drawer closed");

    const openEnv = countingHandlers(names);
    const sb2 = setupEnvironment();
    sb2.window.ACPClient.polling.init(openEnv.handlers, {
      isHidden: () => false,
      isSysresDrawerOpen: () => true,
      delays: { slow: 30, log: 1000, summaryActive: 1000, summaryHidden: 1000, jobsActive: 1000, jobsIdle: 1000, jobsHidden: 1000 },
    });
    sb2.window.ACPClient.polling.start(false);
    await sleep(130);
    sb2.window.ACPClient.polling.stop();
    assert(openEnv.calls.pollNodes >= 2, "nodes polled when sysres drawer open");
    assert(openEnv.calls.pollBackends >= 2, "backends polled when sysres drawer open");
  });

  await test("pollingStrategy: skipInitialJobs defers the first jobs poll; start without it polls immediately", async () => {
    const deferredEnv = countingHandlers(["pollJobsList"]);
    const sb1 = setupEnvironment();
    sb1.window.ACPClient.polling.init(deferredEnv.handlers, {
      isHidden: () => false,
      hasActiveJobs: () => false,
      delays: { jobsInitial: 50, jobsIdle: 1000, jobsActive: 1000, jobsHidden: 1000, summaryActive: 1000, summaryHidden: 1000, log: 1000, slow: 1000 },
    });
    sb1.window.ACPClient.polling.start(true);
    await sleep(20);
    assertEq(deferredEnv.calls.pollJobsList, 0, "first jobs poll deferred when skipInitialJobs");
    await sleep(120);
    sb1.window.ACPClient.polling.stop();
    assert(deferredEnv.calls.pollJobsList >= 1, "deferred first poll fires after the initial delay");

    const immediateEnv = countingHandlers(["pollJobsList"]);
    const sb2 = setupEnvironment();
    sb2.window.ACPClient.polling.init(immediateEnv.handlers, {
      isHidden: () => false,
      hasActiveJobs: () => false,
      delays: { jobsInitial: 50, jobsIdle: 1000, jobsActive: 1000, jobsHidden: 1000, summaryActive: 1000, summaryHidden: 1000, log: 1000, slow: 1000 },
    });
    sb2.window.ACPClient.polling.start(false);
    assertEq(immediateEnv.calls.pollJobsList, 1, "first jobs poll runs immediately without skipInitialJobs");
    sb2.window.ACPClient.polling.stop();
  });

  await test("pollingStrategy: restart schedules exactly one chain per lane — no duplicate timers", async () => {
    const env = countingHandlers(["pollJobsList"]);
    const sb = setupEnvironment();
    const pollCtx = {
      isHidden: () => false,
      hasActiveJobs: () => false,
      delays: { jobsIdle: 25, jobsActive: 25, jobsHidden: 25, summaryActive: 1000, summaryHidden: 1000, log: 1000, slow: 1000 },
    };
    sb.window.ACPClient.polling.init(env.handlers, pollCtx);
    sb.window.ACPClient.polling.start(false);
    await sleep(10); // lane 1 re-arms its timer in the post-await finally
    assert(sb.window._jobsPollTimer, "legacy jobs timer mirrored during run");
    await sleep(130);
    sb.window.ACPClient.polling.stop();
    assert(!sb.window._jobsPollTimer, "legacy jobs timer cleared on stop");
    const first = env.calls.pollJobsList;
    assert(first >= 3, "first chain ticked repeatedly (got " + first + ")");

    // Restart must produce a single fresh chain, not an accumulation.
    sb.window.ACPClient.polling.init(env.handlers, pollCtx);
    sb.window.ACPClient.polling.start(false);
    await sleep(130);
    sb.window.ACPClient.polling.stop();
    const second = env.calls.pollJobsList - first;
    assert(second >= 3, "restarted chain ticks repeatedly (got " + second + ")");
    assert(second <= first + 3, "restart must not double the tick rate (no duplicate chains)");
  });
};
