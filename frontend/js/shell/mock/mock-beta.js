/*
 * ACP Next Shell — mock module "mock-beta" (UI-01).
 * Test/demo only (?demo=1): registers into window.ACPNextModules, never part
 * of a production recipe. Deliberately FAILS the first mount when the sandbox
 * flips window.__acpMockFailNextMount so error-boundary/retry paths can be
 * exercised; records lifecycle calls into window.__acpMockLog.
 */
window.ACPNextModules = window.ACPNextModules || {};
window.__acpMockLog = window.__acpMockLog || [];

(function () {
  "use strict";

  function log(entry) {
    window.__acpMockLog.push(entry);
  }

  window.ACPNextModules["mock-beta"] = {
    descriptor: {
      id: "mock-beta",
      interfaceVersion: 1,
      version: "0.1.0",
      provides: ["viewer"],
      binds: ["structureRef"]
    },
    factory: function (host) {
      log({ module: "mock-beta", call: "factory", instanceId: host && host.instanceId });
      var loadedStructure = null;
      return {
        mount: function () {
          log({ module: "mock-beta", call: "mount" });
          if (window.__acpMockFailNextMount) {
            window.__acpMockFailNextMount = false;
            throw new Error("mock-beta forced mount failure");
          }
        },
        updateContext: function (snapshot) {
          log({
            module: "mock-beta",
            call: "updateContext",
            revision: snapshot ? snapshot.revision : null
          });
        },
        suspend: function () { log({ module: "mock-beta", call: "suspend" }); },
        resume: function () { log({ module: "mock-beta", call: "resume" }); },
        dispose: function () { log({ module: "mock-beta", call: "dispose" }); },
        // Commands entry point: viewer-style structure load.
        loadStructure: function (ref) {
          loadedStructure = ref;
          log({ module: "mock-beta", call: "loadStructure", ref: ref });
        },
        getLoadedStructure: function () { return loadedStructure; }
      };
    }
  };
})();
