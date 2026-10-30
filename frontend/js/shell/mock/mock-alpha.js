/*
 * ACP Next Shell — mock module "mock-alpha" (UI-01).
 * Test/demo only (?demo=1): registers into window.ACPNextModules, never part
 * of a production recipe. Records every lifecycle call into
 * window.__acpMockLog so tests can assert mount/updateContext/suspend/resume/
 * dispose sequencing.
 */
window.ACPNextModules = window.ACPNextModules || {};
window.__acpMockLog = window.__acpMockLog || [];

(function () {
  "use strict";

  function log(entry) {
    window.__acpMockLog.push(entry);
  }

  window.ACPNextModules["mock-alpha"] = {
    descriptor: {
      id: "mock-alpha",
      interfaceVersion: 1,
      version: "0.1.0",
      provides: ["mock-panel"],
      binds: ["taskRef", "researchRef"]
    },
    factory: function (host) {
      var el = null;
      var suspended = false;
      log({ module: "mock-alpha", call: "factory", instanceId: host && host.instanceId });
      return {
        mount: function (hostEl) {
          log({ module: "mock-alpha", call: "mount", instanceId: host && host.instanceId });
          if (hostEl && hostEl.appendChild) {
            el = hostEl.ownerDocument && hostEl.ownerDocument.createElement
              ? hostEl.ownerDocument.createElement("div")
              : null;
            if (el) {
              el.setAttribute("data-mock", "alpha");
              el.textContent = "mock-alpha panel";
              hostEl.appendChild(el);
            }
          }
        },
        updateContext: function (snapshot) {
          log({
            module: "mock-alpha",
            call: "updateContext",
            revision: snapshot ? snapshot.revision : null
          });
        },
        suspend: function () {
          suspended = true;
          log({ module: "mock-alpha", call: "suspend" });
        },
        resume: function () {
          suspended = false;
          log({ module: "mock-alpha", call: "resume" });
        },
        dispose: function () {
          log({ module: "mock-alpha", call: "dispose", wasSuspended: suspended });
          if (el && el.parentNode) el.parentNode.removeChild(el);
          el = null;
        },
        // Commands entry point exercised by tests.
        openTask: function (ref) {
          log({ module: "mock-alpha", call: "openTask", jobId: ref && ref.jobId });
        }
      };
    }
  };
})();
