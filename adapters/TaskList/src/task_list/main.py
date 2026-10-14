"""TaskList CLI - persistent to-do list (sqlite, pure stdlib).

Database: $VISE_ROOT/.vise/state/task_list/tasks.db — survives sessions.
Commands: add | done | list | remove | read | --self-test
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

USAGE = """TaskList usage:
  add <title> [--priority high|medium|low]   add a task
  done <id>                                  mark done
  list [--all]                               list open tasks (--all: include done)
  remove <id>                                delete a task
  read usage|pending                         knowledge contract; 'pending' dumps open tasks"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'medium',
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    done_at TEXT
);
"""

PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def default_db_path() -> Path:
    vise_root = os.environ.get("VISE_ROOT")
    base = Path(vise_root) if vise_root else Path(__file__).resolve().parents[3]
    return base / ".vise" / "state" / "task_list" / "tasks.db"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fmt(row: tuple) -> str:
    tid, title, prio, done, created, done_at = row
    mark = "x" if done else " "
    return f"[{mark}] #{tid} ({prio}) {title}"


def cmd_add(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    cur = conn.execute(
        "INSERT INTO tasks (title, priority, created_at) VALUES (?, ?, ?)",
        (args.title, args.priority, _now()))
    conn.commit()
    print(f"added task #{cur.lastrowid}: {args.title}")
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    cur = conn.execute("UPDATE tasks SET done=1, done_at=? WHERE id=? AND done=0",
                       (_now(), args.id))
    conn.commit()
    if cur.rowcount == 0:
        print(f"task not found or already done: #{args.id}", file=sys.stderr)
        return 1
    print(f"done task #{args.id}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    where = "" if args.all else "WHERE done=0"
    rows = conn.execute(
        f"SELECT * FROM tasks {where} ORDER BY done, "
        "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id"
    ).fetchall()
    if not rows:
        print("(no tasks)")
        return 0
    for row in rows:
        print(_fmt(row))
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    cur = conn.execute("DELETE FROM tasks WHERE id=?", (args.id,))
    conn.commit()
    if cur.rowcount == 0:
        print(f"task not found: #{args.id}", file=sys.stderr)
        return 1
    print(f"removed task #{args.id}")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    if args.topic == "usage":
        print(USAGE)
        return 0
    if args.topic == "pending":
        conn = connect(args.db)
        rows = conn.execute(
            "SELECT * FROM tasks WHERE done=0 ORDER BY "
            "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id"
        ).fetchall()
        if not rows:
            print("(no open tasks)")
            return 0
        for row in rows:
            print(_fmt(row))
        return 0
    print(f"task_list: unknown topic '{args.topic}' (available: usage, pending)",
          file=sys.stderr)
    return 1


def self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        conn = connect(Path(tmp) / "t.db")
        conn.execute("INSERT INTO tasks (title, priority, created_at) VALUES ('rt', 'high', ?)",
                     (_now(),))
        conn.execute("UPDATE tasks SET done=1, done_at=? WHERE id=1", (_now(),))
        conn.commit()
        row = conn.execute("SELECT done FROM tasks WHERE id=1").fetchone()
        conn.close()
    if not row or row[0] != 1:
        print("self-test FAIL: sqlite roundtrip failed", file=sys.stderr)
        return 1
    print(f"task_list OK (db: {default_db_path()})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="task_list")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--db", type=Path, default=default_db_path(),
                        help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("add")
    p.add_argument("title")
    p.add_argument("--priority", default="medium",
                   choices=["high", "medium", "low"])
    p = sub.add_parser("done")
    p.add_argument("id", type=int)
    p = sub.add_parser("list")
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("remove")
    p.add_argument("id", type=int)
    p = sub.add_parser("read")
    p.add_argument("topic")

    args = parser.parse_args()
    if args.self_test:
        return self_test()
    handlers = {"add": cmd_add, "done": cmd_done, "list": cmd_list,
                "remove": cmd_remove, "read": cmd_read}
    handler = handlers.get(args.command)
    if not handler:
        parser.print_help()
        return 2
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
