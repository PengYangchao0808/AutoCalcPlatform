#!/usr/bin/env node
/**
 * `rph` executable (M3-02a).
 *
 * The published `bin` of `@rph/cli`: it is a three-line shim over `main()` so
 * the command surface stays testable in-process. The launcher (in
 * `@rph/dsh-bundle`) — not this file — is the only approved process seam.
 */

import { main } from "./compose.js";

/**
 * A CLI piped into `head` must not crash with an unhandled `EPIPE`.
 *
 * Only `EPIPE` is swallowed; any other stream error still marks the run failed.
 */
for (const stream of [process.stdout, process.stderr]) {
  stream.on("error", (error: NodeJS.ErrnoException) => {
    if (error.code !== "EPIPE") {
      process.exitCode = 1;
    }
  });
}

process.exitCode = await main(process.argv.slice(2));
