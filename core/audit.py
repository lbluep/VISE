#!/usr/bin/env python3
"""audit - append-only execution log for Vise state-changing commands.

The adapter guard answers "did the adapter's *files* change without a
snapshot?"; this module answers "what did agents actually *run*, when, and
what exit code came back?" — the execution-accountability counterpart.

Storage: ``.vise/audit/audit.jsonl``, one JSON object per line,
append-only. Rotation keeps ``audit.1.jsonl`` ... ``audit.{N}.jsonl``
generations (size + count below). The audit directory lives under
``.vise/`` and is machine-local (gitignored).

Privacy boundary (schema v1): a record stores the event, the adapter id and
the invoked *capability name* only. Argument values, stdin and the
environment are never recorded — http_fetch URLs can carry tokens and
adapter inputs may contain private data.

Reliability: auditing is observational. ``record()`` must never change a
command's exit code; failures are swallowed (surfaced on stderr only when
VISE_DEBUG=1).

Record schema v1::

    {"ts": "2026-10-17T09:30:12Z", "pid": 4123, "via": "cli",
     "event": "run", "adapter": "fast_search", "detail": "search",
     "rc": 0, "duration_ms": 47}

Events: run, snapshot, restore, promote, rebaseline, establish, upgrade,
clean, scaffold (register via the standalone init script is v1-unaudited).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import vise_lock

VISE_ROOT = Path(__file__).resolve().parent.parent
AUDIT_DIR = VISE_ROOT / ".vise" / "audit"
AUDIT_FILE = AUDIT_DIR / "audit.jsonl"
MAX_BYTES = 1 * 1024 * 1024
KEEP_GENERATIONS = 5
SCHEMA_VERSION = 1
DEFAULT_LIMIT = 20


def _via() -> str:
    return os.environ.get("VISE_VIA", "cli") or "cli"


def record(event: str, *, adapter: str | None = None,
           detail: str | None = None, rc: int | None = None,
           duration_ms: int | None = None) -> None:
    """Append one audit record. Never raises (observational best effort)."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pid": os.getpid(),
        "via": _via(),
        "event": event,
    }
    if adapter is not None:
        entry["adapter"] = adapter
    if detail is not None:
        entry["detail"] = detail
    if rc is not None:
        entry["rc"] = rc
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    try:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        # O_APPEND: a single short write is atomic on Linux, so concurrent
        # record() calls from un-locked `run` invocations cannot interleave.
        line = (json.dumps(entry, separators=(",", ":"),
                           ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(str(AUDIT_FILE), os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                     0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        _maybe_rotate()
    except OSError as exc:  # pragma: no cover - defensive by contract
        if os.environ.get("VISE_DEBUG"):
            print(f"audit: record failed: {exc}", file=sys.stderr)


def _maybe_rotate() -> None:
    """Rotate once the active log exceeds MAX_BYTES.

    Housekeeping is best effort: take the global write lock with a single
    non-blocking attempt (run events normally hold no lock), re-check size
    under it, and skip if another process holds it — a later event rotates.
    """
    try:
        if AUDIT_FILE.stat().st_size <= MAX_BYTES:
            return
    except OSError:
        return
    try:
        with vise_lock.write_lock("audit rotate", timeout=0):
            try:
                if AUDIT_FILE.stat().st_size <= MAX_BYTES:
                    return  # another process rotated first
                oldest = AUDIT_DIR / f"audit.{KEEP_GENERATIONS}.jsonl"
                if oldest.exists():
                    oldest.unlink()
                for gen in range(KEEP_GENERATIONS - 1, 0, -1):
                    src = AUDIT_DIR / f"audit.{gen}.jsonl"
                    if src.exists():
                        src.rename(AUDIT_DIR / f"audit.{gen + 1}.jsonl")
                AUDIT_FILE.rename(AUDIT_DIR / "audit.1.jsonl")
            except OSError as exc:  # pragma: no cover - defensive
                if os.environ.get("VISE_DEBUG"):
                    print(f"audit: rotate failed: {exc}", file=sys.stderr)
    except vise_lock.LockTimeout:
        # Busy right now; a subsequent event gets another chance.
        pass


def iter_records():
    """Yield audit records newest-first across the active log and rotations.

    Malformed lines are yielded as ``{"_parse_error": "..."}`` rather than
    aborting the query — an audit reader must keep working on a damaged tail.
    """
    paths = [AUDIT_FILE] + [AUDIT_DIR / f"audit.{gen}.jsonl"
                            for gen in range(1, KEEP_GENERATIONS + 1)]
    batches: list[list[dict]] = []
    for path in paths:
        rows: list[dict] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    rows.append({"_parse_error": line,
                                 "_source": path.name})
        except OSError:
            continue
        rows.reverse()
        batches.append(rows)
    for rows in batches:
        yield from rows


def query(adapter: str | None = None, event: str | None = None,
          limit: int = DEFAULT_LIMIT) -> list[dict]:
    out: list[dict] = []
    for row in iter_records():
        if adapter and row.get("adapter") != adapter:
            continue
        if event and row.get("event") != event:
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def cmd_query(args) -> int:
    """`visectl audit [--adapter ID] [--event E] [--limit N] [--json]`."""
    rows = query(adapter=getattr(args, "adapter", None),
                 event=getattr(args, "event", None),
                 limit=getattr(args, "limit", DEFAULT_LIMIT))
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("audit: no records")
        return 0
    for r in rows:
        if "_parse_error" in r:
            print(f"?? unreadable line in {r.get('_source')}: "
                  f"{r['_parse_error'][:80]}")
            continue
        dur = f" {r['duration_ms']}ms" if r.get("duration_ms") is not None \
            else ""
        who = f" {r['adapter']}" if r.get("adapter") else ""
        what = f" {r['detail']}" if r.get("detail") else ""
        rc = f" rc={r['rc']}" if r.get("rc") is not None else ""
        print(f"{r.get('ts', '?')}  {r['event']}{who}{what}{rc}{dur}"
              f"  [{r.get('via', '?')}]")
    return 0
