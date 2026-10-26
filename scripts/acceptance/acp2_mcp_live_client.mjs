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
 *        [--scenario default|incompatible-client]
 *
 * `--scenario default` uses exactly the plugin's production constructor options
 * (`{ capabilities: {}, versionNegotiation: { mode: 'auto' } }`) and expects a
 * successful handshake. `--scenario incompatible-client` additionally pins the
 * client's own `supportedProtocolVersions` to a single version the server does
 * not speak: the server still answers its own version (M0-08), so the *client*
 * must refuse on its own — recorded as `status: "refused"` with exit 0. Any
 * other outcome is an error.
 *
 * Exit codes: 0 handshake succeeded (or refused as expected by the scenario);
 * 1 unexpected handshake outcome; 2 prerequisite (launch / DSH client / SDK)
 * missing.
 */

import { createRequire } from "node:module";
import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";

const EXIT_OK = 0;
const EXIT_FAIL = 1;
const EXIT_PREREQ = 2;
const SCENARIOS = new Set(["default", "incompatible-client"]);
const INCOMPATIBLE_CLIENT_VERSIONS = ["2025-11-25"];
// M0-09 handshake identity (authority: p2-research-operations.v1.json; the
// frozen bridge declares the same pair, so a drift fails this script too).
const RESEARCH_EXTENSION_ID = "acp.research.v1";
const RESEARCH_EXTENSION_VERSION = "1.0.0";

function parseArgs(argv) {
  const args = { launch: null, out: null, scenario: "default" };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--launch" || key === "--out" || key === "--scenario") {
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
  if (!SCENARIOS.has(args.scenario)) {
    throw new Error(`unknown scenario ${args.scenario}`);
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
    scenario: args.scenario,
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
    ...(args.scenario === "incompatible-client"
      ? { supportedProtocolVersions: [...INCOMPATIBLE_CLIENT_VERSIONS] }
      : {}),
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
      args.scenario === "incompatible-client"
        ? {
            capabilities: {},
            versionNegotiation: { mode: "auto" },
            supportedProtocolVersions: [...INCOMPATIBLE_CLIENT_VERSIONS],
          }
        : { capabilities: {}, versionNegotiation: { mode: "auto" } },
    );
    const watchdog = new Promise((_, reject) => {
      timer = setTimeout(
        () => reject(new Error("official-client handshake exceeded 45s")),
        45_000,
      );
    });
    await Promise.race([client.connect(transport), watchdog]);
    if (args.scenario === "incompatible-client") {
      // This scenario exists to observe the client refusing an unsupported
      // version; a successful connect is the wrong outcome.
      result.status = "error";
      result.error = {
        name: "UnexpectedConnect",
        message:
          "the incompatible-client scenario connected although the server version is not supported",
      };
      await client.close();
      writeResult(EXIT_FAIL);
      return;
    }
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
        extensions: capabilities.structuredContent?.data?.extensions ?? null,
      };
    } catch (error) {
      result.capabilities_call = {
        error: { name: String(error?.name ?? "Error"), message: String(error?.message ?? error) },
      };
    }
    // M0-09: the application-level research-extension handshake. The frozen
    // client sends capabilities:{} at initialize, so the request travels in the
    // P1 discovery tool (never in initialize) and the answer is extensions[].
    result.phase = "research_handshake";
    try {
      const handshake = await Promise.race([
        client.callTool({
          name: "acp_capabilities",
          arguments: {
            research_extension: {
              id: RESEARCH_EXTENSION_ID,
              version: RESEARCH_EXTENSION_VERSION,
            },
            client: { name: "dsh-mcp-client", version: "0.0.1" },
          },
        }),
        watchdog,
      ]);
      result.research_handshake = {
        tool: "acp_capabilities",
        isError: handshake.isError ?? false,
        status: handshake.structuredContent?.status ?? null,
        extensions: handshake.structuredContent?.data?.extensions ?? null,
        error_code: handshake.structuredContent?.error?.code ?? null,
        error_params: handshake.structuredContent?.error?.params ?? null,
        content_text_is_json: (() => {
          const block = (handshake.content ?? []).find((item) => item?.type === "text");
          if (typeof block?.text !== "string") return null;
          try {
            JSON.parse(block.text);
            return true;
          } catch {
            return false;
          }
        })(),
      };
    } catch (error) {
      result.research_handshake = {
        tool: "acp_capabilities",
        error: { name: String(error?.name ?? "Error"), message: String(error?.message ?? error) },
      };
    }
    result.status = "ok";
    result.phase = "done";
    await client.close();
    writeResult(EXIT_OK);
  } catch (error) {
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
    if (args.scenario === "incompatible-client" && attempted !== undefined) {
      // The server answered its own version; refusing it is the client's call.
      const answered = wire.find(
        (frame) =>
          frame.direction === "receive" &&
          frame.message?.id === attempted.message.id &&
          frame.message?.result,
      );
      result.status = "refused";
      result.phase = "done";
      result.negotiated_protocol = answered?.message?.result?.protocolVersion ?? null;
      result.tool_list_attempted = false;
      writeResult(EXIT_OK);
      return;
    }
    result.status = "error";
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
