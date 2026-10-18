/*
 * ACP Workbench controller — System & Resources use case
 * (nodes / local backends panels).
 *
 * Owner: frontend/js/workbench/controllers/** (OpenCode). Version: v20261018.1
 *
 * Re-homes the node/backend panel use case out of
 * features/reaction-builder.js (T22). This controller is the SINGLE owner of:
 *   - the panel request orchestration (via the injected Client facade
 *     ACPClient.systemEndpoints — there is NO fetch() in this file);
 *   - the nodes/backends/software-discovery cache writes (`nodesCache`,
 *     `backendsCache`, `softwareDiscoveryCache`, declared in
 *     compat/shared-state.js; every consumer keeps reading those page globals);
 *   - panel rendering (node rows / backend rows + version + multi-install
 *     disclosure) and the retry path (calling loadNodes()/loadBackends()
 *     again re-renders and clears the stored error).
 *
 * Preserved legacy behaviors (fidelity contract):
 *   - loadNodes: GET /api/v1/nodes, default 8 s transport budget; on failure
 *     the cache is cleared and the panel keeps its two existing presentations:
 *       * message containing "not configured" → muted "nodes.empty";
 *       * anything else → `.node-error` with "nodes.load_error: <message>"
 *     (a 503 therefore renders the error state, never a silent success).
 *     The returned promise resolves (legacy swallowed the error) so existing
 *     `.then(...)` callers are unaffected.
 *   - loadBackends: GET /api/backends (literal root path) then best-effort
 *     GET /api/v1/software/discovery; backend failure renders
 *     "backend.load_error" and stops; discovery failure is silent and the
 *     backend list still renders without version badges.
 *   - ping: the row button delegates to the injected onPingNode callback
 *     (reaction-builder keeps pingNode + node-matching responsibilities).
 *   - no request happens while the panel element is absent (drawer closed,
 *     list not mounted) — legacy early return preserved.
 *
 * Wiring: reaction-builder.js calls init({...}) once with the page bindings;
 * it keeps the historical `loadNodes`/`loadBackends` names as thin delegates.
 */
