/* ACP 2.0 Next Shell — module loader (UI-01).
 *
 * Pins ACPShell.loader:
 *   1. load() caches the Promise per moduleId — concurrent loads dedup and the
 *      prepare (registry.resolve) runs once;
 *   2. instantiate() caches per instanceId — concurrent calls invoke the
 *      factory exactly ONCE and share the instance;
 *   3. a FAILED load/instantiate clears its cache so a retry really re-runs;
 *   4. invalidate() bumps the module generation — a late in-flight instance
 *      (factory already ran) is DISPOSED and rejected E_STALE, never handed
 *      to a panel for remount; the next instantiate creates a fresh one.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function setup() {
    const sandbox = makeSandbox({
      fetch: function () { throw new Error("fetch is forbidden in shell loader"); },
    });
    loadScript("js/shell/module-registry.js", sandbox);
    loadScript("js/shell/module-loader.js", sandbox);
    return {
      sandbox,
      registry: sandbox.window.ACPShell.registry,
      loader: sandbox.window.ACPShell.loader,
    };
  }

  function desc(overrides) {
    return Object.assign({
      id: "loadable",
      interfaceVersion: 1,
      version: "0.1.0",
    }, overrides || {});
  }

  function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  await test("acp2_shell_loader: concurrent load dedups — prepare runs once", async () => {
    const { registry, loader } = setup();
    let resolveCount = 0;
    const realResolve = registry.resolve;
    registry.resolve = function (id) {
      resolveCount += 1;
      return realResolve.call(registry, id);
    };
    registry.register(desc(), function () { return { tag: "instance" }; });

    const [a, b, c] = await Promise.all([
      loader.load("loadable"),
      loader.load("loadable"),
      loader.load("loadable"),
    ]);
    assertEq(resolveCount, 1, "registry.prepare must run once for 3 concurrent loads");
    assert(a === b && b === c, "all callers share one record");
    assertEq(a.descriptor.id, "loadable");
    assert(typeof a.factory === "function");
  });

  await test("acp2_shell_loader: concurrent instantiate calls the factory exactly once", async () => {
    const { registry, loader } = setup();
    let factoryCalls = 0;
    const theInstance = { tag: "only" };
    registry.register(desc(), function () {
      factoryCalls += 1;
      return theInstance;
    });

    const [r1, r2, r3] = await Promise.all([
      loader.instantiate("loadable", "panel-1", {}),
      loader.instantiate("loadable", "panel-1", {}),
      loader.instantiate("loadable", "panel-1", {}),
    ]);
    assertEq(factoryCalls, 1, "factory invoked once for concurrent instantiates");
    assert(r1.instance === theInstance && r2.instance === theInstance && r3.instance === theInstance);
    assertEq(r1.generation, 0);

    // A DIFFERENT instanceId of the same module is a separate instance.
    const r4 = await loader.instantiate("loadable", "panel-2", {});
    assertEq(factoryCalls, 2, "second instanceId creates a second instance");
    assert(r4.instance === theInstance, "same factory product (stateless here)");
  });

  await test("acp2_shell_loader: failed load clears cache — retry re-runs prepare", async () => {
    const { registry, loader } = setup();
    // Not registered yet → structured E_NOT_FOUND failure.
    const failed = await loader.load("loadable").then(() => null, (e) => e);
    assert(failed && failed.acpShell, "rejects with a structured error");
    assertEq(failed.acpShell.code, "E_NOT_FOUND");

    // Register now; a retry must succeed (cache was dropped, not poisoned).
    registry.register(desc(), function () { return {}; });
    const rec = await loader.load("loadable");
    assertEq(rec.descriptor.id, "loadable");
  });

  await test("acp2_shell_loader: failed instantiate clears its instance cache — retry re-runs factory", async () => {
    const { registry, loader } = setup();
    let calls = 0;
    registry.register(desc(), function () {
      calls += 1;
      if (calls === 1) throw new Error("factory blew up");
      return { tag: "ok" };
    });

    const first = await loader.instantiate("loadable", "panel-1", {}).then(() => null, (e) => e);
    assert(first instanceof Error, "first instantiate rejects");
    assertEq(calls, 1);

    const second = await loader.instantiate("loadable", "panel-1", {});
    assertEq(calls, 2, "retry after failure re-invokes the factory");
    assertEq(second.instance.tag, "ok");
  });

  await test("acp2_shell_loader: invalidate makes the late in-flight result E_STALE + disposed", async () => {
    const { registry, loader } = setup();
    const gate = deferred();
    let disposed = 0;
    registry.register(desc(), function () {
      // Async instance: resolves only when the test opens the gate.
      return gate.promise.then(function () {
        return {
          dispose: function () { disposed += 1; },
        };
      });
    });

    const pending = loader.instantiate("loadable", "panel-1", {});
    // Let the load+factory microtasks run so the instance is in flight.
    await Promise.resolve();
    await Promise.resolve();

    // Panel disposed / reloaded while the instance was still resolving.
    const newGeneration = loader.invalidate("loadable");
    assertEq(newGeneration, 1, "generation bumped");

    gate.resolve();
    const outcome = await pending.then((r) => ({ ok: true, r }), (e) => ({ ok: false, e }));
    assertEq(outcome.ok, false, "late result must NOT reach the caller");
    assert(outcome.e && outcome.e.acpShell, "structured error");
    assertEq(outcome.e.acpShell.code, "E_STALE");
    assertEq(disposed, 1, "late instance was disposed, not leaked");

    // A fresh instantiate after invalidation works and is generation 1.
    const fresh = await loader.instantiate("loadable", "panel-1", {});
    assertEq(fresh.generation, 1);
    assertEq(fresh.instance.dispose instanceof Function, true);
    assertEq(disposed, 1, "fresh instance NOT disposed (counter unchanged)");
  });

  await test("acp2_shell_loader: setResolver swaps in a future dynamic resolver", async () => {
    const { loader } = setup();
    let asked = [];
    loader.setResolver(function (moduleId) {
      asked.push(moduleId);
      return Promise.resolve({
        ok: true,
        descriptor: { id: moduleId, interfaceVersion: 1, version: "9.9.9" },
        factory: function () { return { dynamic: true }; },
      });
    });
    const rec = await loader.load("future-module");
    assertEq(asked, ["future-module"], "dynamic resolver used instead of the static table");
    assertEq(rec.descriptor.version, "9.9.9");
    const inst = await loader.instantiate("future-module", "p1", {});
    assertEq(inst.instance.dynamic, true);
  });
};
