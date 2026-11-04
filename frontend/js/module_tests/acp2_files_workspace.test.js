"use strict";
module.exports.register = async function (h) {
  const { test, assert, assertEq, makeSandbox, loadScript } = h;
  const fs = require("fs"), path = require("path");
  const root = path.resolve(__dirname, "..", "..");
  const flush = () => Promise.resolve().then(() => Promise.resolve()).then(() => Promise.resolve()).then(() => Promise.resolve());

  function makeElement(doc, tag) {
    const classes = new Set();
    const n = {
      tagName: String(tag || "div").toUpperCase(), ownerDocument: doc, children: [], attrs: {}, parentNode: null,
      hidden: false, textContent: "", className: "", value: "", type: "", id: "", autocomplete: "", clientWidth: 1302,
      style: { setProperty(k, v) { this[k] = v; } },
      listeners: {},
      classList: { add(v) { classes.add(v); }, remove(v) { classes.delete(v); }, toggle(v, on) { if (on) classes.add(v); else classes.delete(v); }, contains(v) { return classes.has(v); } },
      setAttribute(k, v) { this.attrs[k] = String(v); },
      getAttribute(k) { return this.attrs[k] == null ? null : this.attrs[k]; },
      removeAttribute(k) { delete this.attrs[k]; },
      appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
      replaceChildren(...cs) { this.children = []; cs.forEach(c => this.appendChild(c)); },
      addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
      removeEventListener(type, fn) { const a = this.listeners[type] || []; const i = a.indexOf(fn); if (i >= 0) a.splice(i, 1); },
      focus() { doc.activeElement = this; this.focused = true; },
    };
    return n;
  }
  function makeDocument() {
    const doc = { activeElement: null, createElement: tag => makeElement(doc, tag), listeners: {}, getElementById: () => null };
    doc.addEventListener = (type, fn) => { (doc.listeners[type] = doc.listeners[type] || []).push(fn); };
    doc.removeEventListener = (type, fn) => { const a = doc.listeners[type] || []; const i = a.indexOf(fn); if (i >= 0) a.splice(i, 1); };
    return doc;
  }
  function all(node, cls, out) {
    out = out || [];
    node.children.forEach(c => { if (String(c.className).split(/\s+/).indexOf(cls) !== -1) out.push(c); all(c, cls, out); });
    return out;
  }
  function first(node, cls) { return all(node, cls)[0] || null; }
  function rows(node) { return all(node, "acp-files-tree-row"); }
  function findRow(node, p) { return rows(node).find(r => r.getAttribute("data-files-path") === p) || null; }

  await test("project file client uses the injected v2 transport and validates identity", async function () {
    const calls = [];
    const s = makeSandbox(); s.ACPClient = {};
    loadScript("js/client/project-files.js", s);
    s.ACPClient.projectFiles.init({ v2: (url, opts) => {
      calls.push([url, opts.method]);
      if (url.indexOf("/file?") !== -1) return Promise.resolve({ schema_version: "acp.project-files.v1", project_id: "p 1", path: "src/a b.txt", content: "", encoding: "utf-8", size: 0, mtime: 1, returned_bytes: 0, returned_lines: 0, truncated: false, truncation_reason: "", sha256: "abc", sha256_scope: "returned_bytes" });
      return Promise.resolve({ schema_version: "acp.project-files.v1", project_id: "p 1", path: "", entries: [{ path: "src", name: "src", type: "directory", size: 0, mtime: 1 }], limit: 200, truncated: false, truncation_reason: "" });
    } });
    const tree = await s.ACPClient.projectFiles.list("p 1", "", 200);
    assertEq(tree.entries[0].type, "directory");
    assertEq(calls[0][0], "/projects/p%201/tree?path=&limit=200");
    assertEq(calls[0][1], "GET");
    const file = await s.ACPClient.projectFiles.read("p 1", "src/a b.txt", 65536, 1000);
    assertEq(file.content, "");
    assertEq(calls[1][0], "/projects/p%201/file?path=src%2Fa%20b.txt&max_bytes=65536&max_lines=1000");
    await s.ACPClient.projectFiles.list("", "", 200).then(() => { throw new Error("empty project accepted"); }, e => assert(/required/.test(e.message)));
    await s.ACPClient.projectFiles.list("other", "", 200).then(() => { throw new Error("mismatch accepted"); }, e => assert(/another project/.test(e.message)));
  });

  await test("files workspace has an honest no-project state and makes no request", async function () {
    const doc = makeDocument(), host = makeElement(doc, "div");
    const s = makeSandbox({ document: doc }); s.ACPNextModules = {}; s.ACPClient = {};
    loadScript("js/modules/files-workspace/module.js", s);
    const calls = [];
    const pf = { list() { calls.push("list"); return Promise.resolve({ entries: [] }); }, read() { calls.push("read"); return Promise.resolve({}); } };
    const instance = s.ACPNextModules["files-workspace"].factory({ doc, client: { projectFiles: pf }, i18n: { t: k => k, getLang: () => "zh-CN" }, context: { getSnapshot: () => ({ projectId: null }) } });
    instance.mount(host);
    await flush();
    assertEq(calls.length, 0, "no project means no file request");
    const tree = first(host, "acp-files-tree");
    assert(String(first(tree, "acp-files-tree-status").textContent).indexOf("选择项目") !== -1);
    assertEq(instance._internals.MAX_BYTES, 65536);
    assertEq(instance._internals.MAX_LINES, 1000);
    instance.dispose();
  });

  await test("files workspace lazily lists directories, renders text safely and shows truncation", async function () {
    const doc = makeDocument(), host = makeElement(doc, "div");
    const s = makeSandbox({ document: doc }); s.ACPNextModules = {}; s.ACPClient = {};
    loadScript("js/modules/files-workspace/module.js", s);
    const calls = [];
    const pf = {
      list(projectId, dir, limit) {
        calls.push(["list", projectId, dir, limit]);
        if (dir === "") return Promise.resolve({ schema_version: "acp.project-files.v1", project_id: "p1", path: "", entries: [
          { path: "src", name: "src", type: "directory", size: 0, mtime: 1 },
          { path: "README.md", name: "README.md", type: "file", size: 12, mtime: 1 } ], limit: 200, truncated: false, truncation_reason: "" });
        return Promise.resolve({ schema_version: "acp.project-files.v1", project_id: "p1", path: "src", entries: [
          { path: "src/a.txt", name: "a.txt", type: "file", size: 20, mtime: 2 } ], limit: 200, truncated: true, truncation_reason: "limit" });
      },
      read(projectId, file, maxBytes, maxLines) {
        calls.push(["read", projectId, file, maxBytes, maxLines]);
        return Promise.resolve({ schema_version: "acp.project-files.v1", project_id: "p1", path: "src/a.txt", content: "first\n<b>not html</b>\n", encoding: "utf-8", size: 20, mtime: 2, returned_bytes: 20, returned_lines: 2, truncated: true, truncation_reason: "max_lines", sha256: "abc", sha256_scope: "returned_bytes" });
      }
    };
    const instance = s.ACPNextModules["files-workspace"].factory({ doc, client: { projectFiles: pf }, i18n: { t: k => k, getLang: () => "zh-CN" }, context: { getSnapshot: () => ({ projectId: "p1" }) } });
    instance.mount(host);
    await flush();
    assertEq(calls[0], ["list", "p1", "", 200]);
    assert(findRow(host, "src") && findRow(host, "README.md"), "root entries render");
    const tree = first(host, "acp-files-tree-list");
    assertEq(tree.getAttribute("role"), "tree");
    assertEq(findRow(host, "src").getAttribute("role"), "treeitem");
    assertEq(findRow(host, "src").getAttribute("tabindex"), "0", "first tree item is keyboard reachable");
    tree.listeners.keydown[0]({ key: "ArrowRight", preventDefault() {} });
    await flush();
    assertEq(calls[1], ["list", "p1", "src", 200]);
    assertEq(findRow(host, "src").getAttribute("aria-expanded"), "true", "ArrowRight expands the directory");
    assert(findRow(host, "src/a.txt"), "lazy child loaded");
    findRow(host, "src/a.txt").listeners.click[0]({});
    await flush();
    assertEq(calls[2], ["read", "p1", "src/a.txt", 65536, 1000]);
    const pre = first(host, "acp-files-pre");
    assertEq(pre.textContent, "first\n<b>not html</b>\n");
    assertEq(pre.children.length, 0, "content is text, never parsed HTML");
    assertEq(first(host, "acp-files-linenums").textContent, "1\n2");
    assert(String(first(host, "acp-files-read-note").textContent).indexOf("max_lines") !== -1);
    const crumb = first(host, "acp-files-crumbs");
    assertEq(crumb.children.map(c => c.textContent).join(""), "项目根目录/src/a.txt");
    instance.dispose();
  });

  await test("files workspace maps 415/413 truthfully and does not fabricate content", async function () {
    const doc = makeDocument(), host = makeElement(doc, "div");
    const s = makeSandbox({ document: doc }); s.ACPNextModules = {}; s.ACPClient = {};
    loadScript("js/modules/files-workspace/module.js", s);
    let mode = "binary";
    const pf = {
      list() { return Promise.resolve({ project_id: "p1", path: "", entries: [{ path: "blob.bin", name: "blob.bin", type: "file", size: 10, mtime: 1 }], limit: 200, truncated: false }); },
      read() { const e = new Error(mode === "binary" ? "binary" : "too large"); e.status = mode === "binary" ? 415 : 413; return Promise.reject(e); }
    };
    const inst = s.ACPNextModules["files-workspace"].factory({ doc, client: { projectFiles: pf }, i18n: { t: k => k, getLang: () => "zh-CN" }, context: { getSnapshot: () => ({ projectId: "p1" }) } });
    inst.mount(host); await flush();
    findRow(host, "blob.bin").listeners.click[0]({}); await flush();
    let status = first(host, "acp-files-reader-status");
    assertEq(status.getAttribute("data-files-state"), "error");
    assert(String(status.textContent).indexOf("二进制") !== -1);
    assertEq(first(host, "acp-files-pre").textContent, "", "binary never renders content");
    mode = "large";
    findRow(host, "blob.bin").listeners.click[0]({}); await flush();
    status = first(host, "acp-files-reader-status");
    assert(String(status.textContent).indexOf("超过") !== -1);
    inst.dispose();
  });

  await test("files workspace statics pin 300px tree, 200-240 terminal and responsive rules", function () {
    const css = fs.readFileSync(path.join(root, "css/modules/files-workspace.css"), "utf8");
    const html = fs.readFileSync(path.join(root, "next/index.html"), "utf8");
    const shellCss = fs.readFileSync(path.join(root, "css/shell/shell.css"), "utf8");
    assert(/--files-tree-w:\s*300px/.test(shellCss));
    assert(/--files-term-h:\s*200px/.test(shellCss));
    assert(/@container \(max-width: 1024px\)[\s\S]*?\.acp-files-tree\s*\{[\s\S]*?display:\s*none/.test(css));
    assert(/@container \(max-width: 640px\)[\s\S]*?grid-template-rows:\s*minmax\(0, 1fr\) 160px/.test(css));
    assert(!/acp-next-view-letter/.test(html) && !/>\s*[A-G]\s*</.test(html), "no development letters in the files entry");
    const js = fs.readFileSync(path.join(root, "js/modules/files-workspace/module.js"), "utf8");
    assert(!/\bfetch\s*\(/.test(js) && !/innerHTML\s*=/.test(js), "no raw fetch and no HTML injection");
    assert(js.indexOf("projectFiles") !== -1 && js.indexOf("acp-files-terminal-disabled") !== -1);
  });
};
