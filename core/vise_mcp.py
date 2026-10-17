#!/usr/bin/env python3
"""vise_mcp - thin MCP (Model Context Protocol) stdio bridge for Vise.

Exposes the Vise CLI surface to MCP hosts (AI agents) over newline-delimited
JSON-RPC 2.0 on stdin/stdout, without changing how Vise itself works:

  tools:
    vise_list      -> visectl list --json [--category c] [--tag t]
    vise_describe  -> visectl describe <id> --json
    vise_doctor    -> visectl doctor --json
    vise_read      -> visectl read <id> [topic...]
    vise_status    -> visectl adapter status [id] --json
    vise_run       -> visectl run <id> [args...]
    vise_snapshot  -> visectl adapter snapshot <id> -m <msg>
    vise_restore   -> visectl adapter restore <id> (snapshot|--base)

  resources:
    vise://adapters                  -> the enabled adapter list (JSON)
    vise://adapter/<id>/manifest     -> the adapter's vise.adapter.json (JSON)

Destructive surface: vise_restore requires `confirm: true` in its arguments
(schema-level friction against model mistakes) and carries the MCP
destructiveHint annotation; read-only tools carry readOnlyHint.

Every subprocess is launched with VISE_VIA=mcp, so audit records written by
visectl show they came through this bridge.

Stdlib only; launch via `visectl mcp` or register it in an MCP host config:
  { "mcpServers": { "vise": { "command": "<python>", "args": ["core/vise_mcp.py"] } } }
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

VISE_ROOT = Path(__file__).resolve().parent.parent
VISECTL = VISE_ROOT / "core" / "visectl.py"
PROTOCOL_VERSION = "2024-11-05"

# Short capability calls return quickly; snapshot/restore copy whole trees.
TIMEOUT_RUN = 120
TIMEOUT_SLOW = 300

_READ_ONLY = {"readOnlyHint": True}
_WRITE = {"readOnlyHint": False}
_DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True}

TOOLS = [
    {
        "name": "vise_list",
        "description": "List enabled Vise adapters (the resource library index).",
        "annotations": _READ_ONLY,
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
        "annotations": _READ_ONLY,
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
        "annotations": _READ_ONLY,
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "vise_read",
        "description": "Read an adapter's knowledge content (visectl read). "
                       "Omit query for the index of available topics.",
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {
                "adapter_id": {"type": "string"},
                "query": {"type": "array", "items": {"type": "string"},
                          "description": "topic path, e.g. [\"pending\"]"},
            },
            "required": ["adapter_id"],
        },
    },
    {
        "name": "vise_status",
        "description": "Baseline protection status: clean / modified / "
                       "unbaselined for one adapter or all adapters.",
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {
                "adapter_id": {"type": "string",
                               "description": "omit for all adapters"},
            },
        },
    },
    {
        "name": "vise_run",
        "description": "Run an adapter CLI command. Use vise_describe first to "
                       "learn the declared commands and their args.",
        "annotations": _WRITE,
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
    {
        "name": "vise_search",
        "description": "Full-text search across adapters (identity, commands "
                       "and README knowledge). Use before describe/run when "
                       "you do not know which adapter handles a capability.",
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "kind": {"type": "string",
                         "enum": ["adapter", "command", "doc"],
                         "description": "restrict to one surface"},
                "limit": {"type": "integer", "description": "max matches"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "vise_snapshot",
        "description": "Open a protected snapshot of an adapter BEFORE "
                       "editing its files (records why). Required before "
                       "modifying an adapter that will still be run.",
        "annotations": _WRITE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "adapter_id": {"type": "string"},
                "message": {"type": "string",
                            "description": "why the adapter is about to be "
                                           "modified (must not be blank)"},
                "force": {"type": "boolean",
                          "description": "snapshot even if one is already open"},
            },
            "required": ["adapter_id", "message"],
        },
    },
    {
        "name": "vise_restore",
        "description": "Overwrite the adapter's working tree with a snapshot "
                       "generation or the pristine baseline. DESTRUCTIVE: "
                       "uncommitted edits are lost. Requires confirm=true.",
        "annotations": _DESTRUCTIVE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "adapter_id": {"type": "string"},
                "target": {"type": "string", "enum": ["snapshot", "base"],
                           "description": "snapshot = a saved generation; "
                                          "base = the pristine baseline"},
                "gen": {"type": "integer",
                        "description": "snapshot generation (target=snapshot); "
                                       "omit for the newest"},
                "force": {"type": "boolean",
                          "description": "allow rolling back across a Vise "
                                         "upgrade (target=base)"},
                "confirm": {"type": "boolean",
                            "description": "must be explicitly true"},
            },
            "required": ["adapter_id", "target", "confirm"],
        },
    },
]


def _server_version() -> str:
    try:
        path = VISE_ROOT / "core" / "version.py"
        spec = importlib.util.spec_from_file_location("vise_mcp_version", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return str(getattr(mod, "VERSION", "0.0.0"))
    except (OSError, ImportError, AttributeError):
        return "0.0.0"


def run_visectl(argv: list[str], timeout: int = TIMEOUT_RUN) -> tuple[str, bool]:
    """Run visectl and return (output, is_error)."""
    env = dict(os.environ, VISE_VIA="mcp")
    try:
        proc = subprocess.run(
            [sys.executable, str(VISECTL)] + argv,
            capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return f"vise call timed out after {timeout}s", True
    out = (proc.stdout + proc.stderr).strip()
    return out, proc.returncode != 0


def call_tool(name: str, arguments: dict) -> dict:
    try:
        timeout = TIMEOUT_RUN
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
        elif name == "vise_read":
            argv = ["read", arguments["adapter_id"]]
            argv += [str(q) for q in arguments.get("query", [])]
        elif name == "vise_status":
            argv = ["adapter", "status"]
            if arguments.get("adapter_id"):
                argv += [arguments["adapter_id"]]
            argv += ["--json"]
        elif name == "vise_run":
            argv = ["run", arguments["adapter_id"]]
            argv += [str(a) for a in arguments.get("args", [])]
        elif name == "vise_search":
            argv = ["search", str(arguments["query"])]
            if arguments.get("kind"):
                argv += ["--kind", str(arguments["kind"])]
            if arguments.get("limit") is not None:
                argv += ["--limit", str(arguments["limit"])]
            argv.append("--json")
        elif name == "vise_snapshot":
            message = arguments["message"]
            if not str(message).strip():
                return _err("message must not be blank - record WHY the "
                            "adapter is about to be modified")
            argv = ["adapter", "snapshot", arguments["adapter_id"],
                    "-m", str(message), "--json"]
            if arguments.get("force"):
                argv.append("--force")
            timeout = TIMEOUT_SLOW
        elif name == "vise_restore":
            if arguments.get("confirm") is not True:
                return _err("restore overwrites the adapter's working tree; "
                            "re-call with confirm=true to proceed")
            argv = ["adapter", "restore", arguments["adapter_id"]]
            target = arguments.get("target")
            if target == "base":
                argv.append("--base")
                if arguments.get("force"):
                    argv.append("--force")
            elif target == "snapshot":
                if arguments.get("gen") is not None:
                    argv += ["--gen", str(arguments["gen"])]
            else:
                return _err("target must be 'snapshot' or 'base'")
            argv.append("--json")
            timeout = TIMEOUT_SLOW
        else:
            return _err(f"unknown tool: {name}")
        out, is_error = run_visectl(argv, timeout=timeout)
        return {"content": [{"type": "text", "text": out}],
                "isError": is_error}
    except KeyError as exc:
        return _err(f"missing argument: {exc}")


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


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
            "capabilities": {"tools": {"listChanged": False},
                             "resources": {}},
            "serverInfo": {"name": "vise", "version": _server_version()},
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
