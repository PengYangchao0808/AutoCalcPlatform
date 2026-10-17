/*
 * Node test harness for ACP frontend architecture modules.
 * Zero dependencies; loads browser-style scripts (IIFE + window namespace)
 * inside a vm sandbox with per-test DOM/browser stubs.
 *
 * Usage: node frontend/js/module_tests/run_all.js [name-filter]
 */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..", ".."); // frontend/
let passed = 0;
let failed = 0;
const failures = [];

function loadScript(relPath, sandbox) {
  const file = path.join(ROOT, relPath);
  const code = fs.readFileSync(file, "utf8");
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: file });
  return sandbox;
}

function makeSandbox(overrides) {
  // Browser-like: the sandbox global object *is* `window`, so IIFE scripts
  // that assign window.X and read bare X both work.
  const sandbox = {
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
    Date, Math, JSON, URLSearchParams, AbortController, Promise,
    Error, Object, Array, String, Number, Boolean, Map, Set,
  };
  Object.assign(sandbox, overrides || {});
  sandbox.window = sandbox;
  return sandbox;
}

function test(name, fn) {
  const timeoutMs = 5000;
  const guarded = () => Promise.resolve().then(fn);
  const timeout = new Promise((_, reject) => {
    const id = setTimeout(() => reject(new Error("test timed out after " + timeoutMs + "ms")), timeoutMs);
    if (id.unref) id.unref();
  });
  return Promise.race([guarded(), timeout])
    .then(() => { passed++; console.log("  ok - " + name); })
    .catch((e) => { failed++; failures.push({ name, error: e }); console.log("  FAIL - " + name + "\n    " + (e && e.stack ? e.stack.split("\n").slice(0, 4).join("\n    ") : e)); });
}

function assert(cond, msg) { if (!cond) throw new Error(msg || "assertion failed"); }
function assertEq(actual, expected, msg) {
  const a = JSON.stringify(actual), b = JSON.stringify(expected);
  if (a !== b) throw new Error((msg || "not equal") + ": expected " + b + " got " + a);
}

async function runFile(file, filter) {
  const name = path.basename(file, ".test.js");
  if (filter && !name.includes(filter)) return;
  console.log("# " + name);
  const mod = require(file);
  await mod.register({ test, assert, assertEq, loadScript, makeSandbox });
}

async function main() {
  const filter = process.argv[2] || null;
  const dir = __dirname;
  const files = fs.readdirSync(dir).filter((f) => f.endsWith(".test.js")).sort();
  for (const f of files) await runFile(path.join(dir, f), filter);
  console.log("\n" + passed + " passed, " + failed + " failed");
  if (failed) { failures.forEach((f) => console.log("FAILED: " + f.name)); process.exit(1); }
}
main();
