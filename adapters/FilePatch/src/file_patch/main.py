"""FilePatch CLI - controlled file editing with diff preview and backup.

Commands: preview | apply | restore | read | --self-test
New content always comes from stdin; the target file path is an argument.
"""
from __future__ import annotations

import argparse
import difflib
import shutil
import sys
import tempfile
from pathlib import Path

USAGE = """FilePatch usage (new content via stdin):
  preview <file>     print unified diff, write nothing
  apply <file>       print diff, back up to <file>.bak, then write
  apply <file> --yes quiet apply (no diff echo), still backs up
  restore <file>     restore <file> from <file>.bak
Example:  echo 'new content' | visectl run file_patch apply /abs/path.txt"""


def unified_diff(old: str, new: str, name: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{name}", tofile=f"b/{name}"))


def cmd_preview(args: argparse.Namespace) -> int:
    target = Path(args.file)
    old = target.read_text(encoding="utf-8") if target.exists() else ""
    new = sys.stdin.read()
    diff = unified_diff(old, new, target.name)
    print(diff or "(no changes)")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    target = Path(args.file)
    old = target.read_text(encoding="utf-8") if target.exists() else ""
    new = sys.stdin.read()
    diff = unified_diff(old, new, target.name)
    if not diff:
        print("(no changes)")
        return 0
    if not args.yes:
        print(diff)
    if target.exists():
        shutil.copy2(target, target.with_name(target.name + ".bak"))
        note = f" (backup: {target.name}.bak)"
    else:
        note = " (new file)"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new, encoding="utf-8")
    print(f"applied -> {target}{note}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    target = Path(args.file)
    bak = target.with_name(target.name + ".bak")
    if not bak.exists():
        print(f"file_patch: no backup found: {bak}", file=sys.stderr)
        return 1
    shutil.copy2(bak, target)
    print(f"restored {target} from {bak.name}")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    if args.topic == "usage":
        print(USAGE)
        return 0
    print(f"file_patch: unknown topic '{args.topic}' (available: usage)",
          file=sys.stderr)
    return 1


def self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "t.txt"
        f.write_text("line1\nline2\n", encoding="utf-8")
        diff = unified_diff("line1\nline2\n", "line1\nCHANGED\n", "t.txt")
        assert "-line2" in diff and "+CHANGED" in diff, "diff wrong"
        shutil.copy2(f, f.with_name("t.txt.bak"))
        f.write_text("line1\nCHANGED\n", encoding="utf-8")
        shutil.copy2(f.with_name("t.txt.bak"), f)
        assert f.read_text() == "line1\nline2\n", "restore wrong"
    print("file_patch OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="file_patch")
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("preview")
    p.add_argument("file")
    p = sub.add_parser("apply")
    p.add_argument("file")
    p.add_argument("--yes", action="store_true")
    p = sub.add_parser("restore")
    p.add_argument("file")
    p = sub.add_parser("read")
    p.add_argument("topic")

    args = parser.parse_args()
    if args.self_test:
        return self_test()
    handlers = {"preview": cmd_preview, "apply": cmd_apply,
                "restore": cmd_restore, "read": cmd_read}
    handler = handlers.get(args.command)
    if not handler:
        parser.print_help()
        return 2
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
