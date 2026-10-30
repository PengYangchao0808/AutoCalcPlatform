/*
 * ACP Next — reaction-network panel (UI-04, read-only).
 *
 * The only reaction-network data the platform still serves is the legacy
 * mechanism-study report (the `mechanism` workflow is retired):
 *   GET /api/v1/mechanism-studies?job_id=...   -> studies for one task
 *   GET /api/v1/mechanism-studies/{id}/report  -> { reaction_network, ... }
 *
 * This module renders that projection as a client-side SVG (no graph library,
 * no server depiction route exists) and nothing else:
 *   - no chemistry is inferred here: nodes/edges/barriers are rendered verbatim;
 *   - a missing study, a missing network or an empty node list is an explicit
 *     empty state that says where the data would come from;
 *   - node selection publishes `networkNodeId` through WorkspaceContext, edge
 *     selection only shows the frozen step facts.
 *
 * Shell wiring: descriptor provides `network.read`, binds `networkNodeId`;
 * `ACPShell.commands.openNetwork(ref)` resolves the panel.
 */
(function () {
  "use strict";

  var MODULE_ID = "reaction-network";
  var COL_W = 220;
  var ROW_H = 96;
  var NODE_W = 136;
  var NODE_H = 52;
  var PAD = 24;

  var DESCRIPTOR = {
    id: MODULE_ID,
    interfaceVersion: 1,
    version: "0.1.0",
    provides: ["network.read"],
    binds: ["networkNodeId"],
    capabilities: { requires: [], provides: ["network.read"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.network.errorTitle", messageKey: "next.network.errorMessage" },
  };

  var ZH = {
    "next.network.title": "反应网络",
    "next.network.empty": "选择任务后查看反应网络。",
    "next.network.empty_hint": "反应网络来自历史 mechanism 研究；现役工作流不产出该数据。",
    "next.network.loading": "正在读取反应网络…",
    "next.network.no_study": "该任务没有关联的 mechanism 研究。",
    "next.network.no_network": "研究报告未包含 reaction_network。",
    "next.network.error": "反应网络加载失败：{message}",
    "next.network.retry": "重试",
    "next.network.node": "节点",
    "next.network.role": "角色",
    "next.network.energy": "能量",
    "next.network.step": "步骤",
    "next.network.barrier": "正向能垒",
    "next.network.fidelity": "保真度",
    "next.network.status": "状态",
    "next.network.hint": "点击节点或箭头查看冻结字段。",
    "next.network.errorTitle": "反应网络不可用",
    "next.network.errorMessage": "reaction-network 模块加载失败，请查看控制台。",
  };

  function t(key, vars) {
    var shell = typeof window !== "undefined" && window.ACPShell ? window.ACPShell.i18n : null;
    if (shell && typeof shell.t === "function") {
      var translated = shell.t(key, vars);
      if (translated && translated !== key) return translated;
    }
    var value = Object.prototype.hasOwnProperty.call(ZH, key) ? ZH[key] : key;
    if (vars) {
      Object.keys(vars).forEach(function (name) {
        value = value.split("{" + name + "}").join(String(vars[name]));
      });
    }
    return value;
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function el(doc, tag, cls, text) {
    var node = doc.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function describeError(error) {
    if (!error) return "unknown error";
    var body = error.body;
    if (body && typeof body === "object") {
      var detail = body.detail;
      if (detail && typeof detail === "object" && detail.message) {
        return (detail.code ? detail.code + " — " : "") + detail.message;
      }
      if (typeof detail === "string") return detail;
    }
    return error.message ? String(error.message) : String(error);
  }

  function numberText(value, digits) {
    var n = Number(value);
    if (!isFinite(n)) return "—";
    return n.toFixed(digits == null ? 2 : digits);
  }

  function roleKind(role) {
    var text = String(role || "").toLowerCase();
    if (text.indexOf("ts") !== -1 || text.indexOf("transition") !== -1) return "ts";
    if (text.indexOf("react") !== -1 || text.indexOf("start") !== -1) return "reactant";
    if (text.indexOf("prod") !== -1 || text.indexOf("end") !== -1) return "product";
    if (text.indexOf("inter") !== -1 || text.indexOf("int") !== -1) return "intermediate";
    return "unknown";
  }

  /* ── pure layout ───────────────────────────────────────────────────── */

  /**
   * BFS depth layout. Cycles / disconnected nodes degrade gracefully: roots are
   * the indegree-0 nodes (or the first node when the graph is cyclic), and any
   * node the BFS never reached is placed at depth 0 in declaration order.
   */
  function layout(network) {
    var nodes = (network && Array.isArray(network.nodes)) ? network.nodes.slice() : [];
    var edges = (network && Array.isArray(network.edges)) ? network.edges.slice() : [];
    var byId = Object.create(null);
    var order = [];
    nodes.forEach(function (node, index) {
      var id = String(node && node.state_id != null ? node.state_id : "node_" + index);
      byId[id] = node;
      order.push(id);
    });
    var incoming = Object.create(null);
    var outgoing = Object.create(null);
    order.forEach(function (id) { incoming[id] = 0; outgoing[id] = []; });
    edges.forEach(function (edge) {
      var from = String(edge && edge.source_state_id);
      var to = String(edge && edge.sink_state_id);
      if (!byId[from] || !byId[to]) return;
      incoming[to] += 1;
      outgoing[from].push(to);
    });
    var depth = Object.create(null);
    var queue = [];
    order.forEach(function (id) { if (!incoming[id]) queue.push(id); });
    if (!queue.length && order.length) queue.push(order[0]);
    queue.forEach(function (id) { depth[id] = 0; });
    while (queue.length) {
      var current = queue.shift();
      (outgoing[current] || []).forEach(function (next) {
        var candidate = depth[current] + 1;
        if (depth[next] == null || candidate < depth[next]) {
          depth[next] = candidate;
          queue.push(next);
        }
      });
    }
    order.forEach(function (id) { if (depth[id] == null) depth[id] = 0; });
    var rows = Object.create(null);
    var positions = {};
    order.forEach(function (id) {
      var d = depth[id];
      rows[d] = (rows[d] || 0) + 1;
      positions[id] = {
        id: id,
        depth: d,
        x: PAD + d * COL_W,
        y: PAD + (rows[d] - 1) * ROW_H,
        w: NODE_W,
        h: NODE_H,
        node: byId[id],
      };
    });
    var maxDepth = 0;
    order.forEach(function (id) { if (depth[id] > maxDepth) maxDepth = depth[id]; });
    var maxRows = 0;
    Object.keys(rows).forEach(function (d) { if (rows[d] > maxRows) maxRows = rows[d]; });
    return {
      nodes: order.map(function (id) { return positions[id]; }),
      edges: edges.map(function (edge, index) {
        return { edge: edge, index: index, from: positions[String(edge.source_state_id)], to: positions[String(edge.sink_state_id)] };
      }).filter(function (item) { return item.from && item.to; }),
      width: PAD * 2 + maxDepth * COL_W + NODE_W,
      height: PAD * 2 + Math.max(1, maxRows) * ROW_H - (ROW_H - NODE_H),
    };
  }

  /** Deterministic SVG string; `options.selectedNodeId/selectedEdgeIndex`. */
  function buildSvg(network, options) {
    options = options || {};
    var graph = layout(network);
    var parts = [];
    parts.push('<svg class="acp-net-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' +
      String(Math.max(graph.width, 320)) + " " + String(Math.max(graph.height, 200)) + '" role="img">');
    parts.push('<defs><marker id="acp-net-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">' +
      '<path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor"/></marker></defs>');

    graph.edges.forEach(function (item) {
      var from = item.from;
      var to = item.to;
      var x1 = from.x + from.w;
      var y1 = from.y + from.h / 2;
      var x2 = to.x;
      var y2 = to.y + to.h / 2;
      var mid = x1 + (x2 - x1) / 2;
      var selected = options.selectedEdgeIndex === item.index ? " is-selected" : "";
      parts.push('<g class="acp-net-edge' + selected + '" data-edge="' + String(item.index) + '">');
      parts.push('<path d="M ' + String(x1) + " " + String(y1) + " C " + String(mid) + " " + String(y1) +
        ", " + String(mid) + " " + String(y2) + ", " + String(x2) + " " + String(y2) +
        '" fill="none" stroke="currentColor" stroke-width="1.4" marker-end="url(#acp-net-arrow)"/>');
      var label = barrierLabel(item.edge);
      if (label) {
        parts.push('<text class="acp-net-edge-label" x="' + String(mid) + '" y="' + String((y1 + y2) / 2 - 6) +
          '" text-anchor="middle">' + esc(label) + "</text>");
      }
      parts.push("</g>");
    });

    graph.nodes.forEach(function (item) {
      var node = item.node || {};
      var kind = roleKind(node.role);
      var selected = options.selectedNodeId && String(options.selectedNodeId) === item.id ? " is-selected" : "";
      parts.push('<g class="acp-net-node is-' + kind + selected + '" data-node="' + esc(item.id) + '">');
      if (kind === "ts") {
        var cx = item.x + item.w / 2;
        var cy = item.y + item.h / 2;
        var points = [
          String(cx) + "," + String(item.y),
          String(item.x + item.w) + "," + String(cy),
          String(cx) + "," + String(item.y + item.h),
          String(item.x) + "," + String(cy),
        ].join(" ");
        parts.push('<polygon points="' + points + '" class="acp-net-shape"/>');
      } else {
        parts.push('<rect x="' + String(item.x) + '" y="' + String(item.y) + '" width="' + String(item.w) +
          '" height="' + String(item.h) + '" rx="7" class="acp-net-shape"/>');
      }
      parts.push('<text class="acp-net-node-label" x="' + String(item.x + item.w / 2) + '" y="' +
        String(item.y + item.h / 2 - 2) + '" text-anchor="middle">' + esc(node.label || item.id) + "</text>");
      var energy = node.energy_hartree != null ? numberText(node.energy_hartree, 4) + " Ha" : String(node.role || "");
      parts.push('<text class="acp-net-node-meta" x="' + String(item.x + item.w / 2) + '" y="' +
        String(item.y + item.h / 2 + 13) + '" text-anchor="middle">' + esc(energy) + "</text>");
      parts.push("</g>");
    });

    parts.push("</svg>");
    return { svg: parts.join(""), graph: graph };
  }

  function barrierLabel(edge) {
    var barrier = edge && edge.barrier_forward_kcal;
    if (barrier == null || !isFinite(Number(barrier))) return "";
    return numberText(barrier, 1) + " kcal/mol";
  }

  /* ── module ────────────────────────────────────────────────────────── */

  function createReactionNetwork(deps) {
    deps = deps || {};
    var doc = deps.doc || (typeof document !== "undefined" ? document : null);
    var requestFn = deps.request || (typeof window !== "undefined" && window.ACPClient && window.ACPClient.transport
      ? function (path, opts) { return window.ACPClient.transport.request(path, opts); }
      : null);
    var context = deps.context || null;

    var state = {
      generation: 0,
      mounted: false,
      disposed: false,
      suspended: false,
      host: null,
      projectId: "",
      taskRef: null,
      studyId: "",
      network: null,
      selectedNodeId: null,
      selectedEdgeIndex: null,
      ui: {},
      token: 0,
    };
    var cleanup = [];

    function on(node, type, handler) {
      if (!node || typeof node.addEventListener !== "function") return;
      node.addEventListener(type, handler);
      cleanup.push(function () {
        if (typeof node.removeEventListener === "function") node.removeEventListener(type, handler);
      });
    }

    function buildDom() {
      var root = el(doc, "div", "acp-net");
      root.setAttribute("data-acp-module-state", "empty");
      root.setAttribute("data-module", MODULE_ID);

      var head = el(doc, "header", "acp-net-head");
      var sub = el(doc, "p", "acp-net-sub", "");
      head.appendChild(sub);
      head.hidden = true;
      root.appendChild(head);

      var stateNode = el(doc, "p", "acp-net-state", t("next.network.empty"));
      stateNode.setAttribute("aria-live", "polite");
      root.appendChild(stateNode);

      var canvas = el(doc, "div", "acp-net-canvas");
      root.appendChild(canvas);

      var detail = el(doc, "div", "acp-net-detail");
      detail.hidden = true;
      root.appendChild(detail);

      var hint = el(doc, "p", "acp-net-hint", t("next.network.hint"));
      hint.hidden = true;
      root.appendChild(hint);

      state.host.replaceChildren(root);
      state.ui = { root: root, head: head, sub: sub, canvas: canvas, stateNode: stateNode, detail: detail, hint: hint };
      on(canvas, "click", onCanvasClick);
    }

    function setModuleState(name) {
      if (state.ui.root) state.ui.root.setAttribute("data-acp-module-state", name);
    }

    function showEmpty(message, hint) {
      state.network = null;
      state.selectedNodeId = null;
      state.selectedEdgeIndex = null;
      state.ui.canvas.replaceChildren();
      state.ui.detail.hidden = true;
      state.ui.hint.hidden = true;
      state.ui.head.hidden = true;
      state.ui.stateNode.hidden = false;
      state.ui.stateNode.textContent = message;
      if (hint) {
        var extra = el(doc, "span", "acp-net-state-hint", " " + hint);
        state.ui.stateNode.appendChild(extra);
      }
      setModuleState("empty");
    }

    function showLoading() {
      state.ui.canvas.replaceChildren();
      state.ui.detail.hidden = true;
      state.ui.hint.hidden = true;
      state.ui.head.hidden = true;
      state.ui.stateNode.hidden = false;
      state.ui.stateNode.textContent = t("next.network.loading");
      setModuleState("loading");
    }

    function showError(error) {
      state.ui.canvas.replaceChildren();
      state.ui.detail.hidden = true;
      state.ui.hint.hidden = true;
      state.ui.head.hidden = true;
      state.ui.stateNode.hidden = false;
      state.ui.stateNode.textContent = t("next.network.error", { message: describeError(error) });
      var retry = el(doc, "button", "acp-btn acp-btn--ghost", t("next.network.retry"));
      retry.type = "button";
      on(retry, "click", function () { if (state.taskRef) loadForTask(state.taskRef); });
      state.ui.stateNode.appendChild(retry);
      setModuleState("error");
    }

    function renderNetwork() {
      if (!state.network) return;
      state.ui.stateNode.hidden = true;
      state.ui.sub.textContent = state.studyId ? "study " + state.studyId : "";
      state.ui.head.hidden = !state.studyId;
      var built = buildSvg(state.network, {
        selectedNodeId: state.selectedNodeId,
        selectedEdgeIndex: state.selectedEdgeIndex,
      });
      state.ui.canvas.innerHTML = built.svg;
      state.ui.hint.hidden = false;
      setModuleState("ready");
    }

    function onCanvasClick(event) {
      var target = event && event.target;
      if (!target) return;
      var nodeEl = typeof target.closest === "function" ? target.closest("[data-node]") : target;
      if (nodeEl && nodeEl.getAttribute && nodeEl.getAttribute("data-node") != null) {
        selectNode(nodeEl.getAttribute("data-node"));
        return;
      }
      var edgeEl = typeof target.closest === "function" ? target.closest("[data-edge]") : target;
      if (edgeEl && edgeEl.getAttribute && edgeEl.getAttribute("data-edge") != null) {
        selectEdge(Number(edgeEl.getAttribute("data-edge")));
      }
    }

    function nodeById(id) {
      var nodes = (state.network && state.network.nodes) || [];
      for (var i = 0; i < nodes.length; i++) {
        if (String(nodes[i].state_id) === String(id)) return nodes[i];
      }
      return null;
    }

    function selectNode(id) {
      state.selectedNodeId = String(id);
      state.selectedEdgeIndex = null;
      renderNetwork();
      renderNodeDetail(nodeById(id));
      if (context && typeof context.setFocus === "function") {
        try { context.setFocus({ networkNodeId: String(id) }, MODULE_ID); } catch (e) { /* read-only host */ }
      }
    }

    function selectEdge(index) {
      var edges = (state.network && state.network.edges) || [];
      state.selectedEdgeIndex = index;
      state.selectedNodeId = null;
      renderNetwork();
      renderEdgeDetail(edges[index]);
    }

    function detailRow(container, label, value) {
      var row = el(doc, "div", "acp-net-detail-row");
      row.appendChild(el(doc, "span", "acp-net-detail-label", label));
      row.appendChild(el(doc, "strong", "acp-net-detail-value", value == null || value === "" ? "—" : String(value)));
      container.appendChild(row);
    }

    function openDetail(title) {
      var container = state.ui.detail;
      container.hidden = false;
      container.replaceChildren();
      container.appendChild(el(doc, "h4", "acp-net-detail-title", title));
      return container;
    }

    function renderNodeDetail(node) {
      if (!node) return;
      var container = openDetail(t("next.network.node") + " " + String(node.state_id));
      detailRow(container, t("next.network.role"), node.role);
      detailRow(container, t("next.network.energy"), node.energy_hartree != null ? numberText(node.energy_hartree, 6) + " Ha" : "");
    }

    function renderEdgeDetail(edge) {
      if (!edge) return;
      var container = openDetail(t("next.network.step") + " " + String(edge.step_id || edge.ts_id || ""));
      detailRow(container, t("next.network.barrier"), edge.barrier_forward_kcal != null ? numberText(edge.barrier_forward_kcal, 2) + " kcal/mol" : "");
      detailRow(container, t("next.network.fidelity"), edge.fidelity);
      detailRow(container, t("next.network.status"), edge.status);
    }

    function applyNetwork(network) {
      var nodes = (network && Array.isArray(network.nodes)) ? network.nodes : [];
      if (!nodes.length) {
        showEmpty(t("next.network.no_network"), t("next.network.empty_hint"));
        return;
      }
      state.network = network;
      state.selectedNodeId = null;
      state.selectedEdgeIndex = null;
      renderNetwork();
      state.ui.detail.hidden = true;
    }

    function loadForTask(ref) {
      var jobId = ref && ref.jobId ? String(ref.jobId) : "";
      if (!jobId) {
        showEmpty(t("next.network.empty"), t("next.network.empty_hint"));
        return Promise.resolve();
      }
      var token = state.token + 1;
      state.token = token;
      var gen = state.generation;
      showLoading();
      return requestFn("/api/v1/mechanism-studies?job_id=" + encodeURIComponent(jobId), { method: "GET" })
        .then(function (studies) {
          if (!current(gen, token)) return null;
          var list = Array.isArray(studies) ? studies : (studies && studies.studies) || [];
          if (!list.length) {
            showEmpty(t("next.network.no_study"), t("next.network.empty_hint"));
            return null;
          }
          var study = list[0] || {};
          var studyId = String(study.study_id || study.id || "");
          state.studyId = studyId;
          if (!studyId) {
            showEmpty(t("next.network.no_study"), t("next.network.empty_hint"));
            return null;
          }
          return requestFn("/api/v1/mechanism-studies/" + encodeURIComponent(studyId) + "/report", { method: "GET" })
            .then(function (report) {
              if (!current(gen, token)) return;
              applyNetwork(report && report.reaction_network);
            });
        })
        .catch(function (error) {
          if (!current(gen, token)) return;
          showError(error);
        });
    }

    function current(gen, token) {
      return !state.disposed && state.mounted && state.generation === gen && state.token === token;
    }

    function applySnapshot(snapshot) {
      var ref = snapshot && snapshot.taskRef ? snapshot.taskRef : null;
      var projectId = snapshot && snapshot.projectId ? String(snapshot.projectId) : "";
      var nextJob = ref && ref.jobId ? String(ref.jobId) : "";
      var previousJob = state.taskRef && state.taskRef.jobId ? String(state.taskRef.jobId) : "";
      state.projectId = projectId;
      state.taskRef = nextJob ? { jobId: nextJob, attempt: ref.attempt == null ? null : Number(ref.attempt) } : null;
      if (nextJob !== previousJob) {
        state.studyId = "";
        state.network = null;
        if (!nextJob) {
          showEmpty(t("next.network.empty"), t("next.network.empty_hint"));
          return;
        }
        loadForTask(state.taskRef);
      }
    }

    function mount(hostEl, api) {
      api = api || {};
      hostEl = hostEl || (api.panel && api.panel.bodyEl);
      if (!hostEl) throw new Error("reaction-network: mount host missing");
      if (!doc) throw new Error("reaction-network: document missing");
      if (!requestFn) throw new Error("reaction-network: ACPClient.transport missing");
      state.host = hostEl;
      state.generation += 1;
      state.mounted = true;
      state.disposed = false;
      var ctx = api.context || context;
      buildDom();
      showEmpty(t("next.network.empty"), t("next.network.empty_hint"));
      if (ctx && typeof ctx.getSnapshot === "function") applySnapshot(ctx.getSnapshot());
      if (ctx && typeof ctx.subscribe === "function") {
        var unsubscribe = ctx.subscribe(function (snapshot) {
          if (state.disposed) return;
          applySnapshot(snapshot);
        });
        cleanup.push(unsubscribe);
      }
      var langHandler = function () {
        if (state.disposed) return;
        if (state.network) renderNetwork();
        else if (state.taskRef) loadForTask(state.taskRef);
        else showEmpty(t("next.network.empty"), t("next.network.empty_hint"));
      };
      if (doc.addEventListener) {
        doc.addEventListener("acp:langchange", langHandler);
        cleanup.push(function () { doc.removeEventListener("acp:langchange", langHandler); });
      }
      return Promise.resolve();
    }

    function updateContext(snapshot) {
      if (state.disposed || !state.mounted) return;
      applySnapshot(snapshot);
    }

    function openNetwork(ref) {
      if (!ref) return;
      if (ref.nodeId && state.network) selectNode(ref.nodeId);
    }

    function suspend() { state.suspended = true; }

    function resume() { state.suspended = false; }

    function dispose() {
      if (state.disposed) return;
      state.disposed = true;
      state.mounted = false;
      state.token += 1;
      cleanup.splice(0).forEach(function (fn) {
        try { fn(); } catch (e) { /* listener already gone */ }
      });
      if (state.host && typeof state.host.replaceChildren === "function") {
        try { state.host.replaceChildren(); } catch (e) { /* host gone */ }
      }
      state.host = null;
      state.ui = {};
      state.network = null;
    }

    return {
      mount: mount,
      updateContext: updateContext,
      openNetwork: openNetwork,
      suspend: suspend,
      resume: resume,
      dispose: dispose,
    };
  }

  window.ACPNextReactionNetwork = {
    layout: layout,
    buildSvg: buildSvg,
    roleKind: roleKind,
  };

  window.ACPNextModules = window.ACPNextModules || {};
  window.ACPNextModules[MODULE_ID] = {
    descriptor: DESCRIPTOR,
    factory: createReactionNetwork,
  };
})();
