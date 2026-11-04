/*
 * Single source of truth for the ACP shell ids the DSH client half requires.
 *
 * The contract lives in `client/mount.js` (REQUIRED_IDS): the mount runtime
 * refuses to boot unless every id is present in the live document. Test
 * fixtures and the asset-build assertions parse that list instead of keeping a
 * second copy, so removing or renaming a shell id can only be done in one place
 * and the tests follow automatically.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

export const MOUNT_SOURCE = path.join(HERE, "..", "client", "mount.js");

/** @returns {string[]} the ids declared by `var REQUIRED_IDS = [...]` in `sourcePath`. */
export function readRequiredShellIds(sourcePath = MOUNT_SOURCE) {
  const text = fs.readFileSync(sourcePath, "utf8");
  const block = /var REQUIRED_IDS = \[([\s\S]*?)\];/.exec(text);
  if (!block) {
    throw new Error(`could not find "var REQUIRED_IDS = [...]" in ${sourcePath}`);
  }
  const ids = [...block[1].matchAll(/"([^"]+)"/g)].map((match) => match[1]);
  if (ids.length === 0) {
    throw new Error(`REQUIRED_IDS in ${sourcePath} is empty`);
  }
  if (new Set(ids).size !== ids.length) {
    throw new Error(`REQUIRED_IDS in ${sourcePath} contains duplicates`);
  }
  return ids;
}

export const REQUIRED_SHELL_IDS = readRequiredShellIds();
