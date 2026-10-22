#!/usr/bin/env node
/**
 * Frozen official-client handshake for the C `mcp-live` acceptance row.
 *
 * This is NOT a second product transport. It composes exactly the pieces the
 * DSH harness uses:
 *
 *   - `@deepseek-ai/dsh-mcp-client/src/transport.ts::createTransport` (the
 *     plugin's stdio transport factory, including its credential-scrubbed
 *     parent env) from `$ACP2_DSH_SOURCE`;
 *   - `@deepseek-ai/dsh-mcp-client`'s pinned `@modelcontextprotocol/client`
 *     (2.2.0) `Client` with the plugin's own constructor options
 *     (`{ versionNegotiation: { mode: 'auto' } }`).
 *
 * The only addition is a transparent recorder on the official transport's
 * `send` / `onmessage` seam so the initialize exchange and the negotiated
 * protocol version can be archived. It never answers, rewrites, retries or
 * synthesises a frame.
 *
 * Usage: node acp2_mcp_live_client.mjs --launch <mcp-launch.json> --out <dir>
 *
 * Exit codes: 0 handshake + schema snapshot succeeded; 1 handshake failed;
 * 2 prerequisite (launch / DSH client / SDK) missing.
 */

import { createRequire } from "node:module";
import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";

const EXIT_OK = 0;
const EXIT_FAIL = 1;
const EXIT_PREREQ = 2;

function parseArgs(argv) {
  const args = { launch: null, out: null };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--launch" || key === "--out") {
      const value = argv[index + 1];
      if (value === undefined || value.startsWith("--")) {
        throw new Error(`${key} requires a value`);
      }
      args[key.slice(2)] = value;
      index += 1;
    } else {
      throw new Error(`unknown argument ${key}`);
    }
  }
  if (args.launch === null || args.out === null) {
    throw new Error("--launch and --out are required");
  }
  return args;
}

function readPackage(path) {
  const document = JSON.parse(readFileSync(path, "utf8"));
  return { name: document.name, version: document.version };
}

