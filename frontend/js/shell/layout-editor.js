/* Accessible visual recipe editor. Edits are local until Apply. */
window.ACPShell = window.ACPShell || {};
ACPShell.layoutEditor = (function () {
  "use strict";
  var dialog, opener, workspace, draft, controls, status, preview;
  function t(key) { return ACPShell.i18n.t("layout." + key); }
  function node(tag, text) { var e = document.createElement(tag); if (text) e.textContent = text; return e; }
  function labelFor(id) {
    var spec = ACPShell.preferences.modules()[id];
    return spec ? ACPShell.i18n.t(spec.titleKey) : t("none");
  }
  function close() { dialog.close(); if (opener) opener.focus(); }
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
    if (dialog) dialog.remove();
    dialog = node("dialog"); dialog.className = "acp-layout-dialog";
    dialog.setAttribute("aria-labelledby", "acp-layout-title");
    var title = node("h2", t("title") + " · " + ACPShell.i18n.t("shell.workspace." + workspace)); title.id = "acp-layout-title";
    dialog.appendChild(title);
    dialog.appendChild(node("p", t("hint")));
    preview = node("div"); preview.className = "acp-layout-preview";
    preview.setAttribute("aria-label", t("preview")); dialog.appendChild(preview);
    controls = {};
    ["main", "side", "bottom"].forEach(function (slot) {
      var label = node("label", t(slot)), select = node("select");
      select.setAttribute("data-layout-slot", slot);
      var ids = Object.keys(ACPShell.preferences.modules());
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
        ACPShell.layout.applyRecipe(workspace);
        if (saved.persisted) close();
        else status.textContent = t("sessionOnly");
      } catch (error) { status.textContent = t("invalid"); }
    });
    apply.className = "acp-layout-primary"; dialog.appendChild(actions);
    dialog.addEventListener("cancel", function () { if (opener) opener.focus(); });
    document.getElementById("acp-shell").appendChild(dialog);
    sync(); dialog.showModal();
  }
  function init() {
    opener = document.getElementById("acp-layout-edit");
    if (opener && !opener.dataset.bound) { opener.dataset.bound = "true"; opener.addEventListener("click", open); }
  }
  return { init: init, open: open };
})();
