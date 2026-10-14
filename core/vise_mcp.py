#!/usr/bin/env python3
"""vise_mcp - thin MCP (Model Context Protocol) stdio bridge for Vise.

Exposes the Vise CLI surface to MCP hosts (AI agents) over newline-delimited
JSON-RPC 2.0 on stdin/stdout, without changing how Vise itself works:

  tools:
    vise_list      -> visectl list --json [--category c] [--tag t]
    vise_describe  -> visectl describe <id> --json
    vise_doctor    -> visectl doctor --json
    vise_run       -> visectl run <id> [args...]

  resources:
    vise://adapters                  -> the enabled adapter list (JSON)
    vise://adapter/<id>/manifest     -> the adapter's vise.adapter.json (JSON)

Stdlib only; launch via `visectl mcp` or register it in an MCP host config:
  { "mcpServers": { "vise": { "command": "<python>", "args": ["core/vise_mcp.py"] } } }
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

VISE_ROOT = Path(__file__).resolve().parent.parent
VISECTL = VISE_ROOT / "core" / "visectl.py"
PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "vise_list",
        "description": "List enabled Vise adapters (the resource library index).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string",
                             "description": "Filter by provides.category"},
                "tag": {"type": "string",
                        "description": "Filter by provides.tags"},
            },
        },
    },
    {
        "name": "vise_describe",
        "description": "Show an adapter's full manifest: required tools, "
                       "init steps, healthcheck, commands with args.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "adapter_id": {"type": "string"},
            },
            "required": ["adapter_id"],
        },
    },
    {
        "name": "vise_doctor",
        "description": "Diagnose adapters: dependency resolution, versions, "
                       "planned init steps — without executing anything.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "vise_run",
        "description": "Run an adapter CLI command. Use vise_describe first to "
                       "learn the declared commands and their args.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "adapter_id": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"},
                         "description": "Command and arguments, e.g. "
                                        "[\"decode\", \"SGVsbG8=\"]"},
            },
            "required": ["adapter_id"],
        },
    },
]


def run_visectl(argv: list[str]) -> tuple[str, bool]:
    """Run visectl and return (output, is_error)."""
    proc = subprocess.run(
        [sys.executable, str(VISECTL)] + argv,
        capture_output=True, text=True, timeout=120,
    )
    out = (proc.stdout + proc.stderr).strip()
    return out, proc.returncode != 0


def call_tool(name: str, arguments: dict) -> dict:
    try:
        if name == "vise_list":
            argv = ["list", "--json"]
            if arguments.get("category"):
                argv += ["--category", arguments["category"]]
            if arguments.get("tag"):
                argv += ["--tag", arguments["tag"]]
        elif name == "vise_describe":
            argv = ["describe", arguments["adapter_id"], "--json"]
        elif name == "vise_doctor":
            argv = ["doctor", "--json"]
        elif name == "vise_run":
            argv = ["run", arguments["adapter_id"]]
            argv += [str(a) for a in arguments.get("args", [])]
        else:
            return {"content": [{"type": "text",
                                 "text": f"unknown tool: {name}"}],
                    "isError": True}
        out, is_error = run_visectl(argv)
        return {"content": [{"type": "text", "text": out}],
                "isError": is_error}
    except KeyError as exc:
        return {"content": [{"type": "text",
                             "text": f"missing argument: {exc}"}],
                "isError": True}
    except subprocess.TimeoutExpired:
        return {"content": [{"type": "text", "text": "vise_run timed out"}],
                "isError": True}


def list_resources() -> list[dict]:
    out, _ = run_visectl(["list", "--json"])
    resources = [{"uri": "vise://adapters",
                  "name": "Vise adapter registry (enabled adapters)",
                  "mimeType": "application/json"}]
    try:
        for a in json.loads(out):
            resources.append({
                "uri": f"vise://adapter/{a['adapter_id']}/manifest",
                "name": f"Manifest: {a['adapter_id']}",
                "mimeType": "application/json",
            })
    except json.JSONDecodeError:
        pass
    return resources


def read_resource(uri: str) -> dict:
    if uri == "vise://adapters":
        out, _ = run_visectl(["list", "--json"])
    elif uri.startswith("vise://adapter/") and uri.endswith("/manifest"):
        adapter_id = uri[len("vise://adapter/"):-len("/manifest")]
        out, _ = run_visectl(["describe", adapter_id, "--json"])
    else:
        raise ValueError(f"unknown resource: {uri}")
    return {"contents": [{"uri": uri, "mimeType": "application/json",
                          "text": out}]}


def handle(msg: dict) -> dict | None:
    """Handle one JSON-RPC message; None means no reply (notification)."""
    method = msg.get("method", "")
    msg_id = msg.get("id")

    if method.startswith("notifications/"):
        return None

    def result(value) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": value}

    def error(code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": code, "message": message}}

    if method == "initialize":
        return result({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": "vise", "version": "1.0.0"},
        })
    if method == "ping":
        return result({})
    if method == "tools/list":
        return result({"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        return result(call_tool(params.get("name", ""),
                                params.get("arguments") or {}))
    if method == "resources/list":
        return result({"resources": list_resources()})
    if method == "resources/read":
        try:
            return result(read_resource((msg.get("params") or {})["uri"]))
        except (KeyError, ValueError) as exc:
            return error(-32602, str(exc))
    return error(-32601, f"method not found: {method}")


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
