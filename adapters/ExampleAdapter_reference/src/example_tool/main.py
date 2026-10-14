"""example_tool CLI - reference Vise adapter command line.

Handles CLI commands (e.g. "example_tool list", "example_tool decode"):
argument parsing and command dispatch.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from example_tool import __version__  # noqa: E402


def cmd_list(_args: argparse.Namespace) -> int:
    print(f"example_tool v{__version__}")
    print("adapter_id : example_tool")
    print("commands   : list, decode, read")
    return 0


def cmd_decode(args: argparse.Namespace) -> int:
    data = args.text.strip()
    try:
        raw = bytes.fromhex(data) if args.hex else base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error) as exc:
        print(f"decode error: {exc}", file=sys.stderr)
        return 1
    print(raw.decode("utf-8", errors="replace"))
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    """Resource access contract: expose adapter knowledge content."""
    adapter_dir = Path(__file__).resolve().parents[2]
    if args.topic == "info":
        return cmd_list(args)
    if args.topic == "readme":
        readme = adapter_dir / "README.md"
        if not readme.is_file():
            print("read error: README.md not found", file=sys.stderr)
            return 1
        print(readme.read_text(encoding="utf-8"))
        return 0
    print(f"read error: unknown topic '{args.topic}' (info | readme)",
          file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="example_tool",
                                     description="Vise reference adapter CLI")
    parser.add_argument("--self-test", action="store_true",
                        help="run the health-check self test and exit")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="show adapter info").set_defaults(func=cmd_list)

    p_decode = sub.add_parser("decode", help="decode an encoded string")
    p_decode.add_argument("text", help="encoded input (base64 by default)")
    p_decode.add_argument("--hex", action="store_true",
                          help="interpret input as hex instead of base64")
    p_decode.set_defaults(func=cmd_decode)

    p_read = sub.add_parser("read", help="read adapter knowledge content")
    p_read.add_argument("topic", help="info | readme")
    p_read.set_defaults(func=cmd_read)

    args = parser.parse_args()
    if args.self_test:
        print("example_tool self-test OK")
        return 0
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
