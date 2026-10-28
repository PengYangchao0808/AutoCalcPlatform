#!/usr/bin/env node
/*
 * Real-contract probe for the M1-06 thin slice (C-Web minimal consumer).
 *
 * Loads the REAL `workbench/contracts/experience_contract.js` (and, for an
 * evidence payload, the REAL read-only `research-evidence-view.js` feature) in a
 * vm sandbox and validates one platform response file.  This is the frontend's
 * actual validator — not a copy — so a server payload that drifts from the
 * frozen contract fails the slice.
 *
 * Usage:
 *   node evidence_contract_probe.js --contract proposal|record|evidence \
 *        --payload <file.json>
 *
 * Output: one JSON object `{contract, ok, problems, view?}` on stdout.
 * Exit: 0 ok, 1 the payload violates the contract, 2 usage/input error.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const FRONTEND = path.resolve(__dirname, "..", "..");
const CONTRACT_REL = "js/workbench/contracts/experience_contract.js";
const FEATURE_REL = "js/workbench/features/research-evidence-view.js";

const CONTRACT_FUNCTIONS = {
  proposal: "validateProposal",
  record: "validateResearchRecord",
  evidence: "validateEvidenceView",
};

function parseArgs(argv) {
  const args = { contract: "", payload: "" };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    const value = argv[index + 1];
    if ((key === "--contract" || key === "--payload") && value !== undefined) {
      args[key.slice(2)] = value;
      index += 1;
    } else {
      throw new Error(`unknown argument ${String(key)}`);
    }
  }
  if (!args.contract || !args.payload) {
    throw new Error("--contract and --payload are required");
  }
  if (!CONTRACT_FUNCTIONS[args.contract]) {
    throw new Error(`unknown contract ${args.contract}`);
  }
  return args;
}

function load(file, sandbox) {
  const code = fs.readFileSync(path.join(FRONTEND, file), "utf8");
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: path.join(FRONTEND, file) });
  return sandbox;
}

function main() {
  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (error) {
    console.error(String(error instanceof Error ? error.message : error));
    process.exit(2);
  }
  let payload;
  try {
    payload = JSON.parse(fs.readFileSync(args.payload, "utf8"));
  } catch (error) {
    console.error(`cannot read ${args.payload}: ${String(error)}`);
    process.exit(2);
  }
  const sandbox = { console };
  sandbox.window = sandbox;
  load(CONTRACT_REL, sandbox);
  const contracts = sandbox.ACPWorkbench.contracts.experience;
  const check = contracts[CONTRACT_FUNCTIONS[args.contract]](payload);
  const result = {
    contract: args.contract,
    validator: CONTRACT_FUNCTIONS[args.contract],
    ok: check.ok,
    problems: check.problems,
  };
  if (args.contract === "evidence" && check.ok) {
    load(FEATURE_REL, sandbox);
    // The feature must also be able to consume the same payload (read-only).
    const projected = sandbox.ACPResearchEvidence.project(payload);
    result.view = {
      ok: projected.ok,
      supported: projected.supported === true,
      rows: projected.rows.length,
    };
    result.ok = check.ok && projected.ok;
  }
  console.log(JSON.stringify(result));
  process.exit(result.ok ? 0 : 1);
}

main();