window.ACPWorkbench = window.ACPWorkbench || {};
ACPWorkbench.controllers = ACPWorkbench.controllers || {};
ACPWorkbench.controllers.systemResources = (function () {
  "use strict";

  var deps = null;
  var state = {
    nodesLoaded: false,
    backendsLoaded: false,
    nodesError: null,
    backendsError: null,
    discoveryFailed: false,
  };

  function init(injected) {
    deps = injected || {};
    state = {
      nodesLoaded: false,
      backendsLoaded: false,
      nodesError: null,
      backendsError: null,
      discoveryFailed: false,
    };
  }

  function requireDeps() {
    if (!deps) throw new Error("systemResources controller: init(deps) required");
    return deps;
  }

  function client() {
    var d = requireDeps();
    return d.client && d.client.systemEndpoints ? d.client.systemEndpoints : null;
  }

  function element(id) {
    var d = requireDeps();
    return d.document ? d.document.getElementById(id) : null;
  }

  function translate(key, vars) {
    var d = requireDeps();
    return d.translate ? d.translate(key, vars) : key;
  }

  function escapeHtml(value) {
    var d = requireDeps();
    return d.escapeHtml ? d.escapeHtml(value) : String(value == null ? "" : value);
  }

  function messageOf(error) {
    return error && error.message != null ? String(error.message) : String(error || "");
  }

  // ── cache writers (single owner) ─────────────────────────────────────
  function publishNodes(list) {
    nodesCache = list;
  }

  function publishBackends(list) {
    backendsCache = list;
  }

  function publishDiscovery(map) {
    softwareDiscoveryCache = map;
  }

  // ── node panel ───────────────────────────────────────────────────────
  function loadNodes() {
    var list = element("sysres-node-list");
    if (!list) return Promise.resolve(undefined);
    var endpoints = client();
    if (!endpoints) {
      return Promise.reject(new Error("systemResources controller: systemEndpoints missing"));
    }
    return endpoints.fetchNodes().then(function (data) {
      publishNodes((data && data.nodes) || []);
      state.nodesLoaded = true;
      state.nodesError = null;
      renderNodes();
      return undefined;
    }, function (error) {
      // Legacy swallowed the failure: clear the cache but never reject (the
      // node pane is informational and polling must keep going).
      publishNodes([]);
      state.nodesLoaded = true;
      state.nodesError = error;
      var target = element("sysres-node-list");
      if (target) {
        if (messageOf(error).indexOf("not configured") >= 0) {
          target.innerHTML = '<div class="muted">' + escapeHtml(translate("nodes.empty")) + '</div>';
        } else {
          target.innerHTML = '<div class="node-error">' +
            escapeHtml(translate("nodes.load_error") + ": " + messageOf(error)) + '</div>';
        }
      }
      return undefined;
    });
  }

  function renderNodes() {
    var list = element("sysres-node-list");
    if (!list) return;
    list.innerHTML = "";
    if (!nodesCache.length) {
      list.innerHTML = '<div class="muted">' + escapeHtml(translate("nodes.empty")) + '</div>';
      return;
    }
    var doc = deps.document;
    nodesCache.forEach(function (node) {
      var row = doc.createElement("div");
      row.className = "node-row" + (node.status === "offline" ? " offline" : "");
      var dot = doc.createElement("span");
      dot.className = "node-dot " + (node.status || "offline");
      var info = doc.createElement("div");
      info.className = "node-info";
      var name = doc.createElement("div");
      name.className = "node-name";
      name.textContent = (node.name || "--") + "  " + (node.host || "");
      var meta = doc.createElement("div");
      meta.className = "node-meta";
      meta.textContent = translate("nodes.status." + (node.status || "offline")) + " · " +
        (node.running_jobs || 0) + "/" + (node.max_jobs || 0) + " · DISK " +
        (node.disk_usage_pct || 0) + "%";
      info.appendChild(name);
      info.appendChild(meta);
      if (node.error) {
        var err = doc.createElement("div");
        err.className = "node-error";
        err.textContent = node.error;
        info.appendChild(err);
      }
      var actions = doc.createElement("div");
      actions.className = "node-actions";
      var ping = doc.createElement("button");
      ping.className = "node-btn";
      ping.textContent = translate("nodes.ping");
      ping.addEventListener("click", function (event) {
        event.stopPropagation();
        if (deps.onPingNode) deps.onPingNode(node.name);
      });
      actions.appendChild(ping);
      row.appendChild(dot);
      row.appendChild(info);
      row.appendChild(actions);
      list.appendChild(row);
    });
  }

  // ── backend panel ────────────────────────────────────────────────────
  function loadBackends() {
    var list = element("sysres-backend-list");
    if (!list) return Promise.resolve(undefined);
    var endpoints = client();
    if (!endpoints) {
      return Promise.reject(new Error("systemResources controller: systemEndpoints missing"));
    }
    return endpoints.fetchBackends().then(function (data) {
      publishBackends((data && data.backends) || []);
      state.backendsError = null;
      return true;
    }, function (error) {
      publishBackends([]);
      state.backendsLoaded = true;
      state.backendsError = error;
      var target = element("sysres-backend-list");
      if (target) {
        target.innerHTML = '<div class="node-error">' + escapeHtml(translate("backend.load_error")) + '</div>';
      }
      return false;
    }).then(function (ok) {
      if (!ok) return undefined;
      return endpoints.fetchSoftwareDiscovery().then(function (ddata) {
        if (ddata) {
          var map = {};
          (ddata.software || []).forEach(function (sw) {
            if (sw && sw.name) map[String(sw.name)] = sw;
          });
          publishDiscovery(map);
          state.discoveryFailed = false;
        }
      }, function () {
        // Silent: version badges still render without discovery.
        state.discoveryFailed = true;
      }).then(function () {
        state.backendsLoaded = true;
        renderBackends();
        return undefined;
      });
    });
  }

  function renderBackends() {
    var list = element("sysres-backend-list");
    if (!list) return;
    list.innerHTML = "";
    if (!backendsCache.length) {
      list.innerHTML = '<div class="muted">' + escapeHtml(translate("backend.empty")) + '</div>';
      return;
    }
    var doc = deps.document;
    backendsCache.forEach(function (b) {
      var name = String(b.name || "");
      var discovery = softwareDiscoveryCache[name] || null;
      var remoteNodes = Array.isArray(b.remote_nodes) ? b.remote_nodes : [];
      var isRemote = b.remote_available === true && remoteNodes.length > 0;
      var effective = b.available || isRemote;
      var version = String(b.version || b.remote_version || (discovery && discovery.version) || "").trim();
      var resolvedPath = String(b.path || b.remote_path || (discovery && discovery.resolved) || "").trim();

      var row = doc.createElement("div");
      row.className = "node-row" + (effective ? "" : " offline");

      var dot = doc.createElement("span");
      dot.className = "node-dot " + (effective ? "online" : "offline");

      var info = doc.createElement("div");
      info.className = "node-info";

      var nameEl = doc.createElement("div");
      nameEl.className = "node-name";
      nameEl.textContent = name || "--";
      if (version) {
        var badge = doc.createElement("span");
        badge.className = "backend-version";
        badge.title = translate("backend.version");
        badge.textContent = version;
        nameEl.appendChild(badge);
      }
      if (isRemote) {
        var rbadge = doc.createElement("span");
        rbadge.className = "backend-version";
        rbadge.title = remoteNodes.join(", ");
        rbadge.textContent = translate("backend.remote") + ": " + remoteNodes.join(", ");
        nameEl.appendChild(rbadge);
      }
      if (discovery && discovery.multiple === true && Array.isArray(discovery.candidates) && discovery.candidates.length) {
        nameEl.appendChild(buildMultiInstallDetails(discovery, resolvedPath));
      }
      info.appendChild(nameEl);

      if (resolvedPath) {
        var meta = doc.createElement("div");
        meta.className = "node-meta backend-path";
        meta.textContent = resolvedPath;
        info.appendChild(meta);
      }

      row.appendChild(dot);
      row.appendChild(info);
      list.appendChild(row);
    });
  }

  function buildMultiInstallDetails(discovery, resolvedPath) {
    var doc = deps.document;
    var details = doc.createElement("details");
    details.className = "backend-multi";
    var summary = doc.createElement("summary");
    summary.title = translate("backend.multi_badge_title");
    summary.textContent = "⚠";
    details.appendChild(summary);

    var body = doc.createElement("div");
    body.className = "backend-multi-detail";
    discovery.candidates.forEach(function (cand) {
      var line = doc.createElement("div");
      var candPath = String((cand && cand.path) || "");
      var isActive = !!resolvedPath && candPath === resolvedPath;
      line.className = "backend-candidate" + (isActive ? " active" : "");
      var text = candPath || "--";
      var candVersion = String((cand && cand.version) || "").trim();
      if (candVersion) text += " · v" + candVersion;
      var candSource = String((cand && cand.source) || "").trim();
      if (candSource) text += " · " + candSource;
      line.textContent = text;
      if (isActive) {
        var tag = doc.createElement("span");
        tag.className = "backend-candidate-tag";
        tag.textContent = translate("backend.active");
        line.appendChild(tag);
      }
      body.appendChild(line);
    });
    var hint = doc.createElement("div");
    hint.className = "backend-multi-hint";
    hint.textContent = translate("backend.multi_install", { name: String(discovery.name || "") });
    body.appendChild(hint);
    details.appendChild(body);
    return details;
  }

  function getState() {
    return {
      nodesLoaded: state.nodesLoaded,
      backendsLoaded: state.backendsLoaded,
      nodesError: state.nodesError,
      backendsError: state.backendsError,
      discoveryFailed: state.discoveryFailed,
    };
  }

  return {
    init: init,
    loadNodes: loadNodes,
    loadBackends: loadBackends,
    renderNodes: renderNodes,
    renderBackends: renderBackends,
    refresh: function () { return Promise.all([loadNodes(), loadBackends()]); },
    getState: getState,
  };
})();