function toJsonish(value) {
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return { unserializable: String(value) };
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const out = args.out;
  mkdirSync(out, { recursive: true });
  const result = {
    schema: "acp2.c.mcp-live.official-client.v1",
    status: "error",
    phase: "start",
    package_versions: {},
    launch_path: args.launch,
    wire: [],
  };

  const writeResult = (exitCode) => {
    writeFileSync(join(out, "dsh-client.json"), JSON.stringify(result, null, 2) + "\n");
    process.exitCode = exitCode;
  };

  let launch;
  try {
    launch = JSON.parse(readFileSync(args.launch, "utf8"));
  } catch (error) {
    result.error = { name: String(error?.name ?? "Error"), message: String(error?.message ?? error) };
    writeResult(EXIT_PREREQ);
    return;
  }
  result.launch_sha256 = null; // filled by the Python scenario; keeps this file path-only.

  const dshSource = process.env.ACP2_DSH_SOURCE;
  if (!dshSource) {
    result.error = { name: "PrerequisiteError", message: "ACP2_DSH_SOURCE is not set" };
    writeResult(EXIT_PREREQ);
    return;
  }
  const dshClientDir = join(dshSource, "node_modules/@deepseek-ai/dsh-mcp-client");
  let transportModule;
  let sdk;
  try {
    result.package_versions["@deepseek-ai/dsh-mcp-client"] = readPackage(
      join(dshClientDir, "package.json"),
    );
    const requireFromDsh = createRequire(join(dshClientDir, "package.json"));
    const sdkPath = requireFromDsh.resolve("@modelcontextprotocol/client");
    result.package_versions["@modelcontextprotocol/client"] = readPackage(
      join(dirname(dirname(sdkPath)), "package.json"),
    );
    sdk = await import(pathToFileURL(sdkPath).href);
    transportModule = await import(pathToFileURL(join(dshClientDir, "src/transport.ts")).href);
  } catch (error) {
    result.error = {
      name: String(error?.name ?? "PrerequisiteError"),
      message: `cannot load the frozen DSH client: ${String(error?.message ?? error)}`,
    };
    writeResult(EXIT_PREREQ);
    return;
  }

  const wire = result.wire;
  const record = (direction, message) => {
    const frame = { direction, message: toJsonish(message) };
    wire.push(frame);
    appendFileSync(join(out, "wire.jsonl"), JSON.stringify(frame) + "\n");
  };

  const instrument = (transport) => {
    let inner;
    Object.defineProperty(transport, "onmessage", {
      configurable: true,
      enumerable: false,
      get() {
        return inner;
      },
      set(handler) {
        inner =
          typeof handler === "function"
            ? (message, extra) => {
                record("receive", message);
                handler(message, extra);
              }
            : handler;
      },
    });
    const originalSend = transport.send.bind(transport);
    transport.send = async (message, options) => {
      record("send", message);
      return originalSend(message, options);
    };
    return transport;
  };

  const config = {
    transport: "stdio",
    serverName: "acp",
    command: launch.command,
    args: launch.args,
    env: launch.env,
    cwd: launch.cwd,
    toolCallTimeoutMs: 60_000,
    failOnStartupError: true,
  };

  result.status = "handshake";
  result.client_options = {
    client_info: { name: "dsh-mcp-client", version: "0.0.1" },
    capabilities: {},
    versionNegotiation: { mode: "auto" },
  };
  result.sdk_defaults = {
    latest_protocol_version: sdk.LATEST_PROTOCOL_VERSION,
    supported_protocol_versions: sdk.SUPPORTED_PROTOCOL_VERSIONS,
    default_negotiated_protocol_version: sdk.DEFAULT_NEGOTIATED_PROTOCOL_VERSION,
  };
  result.launch = {
    command: launch.command,
    args: launch.args,
    cwd: launch.cwd,
    env_keys: Object.keys(launch.env ?? {}).sort(),
    protocol_version: launch.protocol_version,
    server: launch.server,
  };

  let client;
  let timer;
  try {
    const transport = instrument(transportModule.createTransport(config));
    client = new sdk.Client(
      { name: "dsh-mcp-client", version: "0.0.1" },
      { capabilities: {}, versionNegotiation: { mode: "auto" } },
    );
    const watchdog = new Promise((_, reject) => {
      timer = setTimeout(
        () => reject(new Error("official-client handshake exceeded 45s")),
        45_000,
      );
    });
    await Promise.race([client.connect(transport), watchdog]);
    result.phase = "tools/list";
    const listed = await Promise.race([client.listTools(), watchdog]);
    result.server = {
      server_info: client.getServerVersion() ?? null,
      capabilities: client.getServerCapabilities() ?? null,
    };
    const initialize = wire.find(
      (frame) =>
        frame.direction === "send" &&
        frame.message?.method === "initialize",
    );
    const initializeResponse = initialize
      ? wire.find(
          (frame) =>
            frame.direction === "receive" &&
            frame.message?.id === initialize.message.id &&
            frame.message?.result,
        )
      : undefined;
    result.negotiated_protocol = initializeResponse?.message?.result?.protocolVersion ?? null;
    result.requested_protocol = initialize?.message?.params?.protocolVersion ?? null;
    result.initialize_response = initializeResponse?.message?.result ?? null;
    result.tools = (listed.tools ?? []).map((tool) => ({
      name: tool.name,
      description: tool.description ?? null,
      inputSchema: tool.inputSchema ?? null,
      outputSchema: tool.outputSchema ?? null,
    }));
    result.phase = "capabilities";
    try {
      const capabilities = await Promise.race([
        client.callTool({ name: "acp_capabilities", arguments: {} }),
        watchdog,
      ]);
      result.capabilities_call = {
        isError: capabilities.isError ?? false,
        status: capabilities.structuredContent?.status ?? null,
        operation: capabilities.structuredContent?.operation ?? null,
        tools: capabilities.structuredContent?.data?.tools ?? null,
      };
    } catch (error) {
      result.capabilities_call = {
        error: { name: String(error?.name ?? "Error"), message: String(error?.message ?? error) },
      };
    }
    result.status = "ok";
    result.phase = "done";
    await client.close();
    writeResult(EXIT_OK);
  } catch (error) {
    result.status = "error";
    const attempted = wire.find(
      (frame) => frame.direction === "send" && frame.message?.method === "initialize",
    );
    result.requested_protocol = attempted?.message?.params?.protocolVersion ?? null;
    result.error = {
      name: String(error?.name ?? "Error"),
      message: String(error?.message ?? error),
      stack: typeof error?.stack === "string" ? error.stack.split("\n").slice(0, 12) : null,
    };
    if (client !== undefined) {
      try {
        await client.close();
      } catch {
        /* the handshake already failed; closing must not mask it */
      }
    }
    writeResult(EXIT_FAIL);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

main().catch((error) => {
  const out = process.argv.includes("--out")
    ? process.argv[process.argv.indexOf("--out") + 1]
    : null;
  if (out !== null) {
    mkdirSync(out, { recursive: true });
    writeFileSync(
      join(out, "dsh-client.json"),
      JSON.stringify(
        {
          schema: "acp2.c.mcp-live.official-client.v1",
          status: "error",
          phase: "start",
          error: { name: String(error?.name ?? "Error"), message: String(error?.message ?? error) },
        },
        null,
        2,
      ) + "\n",
    );
  }
  process.exitCode = EXIT_FAIL;
});
