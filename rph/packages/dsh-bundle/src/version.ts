/**
 * Single version source for a package (M3-02).
 *
 * A package's version lives in exactly one place: its own `package.json`. The
 * compiled module reads it back at import time, so a release build cannot print
 * one version while `package.json`/the tarball declares another, and no source
 * file keeps a second hard-coded `0.0.0` constant.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

/** Read `version` from the manifest next to a compiled module. */
export function packageVersion(manifestUrl: URL): string {
  const manifest = JSON.parse(readFileSync(fileURLToPath(manifestUrl), "utf8")) as {
    version?: unknown;
  };
  if (typeof manifest.version !== "string" || manifest.version.length === 0) {
    throw new Error(`${fileURLToPath(manifestUrl)} has no version`);
  }
  return manifest.version;
}

/** `@rph/dsh-bundle`'s own version (`dist/src/version.js` -> `package.json`). */
export const BUNDLE_VERSION = packageVersion(
  new URL("../../package.json", import.meta.url),
);
