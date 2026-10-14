"""RepoMap CLI - repository orientation for AI assistants.

Commands: overview | status | log | tree | read | --self-test
git runs via subprocess; paths may point anywhere (absolute recommended).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

USAGE = """RepoMap usage:
  overview [path]        branch + changes + recent commits + layout + languages
  status [path]          modified/untracked files (porcelain)
  log [n]                recent commits, one per line
  tree [path] [--depth N]  directory tree (skips .git/.venv/node_modules)
Pass absolute paths; the adapter cwd is its own directory."""

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".vise"}


def git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), *args],
                          capture_output=True, text=True, timeout=30)


def require_repo(path: Path) -> Path | None:
    proc = git(path, "rev-parse", "--show-toplevel")
    if proc.returncode != 0:
        print(f"repo_map: {path} is not inside a git repository", file=sys.stderr)
        return None
    return Path(proc.stdout.strip())


def cmd_overview(args: argparse.Namespace) -> int:
    path = Path(args.path).resolve()
    root = require_repo(path)
    if not root:
        return 1
    branch = git(path, "branch", "--show-current").stdout.strip() or "(detached)"
    porc = git(path, "status", "--porcelain").stdout.splitlines()
    modified = sum(1 for ln in porc if ln[:2].strip() and not ln.startswith("??"))
    untracked = sum(1 for ln in porc if ln.startswith("??"))
    commits = git(path, "log", "--oneline", "-5").stdout.strip()

    print(f"root:     {root}")
    print(f"branch:   {branch}")
    print(f"changes:  {modified} modified, {untracked} untracked")
    print("recent commits:")
    print(commits or "  (none)")

    exts: Counter[str] = Counter()
    for p in root.rglob("*"):
        if p.is_file() and not (set(p.parts) & SKIP_DIRS):
            exts[p.suffix or "(none)"] += 1
    top = ", ".join(f"{e}:{n}" for e, n in exts.most_common(8))
    print(f"languages: {top}")
    print("top-level:")
    for p in sorted(root.iterdir()):
        if p.name not in SKIP_DIRS:
            print(f"  {'[dir] ' if p.is_dir() else ''}{p.name}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    path = Path(args.path).resolve()
    if not require_repo(path):
        return 1
    out = git(path, "status", "--porcelain").stdout
    print(out.strip() or "(clean)")
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    path = Path(args.path).resolve()
    if not require_repo(path):
        return 1
    proc = git(path, "log", "--oneline", f"-{args.n}")
    print(proc.stdout.strip() or "(no commits)")
    return 0


def cmd_tree(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"repo_map: not a directory: {root}", file=sys.stderr)
        return 1

    def walk(d: Path, prefix: str, depth: int) -> None:
        if depth > args.depth:
            return
        entries = sorted((e for e in d.iterdir() if e.name not in SKIP_DIRS),
                         key=lambda e: (e.is_file(), e.name))
        for i, e in enumerate(entries):
            last = i == len(entries) - 1
            print(f"{prefix}{'└── ' if last else '├── '}{e.name}")
            if e.is_dir():
                walk(e, prefix + ("    " if last else "│   "), depth + 1)

    print(root)
    walk(root, "", 1)
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    if args.topic == "usage":
        print(USAGE)
        return 0
    print(f"repo_map: unknown topic '{args.topic}' (available: usage)",
          file=sys.stderr)
    return 1


def self_test() -> int:
    proc = subprocess.run(["git", "--version"], capture_output=True, text=True)
    if proc.returncode != 0:
        print("self-test FAIL: git not found", file=sys.stderr)
        return 1
    # tree on the adapter dir itself (offline, no repo assumption)
    ns = argparse.Namespace(path=str(Path(__file__).resolve().parents[2]), depth=1)
    if cmd_tree(ns) != 0:
        print("self-test FAIL: tree failed", file=sys.stderr)
        return 1
    print(f"repo_map OK ({proc.stdout.strip()})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="repo_map")
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("overview")
    p.add_argument("path", nargs="?", default=".")
    p = sub.add_parser("status")
    p.add_argument("path", nargs="?", default=".")
    p = sub.add_parser("log")
    p.add_argument("n", nargs="?", type=int, default=10)
    p = sub.add_parser("tree")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--depth", type=int, default=2)
    p = sub.add_parser("read")
    p.add_argument("topic")

    args = parser.parse_args()
    if args.self_test:
        return self_test()
    handlers = {"overview": cmd_overview, "status": cmd_status, "log": cmd_log,
                "tree": cmd_tree, "read": cmd_read}
    handler = handlers.get(args.command)
    if not handler:
        parser.print_help()
        return 2
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
