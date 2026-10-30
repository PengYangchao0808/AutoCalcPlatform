/* ACP 2.0 Next Shell — module registry (UI-01).
 *
 * Pins the structured-error contract of ACPShell.registry:
 *   1. invalid descriptors are REJECTED with { ok:false, code, message }
 *      (never a bare throw, never stored);
 *   2. missing dependency / incompatible minVersion / dependency cycles all
 *      surface as distinct structured codes at the right time;
 *   3. a valid descriptor registers and resolves with descriptor + factory;
 *   4. bulk collectFrom(window.ACPNextModules) keeps good entries and reports
 *      bad ones in the rejected list.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function setup() {
    const sandbox = makeSandbox({
      fetch: function () { throw new Error("fetch is forbidden in shell registry"); },
    });
    loadScript("js/shell/module-registry.js", sandbox);
    return { registry: sandbox.window.ACPShell.registry, sandbox };
  }

  function desc(overrides) {
    return Object.assign({
      id: "demo-module",
      interfaceVersion: 1,
      version: "0.1.0",
    }, overrides || {});
  }

  const noopFactory = function () { return {}; };

  await test("acp2_shell_registry: invalid descriptors rejected with structured errors", async () => {
    const { registry } = setup();
    const cases = [
      [null, "E_DESCRIPTOR"],
      [{}, "E_DESCRIPTOR"],                                              // no id
      [desc({ id: "Bad_Id" }), "E_DESCRIPTOR"],                          // bad id charset
      [desc({ interfaceVersion: 0 }), "E_INTERFACE"],                    // < 1
      [desc({ interfaceVersion: 1.5 }), "E_INTERFACE"],                  // non-integer
      [desc({ interfaceVersion: 99 }), "E_INTERFACE"],                   // newer than host
      [desc({ version: "1.0" }), "E_DESCRIPTOR"],                        // not x.y.z
      [desc({ provides: "viewer" }), "E_DESCRIPTOR"],                    // not an array
      [desc({ binds: [42] }), "E_DESCRIPTOR"],                           // non-string entry
      [desc({ deps: [{ id: "Bad" }] }), "E_DESCRIPTOR"],                 // bad dep id
      [desc({ deps: [{ id: "dep", minVersion: "x" }] }), "E_DESCRIPTOR"],// bad minVersion
      [desc({ deps: [{ id: "demo-module" }] }), "E_CYCLE"],              // self-cycle
      [desc({ singleton: "yes" }), "E_DESCRIPTOR"],                      // non-boolean
    ];
    for (const [descriptor, expectedCode] of cases) {
      const res = registry.register(descriptor, noopFactory);
      assertEq(res.ok, false, "expected rejection for " + JSON.stringify(descriptor));
      assertEq(res.code, expectedCode, "wrong code for " + JSON.stringify(descriptor));
      assert(typeof res.message === "string" && res.message.length > 0, "message required");
    }
    // A rejected descriptor must NOT be stored.
    assertEq(registry.has("demo-module"), false, "rejected descriptor leaked into registry");

    // factory must be a function
    const badFactory = registry.register(desc(), "not-a-function");
    assertEq(badFactory.ok, false);
    assertEq(badFactory.code, "E_FACTORY");
  });

  await test("acp2_shell_registry: valid register then resolve returns descriptor+factory", async () => {
    const { registry } = setup();
    const descriptor = desc({ provides: ["viewer"], binds: ["structureRef"] });
    const reg = registry.register(descriptor, noopFactory);
    assertEq(reg.ok, true, JSON.stringify(reg));
    assertEq(reg.descriptor.id, "demo-module");

    const res = registry.resolve("demo-module");
    assertEq(res.ok, true, JSON.stringify(res));
    assertEq(res.descriptor.provides, ["viewer"]);
    assert(res.factory === noopFactory, "factory identity preserved");

    const missing = registry.resolve("no-such-module");
    assertEq(missing.ok, false);
    assertEq(missing.code, "E_NOT_FOUND");
    assertEq(missing.id, "no-such-module");
  });

  await test("acp2_shell_registry: missing dependency and version-incompatible dependency", async () => {
    const { registry } = setup();
    // Missing dep: registers fine (deps checked at resolve), fails on resolve.
    const missing = registry.register(
      desc({ id: "needs-missing", deps: [{ id: "ghost", minVersion: "1.0.0" }] }),
      noopFactory
    );
    assertEq(missing.ok, true, "register defers dep existence check");
    const resMissing = registry.resolve("needs-missing");
    assertEq(resMissing.ok, false);
    assertEq(resMissing.code, "E_MISSING_DEP");
    assertEq(resMissing.id, "needs-missing");
    assert(resMissing.message.indexOf("ghost") >= 0, "message names the missing dep");

    // Version incompatible: dep exists at 1.0.0 but 2.0.0 is required.
    assertEq(registry.register(desc({ id: "dep-mod", version: "1.0.0" }), noopFactory).ok, true);
    assertEq(registry.register(
      desc({ id: "needs-version", deps: [{ id: "dep-mod", minVersion: "2.0.0" }] }),
      noopFactory
    ).ok, true);
    const resVersion = registry.resolve("needs-version");
    assertEq(resVersion.ok, false);
    assertEq(resVersion.code, "E_VERSION");
    assert(resVersion.message.indexOf("1.0.0") >= 0, "message carries installed version");
    assert(resVersion.message.indexOf("2.0.0") >= 0, "message carries required version");

    // Compatible minVersion resolves.
    assertEq(registry.register(
      desc({ id: "needs-ok", deps: [{ id: "dep-mod", minVersion: "1.0.0" }] }),
      noopFactory
    ).ok, true);
    assertEq(registry.resolve("needs-ok").ok, true);
  });

  await test("acp2_shell_registry: dependency cycle rejected at register time", async () => {
    const { registry } = setup();
    assertEq(registry.register(
      desc({ id: "cycle-a", deps: [{ id: "cycle-b" }] }),
      noopFactory
    ).ok, true, "a registers while b is absent");

    const resB = registry.register(
      desc({ id: "cycle-b", deps: [{ id: "cycle-a" }] }),
      noopFactory
    );
    assertEq(resB.ok, false);
    assertEq(resB.code, "E_CYCLE");
    assert(resB.message.indexOf("cycle") >= 0, "message mentions the cycle");
    // b was NOT stored; resolving a now reports its dep missing (structured,
    // never a bare throw) — the cycle never becomes loadable.
    assertEq(registry.has("cycle-b"), false, "cyclic registration must not persist");
    const resA = registry.resolve("cycle-a");
    assertEq(resA.ok, false);
    assertEq(resA.code, "E_MISSING_DEP");
  });

  await test("acp2_shell_registry: collectFrom keeps valid ACPNextModules entries, rejects bad", async () => {
    const { registry, sandbox } = setup();
    sandbox.window.ACPNextModules = {
      "good-one": {
        descriptor: desc({ id: "good-one", provides: ["mock-panel"] }),
        factory: noopFactory,
      },
      "bad-no-descriptor": { factory: noopFactory },
      "bad-factory": { descriptor: desc({ id: "bad-factory" }) },
      "bad-id-mismatch": {
        descriptor: desc({ id: "other-id" }),
        factory: noopFactory,
      },
    };
    const summary = registry.collectFrom(sandbox.window.ACPNextModules);
    assertEq(summary.registered, ["good-one"]);
    assertEq(summary.rejected.length, 3, "three malformed entries reported");
    summary.rejected.forEach((err) => {
      assertEq(err.ok, false);
      assert(typeof err.code === "string" && err.code.startsWith("E_"), "structured code");
    });
    assertEq(registry.resolve("good-one").ok, true);
    assertEq(registry.resolve("bad-factory").ok, false, "rejected entry not resolvable");
  });
};
