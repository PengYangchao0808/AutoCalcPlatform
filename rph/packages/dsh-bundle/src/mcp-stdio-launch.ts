/**
 * The **named launcher** for the MCP stdio child (M1-06 / M3-02 seam).
 *
 * This is the only RPH source file allowed to start a process (`check-boundaries`
 * allowlists exactly this path).  It owns the child lifecycle and nothing else:
 * the frame protocol lives in `mcp-stdio.ts`, the bridge semantics live in
 * `@rph/bridge`, and the launcher never interprets a frame.
 *
 * The child is `acp mcp serve --controller <url>` (the B05/B06 adapter): a thin
 * stdio transport over the already-running Controller.  Starting it is the
 * caller's explicit decision (CLI/acceptance), never a side effect of importing
 * the bundle.
 */

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";

import { McpStdioClient, type McpTransport } from "./mcp-stdio.js";

export interface McpLaunchConfig {
  /** Executable, e.g. `python3.11`. */
  readonly command: string;
  /** Arguments, e.g. `["-m", "acp.cli", "mcp", "serve", "--controller", url]`. */
  readonly args: readonly string[];
  readonly cwd?: string;
  readonly env?: Readonly<Record<string, string>>;
  /** Per-request timeout of the MCP client. */
  readonly timeoutMs?: number;
}

export interface LaunchedMcp {
  readonly client: McpStdioClient;
  /** Child pid (diagnostics only; never an identity). */
  readonly pid: number | undefined;
  /** stderr tail (bounded) for diagnosis; stdout is protocol-only. */
  stderrTail(): string;
  /** Close stdin, then wait for exit (SIGKILL after the grace period). */
  close(graceMs?: number): Promise<void>;
}

class ChildTransport implements McpTransport {
  private readonly child: ChildProcessWithoutNullStreams;
  private lineHandler: ((line: string) => void) | null = null;
  private errorHandler: ((error: Error) => void) | null = null;
  private closeHandler: ((error: Error | null) => void) | null = null;
  private buffer = "";
  private closed = false;

  constructor(child: ChildProcessWithoutNullStreams) {
    this.child = child;
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => this.consume(chunk));
    child.stdout.on("error", (error: Error) => this.emitError(error));
    child.on("error", (error: Error) => this.emitError(error));
    child.on("close", (code: number | null, signal: NodeJS.Signals | null) => {
      this.closed = true;
      const handler = this.closeHandler;
      if (handler === null) {
        return;
      }
      handler(
        code === 0 || signal === "SIGTERM"
          ? null
          : new Error(`MCP child exited with code ${String(code)} signal ${String(signal)}`),
      );
    });
  }

  private consume(chunk: string): void {
    this.buffer += chunk;
    let index = this.buffer.indexOf("\n");
    while (index >= 0) {
      const line = this.buffer.slice(0, index).replace(/\r$/, "");
      this.buffer = this.buffer.slice(index + 1);
      if (this.lineHandler !== null && line.trim().length > 0) {
        this.lineHandler(line);
      }
      index = this.buffer.indexOf("\n");
    }
  }

  private emitError(error: Error): void {
    this.errorHandler?.(error);
  }

  send(frame: string): void {
    if (this.closed || this.child.stdin.destroyed) {
      throw new Error("MCP child stdin is closed");
    }
    this.child.stdin.write(`${frame}\n`);
  }

  onLine(handler: (line: string) => void): void {
    this.lineHandler = handler;
  }

  onError(handler: (error: Error) => void): void {
    this.errorHandler = handler;
  }

  onClose(handler: (error: Error | null) => void): void {
    this.closeHandler = handler;
  }

  close(): void {
    if (this.closed) {
      return;
    }
    try {
      this.child.stdin.end();
    } catch {
      // The child may already be gone; close() is idempotent.
    }
  }
}

const STDERR_TAIL_LIMIT = 8192;

/**
 * The one approved child argv: `python -m acp mcp serve --controller <url>`.
 *
 * The argv lives in this file (and only here) so `check-boundaries` can keep a
 * single literal allowlist for "something that looks like an MCP controller
 * adapter": a second module spelling `mcp serve` is a gate failure, not a
 * convention.
 */
export function acpMcpServeArgs(controllerUrl: string): readonly string[] {
  if (typeof controllerUrl !== "string" || controllerUrl.length === 0) {
    throw new TypeError("acpMcpServeArgs requires a controller URL");
  }
  return Object.freeze([
    "-m",
    "acp",
    "mcp",
    "serve",
    "--transport",
    "stdio",
    "--controller",
    controllerUrl,
  ]);
}

/**
 * Launch `acp mcp serve` and wrap its stdio in an `McpStdioClient`.
 *
 * Throws a `TypeError` for a missing command; a child that cannot start is
 * reported through the transport's error/close handlers (the first request then
 * fails with a typed `McpClientError`).
 */
export function launchMcpStdio(config: McpLaunchConfig): LaunchedMcp {
  if (typeof config.command !== "string" || config.command.length === 0) {
    throw new TypeError("launchMcpStdio requires a command");
  }
  const child = spawn(config.command, [...config.args], {
    cwd: config.cwd,
    env: { ...process.env, ...(config.env ?? {}) },
    stdio: ["pipe", "pipe", "pipe"],
  });
  let stderr = "";
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk: string) => {
    stderr = (stderr + chunk).slice(-STDERR_TAIL_LIMIT);
  });
  const transport = new ChildTransport(child);
  const client = new McpStdioClient(
    transport,
    config.timeoutMs === undefined ? {} : { timeoutMs: config.timeoutMs },
  );
  return {
    client,
    pid: child.pid,
    stderrTail: () => stderr,
    async close(graceMs = 5000): Promise<void> {
      client.close();
      if (child.exitCode !== null || child.signalCode !== null) {
        return;
      }
      const exited = new Promise<void>((resolve) => {
        child.once("close", () => resolve());
      });
      const timer = setTimeout(() => {
        child.kill("SIGKILL");
      }, graceMs);
      if (typeof timer === "object" && "unref" in timer) {
        timer.unref();
      }
      await exited;
      clearTimeout(timer);
    },
  };
}
