"""FastSearch CLI - ripgrep wrapper with agent-friendly surface.

Commands: search | files | read | --self-test
ripgrep resolution: PATH -> known bundled locations -> actionable error.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

BUNDLED_RG_CANDIDATES = [
    # Trae CN bundles ripgrep for its own search
    "/usr/share/trae-cn/resources/app/node_modules/@vscode/ripgrep/bin/rg",
    # VS Code bundles
    "/usr/share/code/resources/app/node_modules/@vscode/ripgrep/bin/rg",
]

USAGE = """FastSearch usage:
  search <pattern> [path] [--type py] [-i] [--max N]   regex search, prints file:line:match
  files [path] [--glob '*.py']                         list files in a tree
Patterns use ripgrep regex syntax. Respects .gitignore by default."""

TIPS = """FastSearch tips for agents:
- Prefer --type over --glob when searching one language: --type py
- Use -i for natural-language-ish queries (casing is unpredictable in prose)
- Anchor with --max to keep context small; widen only if results look truncated
- Search before reading whole files: it is orders of magnitude cheaper"""


def find_rg() -> str | None:
    rg = shutil.which("rg")
    if rg:
        return rg
    for cand in BUNDLED_RG_CANDIDATES:
        if Path(cand).exists():
            return cand
    return None


def cmd_search(args: argparse.Namespace) -> int:
    rg = find_rg()
    if not rg:
        print("fast_search: ripgrep (rg) not found. Install ripgrep or set its path "
              "in core/adapter_paths.json.", file=sys.stderr)
        return 2
    cmd = [rg, "--line-number", "--color=never", "--no-heading",
           "--max-count", str(args.max)]
    if args.i:
        cmd.append("-i")
    if args.type:
        cmd += ["--type", args.type]
    cmd += ["--", args.pattern, args.path]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    sys.stdout.write(proc.stdout)
    if proc.returncode not in (0, 1):  # 1 = no matches, not an error
        sys.stderr.write(proc.stderr)
        return proc.returncode
    if proc.returncode == 1:
        print("(no matches)")
    return 0


def cmd_files(args: argparse.Namespace) -> int:
    rg = find_rg()
    if not rg:
        print("fast_search: ripgrep (rg) not found.", file=sys.stderr)
        return 2
    cmd = [rg, "--files", args.path]
    if args.glob:
        cmd += ["--glob", args.glob]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    sys.stdout.write(proc.stdout)
    if proc.returncode not in (0, 1):
        sys.stderr.write(proc.stderr)
        return proc.returncode
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    topics = {"usage": USAGE, "tips": TIPS}
    content = topics.get(args.topic)
    if content is None:
        print(f"fast_search: unknown topic '{args.topic}' (available: {', '.join(topics)})",
              file=sys.stderr)
        return 1
    print(content)
    return 0


def self_test() -> int:
    rg = find_rg()
    if not rg:
        print("self-test FAIL: ripgrep not found", file=sys.stderr)
        return 1
    proc = subprocess.run([rg, "--version"], capture_output=True, text=True)
    first_line = proc.stdout.splitlines()[0] if proc.stdout else "?"
    # Round-trip: search this very file for a known string
    check = subprocess.run([rg, "--color=never", "-c", "ROUNDTRIP_SENTINEL", __file__],
                           capture_output=True, text=True)
    if check.returncode != 0 or int(check.stdout.strip() or "0") < 1:
        print("self-test FAIL: roundtrip search failed", file=sys.stderr)
        return 1
    print(f"fast_search OK ({first_line})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="fast_search")
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("search")
    p.add_argument("pattern")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--type", dest="type")
    p.add_argument("-i", action="store_true")
    p.add_argument("--max", type=int, default=50)

    p = sub.add_parser("files")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--glob")

    p = sub.add_parser("read")
    p.add_argument("topic")

    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.command == "search":
        return cmd_search(args)
    if args.command == "files":
        return cmd_files(args)
    if args.command == "read":
        return cmd_read(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
