"""NoteKeeper CLI - sqlite-backed persistent notes (pure stdlib).

Database lives at $VISE_ROOT/.vise/state/note_keeper/notes.db, so notes
survive sessions and are shared by every agent and shell on this machine.

Commands: add | get | search | list | delete | read | --self-test
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

USAGE = """NoteKeeper usage:
  add <title> <content> [--tags a,b]   store a note (titles are unique)
  get <id_or_title>                    fetch one note
  search <keyword>                     search titles + content
  list [--tag t] [--limit N]           browse notes
  delete <id>                          remove a note
  read usage|recent                    knowledge contract; 'recent' dumps latest 5 notes"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def default_db_path() -> Path:
    vise_root = os.environ.get("VISE_ROOT")
    if vise_root:
        base = Path(vise_root)
    else:
        base = Path(__file__).resolve().parents[3]
    return base / ".vise" / "state" / "note_keeper" / "notes.db"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fmt(row: sqlite3.Row | tuple, full: bool = False) -> str:
    nid, title, content, tags, created, updated = row
    head = f"#{nid} {title}  [tags: {tags or '-'}]  (updated {updated})"
    return f"{head}\n{content}" if full else head


def cmd_add(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    now = _now()
    try:
        cur = conn.execute(
            "INSERT INTO notes (title, content, tags, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (args.title, args.content, args.tags or "", now, now))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.execute(
            "UPDATE notes SET content=?, tags=?, updated_at=? WHERE title=?",
            (args.content, args.tags or "", now, args.title))
        conn.commit()
        cur = conn.execute("SELECT id FROM notes WHERE title=?", (args.title,))
        print(f"updated note #{cur.fetchone()[0]} (title existed)")
        return 0
    print(f"added note #{cur.lastrowid}: {args.title}")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    key = args.id_or_title
    if key.isdigit():
        row = conn.execute("SELECT * FROM notes WHERE id=?", (int(key),)).fetchone()
    else:
        row = conn.execute("SELECT * FROM notes WHERE title=?", (key,)).fetchone()
    if not row:
        print(f"note not found: {key}", file=sys.stderr)
        return 1
    print(_fmt(row, full=True))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    like = f"%{args.keyword}%"
    rows = conn.execute(
        "SELECT * FROM notes WHERE title LIKE ? OR content LIKE ? ORDER BY updated_at DESC",
        (like, like)).fetchall()
    if not rows:
        print("(no matches)")
        return 0
    for row in rows:
        print(_fmt(row))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    if args.tag:
        rows = conn.execute(
            "SELECT * FROM notes WHERE (',' || tags || ',') LIKE ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (f"%,{args.tag},%", args.limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM notes ORDER BY updated_at DESC LIMIT ?",
            (args.limit,)).fetchall()
    if not rows:
        print("(no notes)")
        return 0
    for row in rows:
        print(_fmt(row))
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    cur = conn.execute("DELETE FROM notes WHERE id=?", (args.id,))
    conn.commit()
    if cur.rowcount == 0:
        print(f"note not found: #{args.id}", file=sys.stderr)
        return 1
    print(f"deleted note #{args.id}")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    if args.topic == "usage":
        print(USAGE)
        return 0
    if args.topic == "recent":
        conn = connect(args.db)
        rows = conn.execute(
            "SELECT * FROM notes ORDER BY updated_at DESC LIMIT 5").fetchall()
        if not rows:
            print("(no notes yet)")
            return 0
        for row in rows:
            print(_fmt(row, full=True))
            print("---")
        return 0
    print(f"note_keeper: unknown topic '{args.topic}' (available: usage, recent)",
          file=sys.stderr)
    return 1


def self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        conn = connect(db)
        now = _now()
        conn.execute(
            "INSERT INTO notes (title, content, tags, created_at, updated_at) "
            "VALUES ('roundtrip', 'self-test body', 'test', ?, ?)", (now, now))
        conn.commit()
        row = conn.execute("SELECT * FROM notes WHERE title='roundtrip'").fetchone()
        conn.close()
    if not row or row[2] != "self-test body":
        print("self-test FAIL: sqlite roundtrip failed", file=sys.stderr)
        return 1
    print(f"note_keeper OK (db: {default_db_path()})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="note_keeper")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--db", type=Path, default=default_db_path(),
                        help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("add")
    p.add_argument("title")
    p.add_argument("content")
    p.add_argument("--tags", default="")

    p = sub.add_parser("get")
    p.add_argument("id_or_title")

    p = sub.add_parser("search")
    p.add_argument("keyword")

    p = sub.add_parser("list")
    p.add_argument("--tag")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("delete")
    p.add_argument("id", type=int)

    p = sub.add_parser("read")
    p.add_argument("topic")

    args = parser.parse_args()
    if args.self_test:
        return self_test()
    handlers = {"add": cmd_add, "get": cmd_get, "search": cmd_search,
                "list": cmd_list, "delete": cmd_delete, "read": cmd_read}
    handler = handlers.get(args.command)
    if not handler:
        parser.print_help()
        return 2
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
