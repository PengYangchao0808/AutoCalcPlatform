/* Project file read-only client. Uses the injected v2 transport only.
 * Contracts:
 *   GET /projects/{project_id}/tree?path=&limit=200
 *   GET /projects/{project_id}/file?path=&max_bytes=65536&max_lines=1000
 * The browser never joins a project root or filesystem path itself. */
window.ACPClient = window.ACPClient || {};
ACPClient.projectFiles = (function () {
  "use strict";
  var deps = {}, initialized = false;
  function req(projectId) {
    var id = String(projectId || "");
    if (!id) return Promise.reject(new Error("project_id is required"));
    return Promise.resolve(id);
  }
  function query(values) {
    return Object.keys(values).map(function (key) {
      var value = values[key];
      if (value == null) return "";
      return encodeURIComponent(key) + "=" + encodeURIComponent(String(value));
    }).filter(Boolean).join("&");
  }
  function call(path, opts) {
    if (!initialized || typeof deps.v2 !== "function") return Promise.reject(new Error("project file transport is unavailable"));
    return deps.v2(path, opts || {});
  }
  function validEntry(entry) {
    return entry && typeof entry.path === "string" && typeof entry.name === "string" &&
      typeof entry.type === "string" && isFinite(Number(entry.size));
  }
  function list(projectId, path, limit, opts) {
    return req(projectId).then(function (id) {
      var q = query({ path: path || "", limit: limit || 200 });
      return call("/projects/" + encodeURIComponent(id) + "/tree?" + q, Object.assign({ method: "GET" }, opts || {}));
    }).then(function (body) {
      if (!body || !Array.isArray(body.entries)) throw new Error("project tree response is missing entries");
      if (String(body.project_id) !== String(projectId)) throw new Error("project tree response belongs to another project");
      if (body.entries.some(function (entry) { return !validEntry(entry); })) throw new Error("project tree response contains invalid entries");
      return body;
    });
  }
  function read(projectId, path, maxBytes, maxLines, opts) {
    return req(projectId).then(function (id) {
      if (typeof path !== "string" || !path) return Promise.reject(new Error("path is required"));
      var q = query({ path: path, max_bytes: maxBytes || 65536, max_lines: maxLines || 1000 });
      return call("/projects/" + encodeURIComponent(id) + "/file?" + q, Object.assign({ method: "GET" }, opts || {}));
    }).then(function (body) {
      if (!body || typeof body.content !== "string") throw new Error("project file response is missing text content");
      if (String(body.project_id) !== String(projectId)) throw new Error("project file response belongs to another project");
      if (String(body.path) !== String(path)) throw new Error("project file response belongs to another path");
      return body;
    });
  }
  function init(options) {
    deps = options || {};
    initialized = typeof deps.v2 === "function";
  }
  return { init: init, list: list, read: read };
})();
