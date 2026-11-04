/* DEPRECATED: the generic tool drawer was removed. Platform entries live in
 * topbar System status / Approvals; task modules live in E/F and their owning
 * context. Keep this empty shim for one compatibility release only. */
window.ACPShell = window.ACPShell || {};
ACPShell.toolDrawer = {
  init: function () { return false; },
  open: function () { return { ok: false, code: "tool_drawer_retired" }; },
  close: function () {},
  retired: true
};
