/*
 * ACP Next Shell — module registry (UI-01).
 * Authority: UI-00 module recipe contract (embedded; no draft doc landed yet).
 *
 * Descriptor shape (all fields validated at register time):
 *   {
 *     id: "molecule-viewer",            // required, /^[a-z][a-z0-9-]*$/
 *     interfaceVersion: 1,              // required integer ≥ 1 (ModuleInstance
 *                                       //   contract generation)
 *     version: "0.1.0",                 // required semver-ish string
 *     provides: ["viewer"],             // optional string[] capability tags
 *     binds: ["structureRef"],          // optional string[] WorkspaceContext
 *                                       //   field(s) this module consumes
 *     deps: [{ id, minVersion }],       // optional; loaded BEFORE this module
 *     singleton: true                   // optional; loader keeps one instance
 *   }
 *
 * factory(host, api) → ModuleInstance:
 *   { mount(hostEl), updateContext(snapshot), suspend(), resume(), dispose() }
 *   (mount/updateContext/suspend/resume/dispose all optional; mount may return
 *   a Promise).
 *
 * Every failure is a STRUCTURED result — { ok:false, code, message, id? } —
 * never a bare throw, so the loader/panels can render per-panel error states.
 */
window.ACPShell = window.ACPShell || {};
ACPShell.registry = (function () {
  "use strict";

  var CURRENT_INTERFACE = 1;
  var ID_RE = /^[a-z][a-z0-9-]*$/;

  // id -> { descriptor, factory }
  var modules = Object.create(null);
  var adoptSource = null; // optional window.ACPNextModules bulk table

  function fail(code, message, id) {
    var out = { ok: false, code: code, message: message };
    if (id != null) out.id = id;
    return out;
  }

  function ok(descriptor, factory) {
    return { ok: true, descriptor: descriptor, factory: factory };
  }

  function isStringArray(value) {
    return Array.isArray(value) && value.every(function (v) { return typeof v === "string" && v.length > 0; });
  }

  function looksLikeSemver(version) {
    return typeof version === "string" && /^\d+\.\d+\.\d+$/.test(version);
  }

  function compareSemver(a, b) {
    var pa = String(a).split(".");
    var pb = String(b).split(".");
    for (var i = 0; i < 3; i++) {
      var na = parseInt(pa[i], 10) || 0;
      var nb = parseInt(pb[i], 10) || 0;
      if (na !== nb) return na < nb ? -1 : 1;
    }
    return 0;
  }

  /** Validate a descriptor. Returns null when valid, else a structured error. */
  function validateDescriptor(desc) {
    if (!desc || typeof desc !== "object" || Array.isArray(desc)) {
      return fail("E_DESCRIPTOR", "descriptor must be an object");
    }
    var id = desc.id;
    if (typeof id !== "string" || !ID_RE.test(id)) {
      return fail("E_DESCRIPTOR", "descriptor.id must match " + String(ID_RE), id);
    }
    if (!Number.isInteger(desc.interfaceVersion) || desc.interfaceVersion < 1) {
      return fail("E_INTERFACE", "descriptor.interfaceVersion must be an integer ≥ 1", id);
    }
    if (desc.interfaceVersion > CURRENT_INTERFACE) {
      return fail("E_INTERFACE", "module requires newer host interface " + desc.interfaceVersion + " (host " + CURRENT_INTERFACE + ")", id);
    }
    if (!looksLikeSemver(desc.version)) {
      return fail("E_DESCRIPTOR", "descriptor.version must look like 'x.y.z'", id);
    }
    if (desc.provides !== undefined && !isStringArray(desc.provides)) {
      return fail("E_DESCRIPTOR", "descriptor.provides must be a string[]", id);
    }
    if (desc.binds !== undefined && !isStringArray(desc.binds)) {
      return fail("E_DESCRIPTOR", "descriptor.binds must be a string[]", id);
    }
    if (desc.deps !== undefined) {
      if (!Array.isArray(desc.deps)) return fail("E_DESCRIPTOR", "descriptor.deps must be an array", id);
      for (var i = 0; i < desc.deps.length; i++) {
        var dep = desc.deps[i];
        if (!dep || typeof dep.id !== "string" || !ID_RE.test(dep.id)) {
          return fail("E_DESCRIPTOR", "descriptor.deps[" + i + "].id invalid", id);
        }
        if (dep.id === id) {
          return fail("E_CYCLE", "module depends on itself: " + id, id);
        }
        if (dep.minVersion !== undefined && !looksLikeSemver(dep.minVersion)) {
          return fail("E_DESCRIPTOR", "descriptor.deps[" + i + "].minVersion must look like 'x.y.z'", id);
        }
      }
    }
    if (desc.singleton !== undefined && typeof desc.singleton !== "boolean") {
      return fail("E_DESCRIPTOR", "descriptor.singleton must be boolean", id);
    }
    return null;
  }

  /**
   * register(desc, factory) → { ok, descriptor, factory } | { ok:false, code, message, id }
   * Re-registering the same id replaces the entry (hot-swap during dev), but a
   * dependency-cycle is rejected and the previous entry kept.
   */
  function register(desc, factory) {
    var invalid = validateDescriptor(desc);
    if (invalid) return invalid;
    if (typeof factory !== "function") {
      return fail("E_FACTORY", "factory must be a function(host, api)", desc.id);
    }
    var cycle = findCycle(desc.id, desc, modules);
    if (cycle) {
      return fail("E_CYCLE", "dependency cycle: " + cycle.join(" → "), desc.id);
    }
    modules[desc.id] = { descriptor: desc, factory: factory };
    return ok(desc, factory);
  }

  /** Drop a module (used by tests / reload). Returns true when removed. */
  function unregister(id) {
    if (!modules[id]) return false;
    delete modules[id];
    return true;
  }

  function has(id) {
    return !!lookupEntry(id);
  }

  function lookupEntry(id) {
    if (modules[id]) return modules[id];
    if (adoptSource && adoptSource[id] && adoptSource[id].descriptor &&
        typeof adoptSource[id].factory === "function") {
      var entry = adoptSource[id];
      return { descriptor: entry.descriptor, factory: entry.factory };
  }
    return null;
  }

  /**
   * Bulk-collect from window.ACPNextModules = { id: { descriptor, factory } }.
   * Entries are validated lazily on resolve so a broken third-party entry only
   * fails its own panel. Returns { registered: [ids], rejected: [errors] }.
   */
  function collectFrom(table) {
    adoptSource = table || null;
    var registered = [];
    var rejected = [];
    if (!table || typeof table !== "object") return { registered: registered, rejected: rejected };
    Object.keys(table).forEach(function (id) {
      var entry = table[id];
      if (!entry || !entry.descriptor || typeof entry.factory !== "function") {
        rejected.push(fail("E_ADOPT", "ACPNextModules['" + id + "'] must be { descriptor, factory }", id));
        return;
      }
      var desc = Object.assign({}, entry.descriptor, { id: entry.descriptor.id || id });
      if (desc.id !== id) {
        rejected.push(fail("E_ADOPT", "ACPNextModules key '" + id + "' ≠ descriptor.id '" + desc.id + "'", id));
        return;
      }
      var invalid = validateDescriptor(desc);
      if (invalid) {
        rejected.push(invalid);
        return;
      }
      modules[id] = { descriptor: desc, factory: entry.factory };
      registered.push(id);
    });
    return { registered: registered, rejected: rejected };
  }

  /**
   * resolve(id) → { ok, descriptor, factory } | structured error.
   * Checks: existence → dependency existence → dependency semver compatibility
   * → cycle (defensive, catches cycles introduced via collectFrom).
   */
  function resolve(id) {
    var entry = lookupEntry(id);
    if (!entry) {
      return fail("E_NOT_FOUND", "module not registered: " + id, id);
    }
    var desc = entry.descriptor;
    var deps = desc.deps || [];
    for (var i = 0; i < deps.length; i++) {
      var dep = deps[i];
      var depEntry = lookupEntry(dep.id);
      if (!depEntry) {
        return fail("E_MISSING_DEP", "dependency '" + dep.id + "' required by '" + id + "' is not registered", id);
      }
      if (dep.minVersion && compareSemver(depEntry.descriptor.version, dep.minVersion) < 0) {
        return fail("E_VERSION", "dependency '" + dep.id + "'@" + depEntry.descriptor.version + " < required " + dep.minVersion + " (by '" + id + "')", id);
      }
    }
    var cycle = findCycle(id, desc, modules);
    if (cycle) {
      return fail("E_CYCLE", "dependency cycle: " + cycle.join(" → "), id);
    }
    return ok(desc, entry.factory);
  }

  /** DFS cycle probe; returns the cycle path array or null. */
  function findCycle(startId, startDesc, table) {
    var visited = Object.create(null);
    var stack = [];
    function walk(id, desc) {
      if (visited[id]) {
        if (id === startId) return true;
        return false;
      }
      visited[id] = true;
      stack.push(id);
      var deps = (desc && desc.deps) || [];
      for (var i = 0; i < deps.length; i++) {
        var depId = deps[i].id;
        if (depId === startId) {
          stack.push(depId);
          return true;
        }
        var depEntry = table[depId] || (adoptSource && adoptSource[depId]);
        if (depEntry && walk(depId, depEntry.descriptor)) return true;
      }
      stack.pop();
      visited[id] = false;
      return false;
    }
    if (walk(startId, startDesc)) return stack.concat([startId]);
    return null;
  }

  function listIds() {
    var ids = Object.keys(modules);
    if (adoptSource) {
      Object.keys(adoptSource).forEach(function (id) {
        if (ids.indexOf(id) < 0 && adoptSource[id] && adoptSource[id].descriptor) ids.push(id);
      });
    }
    return ids.sort();
  }

  function getDescriptor(id) {
    var entry = lookupEntry(id);
    return entry ? entry.descriptor : null;
  }

  return {
    CURRENT_INTERFACE: CURRENT_INTERFACE,
    register: register,
    unregister: unregister,
    resolve: resolve,
    has: has,
    collectFrom: collectFrom,
    listIds: listIds,
    getDescriptor: getDescriptor,
    validateDescriptor: validateDescriptor
  };
})();
