#!/usr/bin/env python3
"""baseline.py - the pristine reference state of every adapter (no git needed).

A baseline answers three questions without requiring a repository:

  * what did this adapter look like when it was installed?
  * has the working tree drifted from that state?
  * can the original bytes be handed back on demand?

Storage (all under `.vise/`, a generated artifact - never committed, C7):

    .vise/baseline/<adapter_id>/manifest.json   per-file sha256 + size
    .vise/baseline/<adapter_id>/tree/           full copy of the adapter tree

The tree copy is intentional: a manifest alone records *that* something
changed but not the bytes needed to diff or restore, and adapter trees are
small enough that copying them is cheaper than reconstructing them.

When the baseline is captured
-----------------------------
At install time, from `init/setup.sh`, and **only if absent**. Re-running
setup must never overwrite an existing baseline: if a user has already edited
an adapter, re-baselining would silently bless the edited state as "original".
Accepting a new reference is therefore an explicit act (`rebaseline`).

Extension point (providers)
---------------------------
Baselines are produced by a *provider*: a callable that fingerprints one
adapter directory and names the tree to copy. This module ships one,
`install_scan` (fingerprint the working tree exactly as it sits on disk).
Further providers - "git HEAD" for a git clone, "release manifest" for a
published tarball - register in `PROVIDERS` and nothing else here changes:
callers only use `establish`, `load`, `drift` and `state`.

Upgrades
--------
`init/setup.sh` stamps `.vise/version` with `core/version.py:VERSION`. When a
later setup run sees a different stamp, the install was upgraded in place
(zip/tarball extracted over it - there is no git to ask). `upgrade` then
refreshes baselines: *clean* adapters are re-captured automatically (no user
edits exist to protect), *modified* ones are reported for explicit review -
their drift may mix vendor changes with user edits, and only the user can
tell which. Every baseline manifest records the `vise_version` it was
captured under, so `restore --base` can refuse to roll an adapter back to
bytes from an older Vise.

Exit codes (agent-friendly, baseline domain - distinct from the `viseinit`
0-6 map):
  establish/path/version  0 ok, 2 usage or input error
  status                  0 every adapter is clean, 1 modified/unbaselined
  diff                    0 no drift, 1 drift detected, 2 usage
  verify                  0 intact, 1 corrupted tree / no adapters, 2 usage
  upgrade                 0 all refreshed, 1 some adapters need review, 2 usage
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Callable

from version import VERSION as VISE_VERSION

VISE_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = VISE_ROOT / ".vise" / "registry.json"
BASELINE_DIR = VISE_ROOT / ".vise" / "baseline"
VERSION_FILE = VISE_ROOT / ".vise" / "version"
BASELINE_VERSION = 1

# Runtime noise, not adapter source: excluded from both the fingerprint and
# the copied tree (a `.bak` from file_patch must not drift the baseline).
IGNORE_DIRS = {"__pycache__", ".git", ".vise"}
IGNORE_SUFFIXES = (".pyc", ".pyo", ".bak", ".swp", ".orig", ".rej")
IGNORE_NAMES = {".DS_Store", "Thumbs.db"}


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_ignored(rel: Path) -> bool:
    if any(part in IGNORE_DIRS for part in rel.parts):
        return True
    if rel.name in IGNORE_NAMES or rel.name.endswith(IGNORE_SUFFIXES):
        return True
    return False


def iter_files(root: Path) -> list[tuple[Path, Path]]:
    """(relative, absolute) for every source file under root, sorted."""
    out = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if is_ignored(rel):
            continue
        out.append((rel, path))
    return out


def scan(root: Path) -> dict[str, dict]:
    """Fingerprint a tree: relative path -> {sha256, size}."""
    files: dict[str, dict] = {}
    for rel, path in iter_files(root):
        files[str(rel)] = {"sha256": sha256_of(path), "size": path.stat().st_size}
    return files


def tree_sha(files: dict[str, dict]) -> str:
    """One hash summarizing a whole fingerprint (order-independent)."""
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(f"{rel}\0{files[rel]['sha256']}\n".encode("utf-8"))
    return h.hexdigest()


# --- providers ---------------------------------------------------------------
# Contract: given an adapter directory, return
#   {"source_dir": <dir to copy into tree/>, "files": {rel: {sha256, size}}}
# The copy may come from anywhere - the module itself only copies what it is
# handed, so a future provider can materialize a git object or unpack a
# release archive into a temp dir and return that as `source_dir`.

def _provider_install_scan(adapter_dir: Path) -> dict:
    return {"source_dir": adapter_dir, "files": scan(adapter_dir)}


PROVIDERS: dict[str, Callable[[Path], dict]] = {
    "install": _provider_install_scan,
}


def _copy_patterns() -> list[str]:
    return (list(IGNORE_DIRS) + [f"*{s}" for s in IGNORE_SUFFIXES]
            + list(IGNORE_NAMES))


# --- registry / path helpers -------------------------------------------------

def registry_adapters() -> list[dict]:
    """Enabled adapters from the generated registry; [] plus a hint if unusable."""
    if not REGISTRY_PATH.is_file():
        print(f"baseline: {REGISTRY_PATH.relative_to(VISE_ROOT)} not found "
              "(generated artifact). Run: bash init/setup.sh", file=sys.stderr)
        return []
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"baseline: registry unreadable ({exc}). "
              "Regenerate it with: bash init/setup.sh", file=sys.stderr)
        return []
    if not isinstance(data, dict):
        print("baseline: registry has an unexpected shape. "
              "Regenerate it with: bash init/setup.sh", file=sys.stderr)
        return []
    return [a for a in data.get("adapters", [])
            if a.get("enabled") and a.get("path")]


def adapter_dir(adapter_id: str) -> Path | None:
    for a in registry_adapters():
        if a["adapter_id"] == adapter_id:
            return (VISE_ROOT / a["path"]).resolve()
    return None


def stamped_version() -> str | None:
    """The Vise version this install was last set up with, or None."""
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def manifest_path(adapter_id: str) -> Path:
    return BASELINE_DIR / adapter_id / "manifest.json"


def tree_dir(adapter_id: str) -> Path:
    return BASELINE_DIR / adapter_id / "tree"


def load(adapter_id: str) -> dict | None:
    """Read a baseline manifest, or None when the adapter is unbaselined.

    A manifest whose tree copy is gone counts as *absent*, not as a usable
    baseline: `.vise/baseline/` holds generated state, and a half-deleted
    baseline (partial cleanup, interrupted write) would otherwise be reported
    as `clean` while `restore` fails. Treating it as absent is self-healing -
    the next `establish` recreates it.
    """
    path = manifest_path(adapter_id)
    if not path.is_file():
        return None
    if not tree_dir(adapter_id).is_dir():
        print(f"baseline: {adapter_id} has no tree copy (incomplete baseline); "
              "run: bash init/setup.sh", file=sys.stderr)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"baseline: {adapter_id} manifest unreadable ({exc})",
              file=sys.stderr)
        return None


# --- core operations ---------------------------------------------------------

def establish(adapter_id: str, provider: str = "install",
              refresh: bool = False) -> dict:
    """Create the baseline; skip when one exists unless `refresh` is set."""
    src = adapter_dir(adapter_id)
    if src is None:
        return {"adapter_id": adapter_id, "status": "unknown-adapter"}
    existing = load(adapter_id)
    if existing is not None and not refresh:
        return {"adapter_id": adapter_id, "status": "kept",
                "established_at": existing.get("established_at"),
                "files": len(existing.get("files", {}))}
    prov = PROVIDERS.get(provider)
    if prov is None:
        raise ValueError(f"unknown baseline provider: {provider}")
    result = prov(src)

    dest = tree_dir(adapter_id)
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(result["source_dir"], dest,
                    ignore=shutil.ignore_patterns(*_copy_patterns()))

    manifest = {
        "version": BASELINE_VERSION,
        "adapter_id": adapter_id,
        "provider": provider,
        "established_at": _utcnow(),
        "vise_version": VISE_VERSION,
        "source_root": str(src.relative_to(VISE_ROOT)),
        "files": result["files"],
    }
    path = manifest_path(adapter_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)  # atomic on POSIX and Windows
    return {"adapter_id": adapter_id,
            "status": "refreshed" if existing else "created",
            "established_at": manifest["established_at"],
            "files": len(manifest["files"])}


def drift(adapter_id: str) -> dict:
    """File-level drift of the working tree against its baseline."""
    src = adapter_dir(adapter_id)
    if src is None:
        return {"adapter_id": adapter_id, "baselined": False,
                "reason": "unknown adapter"}
    base = load(adapter_id)
    if base is None:
        return {"adapter_id": adapter_id, "baselined": False,
                "reason": "no baseline"}
    old = base.get("files", {})
    cur = scan(src)
    changed = sorted(p for p in old.keys() & cur.keys()
                     if old[p]["sha256"] != cur[p]["sha256"])
    added = sorted(cur.keys() - old.keys())
    removed = sorted(old.keys() - cur.keys())
    return {"adapter_id": adapter_id, "baselined": True,
            "clean": not (changed or added or removed),
            "changed": changed, "added": added, "removed": removed,
            "established_at": base.get("established_at"),
            "tree_sha": tree_sha(cur)}


def state(adapter_id: str) -> str:
    """'clean' | 'modified' | 'unbaselined'."""
    d = drift(adapter_id)
    if not d["baselined"]:
        return "unbaselined"
    return "clean" if d["clean"] else "modified"


def verify(adapter_id: str) -> dict:
    """Check the baseline tree copy still matches its manifest, byte for byte.

    The manifest is the truth about *hashes*; the tree is the truth about
    *bytes*. Drift detection trusts the manifest, and `restore --base` trusts
    the tree - so a tampered or half-written tree would silently make restore
    produce files that no longer match the manifest. This check turns that
    silent corruption into a loud, actionable failure.
    """
    if adapter_dir(adapter_id) is None:
        return {"adapter_id": adapter_id, "status": "unknown-adapter",
                "ok": False}
    base = load(adapter_id)
    if base is None:
        # Nothing to verify is not corruption: the adapter keeps working.
        return {"adapter_id": adapter_id, "status": "unbaselined", "ok": True}
    expected = base.get("files", {})
    actual = {str(rel): sha256_of(path)
              for rel, path in iter_files(tree_dir(adapter_id))}
    changed = sorted(p for p in expected.keys() & actual.keys()
                     if expected[p]["sha256"] != actual[p])
    missing = sorted(expected.keys() - actual.keys())
    added = sorted(actual.keys() - expected.keys())
    corrupted = bool(changed or missing or added)
    return {"adapter_id": adapter_id,
            "status": "corrupted" if corrupted else "ok",
            "ok": not corrupted,
            "changed": changed, "missing": missing, "added": added,
            "established_at": base.get("established_at")}


def upgrade() -> list[dict]:
    """Reconcile baselines after the install was upgraded in place.

    Per adapter:
      unbaselined  -> capture (a newly shipped adapter gets its baseline)
      clean        -> refresh automatically: no user edits exist, and an
                      unchanged tree means the release did not touch it - only
                      the `vise_version` stamp and timestamp move
      modified     -> NEVER touched automatically: the drift may mix vendor
                      changes with user edits. Reported as needs-review with
                      the drift summary; the user decides (promote their edits
                      first, then `rebaseline --force`).
    """
    rows = []
    for a in registry_adapters():
        aid = a["adapter_id"]
        d = drift(aid)
        if not d["baselined"]:
            r = establish(aid)
            rows.append({"adapter_id": aid, "action": "baselined",
                         "files": r.get("files", 0)})
        elif d["clean"]:
            r = establish(aid, refresh=True)
            rows.append({"adapter_id": aid, "action": "refreshed",
                         "files": r.get("files", 0)})
        else:
            rows.append({"adapter_id": aid, "action": "needs-review",
                         "changed": len(d["changed"]), "added": len(d["added"]),
                         "removed": len(d["removed"])})
    return rows


# --- CLI ---------------------------------------------------------------------

def cmd_establish(args: argparse.Namespace) -> int:
    ids = ([a["adapter_id"] for a in registry_adapters()]
           if args.all else [args.adapter_id])
    if not ids or ids == [None]:
        print("baseline: nothing to do (no adapter selected)", file=sys.stderr)
        return 2
    results = []
    failed = False
    for aid in ids:
        try:
            r = establish(aid, provider=args.provider, refresh=args.refresh)
        except ValueError as exc:
            print(f"baseline: {exc}", file=sys.stderr)
            return 2
        if r["status"] == "unknown-adapter":
            failed = True
        results.append(r)
    if args.json:
        print(json.dumps(results if args.all else results[0], indent=2))
        return 1 if failed else 0
    for r in results:
        if r["status"] == "kept":
            print(f"[KEEP] {r['adapter_id']}: baseline exists "
                  f"({r['established_at']}) - use --refresh to overwrite")
        elif r["status"] == "created" or r["status"] == "refreshed":
            print(f"[{r['status'].upper():<9}] {r['adapter_id']}: "
                  f"{r['files']} files @ {r['established_at']}")
        else:
            print(f"[FAIL] {r['adapter_id']}: {r['status']}")
    return 1 if failed else 0


def cmd_status(args: argparse.Namespace) -> int:
    ids = ([a["adapter_id"] for a in registry_adapters()]
           if not args.adapter_id else [args.adapter_id])
    if args.adapter_id and adapter_dir(args.adapter_id) is None:
        print(f"baseline: unknown adapter '{args.adapter_id}'", file=sys.stderr)
        return 2
    rows = []
    for aid in ids:
        d = drift(aid)
        rows.append({
            "adapter_id": aid,
            "state": ("unbaselined" if not d["baselined"]
                      else ("clean" if d["clean"] else "modified")),
            "established_at": d.get("established_at"),
            "drifted_files": (len(d.get("changed", []))
                              + len(d.get("added", []))
                              + len(d.get("removed", []))),
        })
    dirty = [r for r in rows if r["state"] != "clean"]
    # A registry with no adapters is a broken install, not an all-clear: the
    # exit code must not depend on the output format.
    exit_code = 1 if (dirty or not rows) else 0
    if args.json:
        print(json.dumps(rows if not args.adapter_id else rows[0], indent=2))
        return exit_code
    if not rows:
        print("baseline: no adapters registered. Run: bash init/setup.sh",
              file=sys.stderr)
        return exit_code
    print(f"{'adapter_id':<20} {'state':<12} {'files':>5}  baseline")
    print("-" * 64)
    for r in rows:
        print(f"{r['adapter_id']:<20} {r['state']:<12} "
              f"{r['drifted_files']:>5}  {r['established_at'] or '-'}")
    print(f"\nBaseline: {len(rows) - len(dirty)}/{len(rows)} clean")
    return exit_code


def cmd_diff(args: argparse.Namespace) -> int:
    d = drift(args.adapter_id)
    if args.json:
        print(json.dumps(d, indent=2))
        return 0 if d.get("clean") else 1
    if not d["baselined"]:
        print(f"baseline: {args.adapter_id} is not baselined "
              f"({d.get('reason', '?')}) - run: bash init/setup.sh")
        return 1
    if d["clean"]:
        print(f"baseline: {args.adapter_id} is clean "
              f"(baseline {d.get('established_at')})")
        return 0
    for kind, mark in (("changed", "M"), ("added", "A"), ("removed", "D")):
        for path in d[kind]:
            print(f"  {mark} {path}")
    print(f"\nBaseline drift: {len(d['changed'])} changed, "
          f"{len(d['added'])} added, {len(d['removed'])} removed")
    return 1


def cmd_verify(args: argparse.Namespace) -> int:
    """`verify [id] [--json]` - 0 intact, 1 corrupted/empty registry, 2 usage."""
    if args.adapter_id and adapter_dir(args.adapter_id) is None:
        print(f"baseline: unknown adapter '{args.adapter_id}'", file=sys.stderr)
        return 2
    ids = ([a["adapter_id"] for a in registry_adapters()]
           if not args.adapter_id else [args.adapter_id])
    rows = [verify(aid) for aid in ids]
    bad = [r for r in rows if r["status"] == "corrupted"]
    # Empty registry: broken install, not an all-clear. Unbaselined is NOT
    # corruption - it is reported, but keeps the exit code clean.
    exit_code = 1 if (bad or not rows) else 0
    if args.json:
        print(json.dumps(rows if not args.adapter_id else rows[0], indent=2))
        return exit_code
    if not rows:
        print("baseline: no adapters registered. Run: bash init/setup.sh",
              file=sys.stderr)
        return exit_code
    for r in rows:
        if r["status"] == "ok":
            print(f"[OK]   {r['adapter_id']}: tree matches manifest")
        elif r["status"] == "unbaselined":
            print(f"[SKIP] {r['adapter_id']}: no baseline to verify")
        else:
            print(f"[BAD]  {r['adapter_id']}: baseline corrupted")
            for kind, mark in (("changed", "M"), ("missing", "D"),
                               ("added", "A")):
                for path in r[kind]:
                    print(f"         {mark} {path}")
            print(f"         rebuild with: .venv/bin/python core/baseline.py "
                  f"establish {r['adapter_id']} --refresh "
                  f"(only if the working tree is clean)")
    return exit_code


def cmd_path(args: argparse.Namespace) -> int:
    if args.adapter_id:
        print(manifest_path(args.adapter_id))
        print(tree_dir(args.adapter_id))
    else:
        print(BASELINE_DIR)
    return 0


def cmd_version(_args: argparse.Namespace) -> int:
    """Print the current Vise version (used by setup.sh for the stamp)."""
    print(VISE_VERSION)
    return 0


def cmd_upgrade(args: argparse.Namespace) -> int:
    """`upgrade [--json]` - 0 all reconciled, 1 some need review, 2 usage."""
    rows = upgrade()
    pending = [r for r in rows if r["action"] == "needs-review"]
    exit_code = 1 if (pending or not rows) else 0
    if args.json:
        print(json.dumps(rows, indent=2))
        return exit_code
    if not rows:
        print("baseline: no adapters registered. Run: bash init/setup.sh",
              file=sys.stderr)
        return exit_code
    for r in rows:
        if r["action"] == "baselined":
            print(f"[NEW]     {r['adapter_id']}: baseline captured "
                  f"({r['files']} files)")
        elif r["action"] == "refreshed":
            print(f"[REFRESH] {r['adapter_id']}: clean, re-stamped for "
                  f"v{VISE_VERSION}")
        else:
            print(f"[REVIEW]  {r['adapter_id']}: drift after upgrade - "
                  f"{r['changed']} changed, {r['added']} added, "
                  f"{r['removed']} removed")
    if pending:
        print("\nDrift after an upgrade may mix vendor changes with your "
              "edits - only you can tell which.")
        print("  If you did NOT edit the adapter:  visectl adapter "
              "rebaseline <id> --force")
        print("  If you DID edit it: promote your edits first "
              "(visectl adapter promote <id> -m \"...\"), then rebaseline "
              "--force.")
        print("  Do NOT `restore --base` an upgraded adapter: the baseline "
              "still holds the OLD release's bytes.")
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="baseline.py",
        description="Pristine reference state of every adapter (git-free).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_est = sub.add_parser("establish",
                           help="capture the baseline (skipped if it exists)")
    p_est.add_argument("adapter_id", nargs="?")
    p_est.add_argument("--all", action="store_true")
    p_est.add_argument("--provider", default="install",
                       choices=sorted(PROVIDERS))
    p_est.add_argument("--refresh", action="store_true",
                       help="overwrite an existing baseline (explicit accept)")
    p_est.add_argument("--json", action="store_true")

    p_st = sub.add_parser("status", help="clean / modified / unbaselined")
    p_st.add_argument("adapter_id", nargs="?")
    p_st.add_argument("--json", action="store_true")

    p_df = sub.add_parser("diff", help="file-level drift against the baseline")
    p_df.add_argument("adapter_id")
    p_df.add_argument("--json", action="store_true")

    p_ve = sub.add_parser("verify",
                          help="check the baseline tree matches its manifest")
    p_ve.add_argument("adapter_id", nargs="?")
    p_ve.add_argument("--json", action="store_true")

    p_pa = sub.add_parser("path", help="print baseline paths")
    p_pa.add_argument("adapter_id", nargs="?")

    sub.add_parser("version", help="print the current Vise version")

    p_up = sub.add_parser("upgrade",
                          help="reconcile baselines after a Vise upgrade")
    p_up.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "establish" and not args.all and not args.adapter_id:
        parser.error("establish requires an adapter_id or --all")
    return {"establish": cmd_establish, "status": cmd_status,
            "diff": cmd_diff, "verify": cmd_verify, "path": cmd_path,
            "version": cmd_version, "upgrade": cmd_upgrade}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
