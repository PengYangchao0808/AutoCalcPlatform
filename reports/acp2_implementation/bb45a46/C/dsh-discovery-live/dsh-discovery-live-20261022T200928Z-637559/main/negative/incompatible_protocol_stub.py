#!/usr/bin/env python3
"""MCP stdio fixture: reject every protocol version this client asks for."""
import json
import os
import sys

wire = os.environ.get("STUB_WIRE")
log = open(wire, "a", encoding="utf-8") if wire else None


def record(kind, payload):
    if log:
        log.write(json.dumps({"kind": kind, "payload": payload}) + "\n")
        log.flush()


def emit(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except ValueError:
        continue
    record("recv", message)
    request_id = message.get("id")
    if message.get("method") == "initialize":
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32602,
                "message": "unsupported protocol version",
                "data": {
                    "code": "unsupported_protocol_version",
                    "supported": ["2025-06-18"],
                },
            },
        }
        emit(response)
        record("send", response)
    elif request_id is not None:
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "method not found"},
        }
        emit(response)
        record("send", response)
if log:
    log.close()
