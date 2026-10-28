/* M5-03 finding F6: the Workbench science panel must not probe a synthetic
 * "demo-project" on page load.
 *
 * Regression: the panel used to mount with projectId = "demo-project" and
 * auto-discover immediately, so every fresh page produced a stable
 * `GET /api/v2/science/capabilities?project_id=demo-project` -> 403 console
 * error before the operator did anything.
 *
 * Pinned contracts:
 *   1. mount() with no bound project issues ZERO capabilities requests;
 *   2. discover() without a project stays idle and issues no request;
 *   3. selecting a project (setProject) triggers discovery for exactly that
 *      project -- the user's explicit selection is the only live trigger;
 *   4. clearing the project stops the probe and drops discovered questions;
 *   5. fixture mode (an explicit demo switch) still works unbound.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const FRONTEND = path.resolve(__dirname, "..", "..");
const FEATURE_REL = "js/workbench/features/science-question.js";

function makeDom() {
  const elements = new Map();
  function makeEl(id) {
    return {
      id,
      innerHTML: "",
      textContent: "",
      value: "",
      disabled: false,
      hidden: false,
      attrs: {},
      listeners: {},
      setAttribute(name, value) {
        this.attrs[name] = String(value);
        if (name === "hidden") this.hidden = true;
      },
      removeAttribute(name) {
        delete this.attrs[name];
        if (name === "hidden") this.hidden = false;
      },
      getAttribute(name) {
        return this.attrs[name] === undefined ? null : this.attrs[name];
      },
      addEventListener(type, handler) {
        (this.listeners[type] = this.listeners[type] || []).push(handler);
      },
    };
  }
  return {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, makeEl(id));
      return elements.get(id);
    },
    addEventListener() {},
    querySelectorAll() {
      return [];
    },
    createElement(tag) {
      return makeEl(tag);
    },
  };
}

module.exports = {
  async register({ test, assert, assertEq, loadScript, makeSandbox }) {
    function setup(options) {
      options = options || {};
      const calls = [];
      const sandbox = makeSandbox({
        fetch: function () {
          throw new Error("fetch is forbidden in the project-binding test");
        },
        document: makeDom(),
      });
      loadScript(FEATURE_REL, sandbox);
      const W = sandbox.window;
      const Q = W.ACPScienceQuestion;
      const science = {
        fetchCapabilities(scope) {
          calls.push({ scope: scope });
          return Promise.resolve(JSON.parse(JSON.stringify(Q.FIXTURES.capabilities)));
        },
      };
      const initOptions = { science: science };
      if (options.projectId !== undefined) initOptions.projectId = options.projectId;
      Q.init(initOptions);
      return {
        sandbox,
        W,
        Q,
        calls,
        dom: sandbox.document,
        bodyHtml: () => sandbox.document.getElementById("science-question-body").innerHTML,
      };
    }

    function flush() {
      return new Promise((resolve) => setTimeout(resolve, 0));
    }

    await test("page mount with no selected project sends no capabilities request", async () => {
      const h = setup();
      h.Q.mount(h.dom, {});
      await flush();
      assertEq(h.calls.length, 0, "no request may leave an unbound panel");
      const state = h.Q.getState();
      assertEq(state.projectId, null, "no synthetic project is bound");
      assertEq(state.phase, "idle", "the panel stays idle");
      assertEq(state.discovered, false, "nothing was discovered");
      assert(h.bodyHtml().indexOf("请先在左侧选择项目") !== -1,
        "the panel asks for a project instead of failing");
    });

    await test("explicit discover() without a project issues no request", async () => {
      const h = setup();
      h.Q.mount(h.dom, { autoDiscover: false });
      const state = await h.Q.discover();
      await flush();
      assertEq(h.calls.length, 0, "discover() must not fall back to demo-project");
      assertEq(state.phase, "idle", "the panel stays idle");
      assertEq(state.error, null, "no error is surfaced for a missing project");
    });

    await test("selecting a project triggers discovery for that project", async () => {
      const h = setup();
      h.Q.mount(h.dom, {});
      await flush();
      assertEq(h.calls.length, 0, "no request before selection");
      h.Q.setProject("proj-42");
      await flush();
      assertEq(h.calls.length, 1, "selection triggers exactly one discovery");
      assertEq(h.calls[0].scope.projectId, "proj-42", "the selected project is sent");
      assertEq(h.Q.getState().discovered, true, "capabilities were adopted");
      assert(h.Q.getState().question !== null, "a question is selected after discovery");
    });

    await test("init({projectId}) binds the project before mount discovery", async () => {
      const h = setup({ projectId: "proj-init" });
      h.Q.mount(h.dom, {});
      await flush();
      assertEq(h.calls.length, 1, "a bound project may auto-discover");
      assertEq(h.calls[0].scope.projectId, "proj-init", "the bound project is used");
    });

    await test("clearing the project stops probing and drops questions", async () => {
      const h = setup();
      h.Q.mount(h.dom, {});
      h.Q.setProject("proj-42");
      await flush();
      assertEq(h.calls.length, 1, "the selected project was discovered");
      h.Q.setProject("");
      await flush();
      assertEq(h.calls.length, 1, "clearing the project issues no request");
      const state = h.Q.getState();
      assertEq(state.projectId, null, "the project binding is cleared");
      assertEq(state.question, null, "the selected question is dropped");
      assertEq(state.discovered, false, "the panel is unbound again");
      await h.Q.discover();
      await flush();
      assertEq(h.calls.length, 1, "a later discover() still refuses to probe");
    });

    await test("fixture mode still loads unbound (explicit demo switch)", async () => {
      const h = setup();
      h.Q.mount(h.dom, { autoDiscover: false, fixtureMode: true });
      const state = await h.Q.discover();
      assertEq(h.calls.length, 1, "fixture mode uses its embedded catalog path");
      assertEq(state.demo, true, "fixture discovery is visibly demo data");
    });
  },
};
