/*
 * ACP Next Shell — module loader (UI-01).
 * Authority: UI-00 module recipe contract (embedded).
 *
 * P0 resolution is the STATIC window.ACPNextModules table (no dynamic script
 * injection), but the surface is already the future dynamic one:
 *
 *   load(moduleId)                 → Promise<{ id, descriptor, factory, generation }>
 *   instantiate(moduleId, instanceId, host)
 *                                  → Promise<{ instance, descriptor, generation, moduleId }>
 *   invalidate(moduleId)           → drop cached module + instances, bump generation
 *   isStale(moduleId, generation)  → late-result guard for panels
 *
 * Pinned behaviors (acp2_shell_loader.test.js):
 *   - load() caches the Promise per moduleId: concurrent calls dedup, the
 *     underlying prepare (registry.resolve / future script fetch) runs once;
 *   - instantiate() caches per instanceId: concurrent calls share ONE factory
 *     invocation (factory 只调一次);
 *   - a FAILED load/instantiate clears its cache so a retry really re-runs;
 *   - invalidate() bumps the module generation; a late in-flight result with
 *     an old generation is DISPOSED (instance.dispose()) and rejected with
 *     E_STALE — never handed to a panel to remount.
 */
window.ACPShell = window.ACPShell || {};
ACPShell.loader = (function () {
  "use strict";

  // moduleId -> Promise<{id, descriptor, factory, generation}>
  var modulePromises = Object.create(null);
  // instanceKey (instanceId or moduleId fallback) -> Promise<instanceResult>
  var instancePromises = Object.create(null);
  // moduleId -> monotonically increasing generation
  var generations = Object.create(null);
  // instanceKey -> moduleId (for invalidate bookkeeping)
  var instanceOwners = Object.create(null);
  // future dynamic-script seam; P0 stays null (static table only)
  var dynamicResolver = null;

  function generationOf(moduleId) {
    return generations[moduleId] || 0;
  }

  function isStale(moduleId, generation) {
    return generationOf(moduleId) !== generation;
  }

  function structuredError(code, message, moduleId) {
    var err = new Error(message);
    err.acpShell = { ok: false, code: code, message: message, id: moduleId };
    return err;
  }

  function disposeQuietly(instance) {
    if (instance && typeof instance.dispose === "function") {
      try {
        instance.dispose();
      } catch (error) {
        if (typeof console !== "undefined" && console.error) {
          console.error("Module dispose threw", error);
        }
      }
    }
  }

  /** Prepare a module record once per generation; concurrent calls dedup. */
  function load(moduleId) {
    if (typeof moduleId !== "string" || !moduleId) {
      return Promise.reject(structuredError("E_DESCRIPTOR", "moduleId must be a non-empty string", moduleId));
    }
    var cached = modulePromises[moduleId];
    if (cached) return cached;
    var generation = generationOf(moduleId);
    var prepare = dynamicResolver
      ? Promise.resolve().then(function () { return dynamicResolver(moduleId); })
      : Promise.resolve().then(function () { return ACPShell.registry.resolve(moduleId); });
    var promise = prepare.then(function (res) {
      if (!res || !res.ok) {
        var info = (res && res.error) || res || {};
        throw structuredError(info.code || "E_LOAD", info.message || ("failed to load module " + moduleId), moduleId);
      }
      if (isStale(moduleId, generation)) {
        throw structuredError("E_STALE", "module '" + moduleId + "' invalidated during load", moduleId);
      }
      return { id: moduleId, descriptor: res.descriptor, factory: res.factory, generation: generation };
    });
    promise.catch(function () {
      if (modulePromises[moduleId] === promise) delete modulePromises[moduleId];
    });
    modulePromises[moduleId] = promise;
    return promise;
  }

  /**
   * Create (or hand back) the instance for (moduleId, instanceId).
   * instanceId keys the dedup: two panels of the same module with different
   * instanceIds get two instances; a retry of the SAME panel reuses nothing
   * after invalidate().
   */
  function instantiate(moduleId, instanceId, host) {
    var key = instanceId || moduleId;
    var existing = instancePromises[key];
    if (existing) return existing;
    var promise = load(moduleId).then(function (rec) {
      var instance = rec.factory(host || {});
      return Promise.resolve(instance).then(function (resolved) {
        var result = {
          instance: resolved,
          descriptor: rec.descriptor,
          moduleId: moduleId,
          generation: rec.generation
        };
        if (isStale(moduleId, result.generation)) {
          disposeQuietly(result.instance);
          throw structuredError("E_STALE", "module '" + moduleId + "' invalidated during instantiate", moduleId);
        }
        return result;
      });
    });
    promise.catch(function () {
      if (instancePromises[key] === promise) {
        delete instancePromises[key];
        if (instanceOwners[key] === moduleId) delete instanceOwners[key];
      }
    });
    instancePromises[key] = promise;
    instanceOwners[key] = moduleId;
    return promise;
  }

  /**
   * Drop every cached artifact for a module and bump its generation so any
   * in-flight result is recognised stale. Mounted instances are NOT touched
   * here — the panel owns its instance lifecycle (dispose on unmount/reload).
   */
  function invalidate(moduleId) {
    generations[moduleId] = generationOf(moduleId) + 1;
    delete modulePromises[moduleId];
    Object.keys(instancePromises).forEach(function (key) {
      if (instanceOwners[key] === moduleId) {
        delete instancePromises[key];
        delete instanceOwners[key];
      }
    });
    return generationOf(moduleId);
  }

  /** Future seam: replace static resolution with dynamic <script> loading. */
  function setResolver(fn) {
    dynamicResolver = typeof fn === "function" ? fn : null;
  }

  function stats() {
    return {
      modules: Object.keys(modulePromises).length,
      instances: Object.keys(instancePromises).length,
      generations: Object.assign({}, generations)
    };
  }

  return {
    load: load,
    instantiate: instantiate,
    invalidate: invalidate,
    isStale: isStale,
    generationOf: generationOf,
    setResolver: setResolver,
    stats: stats
  };
})();
