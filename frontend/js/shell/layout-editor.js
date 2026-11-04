/*
 * ACP Next Shell — accessible visual recipe editor (A-63).
 *
 * Edits stay local until Apply. A-63 adds the modal contract:
 *   - role=dialog + aria-modal + aria-labelledby/aria-describedby;
 *   - initial focus on the first control, explicit Tab/Shift+Tab focus trap;
 *   - Escape closes the dialog and restores focus to the opener;
 *   - `data-acp-dialog-open` mirrors the open/closed state.
 * Keyboard activation of the dialog's controls is native (<button>/<select>/
 * <input>), so no synthetic click handling is layered on top.
 */
window.ACPShell = window.ACPShell || {};
ACPShell.layoutEditor = (function () {
  "use strict";
  var FOCUSABLE_TAGS = { A: true, BUTTON: true, SELECT: true, INPUT: true, TEXTAREA: true };
  var dialog, opener, returnFocus, workspace, draft, controls, status, preview;
  function t(key) { return ACPShell.i18n.t("layout." + key); }
  function node(tag, text) { var e = document.createElement(tag); if (text) e.textContent = text; return e; }
  function focusNode(target) {
    if (!target || typeof target.focus !== "function") return false;
    try { target.focus(); return true; } catch (e) { return false; }
  }
  function isInside(root, element) {
    var cur = element;
    var guard = 0;
    while (cur && guard < 400) {
      if (cur === root) return true;
      cur = cur.parentNode;
      guard += 1;
    }
    return false;
  }
  /** Tabbable controls inside the dialog, in DOM (= Tab) order. */
  function focusables() {
    var out = [];
    if (!dialog) return out;
    (function walk(current) {
      var children = current.children || [];
      for (var i = 0; i < children.length; i++) {
        var child = children[i];
        var tag = String(child.tagName || "").toUpperCase();
        var explicit = child.getAttribute ? child.getAttribute("tabindex") : null;
        var tabbable = explicit != null
          ? Number(explicit) >= 0
          : (FOCUSABLE_TAGS[tag] && !(tag === "A" && child.getAttribute("href") == null));
        if (tabbable && !child.hidden && !(child.getAttribute && child.getAttribute("disabled") != null)) {
          out.push(child);
        }
        walk(child);
      }
    })(dialog);
    return out;
  }
  function restoreFocus() {
    var target = returnFocus && returnFocus.isConnected !== false ? returnFocus : opener;
    returnFocus = null;
    focusNode(target);
  }
  function isOpen() {
    return !!(dialog && dialog.getAttribute && dialog.getAttribute("data-acp-dialog-open") === "true");
  }
  function close() {
    if (!dialog || !isOpen()) { restoreFocus(); return; }
    if (dialog.open !== false && typeof dialog.close === "function") {
      try { dialog.close(); } catch (error) { /* not open in this environment */ }
    }
    dialog.open = false;
    dialog.setAttribute("data-acp-dialog-open", "false");
    restoreFocus();
  }
  /** A-63 modal keyboard contract: Escape closes, Tab cycles inside the dialog. */
  function onDialogKeydown(event) {
    if (!event) return;
    if (event.key === "Escape") {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      close();
      return;
    }
    if (event.key !== "Tab") return;
    var list = focusables();
    var active = document.activeElement;
    if (!list.length) { focusNode(dialog); return; }
    var first = list[0], last = list[list.length - 1];
    var inside = isInside(dialog, active);
    if (event.shiftKey) {
      if (!inside || active === first) {
        if (typeof event.preventDefault === "function") event.preventDefault();
        focusNode(last);
      }
      return;
    }
    if (!inside || active === last) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      focusNode(first);
    }
  }
  function labelFor(id) {
    var spec = ACPShell.preferences.modules()[id];
    return spec ? ACPShell.i18n.t(spec.titleKey) : t("none");
  }
  function drawPreview() {
    preview.textContent = "";
    preview.style.gridTemplateColumns = draft.side ? "minmax(0,1fr) " + draft.sideWidth + "%" : "1fr";
    preview.style.gridTemplateRows = draft.bottom ? "minmax(0,1fr) " + draft.bottomHeight + "%" : "1fr";
    ["main", "side", "bottom"].forEach(function (slot) {
      if (!draft[slot]) return;
      var card = node("div", labelFor(draft[slot]));
      card.className = "acp-layout-preview-panel";
      card.style.gridColumn = slot === "side" ? "2" : "1";
      card.style.gridRow = slot === "bottom" ? "2" : slot === "side" ? "1 / -1" : "1";
      preview.appendChild(card);
    });
  }
  function sync() {
    Object.keys(controls).forEach(function (key) { controls[key].value = String(draft[key]); });
    drawPreview();
  }
  function open() {
    workspace = ACPShell.layout.getCurrentWorkspace();
    draft = ACPShell.preferences.get(workspace);
    returnFocus = document.activeElement &&
      document.activeElement !== document.body &&
      !isInside(dialog, document.activeElement)
      ? document.activeElement
      : opener;
    if (dialog && dialog.parentNode) dialog.parentNode.removeChild(dialog);
    dialog = node("dialog");
    dialog.id = "acp-layout-dialog";
    dialog.className = "acp-layout-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "acp-layout-title");
    dialog.setAttribute("aria-describedby", "acp-layout-hint");
    dialog.setAttribute("data-acp-dialog", "layout");
    dialog.setAttribute("data-acp-dialog-open", "true");
    var title = node("h2", t("title") + " · " + ACPShell.i18n.t("shell.workspace." + workspace));
    title.id = "acp-layout-title";
    dialog.appendChild(title);
    var hint = node("p", t("hint"));
    hint.id = "acp-layout-hint";
    dialog.appendChild(hint);
    preview = node("div");
    preview.className = "acp-layout-preview";
    preview.setAttribute("role", "group");
    preview.setAttribute("aria-label", t("preview"));
    dialog.appendChild(preview);
    controls = {};
    ["main", "side", "bottom"].forEach(function (slot) {
      var label = node("label", t(slot)), select = node("select");
      select.setAttribute("data-layout-slot", slot);
      var ids = Object.keys(ACPShell.preferences.pickerModules ? ACPShell.preferences.pickerModules() : ACPShell.preferences.modules());
      if (slot !== "main") ids.unshift("");
      ids.forEach(function (id) {
        var option = node("option", labelFor(id)); option.value = id; select.appendChild(option);
      });
      select.addEventListener("change", function () {
        var previous = draft[slot], next = select.value;
        if (slot !== "main" && next === draft.main && !previous) {
          status.textContent = t("mainRequired"); sync(); return;
        }
        // Moving an occupied module swaps positions, avoiding duplicate instances.
        ["main", "side", "bottom"].forEach(function (other) {
          if (other !== slot && next && draft[other] === next) draft[other] = previous;
        });
        draft[slot] = next;
        status.textContent = "";
        sync();
      });
      controls[slot] = select; label.appendChild(select); dialog.appendChild(label);
    });
    ["sideWidth", "bottomHeight"].forEach(function (key) {
      var label = node("label", t(key)), range = node("input"), value = node("output");
      range.type = "range"; range.min = "20"; range.max = "65"; range.step = "1";
      range.setAttribute("data-layout-size", key);
      value.textContent = draft[key] + "%";
      range.addEventListener("input", function () { draft[key] = Number(range.value); value.textContent = draft[key] + "%"; drawPreview(); });
      controls[key] = range; label.appendChild(range); label.appendChild(value); dialog.appendChild(label);
    });
    status = node("p"); status.setAttribute("role", "status"); dialog.appendChild(status);
    var actions = node("div"); actions.className = "acp-layout-actions";
    function button(key, action) { var b = node("button", t(key)); b.type = "button"; b.addEventListener("click", action); actions.appendChild(b); return b; }
    button("reset", function () {
      draft = ACPShell.preferences.defaults(workspace); sync();
      dialog.querySelectorAll("output").forEach(function (out, i) { out.textContent = draft[i ? "bottomHeight" : "sideWidth"] + "%"; });
    });
    button("cancel", close);
    var apply = button("apply", function () {
      try {
        var saved = ACPShell.preferences.save(workspace, draft);
        ["side", "bottom"].forEach(function (slot) { ACPShell.layout.setCollapsed(workspace, slot, false); });
        // A-63: never let the re-render steal focus out of the modal; focus is
        // restored to the opener by close() on the persisted path.
        ACPShell.layout.applyRecipe(workspace, { focus: "none" });
        if (saved.persisted) close();
        else status.textContent = t("sessionOnly");
      } catch (error) { status.textContent = t("invalid"); }
    });
    apply.className = "acp-layout-primary"; dialog.appendChild(actions);
    dialog.addEventListener("keydown", onDialogKeydown);
    dialog.addEventListener("cancel", function (event) {
      // Escape request from the browser: keep the shell's restore semantics.
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      close();
    });
    document.getElementById("acp-shell").appendChild(dialog);
    sync();
    if (typeof dialog.showModal === "function") {
      try { dialog.showModal(); } catch (error) { dialog.open = true; }
    } else {
      dialog.open = true;
    }
    focusNode(controls.main || focusables()[0]);
  }
  function init() {
    opener = document.getElementById("acp-layout-edit");
    if (!opener || typeof opener.addEventListener !== "function") return;
    if (opener.getAttribute && opener.getAttribute("data-acp-bound") === "true") return;
    if (opener.setAttribute) opener.setAttribute("data-acp-bound", "true");
    opener.addEventListener("click", open);
  }
  return { init: init, open: open, close: close, _isOpen: isOpen };
})();
