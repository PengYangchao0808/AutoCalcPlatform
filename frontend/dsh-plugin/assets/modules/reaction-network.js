/* file: js/modules/reaction-network/module.js */
/*
 * ACP Next — reaction-network panel (UI-04, read-only).
 *
 * PRIMARY SOURCE (project chemistry network, contract
 * docs/reference/next-change/ACP_Chemistry_Network_Contract_20261103.md §4/§6):
 *   GET /api/v2/projects/{projectId}/chemistry-network?limit=200[&cursor=...]
 * Nodes are STRUCTURE VERSIONS keyed by `content_checksum`, not molecules; the
 * identity banner is driven by `identity_kind` and must not upgrade the claim.
 * Plug in `deps.networkSource.fetchNetwork({projectId, projectName, cursor,
 * limit})` to replace the HTTP read (same wire payload) — no other seam.
 *
 * LEGACY FALLBACK — only when the workspace has NO projectId (a standalone task
 * view). The `mechanism` workflow is retired, so this is historical display:
 *   GET /api/v1/mechanism-studies?job_id=...   -> studies for one task
 *   GET /api/v1/mechanism-studies/{id}/report  -> { reaction_network, ... }
 * That path is labelled `source: "legacy_mechanism"` in the banner.
 *
 * Cytoscape.js renders the real node/edge graph; cytoscape-elk + elkjs provide
 * deterministic left-to-right layered positioning. Isolated nodes are excluded
 * from ELK and placed in a deterministic grid below the connected components.
 * A compact SVG/preset fallback remains for a missing browser dependency:
 *   - no chemistry is inferred here: nodes/edges/names are rendered verbatim,
 *     unknown relations are dropped, a node without `node_id` is dropped
 *     (an identity is never invented);
 *   - four states are distinct: 未连接 (transport/503), 空态, 就绪, 错误+重试.
 *     A rejected read is NEVER rendered as an empty success;
 *   - node click publishes `networkNodeId` + navigates via
 *     `commands.openStructure`; edge click navigates via `commands.openTask`.
 *     Every command call is guarded — a handler never throws.
 *   - read-only: no submit, no candidate write, no polling timer.
 *
 * Shell wiring: descriptor provides `network.read`, binds `networkNodeId`;
 * `ACPShell.commands.openNetwork(ref)` resolves the panel.
 */
