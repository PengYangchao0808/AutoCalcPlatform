"use strict";
/*
 * Module tests — W-05 locality-neutral energy copy (E2).
 *
 * The energy viewer used to classify one empty state as "远程/remote IRC 轨迹".
 * Transport locality is a diagnostic, never a user-facing category: the message
 * key is now `energy.irc_unavailable` (neutral) while the raw backend reason
 * (`remote_fetch_unavailable` / `remote_fetch_failed`) survives in a separate
 * detail line / title attribute.
 *
 * Guards:
 *   1. zh-CN + en-US both define the neutral key, neither sentence mentions
 *      远程/remote, and the old classified key is gone from both locales.
 *   2. energy.js selects the neutral key for `remote_fetch*` reasons and still
 *      forwards the raw reason to the detail channel (diagnosability kept).
 *   3. `energyGraphRenderState` renders that detail in its own diagnostic node.
 */
const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const I18N_REL = path.join("js", "workbench", "i18n.js");
const ENERGY_REL = path.join("js", "workbench", "features", "energy.js");
const I18N_JS = path.join(REPO_ROOT, "frontend", I18N_REL);
const ENERGY_JS = path.join(REPO_ROOT, "frontend", ENERGY_REL);

const ZH_BLOCK_RE = /"zh-CN":\s*\{(.*?)\n\s*"en-US":/s;
const EN_BLOCK_RE = /"en-US":\s*\{(.*?)(?:\n\s*\};)/s;

function readText(file) {
  return fs.readFileSync(file, "utf8");
}

function localeBlocks() {
  const src = readText(I18N_JS);
  const zh = ZH_BLOCK_RE.exec(src);
  const en = EN_BLOCK_RE.exec(src);
  if (!zh || !en) throw new Error("i18n.js must contain both zh-CN and en-US blocks");
  return { zh: zh[1], en: en[1] };
}

function valueOf(block, key) {
  const m = new RegExp('"' + key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + '"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)"').exec(block);
  return m ? m[1] : null;
}

function loadI18n(h) {
  const sandbox = h.makeSandbox({});
  sandbox.currentLang = "zh-CN";
  h.loadScript(I18N_REL, sandbox);
  if (typeof sandbox.t !== "function") throw new Error("i18n.js must expose global t()");
  return sandbox;
}

function loadEnergyWithRenderState(h) {
  const sandbox = h.makeSandbox({});
  sandbox.currentLang = "zh-CN";
  h.loadScript(I18N_REL, sandbox);
  const el = { innerHTML: "", querySelector: () => null };
  sandbox.document = { getElementById: (id) => (id === "viewer-energy" ? el : null) };
  sandbox.escapeHtml = (s) =>
    String(s == null ? "" : s)
      .split("&").join("&amp;")
      .split("<").join("&lt;")
      .split(">").join("&gt;")
      .split('"').join("&quot;")
      .split("'").join("&#39;");
  sandbox.disposeViewerFraming = () => {};
  sandbox.energyGraphState = {
    viewer: null,
    requestToken: 0,
    loadedFrameIndex: null,
    loadedFrameId: "",
    structureFraming: { pending: false, settled: false, observer: null, timer: null, lastW: 0, lastH: 0, epoch: 0 },
    resizeObserver: null,
    structureAbort: null,
  };
  h.loadScript(ENERGY_REL, sandbox);
  if (typeof sandbox.energyGraphRenderState !== "function") {
    throw new Error("energy.js must expose energyGraphRenderState()");
  }
  return { sandbox, el };
}

module.exports.register = async function (h) {
  const { test, assert, assertEq } = h;

  await test("energy i18n: neutral key defined in both locales without transport wording", () => {
    const { zh, en } = localeBlocks();

    const zhValue = valueOf(zh, "energy.irc_unavailable");
    const enValue = valueOf(en, "energy.irc_unavailable");
    assert(zhValue, "zh-CN must define energy.irc_unavailable");
    assert(enValue, "en-US must define energy.irc_unavailable");
    assert(!/远程/.test(zhValue), "zh-CN message must not classify the target as remote: " + zhValue);
    assert(!/remote/i.test(enValue), "en-US message must not mention remote: " + enValue);

    // The old classified key is retired in both locales (no leftover half-rename).
    assert(!/"energy\.irc_remote_error"\s*:/.test(zh), "zh-CN must not keep energy.irc_remote_error");
    assert(!/"energy\.irc_remote_error"\s*:/.test(en), "en-US must not keep energy.irc_remote_error");

    // Real runtime lookup, not just static text: t() must resolve the key in both locales.
    const sandbox = loadI18n(h);
    assertEq(sandbox.t("energy.irc_unavailable"), zhValue, "zh-CN runtime lookup");
    sandbox.currentLang = "en-US";
    assertEq(sandbox.t("energy.irc_unavailable"), enValue, "en-US runtime lookup");
  });

  await test("energy.js: remote_fetch* selects the neutral key and keeps the raw reason", () => {
    const src = readText(ENERGY_JS);

    assert(src.indexOf('t("energy.irc_remote_error")') === -1,
      "energy.js must not reference the retired classified key");
    assert(src.indexOf('t("energy.irc_unavailable")') !== -1,
      "energy.js must select energy.irc_unavailable for transport failures");
    assert(/function energyGraphRenderState\(state, message, detail\)/.test(src),
      "energyGraphRenderState must accept an optional detail argument");
    // The backend reason is still inspected and forwarded (diagnosability, not
    // silently dropped when the user-facing sentence is neutralised).
    assert(src.indexOf('ircReason.indexOf("remote_fetch") === 0') !== -1,
      "energy.js must still detect remote_fetch* reasons for diagnostics");
    assert(/energyGraphRenderState\("empty", ircMessage, ircDetail\)/.test(src),
      "the IRC empty state must forward the diagnostic detail to the renderer");
    assert(src.indexOf('t("energy.state_detail_reason", { reason: ircReason })') !== -1,
      "the raw backend reason must be rendered through the detail key");
  });

  await test("energy renderState: diagnostic detail gets its own node and title", () => {
    const { sandbox, el } = loadEnergyWithRenderState(h);

    const message = sandbox.t("energy.irc_unavailable");
    const detail = sandbox.t("energy.state_detail_reason", { reason: "remote_fetch_failed" });
    sandbox.energyGraphRenderState("empty", message, detail);

    assert(el.innerHTML.indexOf("暂时无法读取 IRC 轨迹") !== -1,
      "neutral zh-CN message must render in the empty card: " + el.innerHTML);
    assert(el.innerHTML.indexOf('data-energy-state-detail="1"') !== -1,
      "detail must render in its own diagnostic node");
    assert(el.innerHTML.indexOf("remote_fetch_failed") !== -1,
      "raw backend reason must stay visible for diagnosis");
    assert(el.innerHTML.indexOf('title="诊断信息：remote_fetch_failed"') !== -1,
      "detail node must also expose the reason as a title");
  });
};
