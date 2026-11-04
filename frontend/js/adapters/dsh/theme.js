/*
 * ACP adapter layer — DSH theme variables → ACP custom properties.
 *
 * Mapping table (DSH source property → ACP target property). Every ACP name
 * below is an EXISTING definition in the Workbench stylesheets:
 *   frontend/css/workbench/base.css   : --bg-*, --border-*, --text-*, --accent*,
 *                                       --green, --amber, --red, --radius*
 *   frontend/css/shell/shell.css      : --shell-topbar-h, --shell-side-w, --shell-violet
 *
 *   --dsh-bg               → --bg-base
 *   --dsh-surface          → --bg-panel
 *   --dsh-surface-raised   → --bg-elevated
 *   --dsh-canvas           → --bg-canvas
 *   --dsh-border           → --border-default
 *   --dsh-border-subtle    → --border-subtle
 *   --dsh-text             → --text-primary
 *   --dsh-text-muted       → --text-secondary
 *   --dsh-text-faint       → --text-tertiary
 *   --dsh-accent           → --accent
 *   --dsh-accent-rgb       → --accent-rgb
 *   --dsh-success          → --green
 *   --dsh-warning          → --amber
 *   --dsh-danger           → --red
 *   --dsh-radius           → --radius
 *   --dsh-radius-sm        → --radius-sm
 *   --dsh-topbar-h         → --shell-topbar-h
 *   --dsh-sidebar-w        → --shell-side-w
 *   --dsh-violet           → --shell-violet
 *
 * Documented fallbacks (only these two; used when options.fallback !== false
 * and the DSH variable is absent/empty — those values equal the ACP defaults):
 *   --dsh-accent = #4ea1ff, --dsh-radius = 10px
 *
 * Writes go to the TARGET element only (default documentElement), never to
 * :root through a separate code path. No DOM access at load time.
 */
window.ACPAdapters = window.ACPAdapters || {};
ACPAdapters.dsh = ACPAdapters.dsh || {};