(function () {
  "use strict";

  var MODULE_ID = "reaction-network";
  var LIMIT = 200;
  var COL_W = 220;
  var ROW_H = 96;
  var NODE_W = 136;
  var NODE_H = 52;
  var PAD = 24;
  var GRID_COL_W = 144;
  var GRID_ROW_H = 84;
  var GRID_GAP = 112;
  var FIT_PAD = 40;
  var elkRegistration = { cytoscape: null, extension: null };

  // Stable input order + a fixed seed make ELK repeatable. Connected components
  // are separated by ELK itself; no synthetic component edges are introduced.
  var ELK_OPTIONS = {
    "elk.algorithm": "layered",
    "elk.direction": "RIGHT",
    "elk.separateConnectedComponents": true,
    "elk.randomSeed": 1,
    "elk.spacing.componentComponent": 96,
    "elk.spacing.nodeNode": 42,
    "elk.layered.spacing.nodeNodeBetweenLayers": 92,
    "elk.layered.spacing.edgeNodeBetweenLayers": 32,
    "elk.layered.spacing.edgeEdgeBetweenLayers": 24,
  };

  var DESCRIPTOR = {
    id: MODULE_ID,
    interfaceVersion: 1,
    version: "0.2.0",
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
    "next.network.empty": "选择项目后查看项目化学网络。",
    "next.network.empty_hint": "反应网络来自历史 mechanism 研究；现役工作流不产出该数据。",
    "next.network.empty.project": "该项目暂无结构",
    "next.network.loading": "正在读取反应网络…",
    "next.network.disconnected": "未连接：{message}",
    "next.network.truncated": "已截断",
    "next.network.skipped": "跳过 {count} 条记录",
    "next.network.source.structure_content_v1": "结构版本网络 · 未做分子归集",
    "next.network.source.molecule_v1": "分子网络",
    "next.network.source.mixed": "分子网络（含未识别结构）",
    "next.network.source.legacy_mechanism": "遗留机理记录",
    "next.network.identified": "{count} 个已识别",
    "next.network.unidentified": "{count} 个未识别",
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
    "next.network.formula": "化学式",
    "next.network.smiles": "规范 SMILES",
    "next.network.atoms": "原子数",
    "next.network.tasks": "引用任务",
    "next.network.edge_from": "上游任务",
    "next.network.edge_to": "下游任务",
    "next.network.fit": "适应画布",
    "next.network.relayout": "重新布局",
    "next.network.zoom_in": "放大",
    "next.network.zoom_out": "缩小",
    "next.network.hint": "滚轮缩放，拖动空白区域平移；点击节点或箭头查看冻结字段。",
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
   * Deterministic centre positions for isolated nodes. The column count depends
   * only on the number of nodes, never on viewport size or Math.random().
   */
  function isolatedGrid(ids, origin) {
    ids = Array.isArray(ids) ? ids : [];
    origin = origin || {};
    var startX = Number(origin.x) || 0;
    var startY = Number(origin.y) || 0;
    var columns = Math.max(1, Math.ceil(Math.sqrt(ids.length)));
    var positions = {};
    ids.forEach(function (id, index) {
      positions[id] = {
        x: startX + (index % columns) * GRID_COL_W,
        y: startY + Math.floor(index / columns) * GRID_ROW_H,
        column: index % columns,
        row: Math.floor(index / columns),
      };
    });
    return { positions: positions, columns: columns, rows: Math.ceil(ids.length / columns) };
  }

  function elkOptions() {
    var copy = {};
    Object.keys(ELK_OPTIONS).forEach(function (key) { copy[key] = ELK_OPTIONS[key]; });
    return copy;
  }

  /**
   * Single-component BFS fallback. Cycles degrade to declaration-order roots;
   * unknown endpoints are dropped. The outer layout owns component/grid bands.
   */
  function layoutComponent(network) {
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

  /**
   * Component-aware deterministic fallback/model. ELK replaces the connected
   * bands in the primary renderer; zero-degree nodes always use this grid.
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
    var adjacent = Object.create(null);
    var degree = Object.create(null);
    order.forEach(function (id) { adjacent[id] = []; degree[id] = 0; });
    var validEdges = [];
    edges.forEach(function (edge, index) {
      var from = String(edge && edge.source_state_id);
      var to = String(edge && edge.sink_state_id);
      if (!byId[from] || !byId[to]) return;
      adjacent[from].push(to);
      adjacent[to].push(from);
      degree[from] += 1;
      degree[to] += 1;
      validEdges.push({ edge: edge, index: index, fromId: from, toId: to });
    });

    var components = [];
    var visited = Object.create(null);
    order.forEach(function (id) {
      if (!degree[id] || visited[id]) return;
      var component = [];
      var queue = [id];
      visited[id] = true;
      while (queue.length) {
        var current = queue.shift();
        component.push(current);
        adjacent[current].forEach(function (next) {
          if (!visited[next]) { visited[next] = true; queue.push(next); }
        });
      }
      components.push(component);
    });

    var positions = {};
    var nextY = PAD;
    components.forEach(function (component, componentIndex) {
      var member = Object.create(null);
      component.forEach(function (id) { member[id] = true; });
      var subNetwork = {
        nodes: component.map(function (id) { return byId[id]; }),
        edges: validEdges.filter(function (item) { return member[item.fromId] && member[item.toId]; })
          .map(function (item) { return item.edge; }),
      };
      var sub = layoutComponent(subNetwork);
      sub.nodes.forEach(function (item) {
        item.y += nextY - PAD;
        item.component = componentIndex;
        item.isolated = false;
        positions[item.id] = item;
      });
      nextY += Math.max(ROW_H, sub.height);
    });

    var isolatedIds = order.filter(function (id) { return !degree[id]; });
    var grid = isolatedGrid(isolatedIds, { x: PAD, y: components.length ? nextY + GRID_GAP : PAD });
    isolatedIds.forEach(function (id) {
      var p = grid.positions[id];
      positions[id] = {
        id: id, depth: 0, component: null, isolated: true,
        x: p.x, y: p.y, w: NODE_W, h: NODE_H, node: byId[id],
      };
    });
    var maxRight = NODE_W + PAD * 2;
    var maxBottom = NODE_H + PAD * 2;
    order.forEach(function (id) {
      var item = positions[id];
      if (!item) return;
      maxRight = Math.max(maxRight, item.x + item.w + PAD);
      maxBottom = Math.max(maxBottom, item.y + item.h + PAD);
    });
    return {
      nodes: order.map(function (id) { return positions[id]; }),
      edges: validEdges.map(function (item) {
        return { edge: item.edge, index: item.index, from: positions[item.fromId], to: positions[item.toId] };
      }),
      isolated: isolatedIds,
      isolatedGrid: grid,
      width: maxRight,
      height: maxBottom,
    };
  }

  /** Cytoscape projection: identities and edges come only from the real graph.
   * Labels are plain text, never HTML. */
  function cytoscapeElements(network) {
    var graph = layout(network);
    var elements = graph.nodes.map(function (item) {
      var node = item.node || {};
      var kind = roleKind(node.role);
      return {
        group: "nodes",
        data: { id: item.id, nodeId: item.id, label: String(node.label || item.id), kind: kind },
        classes: "is-" + kind + (item.isolated ? " is-isolated" : ""),
        position: { x: item.x + item.w / 2, y: item.y + item.h / 2 },
      };
    });
    graph.edges.forEach(function (item) {
      elements.push({
        group: "edges",
        data: {
          id: "edge_" + String(item.index),
          source: item.from.id,
          target: item.to.id,
          edgeIndex: item.index,
          label: barrierLabel(item.edge),
        },
      });
    });
    return elements;
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

  /* ── wire → internal graph (contract §4) ───────────────────────────── */

  var RELATIONS = { lineage: true };
  // `mixed` = some structures carry a molecule identity, some only a content
  // hash (contract §4). It must stay visible — never folded into molecule_v1.
  var IDENTITY_KINDS = { structure_content_v1: true, molecule_v1: true, mixed: true, legacy_mechanism: true };

  function text(value) {
    return value == null ? "" : String(value);
  }

  function textList(value) {
    if (!Array.isArray(value)) return [];
    return value.filter(function (item) { return item != null && String(item) !== ""; })
      .map(function (item) { return String(item); });
  }

  /** Non-negative count from the wire, or null when the field is absent. */
  function countOrNull(value) {
    var n = Number(value);
    if (value == null || typeof value === "boolean" || !isFinite(n) || n < 0) return null;
    return Math.floor(n);
  }

  /**
   * Pure mapping of the project chemistry-network payload into the internal
   * renderer shape (`state_id`/`role`/`label`), keeping the identity fields so
   * a click can navigate. Discipline (contract §2/§3/§7):
   *   - `node_id` is the only identity: a node without it is dropped, never
   *     replaced by a name, formula, SMILES or index;
   *   - `relation` is a closed enum: an unknown relation is dropped, never
   *     guessed into an edge;
   *   - a lineage edge is only drawn when BOTH tasks resolve to a rendered
   *     node (i.e. the reference is persisted) — no speculative edges;
   *   - `canonical_smiles`/`inchikey` are display metadata only, never an
   *     identity key (contract §7.1/§7.2);
   *   - extra wire fields are ignored.
   */
  function mapWireNetwork(payload) {
    payload = payload && typeof payload === "object" ? payload : {};
    var identityKind = text(payload.identity_kind);
    var nodes = [];
    var byTask = Object.create(null);
    (Array.isArray(payload.nodes) ? payload.nodes : []).forEach(function (raw) {
      if (!raw || typeof raw !== "object") return;
      var nodeId = text(raw.node_id);
      if (!nodeId) return;
      var taskIds = textList(raw.task_ids);
      var names = textList(raw.molecule_names);
      var formula = text(raw.formula);
      var canonicalSmiles = text(raw.canonical_smiles);
      var nodeKind = text(raw.identity_kind) || identityKind;
      var label = names.length ? names[0] : (formula || "");
      // A molecule-identity node may have no display name and no formula; its
      // SMILES is the next honest display key before the raw node id.
      if (!label && nodeKind === "molecule_v1") label = canonicalSmiles;
      if (!label) label = nodeId;
      var node = {
        state_id: nodeId,
        node_id: nodeId,
        identity_kind: nodeKind,
        scheme: text(raw.scheme),
        task_ids: taskIds,
        molecule_names: names,
        canonical_smiles: canonicalSmiles,
        inchikey: text(raw.inchikey),
        formula: formula,
        charge: typeof raw.charge === "number" ? raw.charge : null,
        multiplicity: typeof raw.multiplicity === "number" ? raw.multiplicity : null,
        atom_count: typeof raw.atom_count === "number" ? raw.atom_count : null,
        role: "structure",
        label: label,
      };
      taskIds.forEach(function (taskId) { if (!byTask[taskId]) byTask[taskId] = nodeId; });
      nodes.push(node);
    });
    var edges = [];
    (Array.isArray(payload.edges) ? payload.edges : []).forEach(function (raw) {
      if (!raw || typeof raw !== "object") return;
      var relation = text(raw.relation);
      if (!RELATIONS[relation]) return;
      var fromTask = text(raw.from_task);
      var toTask = text(raw.to_task);
      if (!fromTask || !toTask) return;
      var fromNode = byTask[fromTask];
      var toNode = byTask[toTask];
      if (!fromNode || !toNode || fromNode === toNode) return;
      edges.push({
        source_state_id: fromNode,
        sink_state_id: toNode,
        relation: relation,
        from_task: fromTask,
        to_task: toTask,
        depth: typeof raw.depth === "number" ? raw.depth : 1,
      });
    });
    return {
      nodes: nodes,
      edges: edges,
      identity_kind: identityKind,
      // Identity-level counts (contract §4): only the mixed banner reports them.
      identified_nodes: countOrNull(payload.identified_nodes),
      unidentified_nodes: countOrNull(payload.unidentified_nodes),
      truncated: payload.truncated === true,
      next_cursor: payload.next_cursor == null ? null : String(payload.next_cursor),
      skipped: Array.isArray(payload.skipped) ? payload.skipped.slice() : [],
    };
  }

  /** Transport failure (no HTTP answer) or an explicit 503 → 未连接, not empty. */
  function isDisconnected(error) {
    if (!error) return false;
    var status = error.status != null ? Number(error.status)
      : (error.body && error.body.status != null ? Number(error.body.status) : NaN);
    if (status !== status) return true; // NaN: no HTTP response at all
    if (status === 0 || status === 503) return true;
    var code = typeof error.code === "string" ? error.code : "";
    return code === "unavailable" || code === "ECONNREFUSED" || code === "ENOTFOUND";
  }

  /* ── module ────────────────────────────────────────────────────────── */

  function createReactionNetwork(deps) {
    deps = deps || {};
    var doc = deps.doc || (typeof document !== "undefined" ? document : null);
    var requestFn = deps.request || (typeof window !== "undefined" && window.ACPClient && window.ACPClient.transport
      ? function (path, opts) { return window.ACPClient.transport.request(path, opts); }
      : null);
    var context = deps.context || null;
    // Optional adapter seam: same wire payload as the HTTP read.
    var networkSource = deps.networkSource || null;
    var commandsApi = deps.commands || null;
    var cytoscapeFn = deps.cytoscape || (typeof window !== "undefined" ? window.cytoscape : null);
    var cytoscapeElkExtension = deps.cytoscapeElk || (typeof window !== "undefined" ? window.cytoscapeElk : null);

    var state = {
      generation: 0,
      mounted: false,
      disposed: false,
      suspended: false,
      host: null,
      projectId: "",
      projectName: "",
      taskRef: null,
      studyId: "",
      network: null,
      identityKind: "",
      counts: { identified: null, unidentified: null },
      sourceKind: "",
      meta: null,
      lastQuery: null,
      selectedNodeId: null,
      selectedEdgeIndex: null,
      ui: {},
      token: 0,
      cy: null,
      layout: null,
      layoutToken: 0,
      renderer: "svg",
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

      // Identity honesty banner: driven by `identity_kind`, never by guesswork.
      var source = el(doc, "p", "acp-net-source", "");
      source.hidden = true;
      root.appendChild(source);

      var stateNode = el(doc, "p", "acp-net-state", t("next.network.empty"));
      stateNode.setAttribute("aria-live", "polite");
      root.appendChild(stateNode);

      var viewport = el(doc, "div", "acp-net-viewport");
      viewport.hidden = true;
      var tools = el(doc, "div", "acp-net-tools");
      tools.setAttribute("role", "toolbar");
      tools.setAttribute("aria-label", t("next.network.title"));

      function tool(action, labelKey, textValue, icon) {
        var button = el(doc, "button", "acp-net-tool" + (icon ? " acp-net-tool--icon" : ""), textValue);
        button.type = "button";
        button.setAttribute("data-net-action", action);
        button.setAttribute("aria-label", t(labelKey));
        button.setAttribute("title", t(labelKey));
        tools.appendChild(button);
        return button;
      }

      var zoomOut = tool("zoom-out", "next.network.zoom_out", "−", true);
      var zoomIn = tool("zoom-in", "next.network.zoom_in", "+", true);
      var fit = tool("fit", "next.network.fit", t("next.network.fit"), false);
      var relayout = tool("relayout", "next.network.relayout", t("next.network.relayout"), false);
      viewport.appendChild(tools);

      var canvas = el(doc, "div", "acp-net-canvas");
      canvas.setAttribute("aria-label", t("next.network.title"));
      viewport.appendChild(canvas);
      root.appendChild(viewport);

      var detail = el(doc, "div", "acp-net-detail");
      detail.hidden = true;
      root.appendChild(detail);

      var hint = el(doc, "p", "acp-net-hint", t("next.network.hint"));
      hint.hidden = true;
      root.appendChild(hint);

      state.host.replaceChildren(root);
      state.ui = {
        root: root, head: head, sub: sub, source: source, viewport: viewport,
        tools: tools, canvas: canvas, stateNode: stateNode, detail: detail, hint: hint,
        zoomOut: zoomOut, zoomIn: zoomIn, fit: fit, relayout: relayout,
      };
      on(canvas, "click", onCanvasClick);
      on(zoomOut, "click", function () { zoomGraph(1 / 1.25); });
      on(zoomIn, "click", function () { zoomGraph(1.25); });
      on(fit, "click", fitGraph);
      on(relayout, "click", runGraphLayout);
    }

    function graphStyle() {
      return [
        { selector: "node", style: {
          width: 18, height: 18, shape: "ellipse",
          "background-color": "#8295a9", "border-width": 1.2, "border-color": "#c0ccda",
          label: "data(label)", color: "#e5edf5", "font-size": 11,
          "font-family": "SFMono-Regular, Consolas, monospace",
          "text-valign": "bottom", "text-halign": "center", "text-margin-y": 9,
          "text-wrap": "ellipsis", "text-max-width": 150,
          "text-background-color": "#0d1219", "text-background-opacity": 0.88,
          "text-background-padding": 3,
          "overlay-opacity": 0, "overlay-padding": 7,
        } },
        { selector: "node.is-reactant", style: { "background-color": "#559ff2", "border-color": "#8bc2ff" } },
        { selector: "node.is-product", style: { "background-color": "#4caf8b", "border-color": "#83d5b8" } },
        { selector: "node.is-ts", style: { shape: "diamond", width: 21, height: 21, "background-color": "#c79643", "border-color": "#e5bd72" } },
        { selector: "node.is-intermediate", style: { "background-color": "#9680c7", "border-color": "#c0afe8" } },
        { selector: "node:selected", style: {
          "background-color": "#4ea1ff", "border-color": "#e5f2ff", "border-width": 2.4,
          color: "#ffffff", "text-background-color": "#162b40", "text-background-opacity": 1,
        } },
        { selector: "edge", style: {
          width: 1.35, "line-color": "#5d7185", "target-arrow-color": "#7890a6",
          "target-arrow-shape": "triangle", "arrow-scale": 0.85,
          "curve-style": "bezier", "control-point-step-size": 48,
          label: "data(label)", color: "#a9b8c8", "font-size": 9,
          "font-family": "SFMono-Regular, Consolas, monospace",
          "text-background-color": "#0d1219", "text-background-opacity": 0.92,
          "text-background-padding": 3, "overlay-opacity": 0, "overlay-padding": 6,
        } },
        { selector: "edge:selected", style: {
          width: 2.2, "line-color": "#4ea1ff", "target-arrow-color": "#4ea1ff", color: "#d8ebff",
        } },
      ];
    }

    function destroyGraph() {
      state.layoutToken += 1;
      if (state.layout && typeof state.layout.stop === "function") {
        try { state.layout.stop(); } catch (e) { /* layout already finished */ }
      }
      state.layout = null;
      if (state.cy && typeof state.cy.destroy === "function") {
        try { state.cy.destroy(); } catch (e) { /* renderer already gone */ }
      }
      state.cy = null;
      if (state.ui.canvas && typeof state.ui.canvas.replaceChildren === "function") {
        state.ui.canvas.replaceChildren();
      }
    }

    function ensureElkRegistered() {
      if (!cytoscapeFn || !cytoscapeElkExtension || typeof cytoscapeFn.use !== "function") return false;
      if (elkRegistration.cytoscape !== cytoscapeFn || elkRegistration.extension !== cytoscapeElkExtension) {
        cytoscapeFn.use(cytoscapeElkExtension);
        elkRegistration = { cytoscape: cytoscapeFn, extension: cytoscapeElkExtension };
      }
      return true;
    }

    function fitGraph() {
      if (!state.cy) return;
      try {
        state.cy.resize();
        state.cy.fit(state.cy.elements(), FIT_PAD);
      } catch (e) { /* viewport may have been detached */ }
    }

    function zoomGraph(factor) {
      if (!state.cy) return;
      try {
        state.cy.zoom({
          level: state.cy.zoom() * factor,
          renderedPosition: { x: state.cy.width() / 2, y: state.cy.height() / 2 },
        });
      } catch (e) { /* viewport may have been detached */ }
    }

    function placeIsolatedGrid(cy) {
      if (!state.network || !cy) return;
      var isolatedIds = layout(state.network).isolated;
      if (!isolatedIds.length) return;
      var connected = cy.nodes().filter(function (node) { return isolatedIds.indexOf(node.id()) === -1; });
      var origin = { x: 0, y: 0 };
      if (connected.length) {
        var box = connected.boundingBox();
        origin = { x: box.x1, y: box.y2 + GRID_GAP };
      }
      var grid = isolatedGrid(isolatedIds, origin);
      isolatedIds.forEach(function (id) {
        var node = cy.getElementById(id);
        var p = grid.positions[id];
        if (node && p) node.position({ x: p.x, y: p.y });
      });
    }

    function runGraphLayout() {
      var cy = state.cy;
      if (!cy || !state.network) return;
      var graph = layout(state.network);
      var isolatedIds = graph.isolated;
      var connected = cy.nodes().filter(function (node) { return isolatedIds.indexOf(node.id()) === -1; });
      if (!connected.length) {
        if (state.ui.root) state.ui.root.setAttribute("data-net-layout", "grid");
        placeIsolatedGrid(cy);
        fitGraph();
        return;
      }
      var hasElk = false;
      try { hasElk = ensureElkRegistered(); } catch (e) { hasElk = false; }
      if (!hasElk) {
        if (state.ui.root) state.ui.root.setAttribute("data-net-layout", "preset");
        placeIsolatedGrid(cy);
        fitGraph();
        return;
      }
      var token = state.layoutToken + 1;
      state.layoutToken = token;
      if (state.ui.root) state.ui.root.setAttribute("data-net-layout", "elk");
      var layoutElements = connected.add(cy.edges());
      try {
        state.layout = layoutElements.layout({
          name: "elk",
          fit: false,
          animate: false,
          nodeDimensionsIncludeLabels: true,
          elk: elkOptions(),
          stop: function () {
            if (state.disposed || state.cy !== cy || state.layoutToken !== token) return;
            state.layout = null;
            placeIsolatedGrid(cy);
            fitGraph();
          },
        });
        state.layout.run();
      } catch (e) {
        state.layout = null;
        if (state.ui.root) state.ui.root.setAttribute("data-net-layout", "preset");
        placeIsolatedGrid(cy);
        fitGraph();
      }
    }

    function syncGraphSelection() {
      if (state.cy) {
        try {
          state.cy.elements().unselect();
          var id = state.selectedNodeId != null ? String(state.selectedNodeId)
            : (state.selectedEdgeIndex != null ? "edge_" + String(state.selectedEdgeIndex) : "");
          if (id) {
            var selected = state.cy.getElementById(id);
            if (selected && typeof selected.select === "function") selected.select();
          }
        } catch (e) { /* selection is visual only */ }
        return;
      }
      if (!state.network || !state.ui.canvas) return;
      var built = buildSvg(state.network, {
        selectedNodeId: state.selectedNodeId,
        selectedEdgeIndex: state.selectedEdgeIndex,
      });
      state.ui.canvas.innerHTML = built.svg;
    }

    function renderCytoscape() {
      var cy = cytoscapeFn({
        container: state.ui.canvas,
        elements: cytoscapeElements(state.network),
        style: graphStyle(),
        minZoom: 0.15,
        maxZoom: 3,
        wheelSensitivity: 0.2,
        zoomingEnabled: true,
        userZoomingEnabled: true,
        panningEnabled: true,
        userPanningEnabled: true,
        boxSelectionEnabled: false,
        autounselectify: true,
      });
      state.cy = cy;
      state.renderer = "cytoscape";
      state.ui.tools.hidden = false;
      if (state.ui.root) state.ui.root.setAttribute("data-net-renderer", "cytoscape");
      cy.on("tap", "node", function (event) {
        if (state.disposed || !event || !event.target) return;
        selectNode(event.target.data("nodeId"));
      });
      cy.on("tap", "edge", function (event) {
        if (state.disposed || !event || !event.target) return;
        selectEdge(Number(event.target.data("edgeIndex")));
      });
      syncGraphSelection();
      runGraphLayout();
    }
    function setModuleState(name) {
      if (state.ui.root) state.ui.root.setAttribute("data-acp-module-state", name);
    }

    /** Fine-grained state, so 未连接 is never conflated with 错误 (or 空态). */
    function setNetState(name) {
      if (state.ui.root) state.ui.root.setAttribute("data-acp-net-state", name);
    }

    /** Banner copy: known identity kinds are translated, an unknown one is
     *  shown raw — the module never upgrades the claim. */
    function sourceLabel() {
      var kind = state.identityKind;
      if (!kind) return "";
      if (IDENTITY_KINDS[kind]) return t("next.network.source." + kind);
      return kind;
    }

    /**
     * `mixed` must NEVER be rendered as a plain molecule network: the banner
     * appends the two response counts (identified / unidentified), so the
     * unmerged remainder stays visible.
     */
    function identityCountsText() {
      var counts = state.counts || {};
      var parts = [];
      if (counts.identified != null) parts.push(t("next.network.identified", { count: counts.identified }));
      if (counts.unidentified != null) parts.push(t("next.network.unidentified", { count: counts.unidentified }));
      return parts.join(" · ");
    }

    function renderSourceBanner() {
      var source = state.ui.source;
      if (!source) return;
      var label = sourceLabel();
      var structural = state.identityKind === "structure_content_v1";
      var mixed = state.identityKind === "mixed";
      if (mixed) {
        var countsText = identityCountsText();
        if (countsText) label = label + " · " + countsText;
      }
      source.hidden = !label;
      source.textContent = label;
      source.className = "acp-net-source" + (structural ? " is-structure-version" : "") + (mixed ? " is-mixed" : "");
      // Amber is a scientific warning, not chrome: inline so it survives any
      // host stylesheet that does not know this panel.
      source.setAttribute("style", structural ? "color:#d6a94f;" : "");
      if (state.identityKind) source.setAttribute("data-identity-kind", state.identityKind);
      else source.removeAttribute("data-identity-kind");
      if (mixed) {
        if (state.counts && state.counts.identified != null) source.setAttribute("data-identified-nodes", String(state.counts.identified));
        else source.removeAttribute("data-identified-nodes");
        if (state.counts && state.counts.unidentified != null) source.setAttribute("data-unidentified-nodes", String(state.counts.unidentified));
        else source.removeAttribute("data-unidentified-nodes");
      } else {
        source.removeAttribute("data-identified-nodes");
        source.removeAttribute("data-unidentified-nodes");
      }
    }

    /** Task/truncation bookkeeping, rendered in the header sub line. */
    function metaText() {
      var parts = [];
      if (state.studyId) parts.push("study " + state.studyId);
      if (state.meta && state.meta.truncated) parts.push(t("next.network.truncated"));
      if (state.meta && state.meta.skipped) parts.push(t("next.network.skipped", { count: state.meta.skipped }));
      return parts.join(" · ");
    }

    function showEmpty(message, hint) {
      state.network = null;
      state.selectedNodeId = null;
      state.selectedEdgeIndex = null;
      destroyGraph();
      state.ui.viewport.hidden = true;
      state.ui.detail.hidden = true;
      state.ui.hint.hidden = true;
      state.ui.head.hidden = true;
      state.ui.stateNode.hidden = false;
      state.ui.stateNode.textContent = message;
      if (hint) {
        var extra = el(doc, "span", "acp-net-state-hint", " " + hint);
        state.ui.stateNode.appendChild(extra);
      }
      renderSourceBanner();
      setModuleState("empty");
      setNetState("empty");
    }

    function showLoading() {
      destroyGraph();
      state.ui.viewport.hidden = true;
      state.ui.detail.hidden = true;
      state.ui.hint.hidden = true;
      state.ui.head.hidden = true;
      state.ui.source.hidden = true;
      state.ui.stateNode.hidden = false;
      state.ui.stateNode.textContent = t("next.network.loading");
      setModuleState("loading");
      setNetState("loading");
    }

    function appendRetry(textKey) {
      var retry = el(doc, "button", "acp-btn acp-btn--ghost", t(textKey));
      retry.type = "button";
      on(retry, "click", function () { retryLast(); });
      state.ui.stateNode.appendChild(retry);
    }

    function showError(error) {
      destroyGraph();
      state.ui.viewport.hidden = true;
      state.ui.detail.hidden = true;
      state.ui.hint.hidden = true;
      state.ui.head.hidden = true;
      state.ui.source.hidden = true;
      state.ui.stateNode.hidden = false;
      state.ui.stateNode.textContent = t("next.network.error", { message: describeError(error) });
      appendRetry("next.network.retry");
      setModuleState("error");
      setNetState("error");
    }

    /** 未连接: the Controller/endpoint is unreachable — never an empty success. */
    function showDisconnected(error) {
      destroyGraph();
      state.ui.viewport.hidden = true;
      state.ui.detail.hidden = true;
      state.ui.hint.hidden = true;
      state.ui.head.hidden = true;
      state.ui.source.hidden = true;
      state.ui.stateNode.hidden = false;
      state.ui.stateNode.textContent = t("next.network.disconnected", { message: describeError(error) });
      appendRetry("next.network.retry");
      setModuleState("error");
      setNetState("disconnected");
    }

    function renderNetwork() {
      if (!state.network) return;
      destroyGraph();
      state.ui.stateNode.hidden = true;
      state.ui.viewport.hidden = false;
      var text = metaText();
      state.ui.sub.textContent = text;
      state.ui.head.hidden = !text;
      state.ui.hint.textContent = t("next.network.hint");
      state.ui.zoomOut.setAttribute("aria-label", t("next.network.zoom_out"));
      state.ui.zoomOut.setAttribute("title", t("next.network.zoom_out"));
      state.ui.zoomIn.setAttribute("aria-label", t("next.network.zoom_in"));
      state.ui.zoomIn.setAttribute("title", t("next.network.zoom_in"));
      state.ui.fit.textContent = t("next.network.fit");
      state.ui.fit.setAttribute("aria-label", t("next.network.fit"));
      state.ui.fit.setAttribute("title", t("next.network.fit"));
      state.ui.relayout.textContent = t("next.network.relayout");
      state.ui.relayout.setAttribute("aria-label", t("next.network.relayout"));
      state.ui.relayout.setAttribute("title", t("next.network.relayout"));
      renderSourceBanner();
      if (typeof cytoscapeFn === "function") {
        try {
          renderCytoscape();
        } catch (e) {
          destroyGraph();
          state.renderer = "svg";
          state.ui.tools.hidden = true;
          if (state.ui.root) state.ui.root.setAttribute("data-net-renderer", "svg");
          syncGraphSelection();
        }
      } else {
        state.renderer = "svg";
        state.ui.tools.hidden = true;
        if (state.ui.root) state.ui.root.setAttribute("data-net-renderer", "svg");
        syncGraphSelection();
      }
      state.ui.hint.hidden = false;
      setModuleState("ready");
      setNetState("ready");
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
      var node = nodeById(id);
      state.selectedNodeId = String(id);
      state.selectedEdgeIndex = null;
      syncGraphSelection();
      renderNodeDetail(node);
      if (context && typeof context.setFocus === "function") {
        try { context.setFocus({ networkNodeId: String(id) }, MODULE_ID); } catch (e) { /* read-only host */ }
      }
      openStructureFor(node);
    }

    function selectEdge(index) {
      var edges = (state.network && state.network.edges) || [];
      state.selectedEdgeIndex = index;
      state.selectedNodeId = null;
      syncGraphSelection();
      renderEdgeDetail(edges[index]);
      openTaskFor(edges[index]);
    }

    /** Navigation only — every call is guarded; a click never throws. */
    function commandsRef() {
      if (commandsApi && typeof commandsApi === "object") return commandsApi;
      return typeof window !== "undefined" && window.ACPShell ? window.ACPShell.commands || null : null;
    }

    /**
     * Node → 分子工作区. The ref carries the node identity plus the first
     * referencing task (a persisted reference); no entry id is invented, so an
     * unresolvable structure stays visible as the viewer's own error state.
     */
    /**
     * Node → 分子工作区. `commands.openStructure` wraps the whole argument as
     * `structureRef` (shell/commands.js:81-87), so these are FLAT fields — a
     * nested `structureRef` would be read by nobody. The ref carries the node
     * identity plus the first referencing task (a persisted reference); no
     * entry id is invented, so an unresolvable structure stays visible as the
     * viewer's own error state.
     *
     * Identity passthrough: a `molecule_v1` node is keyed by the molecule
     * identity (`moleculeIdentityKey`), every other node by its content
     * checksum (`contentChecksum`) — never both, never a substituted id.
     */
    function openStructureFor(node) {
      if (!node) return;
      var commands = commandsRef();
      if (!commands || typeof commands.openStructure !== "function") return;
      var taskIds = node.task_ids || [];
      var nodeId = String(node.node_id || node.state_id);
      var ref = { nodeId: nodeId, source: MODULE_ID };
      if (node.identity_kind === "molecule_v1") ref.moleculeIdentityKey = nodeId;
      else ref.contentChecksum = nodeId;
      if (taskIds.length) ref.jobId = String(taskIds[0]);
      if (state.projectId) ref.projectId = state.projectId;
      try { commands.openStructure(ref); } catch (e) { /* navigation is best-effort */ }
    }

    /** Lineage edge → 任务工作区 (`to_task` is the downstream task).
     *  `commands.openTask` requires `jobId` and only copies the camelCase
     *  `taskId` into the delivered taskRef (shell/commands.js:92-96). */
    function openTaskFor(edge) {
      if (!edge) return;
      var commands = commandsRef();
      if (!commands || typeof commands.openTask !== "function") return;
      var taskId = edge.to_task == null ? "" : String(edge.to_task);
      if (!taskId) return;
      try { commands.openTask({ taskId: taskId, jobId: taskId, source: MODULE_ID }); } catch (e) { /* best-effort */ }
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
      if (node.formula) detailRow(container, t("next.network.formula"), node.formula);
      // With a molecule identity the canonical SMILES is the readable key;
      // it is metadata only and never re-derives the node identity.
      if (node.canonical_smiles) detailRow(container, t("next.network.smiles"), node.canonical_smiles);
      if (node.atom_count != null) detailRow(container, t("next.network.atoms"), String(node.atom_count));
      if (node.task_ids && node.task_ids.length) detailRow(container, t("next.network.tasks"), node.task_ids.join(", "));
    }

    function renderEdgeDetail(edge) {
      if (!edge) return;
      var container = openDetail(t("next.network.step") + " " + String(edge.step_id || edge.ts_id || edge.relation || ""));
      detailRow(container, t("next.network.barrier"), edge.barrier_forward_kcal != null ? numberText(edge.barrier_forward_kcal, 2) + " kcal/mol" : "");
      detailRow(container, t("next.network.fidelity"), edge.fidelity);
      detailRow(container, t("next.network.status"), edge.status);
      if (edge.from_task) detailRow(container, t("next.network.edge_from"), edge.from_task);
      if (edge.to_task) detailRow(container, t("next.network.edge_to"), edge.to_task);
    }

    function applyNetwork(network) {
      var nodes = (network && Array.isArray(network.nodes)) ? network.nodes : [];
      if (!nodes.length) {
        if (state.sourceKind === "project") showEmpty(t("next.network.empty.project"), metaText());
        else showEmpty(t("next.network.no_network"), t("next.network.empty_hint"));
        return;
      }
      state.network = network;
      state.selectedNodeId = null;
      state.selectedEdgeIndex = null;
      renderNetwork();
      state.ui.detail.hidden = true;
    }

    /** Retry replays the EXACT query that failed (same project/limit/cursor). */
    function retryLast() {
      if (state.lastQuery && state.lastQuery.projectId) {
        loadProject(state.lastQuery.projectId, { cursor: state.lastQuery.cursor });
        return;
      }
      if (state.taskRef) loadLegacyTask(state.taskRef);
      else resetToIdle();
    }

    function resetToIdle() {
      state.studyId = "";
      state.network = null;
      state.identityKind = "";
      state.counts = { identified: null, unidentified: null };
      state.sourceKind = "";
      state.meta = null;
      state.lastQuery = null;
      showEmpty(t("next.network.empty"), "");
    }

    function requestProjectNetwork(query) {
      var path = "/api/v2/projects/" + encodeURIComponent(query.projectId) +
        "/chemistry-network?limit=" + String(query.limit);
      if (query.cursor) path += "&cursor=" + encodeURIComponent(query.cursor);
      return requestFn(path, { method: "GET" });
    }

    /**
     * Project network (primary). A rejected read is an error / 未连接 state —
     * never an empty success.
     */
    function loadProject(projectId, options) {
      options = options || {};
      var token = state.token + 1;
      state.token = token;
      var gen = state.generation;
      var cursor = options.cursor ? String(options.cursor) : "";
      state.studyId = "";
      state.lastQuery = { projectId: String(projectId), cursor: cursor };
      showLoading();
      var read;
      if (networkSource && typeof networkSource.fetchNetwork === "function") {
        read = networkSource.fetchNetwork({
          projectId: String(projectId),
          projectName: state.projectName,
          cursor: cursor || null,
          limit: LIMIT,
        });
      } else {
        read = requestProjectNetwork({ projectId: String(projectId), cursor: cursor, limit: LIMIT });
      }
      return Promise.resolve(read).then(function (wire) {
        if (!current(gen, token)) return null;
        var graph = mapWireNetwork(wire);
        state.identityKind = graph.identity_kind;
        state.counts = { identified: graph.identified_nodes, unidentified: graph.unidentified_nodes };
        state.sourceKind = "project";
        state.meta = { truncated: graph.truncated, skipped: graph.skipped.length, next_cursor: graph.next_cursor };
        applyNetwork(graph);
        return null;
      }).catch(function (error) {
        if (!current(gen, token)) return;
        if (isDisconnected(error)) showDisconnected(error);
        else showError(error);
      });
    }

    /** Legacy mechanism-study projection — only reachable without a projectId. */
    function loadLegacyTask(ref) {
      var jobId = ref && ref.jobId ? String(ref.jobId) : "";
      if (!jobId) {
        resetToIdle();
        return Promise.resolve();
      }
      var token = state.token + 1;
      state.token = token;
      var gen = state.generation;
      state.lastQuery = null;
      state.meta = null;
      state.identityKind = "legacy_mechanism";
      state.counts = { identified: null, unidentified: null };
      state.sourceKind = "legacy_mechanism";
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
          if (isDisconnected(error)) showDisconnected(error);
          else showError(error);
        });
    }

    /** Primary source decision: project network, else the labelled legacy path. */
    function loadCurrent() {
      if (state.projectId) { loadProject(state.projectId); return Promise.resolve(); }
      if (state.taskRef) return loadLegacyTask(state.taskRef);
      resetToIdle();
      return Promise.resolve();
    }

    function current(gen, token) {
      return !state.disposed && state.mounted && state.generation === gen && state.token === token;
    }

    function applySnapshot(snapshot) {
      var ref = snapshot && snapshot.taskRef ? snapshot.taskRef : null;
      var projectId = snapshot && snapshot.projectId ? String(snapshot.projectId) : "";
      var projectName = snapshot && snapshot.projectName ? String(snapshot.projectName) : "";
      var nextJob = ref && ref.jobId ? String(ref.jobId) : "";
      var previousJob = state.taskRef && state.taskRef.jobId ? String(state.taskRef.jobId) : "";
      var projectChanged = projectId !== state.projectId;
      state.projectId = projectId;
      if (projectName) state.projectName = projectName;
      state.taskRef = nextJob ? { jobId: nextJob, attempt: ref.attempt == null ? null : Number(ref.attempt) } : null;
      if (projectChanged) {
        state.studyId = "";
        state.network = null;
        loadCurrent();
        return;
      }
      // With a project the graph is project-scoped: a task switch does not
      // change it. Without one, the task IS the query (legacy projection).
      if (!projectId && nextJob !== previousJob) {
        state.studyId = "";
        state.network = null;
        loadCurrent();
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
      if (api.commands) commandsApi = api.commands;
      buildDom();
      showEmpty(t("next.network.empty"), "");
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
        else if (state.taskRef) loadLegacyTask(state.taskRef);
        else if (state.projectId) loadProject(state.projectId);
        else showEmpty(t("next.network.empty"), "");
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

    function resume() {
      state.suspended = false;
      if (state.cy) {
        try { state.cy.resize(); } catch (e) { /* viewport detached */ }
      }
    }

    function dispose() {
      if (state.disposed) return;
      state.disposed = true;
      state.mounted = false;
      state.token += 1;
      destroyGraph();
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
    isolatedGrid: isolatedGrid,
    elkOptions: elkOptions,
    cytoscapeElements: cytoscapeElements,
    buildSvg: buildSvg,
    roleKind: roleKind,
    mapWireNetwork: mapWireNetwork,
    isDisconnected: isDisconnected,
  };

  window.ACPNextModules = window.ACPNextModules || {};
  window.ACPNextModules[MODULE_ID] = {
    descriptor: DESCRIPTOR,
    factory: createReactionNetwork,
  };
})();

