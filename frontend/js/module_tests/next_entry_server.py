#!/usr/bin/env python3
"""Isolated ACP service launcher for the /next/ browser integration lane.

Mirrors the live-backend pattern of
``scripts/acceptance/acp2_experience_scenario_manual_browser_fixtures.py``
(``_start_live_backend``): real ``acp.api.server.create_app`` on an ephemeral
loopback port with a temporary ``ACP_RUN_ROOT`` — never the production service,
never port 8765, never a production run_root.

Usage::

    python3 frontend/js/module_tests/next_entry_server.py [--port N] [--run-root DIR]

* ``--port``      — loopback port; ``0``/omitted picks a free ephemeral port.
* ``--run-root``  — use this run_root instead of creating a temp one. When a
  temp run_root is created (default ``/tmp/opencode/acp-next-e2e-<pid>``) it is
  removed on shutdown; a caller-provided run_root is left untouched.

Prints ``READY <base_url> run_root=<dir>`` on stdout (flushed) once the server
answers ``GET /`` with 200, so a driver script can wait deterministically.
Shutdown: SIGINT/SIGTERM (or ``server.should_exit``) stops uvicorn, then the
temp run_root is removed.
"""

from __future__ import annotations

import argparse
import atexit
import os
import shutil
import signal
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _health_ok(base_url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(base_url + "/", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--port", type=int, default=0,
                        help="loopback port (0 = ephemeral free port)")
    parser.add_argument("--run-root", type=str, default=None,
                        help="explicit run_root (default: temp dir under /tmp/opencode, removed on exit)")
    args = parser.parse_args()

    owns_run_root = args.run_root is None
    if owns_run_root:
        base_tmp = Path(os.environ.get("TMPDIR") or "/tmp/opencode")
        base_tmp.mkdir(parents=True, exist_ok=True)
        run_root = Path(tempfile.mkdtemp(prefix="acp-next-e2e-", dir=str(base_tmp)))
    else:
        run_root = Path(args.run_root)
        run_root.mkdir(parents=True, exist_ok=True)

    port = args.port if args.port and args.port > 0 else _free_port()

    # ``acp.api.server`` builds a module-level app at import time; pin its
    # run_root to the temp directory BEFORE the import so no production path
    # is ever resolved.
    os.environ["ACP_RUN_ROOT"] = str(run_root)
    os.environ["ACP_HOST"] = "127.0.0.1"
    os.environ["ACP_PORT"] = str(port)

    import uvicorn  # noqa: PLC0415 — after env pinning on purpose

    from acp.api.server import create_app

    app = create_app(run_root=run_root, host="127.0.0.1", port=port, max_running=1)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="on",
            access_log=False,
        )
    )

    base_url = "http://127.0.0.1:%d" % port

    def _announce() -> None:
        # Wait for startup, then probe GET / before declaring READY.
        deadline = time.monotonic() + 60.0
        while not server.started:
            if time.monotonic() > deadline:
                print("ERROR live backend did not start within 60s", flush=True)
                server.should_exit = True
                return
            time.sleep(0.05)
        health_deadline = time.monotonic() + 30.0
        while not _health_ok(base_url):
            if time.monotonic() > health_deadline:
                print("ERROR live backend started but GET / never answered 200",
                      flush=True)
                server.should_exit = True
                return
            time.sleep(0.05)
        print("READY %s run_root=%s" % (base_url, run_root), flush=True)

    threading.Thread(target=_announce, name="next-entry-server-announce", daemon=True).start()

    # Deterministic teardown. uvicorn installs its own SIGINT/SIGTERM capture
    # when Server.run() drives the loop in the main thread; that path can exit
    # without returning into our ``finally``. So: (a) request a graceful stop
    # from our own handlers (uvicorn still owns the loop), and (b) guarantee
    # the temp run_root is removed via ``atexit`` no matter how we wind down.
    def _cleanup_run_root() -> None:
        if owns_run_root:
            shutil.rmtree(run_root, ignore_errors=True)
            print("STOPPED %s run_root_removed=True" % base_url, flush=True)

    atexit.register(_cleanup_run_root)

    def _handle_signal(signum, frame):  # noqa: ARG001
        server.should_exit = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    server.run()  # main thread; returns once should_exit drains the loop
    return 0


if __name__ == "__main__":
    sys.exit(main())