ACPAdapters.dsh.theme = (function () {
  "use strict";

  var MAPPING = [
    ["--dsh-bg", "--bg-base"],
    ["--dsh-surface", "--bg-panel"],
    ["--dsh-surface-raised", "--bg-elevated"],
    ["--dsh-canvas", "--bg-canvas"],
    ["--dsh-border", "--border-default"],
    ["--dsh-border-subtle", "--border-subtle"],
    ["--dsh-text", "--text-primary"],
    ["--dsh-text-muted", "--text-secondary"],
    ["--dsh-text-faint", "--text-tertiary"],
    ["--dsh-accent", "--accent"],
    ["--dsh-accent-rgb", "--accent-rgb"],
    ["--dsh-success", "--green"],
    ["--dsh-warning", "--amber"],
    ["--dsh-danger", "--red"],
    ["--dsh-radius", "--radius"],
    ["--dsh-radius-sm", "--radius-sm"],
    ["--dsh-topbar-h", "--shell-topbar-h"],
    ["--dsh-sidebar-w", "--shell-side-w"],
    ["--dsh-violet", "--shell-violet"]
  ];

  var DEFAULT_VALUES = {
    "--dsh-accent": "#4ea1ff",
    "--dsh-radius": "10px"
  };

  var DEFAULT_LINKS = [
    "css/workbench/base.css",
    "css/shell/shell.css"
  ];

  function trim(value) {
    if (typeof value !== "string") return "";
    return value.replace(/^[\s]+|[\s]+$/g, "");
  }

  function resolveStyleReader(doc, options) {
    if (typeof options.getComputedStyle === "function") return options.getComputedStyle;
    var view = doc && doc.defaultView;
    if (view && typeof view.getComputedStyle === "function") return view.getComputedStyle;
    /* bare `typeof` on an undeclared identifier is safe in every realm */
    if (typeof getComputedStyle === "function") return getComputedStyle;
    return null;
  }

  function allTargets() {
    return MAPPING.map(function (pair) { return pair[1]; });
  }

  function apply(doc, options) {
    options = options || {};
    var result = { ok: true, applied: [], skipped: [] };
    if (!doc || !doc.documentElement) {
      result.ok = false;
      result.skipped = allTargets();
      return result;
    }
    var reader = resolveStyleReader(doc, options);
    if (!reader) {
      result.ok = false;
      result.skipped = allTargets();
      return result;
    }

    var view = doc.defaultView || null;
    var probe = options.probe || doc.documentElement;
    var target = options.target || doc.documentElement;
    var probeStyle = reader.call(view, probe);
    var targetStyle = (target === probe) ? probeStyle : reader.call(view, target);
    var writer = (target && target.style && typeof target.style.setProperty === "function")
      ? target.style
      : targetStyle;
    if (!writer || typeof writer.setProperty !== "function") {
      result.ok = false;
      result.skipped = allTargets();
      return result;
    }

    var fallback = options.fallback !== false;
    MAPPING.forEach(function (pair) {
      var dshName = pair[0];
      var acpName = pair[1];
      var value = (probeStyle && typeof probeStyle.getPropertyValue === "function")
        ? trim(probeStyle.getPropertyValue(dshName))
        : "";
      if (!value && fallback && Object.prototype.hasOwnProperty.call(DEFAULT_VALUES, dshName)) {
        value = DEFAULT_VALUES[dshName];
      }
      if (!value) { result.skipped.push(acpName); return; }
      writer.setProperty(acpName, value);
      result.applied.push(acpName);
    });
    return result;
  }

  function normaliseHref(href, base) {
    var value = String(href);
    if (/^https?:\/\//i.test(value) || value.charAt(0) === "/") return value;
    var b = normalizeBase(base);
    if (!b) return "/" + value;
    return b + "/" + value;
  }

  function normalizeBase(base) {
    var b = (typeof base === "string") ? base : "/acp";
    b = b.replace(/\/{2,}/g, "/");
    if (b.length > 1 && b.charAt(b.length - 1) === "/") b = b.slice(0, -1);
    if (b === "/") return "";
    return b;
  }

  function existingHrefs(doc, marker) {
    var found = [];
    var nodes = null;
    if (doc.head && typeof doc.querySelectorAll === "function") {
      nodes = doc.querySelectorAll("link[" + marker + "]");
    } else if (doc.head && doc.head.children) {
      nodes = doc.head.children;
    }
    if (!nodes) return found;
    var length = nodes.length || 0;
    for (var i = 0; i < length; i += 1) {
      var node = nodes[i];
      if (!node) continue;
      var attr = (typeof node.getAttribute === "function") ? node.getAttribute(marker) : null;
      if (attr === null && nodes !== doc.head.children) continue;
      var href = (typeof node.getAttribute === "function") ? node.getAttribute("href") : node.href;
      if (typeof href === "string") found.push(href);
    }
    return found;
  }

  function linkStyles(doc, options) {
    options = options || {};
    var result = { ok: true, added: [], skipped: [] };
    var hrefs = Array.isArray(options.hrefs) ? options.hrefs : DEFAULT_LINKS;
    var marker = (typeof options.marker === "string" && options.marker) ? options.marker : "data-acp-dsh-css";
    if (!doc || !doc.head || typeof doc.createElement !== "function") {
      result.ok = false;
      result.skipped = hrefs.slice();
      return result;
    }
    var base = (options.base === undefined) ? "/acp" : options.base;
    var seen = existingHrefs(doc, marker);

    hrefs.forEach(function (href) {
      var full = normaliseHref(href, base);
      if (seen.indexOf(full) !== -1) { result.skipped.push(full); return; }
      var link = doc.createElement("link");
      if (link && typeof link.setAttribute === "function") {
        link.setAttribute("rel", "stylesheet");
        link.setAttribute("href", full);
        link.setAttribute(marker, "1");
      } else if (link) {
        link.rel = "stylesheet";
        link.href = full;
      }
      doc.head.appendChild(link);
      seen.push(full);
      result.added.push(full);
    });
    return result;
  }

  return {
    MAPPING: MAPPING,
    DEFAULT_VALUES: DEFAULT_VALUES,
    DEFAULT_LINKS: DEFAULT_LINKS,
    apply: apply,
    linkStyles: linkStyles
  };
})();
