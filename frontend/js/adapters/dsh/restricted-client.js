/*
 * ACP adapter layer — read-only client facade.
 *
 * `create(fullClient, {allowWrite:false})` returns a NEW object exposing only
 * the read namespaces of window.ACPClient. The source object is never mutated,
 * and every namespace that is allowed in full keeps its identity (`===`), so
 * existing module-equality checks keep working.
 *
 * Per-namespace policy:
 *   transport, store, events, eventInvalidation, polling, jobDetail,
 *   taskView, research, systemEndpoints          → whole object (identity)
 *   science                                      → read-only COPY without `preflight`
 *   frameCandidates                              → only `list` (write: whole)
 *   jobCommands, researchCommands, wizardEndpoints → only with allowWrite
 *
 * `task-explorer` already hides its write actions when `jobCommands` is
 * absent, so this is a supported, non-invasive enforcement for a read-only
 * first slice.
 */
window.ACPAdapters = window.ACPAdapters || {};
ACPAdapters.dsh = ACPAdapters.dsh || {};

ACPAdapters.dsh.restrictedClient = (function () {
  "use strict";

  var READ_NAMESPACES = [
    "transport", "store", "events", "eventInvalidation", "polling",
    "jobDetail", "taskView", "research", "systemEndpoints",
    "science", "frameCandidates"
  ];
  var WRITE_NAMESPACES = ["jobCommands", "researchCommands", "wizardEndpoints"];

  function has(obj, name) {
    return !!obj && Object.prototype.hasOwnProperty.call(obj, name) && obj[name] != null;
  }

  function fail(code, namespace) {
    var err = new Error("write namespace exposed: " + namespace);
    err.code = code;
    err.namespace = namespace;
    return err;
  }

  function copyWithout(source, dropName) {
    var out = {};
    Object.keys(source).forEach(function (key) {
      if (key !== dropName) out[key] = source[key];
    });
    return out;
  }

  function create(fullClient, options) {
    options = options || {};
    var allowWrite = options.allowWrite === true;
    var allow = Array.isArray(options.allow) ? options.allow : null;
    var out = {};

    function permitted(name) {
      return !allow || allow.indexOf(name) !== -1;
    }

    READ_NAMESPACES.forEach(function (name) {
      if (!permitted(name) || !has(fullClient, name)) return;
      var value = fullClient[name];
      if (name === "science") {
        if (allowWrite) { out.science = value; return; }
        var science = (typeof value === "object") ? copyWithout(value, "preflight") : value;
        out.science = science;
        return;
      }
      if (name === "frameCandidates") {
        if (allowWrite) { out.frameCandidates = value; return; }
        if (value && typeof value === "object" && typeof value.list === "function") {
          out.frameCandidates = { list: value.list };
          // Initialisation only binds the read transport; it is not a write.
          if (typeof value.init === "function") out.frameCandidates.init = value.init;
        }
        return; // no `list` at all → never invent the namespace
      }
      out[name] = value;
    });

    if (allowWrite) {
      WRITE_NAMESPACES.forEach(function (name) {
        if (permitted(name) && has(fullClient, name)) out[name] = fullClient[name];
      });
    }
    return out;
  }

  function writeNamespacesPresent(client) {
    var found = [];
    WRITE_NAMESPACES.forEach(function (name) {
      if (has(client, name)) found.push(name);
    });
    return found.sort();
  }

  function describe(client) {
    var present = writeNamespacesPresent(client);
    var frameCandidates = (client && client.frameCandidates) || null;
    var hasSave = !!(frameCandidates && typeof frameCandidates.save === "function");
    return {
      readOnly: present.length === 0 && !hasSave,
      namespaces: (client ? Object.keys(client) : []).sort(),
      writeNamespacesPresent: present
    };
  }

  function assertReadOnly(client) {
    var present = writeNamespacesPresent(client);
    if (present.length) throw fail("E_WRITE_NAMESPACE", present[0]);
    var frameCandidates = (client && client.frameCandidates) || null;
    if (frameCandidates && typeof frameCandidates.save === "function") {
      throw fail("E_WRITE_NAMESPACE", "frameCandidates.save");
    }
    return true;
  }

  return {
    READ_NAMESPACES: READ_NAMESPACES,
    WRITE_NAMESPACES: WRITE_NAMESPACES,
    create: create,
    describe: describe,
    assertReadOnly: assertReadOnly
  };
})();
