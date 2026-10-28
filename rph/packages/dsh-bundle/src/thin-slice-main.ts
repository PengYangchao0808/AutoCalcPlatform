#!/usr/bin/env node
/**
 * CLI entry for the M1-06 thin-slice runner.
 *
 * Usage: node packages/dsh-bundle/dist/src/thin-slice-main.js \
 *          --config <thin-slice.json> --out <dir>
 *
 * Exit codes: 0 the slice ran and every step produced its expected shape;
 * 1 a step failed (the typed refusal is in `runner.json`); 2 usage/prerequisite
 * error (missing config, unreadable JSON, missing launch command).
 *
 * The launcher is the only process seam; the Controller must already be running
 * (this entry never starts one).
 */

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";

import { runThinSlice, type ThinSliceConfig } from "./thin-slice.js";

interface Args {
  readonly config: string;
  readonly out: string;
}

function parseArgs(argv: readonly string[]): Args {
  let config = "";
  let out = "";
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    const value = argv[index + 1];
    if (key === "--config" && value !== undefined) {
      config = value;
      index += 1;
    } else if (key === "--out" && value !== undefined) {
      out = value;
      index += 1;
    } else {
      throw new Error(`unknown argument ${String(key)}`);
    }
  }
  if (config.length === 0 || out.length === 0) {
    throw new Error("--config and --out are required");
  }
  return { config, out };
}

async function main(): Promise<void> {
  let args: Args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (error) {
    console.error(String(error instanceof Error ? error.message : error));
    process.exit(2);
  }
  mkdirSync(args.out, { recursive: true });
  let config: ThinSliceConfig;
  try {
    config = JSON.parse(readFileSync(args.config, "utf8")) as ThinSliceConfig;
  } catch (error) {
    console.error(`cannot read the thin-slice config: ${String(error)}`);
    process.exit(2);
  }
  try {
    const result = await runThinSlice(config, { outDir: args.out });
    writeFileSync(
      join(args.out, "runner-result.json"),
      JSON.stringify({ status: result.status, errors: result.errors }, null, 2) + "\n",
    );
    console.log(JSON.stringify({ status: result.status, steps: result.steps.length }));
    process.exit(result.status === "ok" ? 0 : 1);
  } catch (error) {
    writeFileSync(
      join(args.out, "runner-error.json"),
      JSON.stringify(
        { status: "error", message: error instanceof Error ? error.message : String(error) },
        null,
        2,
      ) + "\n",
    );
    console.error(String(error instanceof Error ? error.stack ?? error.message : error));
    process.exit(1);
  }
}

await main();
