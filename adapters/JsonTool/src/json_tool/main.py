"""JsonTool CLI - jq-lite in pure Python stdlib.

Commands: get | pretty | keys | validate | read | --self-test
Input is a JSON file, or '-' to read stdin (pipe-friendly).
"""
from __future__ import annotations

import argparse
import json
import sys

USAGE = """JsonTool usage:
  get <file|-> <path>      extract by dot path: summary.ok, items.0.name
  pretty <file|->          reformat with indentation
  keys <file|-> [path]     list object keys (top level if path omitted)
  validate <file|->        exit 0 if valid JSON, else print error location
Use '-' as the file to read from stdin, e.g.:
  visectl run note_keeper list --json | json_tool get - 0.title"""


def load_json(file_arg: str) -> tuple[object | None, str | None]:
    """Return (data, error)."""
    try:
        text = sys.stdin.read() if file_arg == "-" else open(file_arg, encoding="utf-8").read()
    except OSError as e:
        return None, f"cannot read {file_arg}: {e}"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"invalid JSON in {file_arg}: line {e.lineno} col {e.colno}: {e.msg}"


def dig(data: object, path: str) -> tuple[object | None, str | None]:
    """Walk a dot path; arrays indexed by integer components."""
    node = data
    for part in path.split("."):
        if isinstance(node, dict):
            if part not in node:
                return None, f"key not found: '{part}' (in path '{path}')"
            node = node[part]
        elif isinstance(node, list):
            if not part.isdigit() or int(part) >= len(node):
                return None, f"bad index: '{part}' (list len {len(node)})"
            node = node[int(part)]
        else:
            return None, f"cannot descend into {type(node).__name__} at '{part}'"
    return node, None


def emit(value: object) -> None:
    if isinstance(value, (dict, list)):
        print(json.dumps(value, indent=2, ensure_ascii=False))
    else:
        print(value)


def cmd_get(args: argparse.Namespace) -> int:
    data, err = load_json(args.file)
    if err:
        print(f"json_tool: {err}", file=sys.stderr)
        return 1
    node, err = dig(data, args.path)
    if err:
        print(f"json_tool: {err}", file=sys.stderr)
        return 1
    emit(node)
    return 0


def cmd_pretty(args: argparse.Namespace) -> int:
    data, err = load_json(args.file)
    if err:
        print(f"json_tool: {err}", file=sys.stderr)
        return 1
    emit(data)
    return 0


def cmd_keys(args: argparse.Namespace) -> int:
    data, err = load_json(args.file)
    if err:
        print(f"json_tool: {err}", file=sys.stderr)
        return 1
    node = data
    if args.path:
        node, err = dig(data, args.path)
        if err:
            print(f"json_tool: {err}", file=sys.stderr)
            return 1
    if not isinstance(node, dict):
        print(f"json_tool: value at path is {type(node).__name__}, not an object",
              file=sys.stderr)
        return 1
    for k in node:
        print(k)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    _, err = load_json(args.file)
    if err:
        print(f"json_tool: {err}", file=sys.stderr)
        return 1
    print(f"{args.file}: valid JSON")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    if args.topic == "usage":
        print(USAGE)
        return 0
    print(f"json_tool: unknown topic '{args.topic}' (available: usage)",
          file=sys.stderr)
    return 1


def self_test() -> int:
    data = json.loads('{"summary": {"ok": 5}, "items": [{"name": "a"}]}')
    node, err = dig(data, "items.0.name")
    assert err is None and node == "a", f"roundtrip failed: {err}"
    node, err = dig(data, "summary.ok")
    assert err is None and node == 5, f"roundtrip failed: {err}"
    _, err = dig(data, "summary.missing")
    assert err is not None, "missing key should error"
    print("json_tool OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="json_tool")
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("get")
    p.add_argument("file")
    p.add_argument("path")
    p = sub.add_parser("pretty")
    p.add_argument("file")
    p = sub.add_parser("keys")
    p.add_argument("file")
    p.add_argument("path", nargs="?")
    p = sub.add_parser("validate")
    p.add_argument("file")
    p = sub.add_parser("read")
    p.add_argument("topic")

    args = parser.parse_args()
    if args.self_test:
        return self_test()
    handlers = {"get": cmd_get, "pretty": cmd_pretty, "keys": cmd_keys,
                "validate": cmd_validate, "read": cmd_read}
    handler = handlers.get(args.command)
    if not handler:
        parser.print_help()
        return 2
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
