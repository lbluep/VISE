#!/usr/bin/env python3
"""adapter_guard.py - protect, roll back and sediment changes to adapter code.

The workflow this module exists for: an agent calls `visectl run <id>`, finds
the adapter solves *most* of its problem but not all of it, and edits the
adapter's source. That edit needs a way back, and a way to become permanent.

  1. `snapshot` the adapter BEFORE touching it  (mandatory - see Gate)
  2. edit the adapter source (file_patch is the sanctioned writer)
  3. validate with `viseinit <id>` (or the adapter's own `--self-test`)
  4. failing  -> `restore` the snapshot   (reject the experiment)
     passing  -> `promote` a patch        (sediment it - never auto-commits)

Git-independent by design: someone who installed Vise from a zip or tarball
has no repository, yet needs the same protection. When a repository *is*
present, `promote` still only writes the patch; committing stays a separate,
deliberate act (C13).

Gate
----
A snapshot is *open* from the moment it is taken until the reference is
explicitly accepted. An adapter whose tree drifted from its baseline while no
snapshot is open is **unprotected**: `visectl run` and `promote` refuse it
(exit 7) and point at `snapshot` or `restore`. `--allow-modified` is the
escape hatch for a deliberate manual customization.

    protected  ==  clean, or unbaselined, or an open snapshot exists
    violation  ==  modified AND no open snapshot

Restore and promote deliberately *keep* the snapshot open: it remains the
rollback point for the change that was just accepted, so the adapter stays
usable. Only `rebaseline` closes snapshots - it is the explicit "this state is
now the reference" act, and it is what makes the adapter clean again.

Storage (generated, never committed, C7):

    .vise/snapshots/<adapter_id>/<ts>.json   snapshot metadata
    .vise/snapshots/<adapter_id>/<ts>/       full copy of the adapter tree
    .vise/patches/<adapter_id>/<ts>_<slug>.patch

Retention: the newest MAX_GENERATIONS snapshots are kept per adapter, plus the
newest *open* snapshot regardless of age (it is the rollback point for work in
progress). Older open snapshots are pruned like any other, so `.vise/` stays
bounded even for an adapter edited repeatedly.

Exit codes (agent-friendly, guard domain - distinct from the `viseinit` 0-6
map and from the manifest domain). The code reflects the *outcome*; it never
depends on whether `--json` was requested:
  snapshot                   0 ok (created, or already open), 2 unknown adapter
  list                       0 ok
  restore                    0 restored/dry-run, 2 unknown adapter,
                             4 no such generation / no snapshot / no baseline
  promote                    0 promoted/dry-run, 2 unknown adapter,
                             4 nothing to promote, 7 refused (C13 gate)
  check                      0 all protected, 2 no adapters registered,
                             7 unprotected drift
  rebaseline                 0 ok, 2 no adapter selected, 7 refused (modified;
                             --force is required to accept the tree)
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

import baseline as vise_baseline

VISE_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS_DIR = VISE_ROOT / ".vise" / "snapshots"
PATCHES_DIR = VISE_ROOT / ".vise" / "patches"
SNAPSHOT_VERSION = 1
MAX_GENERATIONS = 5

OPEN = "open"
CLOSED = "closed"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 4
EXIT_UNPROTECTED = 7


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _slug(text: str, limit: int = 40) -> str:
    keep = [c if c.isalnum() else "-" for c in text.strip().lower()]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return (slug[:limit].rstrip("-")) or "change"


def _snapshot_meta_path(adapter_id: str, ts: str) -> Path:
    return SNAPSHOTS_DIR / adapter_id / f"{ts}.json"


def _snapshot_tree_dir(adapter_id: str, ts: str) -> Path:
    return SNAPSHOTS_DIR / adapter_id / ts


def _unique_ts(adapter_id: str) -> str:
    """Second-precision, always zero-padded ordinal so sorts stay chronological.

    A bare `...Z` would sort *before* a `...Z-2` written in the same second,
    which would silently invert "generation 0 = newest".
    """
    base = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    n = 1
    while True:
        ts = f"{base}-{n:03d}"
        if not (SNAPSHOTS_DIR / adapter_id / ts).exists() \
                and not _snapshot_meta_path(adapter_id, ts).exists():
            return ts
        n += 1


def _copy_patterns() -> list[str]:
    return (list(vise_baseline.IGNORE_DIRS)
            + [f"*{s}" for s in vise_baseline.IGNORE_SUFFIXES]
            + list(vise_baseline.IGNORE_NAMES))


# --- reading snapshots -------------------------------------------------------

def list_snapshots(adapter_id: str) -> list[dict]:
    """Snapshot metadata, newest first."""
    d = SNAPSHOTS_DIR / adapter_id
    if not d.is_dir():
        return []
    metas = []
    for path in sorted(d.glob("*.json"), reverse=True):
        if not _snapshot_tree_dir(adapter_id, path.stem).is_dir():
            continue  # metadata without a tree is unusable
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        meta.setdefault("ts", path.stem)
        meta.setdefault("status", CLOSED)
        metas.append(meta)
    return metas


def open_snapshot(adapter_id: str) -> dict | None:
    """The newest snapshot still awaiting an accept/reject decision."""
    for meta in list_snapshots(adapter_id):
        if meta.get("status") == OPEN:
            return meta
    return None


def check(adapter_id: str) -> dict:
    """Gate evaluation. See the module docstring for the rule."""
    src = vise_baseline.adapter_dir(adapter_id)
    if src is None:
        return {"adapter_id": adapter_id, "state": "unknown", "ok": False,
                "protected": False, "note": "unknown adapter"}
    d = vise_baseline.drift(adapter_id)
    state = "unbaselined" if not d["baselined"] else (
        "clean" if d["clean"] else "modified")
    snap = open_snapshot(adapter_id)
    protected = state in ("clean", "unbaselined") or snap is not None
    note = ""
    if state == "unbaselined":
        note = "no baseline - run: bash init/setup.sh"
    elif state == "modified" and snap is None:
        note = ("modified with no open snapshot - run one of: "
                f"visectl adapter snapshot {adapter_id} -m \"why\", "
                f"visectl adapter restore {adapter_id}, "
                f"visectl adapter rebaseline {adapter_id}")
    return {"adapter_id": adapter_id, "state": state, "ok": protected,
            "protected": protected, "open_snapshot": snap["ts"] if snap else None,
            "note": note, "drift": d if state == "modified" else None}


# --- operations --------------------------------------------------------------

def snapshot(adapter_id: str, reason: str, triggered_by: str | None = None,
             force: bool = False) -> dict:
    """Copy the adapter tree and open a snapshot for it."""
    src = vise_baseline.adapter_dir(adapter_id)
    if src is None:
        return {"adapter_id": adapter_id, "status": "unknown-adapter"}
    existing = open_snapshot(adapter_id)
    if existing is not None and not force:
        return {"adapter_id": adapter_id, "status": "already-open",
                "ts": existing["ts"], "reason": existing.get("reason")}

    ts = _unique_ts(adapter_id)
    dest = _snapshot_tree_dir(adapter_id, ts)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(*_copy_patterns()))

    d = vise_baseline.drift(adapter_id)
    files = vise_baseline.scan(src)
    base = vise_baseline.load(adapter_id)
    meta = {
        "version": SNAPSHOT_VERSION,
        "adapter_id": adapter_id,
        "ts": ts,
        "created_at": _utcnow(),
        "reason": reason,
        "triggered_by": triggered_by,
        "status": OPEN,
        "baseline_established_at": (base or {}).get("established_at"),
        "taken_while": "clean" if d.get("clean") else "modified",
        "tree_sha": vise_baseline.tree_sha(files),
        "files": len(files),
    }
    path = _snapshot_meta_path(adapter_id, ts)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)

    pruned = _prune(adapter_id)
    return {"adapter_id": adapter_id, "status": "created", "ts": ts,
            "reason": reason, "taken_while": meta["taken_while"],
            "files": meta["files"], "pruned": pruned}


def _prune(adapter_id: str) -> list[str]:
    """Bound the history to MAX_GENERATIONS, keeping the actionable anchor.

    The newest MAX_GENERATIONS are kept, plus - regardless of age - the newest
    *open* snapshot, because that is the rollback point for work still in
    progress. Older open snapshots are pruned like any other: an adapter edited
    repeatedly (the normal case, since a snapshot can be stacked with `--force`)
    must not grow `.vise/` without bound.
    """
    metas = list_snapshots(adapter_id)                     # newest first
    keep = {meta["ts"] for meta in metas[:MAX_GENERATIONS]}
    newest_open = next((m for m in metas if m.get("status") == OPEN), None)
    if newest_open is not None:
        keep.add(newest_open["ts"])
    removed = []
    for meta in metas:
        ts = meta["ts"]
        if ts in keep:
            continue
        shutil.rmtree(_snapshot_tree_dir(adapter_id, ts), ignore_errors=True)
        _snapshot_meta_path(adapter_id, ts).unlink(missing_ok=True)
        removed.append(ts)
    return removed


def restore(adapter_id: str, gen: int | None = None,
            to_baseline: bool = False, dry_run: bool = False) -> dict:
    """Reinstate a snapshot (or the baseline) byte-for-byte."""
    src = vise_baseline.adapter_dir(adapter_id)
    if src is None:
        return {"adapter_id": adapter_id, "status": "unknown-adapter"}
    metas = list_snapshots(adapter_id)

    if to_baseline:
        base_tree = vise_baseline.tree_dir(adapter_id)
        if vise_baseline.load(adapter_id) is None or not base_tree.is_dir():
            return {"adapter_id": adapter_id, "status": "no-baseline",
                    "reason": "run: bash init/setup.sh"}
        origin, ts = base_tree, "baseline"
    else:
        if not metas:
            return {"adapter_id": adapter_id, "status": "no-snapshot",
                    "reason": f"no snapshot for {adapter_id}; see --base"}
        index = gen if gen is not None else 0
        if index < 0 or index >= len(metas):
            return {"adapter_id": adapter_id, "status": "no-such-generation",
                    "reason": f"generation {index} out of range "
                              f"(0..{len(metas) - 1}, 0 = newest)"}
        origin, ts = _snapshot_tree_dir(adapter_id, metas[index]["ts"]), \
            metas[index]["ts"]

    if dry_run:
        return {"adapter_id": adapter_id, "status": "dry-run", "from": ts,
                "files": len(vise_baseline.iter_files(origin))}

    # Reinstate in place: overwrite what the origin has, then drop what the
    # origin lacks. Copying before deleting means an interrupted restore leaves
    # a superset of the origin (recoverable by re-running) rather than a
    # half-destroyed adapter. Untracked noise (__pycache__, *.bak, ...) is
    # skipped by iter_files, so it survives untouched either way.
    origin_files = {str(rel): path
                    for rel, path in vise_baseline.iter_files(origin)}
    for rel, path in vise_baseline.iter_files(origin):
        target = src / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    for rel, path in vise_baseline.iter_files(src):
        if str(rel) not in origin_files:
            path.unlink()

    # Snapshots stay open: they are the rollback point for whatever comes next.
    return {"adapter_id": adapter_id, "status": "restored", "from": ts,
            "files": len(vise_baseline.iter_files(src))}


def _read_lines(path: Path) -> list[str] | None:
    """Text lines, or None for binary (a NUL byte in the first 8 KiB)."""
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="surrogateescape").splitlines(True)


def _unified_patch(adapter_id: str, base_tree: Path, cur_dir: Path,
                   label: str) -> tuple[str, list[str], int]:
    """Unified diff baseline -> current; returns (patch, changed, skipped)."""
    def relmap(root: Path) -> dict[str, Path]:
        return {str(rel): path for rel, path in vise_baseline.iter_files(root)}

    old, new = relmap(base_tree), relmap(cur_dir)
    chunks: list[str] = []
    changed: list[str] = []
    skipped = 0
    for rel in sorted(old.keys() | new.keys()):
        o, n = old.get(rel), new.get(rel)
        if o is not None and n is not None:
            if vise_baseline.sha256_of(o) == vise_baseline.sha256_of(n):
                continue
            o_lines, n_lines = _read_lines(o), _read_lines(n)
        else:
            o_lines = _read_lines(o) if o is not None else []
            n_lines = _read_lines(n) if n is not None else []
        if o_lines is None or n_lines is None:
            skipped += 1
            continue
        from_name = f"a/{rel}" if o is not None else "/dev/null"
        to_name = f"b/{rel}" if n is not None else "/dev/null"
        chunks.extend(difflib.unified_diff(
            o_lines, n_lines, fromfile=from_name, tofile=to_name, n=3))
        changed.append(rel)
    header = (f"# Vise adapter patch: {adapter_id}\n"
              f"# {label}\n"
              f"# Paths below are relative to the adapter directory.\n")
    body = "".join(chunks)
    if body and not body.endswith("\n"):
        body += "\n"
    return (header + body if body else "", changed, skipped)


def promote(adapter_id: str, message: str,
            dry_run: bool = False) -> dict:
    """Record the current improvement as a patch. Never commits (C13)."""
    src = vise_baseline.adapter_dir(adapter_id)
    if src is None:
        return {"adapter_id": adapter_id, "status": "unknown-adapter"}
    base_tree = vise_baseline.tree_dir(adapter_id)
    if vise_baseline.load(adapter_id) is None or not base_tree.is_dir():
        return {"adapter_id": adapter_id, "status": "no-baseline",
                "reason": "nothing to diff against - run: bash init/setup.sh"}
    d = vise_baseline.drift(adapter_id)
    if d.get("clean"):
        return {"adapter_id": adapter_id, "status": "nothing-to-promote",
                "reason": "working tree already matches the baseline"}

    rel_dir = src.relative_to(VISE_ROOT)
    label = (f"{message}\n"
             f"# promoted {_utcnow()}\n"
             f"# apply with: patch -p1 -d {rel_dir} <this file>")
    patch, changed, skipped = _unified_patch(adapter_id, base_tree, src, label)
    if not patch:
        return {"adapter_id": adapter_id, "status": "nothing-to-promote",
                "reason": f"only binary files differ ({skipped} skipped)"}
    if dry_run:
        return {"adapter_id": adapter_id, "status": "dry-run",
                "files": changed, "skipped": skipped, "bytes": len(patch)}

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = PATCHES_DIR / adapter_id
    out_dir.mkdir(parents=True, exist_ok=True)
    # Never overwrite: two promotes of the same message within one second are
    # two distinct patches, so an ordinal suffix disambiguates them.
    out_path = out_dir / f"{ts}_{_slug(message)}.patch"
    n = 1
    while out_path.exists():
        n += 1
        out_path = out_dir / f"{ts}_{_slug(message)}-{n:02d}.patch"
    out_path.write_text(patch, encoding="utf-8", errors="surrogateescape")
    return {"adapter_id": adapter_id, "status": "promoted",
            "patch": str(out_path.relative_to(VISE_ROOT)),
            "files": changed, "skipped": skipped,
            "note": "patch written; committing is a separate act (C13)"}


# --- CLI ---------------------------------------------------------------------

def cmd_snapshot(args: argparse.Namespace) -> int:
    r = snapshot(args.adapter_id, args.message, args.triggered_by, args.force)
    status = r["status"]
    if args.json:
        print(json.dumps(r, indent=2))
    elif status == "created":
        print(f"[SNAPSHOT] {r['adapter_id']} @ {r['ts']} "
              f"({r['files']} files, taken while {r['taken_while']})")
        for ts in r["pruned"]:
            print(f"  pruned old generation: {ts}")
    elif status == "already-open":
        print(f"[OPEN] {r['adapter_id']} already has an open snapshot "
              f"({r['ts']}); use --force to add another")
    else:
        print(f"[FAIL] {r['adapter_id']}: {status}", file=sys.stderr)
    # The exit code reflects the outcome, never the requested output format.
    if status in ("created", "already-open"):
        return EXIT_OK
    return EXIT_USAGE if status == "unknown-adapter" else EXIT_UNAVAILABLE


def cmd_list(args: argparse.Namespace) -> int:
    metas = list_snapshots(args.adapter_id)
    if args.json:
        print(json.dumps(metas, indent=2))
        return EXIT_OK
    if not metas:
        print(f"guard: no snapshots for {args.adapter_id} "
              f"(keep at most {MAX_GENERATIONS})")
        return EXIT_OK
    print(f"{'ts':<22} {'status':<8} {'while':<10} reason")
    print("-" * 84)
    for m in metas:
        print(f"{m['ts']:<22} {m.get('status', '?'):<8} "
              f"{m.get('taken_while', '?'):<10} {m.get('reason', '')}")
    return EXIT_OK


def cmd_restore(args: argparse.Namespace) -> int:
    r = restore(args.adapter_id, args.gen, args.base, args.dry_run)
    status = r["status"]
    if args.json:
        print(json.dumps(r, indent=2))
    elif status in ("restored", "dry-run"):
        prefix = "[dry-run] would restore" if status == "dry-run" \
            else "restored"
        print(f"[RESTORE] {r['adapter_id']}: {prefix} from {r['from']} "
              f"({r['files']} files)")
    else:
        print(f"[FAIL] {r['adapter_id']}: {status} - "
              f"{r.get('reason', '')}", file=sys.stderr)
    if status in ("restored", "dry-run"):
        return EXIT_OK
    return EXIT_USAGE if status == "unknown-adapter" else EXIT_UNAVAILABLE


def cmd_promote(args: argparse.Namespace) -> int:
    gate = check(args.adapter_id)
    if gate["state"] == "modified" and not gate["protected"]:
        print(f"[BLOCKED] {gate['note']}", file=sys.stderr)
        if args.json:
            print(json.dumps({"adapter_id": args.adapter_id,
                              "status": "blocked", "reason": gate["note"]},
                             indent=2))
        return EXIT_UNPROTECTED
    r = promote(args.adapter_id, args.message, args.dry_run)
    status = r["status"]
    if args.json:
        print(json.dumps(r, indent=2))
    elif status == "promoted":
        print(f"[PROMOTE] {args.adapter_id}: {r['patch']} "
              f"({len(r['files'])} files"
              + (f", {r['skipped']} binary skipped" if r["skipped"] else "")
              + ")")
        print(f"  {r['note']}")
    elif status == "dry-run":
        print(f"[dry-run] would write a patch touching {len(r['files'])} files")
    else:
        print(f"[NOOP] {args.adapter_id}: {status} - "
              f"{r.get('reason', '')}", file=sys.stderr)
    if status in ("promoted", "dry-run"):
        return EXIT_OK
    return EXIT_USAGE if status == "unknown-adapter" else EXIT_UNAVAILABLE


def cmd_check(args: argparse.Namespace) -> int:
    ids = ([a["adapter_id"] for a in vise_baseline.registry_adapters()]
           if not args.adapter_id else [args.adapter_id])
    # No adapters to check is a broken install, not an all-clear.
    if not ids:
        print("guard: no adapters registered. Run: bash init/setup.sh",
              file=sys.stderr)
        return EXIT_USAGE
    results = [check(aid) for aid in ids]
    if args.json:
        print(json.dumps(results if not args.adapter_id else results[0],
                         indent=2))
    else:
        for r in results:
            if r["state"] == "unknown":
                print(f"[FAIL] {r['adapter_id']}: unknown adapter")
            elif r["protected"]:
                detail = (f"open snapshot {r['open_snapshot']}"
                          if r["open_snapshot"] else r["state"])
                print(f"[OK]   {r['adapter_id']}: protected ({detail})")
            else:
                print(f"[BLOCK]{r['adapter_id']}: unprotected drift")
                print(f"       -> {r['note']}")
    return EXIT_OK if all(r["protected"] for r in results) \
        else EXIT_UNPROTECTED


def cmd_rebaseline(args: argparse.Namespace) -> int:
    """Accept the current tree as the new reference (explicit act)."""
    ids = ([a["adapter_id"] for a in vise_baseline.registry_adapters()]
           if args.all else [args.adapter_id])
    if not ids or ids == [None]:
        print("guard: nothing to do (no adapter selected)", file=sys.stderr)
        return EXIT_USAGE
    out = []
    refused = False
    for aid in ids:
        d = vise_baseline.drift(aid)
        if d["baselined"] and not d["clean"] and not args.force:
            refused = True
            out.append({"adapter_id": aid, "status": "refused",
                        "reason": "tree is modified; pass --force to accept it "
                                  "as the new reference"})
            continue
        r = vise_baseline.establish(aid, refresh=True)
        closed = []
        for meta in list_snapshots(aid):
            if meta.get("status") != OPEN:
                continue
            path = _snapshot_meta_path(aid, meta["ts"])
            meta["status"] = CLOSED
            meta["closed_at"] = _utcnow()
            path.write_text(json.dumps(meta, indent=2, ensure_ascii=False)
                            + "\n", encoding="utf-8")
            closed.append(meta["ts"])
        r["closed_snapshots"] = closed
        out.append(r)
    if args.json:
        print(json.dumps(out if args.all else out[0], indent=2))
        return EXIT_UNPROTECTED if refused else EXIT_OK
    for r in out:
        if r["status"] == "refused":
            print(f"[REFUSE] {r['adapter_id']}: {r['reason']}")
        else:
            print(f"[REBASELINE] {r['adapter_id']}: {r['status']} "
                  f"({r['files']} files); closed "
                  f"{len(r['closed_snapshots'])} snapshot(s)")
    return EXIT_UNPROTECTED if refused else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="adapter_guard.py",
        description="Protect, roll back and sediment adapter-code changes.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_snap = sub.add_parser("snapshot", help="snapshot before editing (open)")
    p_snap.add_argument("adapter_id")
    p_snap.add_argument("-m", "--message", required=True,
                        help="why the adapter is about to be modified")
    p_snap.add_argument("--triggered-by", default=None)
    p_snap.add_argument("--force", action="store_true",
                        help="add a snapshot even if one is already open")
    p_snap.add_argument("--json", action="store_true")

    p_ls = sub.add_parser("list", help="list snapshot generations")
    p_ls.add_argument("adapter_id")
    p_ls.add_argument("--json", action="store_true")

    p_res = sub.add_parser("restore", help="reinstate a snapshot or baseline")
    p_res.add_argument("adapter_id")
    p_res.add_argument("--gen", type=int, default=None,
                       help="generation to restore (0 = newest, default)")
    p_res.add_argument("--base", action="store_true",
                       help="restore the pristine baseline instead")
    p_res.add_argument("--dry-run", action="store_true")
    p_res.add_argument("--json", action="store_true")

    p_pr = sub.add_parser("promote", help="write a patch for the improvement")
    p_pr.add_argument("adapter_id")
    p_pr.add_argument("-m", "--message", required=True)
    p_pr.add_argument("--dry-run", action="store_true")
    p_pr.add_argument("--json", action="store_true")

    p_chk = sub.add_parser("check", help="gate: is the adapter protected?")
    p_chk.add_argument("adapter_id", nargs="?")
    p_chk.add_argument("--json", action="store_true")

    p_re = sub.add_parser("rebaseline",
                          help="accept the current tree as the reference")
    p_re.add_argument("adapter_id", nargs="?")
    p_re.add_argument("--all", action="store_true")
    p_re.add_argument("--force", action="store_true")
    p_re.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    # The reason is what makes a rollback decision auditable later, so a blank
    # one is rejected here rather than silently recorded (--message is required
    # by argparse, but `-m ""` satisfies that while carrying no information).
    if args.command in ("snapshot", "promote") and not args.message.strip():
        parser.error(f"{args.command}: --message must not be blank - "
                     "record WHY the adapter changes")
    return {"snapshot": cmd_snapshot, "list": cmd_list,
            "restore": cmd_restore, "promote": cmd_promote,
            "check": cmd_check, "rebaseline": cmd_rebaseline}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
