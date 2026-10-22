#!/usr/bin/env node
/**
 * Emit the compiled bridge contract as JSON.
 *
 * The acceptance scenario consumes this file instead of parsing TypeScript, so
 * the doc table and the code cannot drift silently. The output carries no
 * scientific data: names, versions, methods, error classes and wiring tasks.
 */

import {
  BRIDGE_EXTENSION_ID,
  BRIDGE_EXTENSION_VERSION,
  P1_TOOL_NAMES,
  P2_OPERATION_MAP,
  P2_OPERATIONS,
} from "@rph/bridge";

const p2 = P2_OPERATIONS.map((operation) => {
  const mapping = P2_OPERATION_MAP[operation];
  return {
    operation: mapping.operation,
    row: mapping.row,
    tool: mapping.tool,
    namespacedId: mapping.namespacedId,
    toolVersion: mapping.toolVersion,
    controllerClientMethod: mapping.controllerClientMethod,
    errorCodes: [...mapping.errorCodes],
    wiringTask: mapping.wiringTask,
    longTask: mapping.longTask,
  };
});

const document = {
  schema: "rph.bridge-contract.v1",
  extension: {
    id: BRIDGE_EXTENSION_ID,
    version: BRIDGE_EXTENSION_VERSION,
  },
  p1: [...P1_TOOL_NAMES],
  p2,
};

process.stdout.write(`${JSON.stringify(document, null, 2)}\n`);
