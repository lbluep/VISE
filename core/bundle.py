#!/usr/bin/env python3
"""bundle - export (and, after decision D5, import) a portable Vise tree.

A bundle is a tar.gz carrying everything needed to move a Vise library to
another machine or hand it to someone else:

  * adapters/            - all adapter trees (incl. the reference template)
  * vise.release.json    - the release manifest
  * .vise/baseline/      - baselines (provenance + fingerprints)
  * .vise/patches/       - promote-generated patches (audit chain)
  * .vise/state/         - adapter runtime state (notes, tasks, ...)
  * .vise/audit/         - only with --include-audit (machine-local history)
  * BUNDLE.json          - self-verification manifest (sha256 per file,
                           source VISE version, created_at)

Excluded by design: snapshots/ (large and machine-specific; recovery comes
from baseline/), python/ (venv rebuilt by init/setup.sh), reports/ caches,
__pycache__, and the machine-local files installed.json / tool_paths.json /
version / registry.json / requirements.txt (the latter two are regenerated
by register_adapters).

Export takes the write lock briefly so it never reads a half-written
registry or baseline. The output tarball is written to a caller-chosen path
(usually outside VISE_ROOT); it is a user file, NOT an uninstall-ledger
entry.

`bundle import` (D5) first verifies the BUNDLE.json hashes, then
reconciles the tree per adapter against the local state:

  * fresh   the adapter is unknown locally (not registered, no baseline,
            no directory) - its files, patches and runtime state are
            extracted verbatim; the bundle's baseline comes with them
            when present, otherwise one is established after registration.
  * merge   the adapter exists locally - the delivered bytes (BUNDLE.json
            hashes) are compared three-way (old baseline / bundle / disk)
            per file via baseline.classify: update and delete are applied,
            keep and user files are left alone, review paths are reported
            and never touched (unless --conflicts=theirs takes the bundle
            bytes). A pre-merge snapshot protects the local state; the
            baseline is re-established from the applied tree afterwards.

vise.release.json rides in the bundle but is never written on import -
it is regenerated locally with the registry refresh. Patches are
copy-if-absent by filename (never replayed); local state of existing
adapters is never touched. Dry-run computes and prints the whole plan
without a single write.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sys
import tarfile
import time
from pathlib import Path

VISE_ROOT = Path(__file__).resolve().parent.parent

# .vise paths included in a bundle (relative to VISE_ROOT).
VISE_INCLUDE = [".vise/baseline", ".vise/patches", ".vise/state"]
VISE_EXCLUDE_NAMES = {
    "snapshots", "python", "reports", "installed.json",
    "tool_paths.json", "version", "registry.json", "requirements.txt",
}

sys.path.insert(0, str(VISE_ROOT / "core"))
sys.path.insert(0, str(VISE_ROOT / "init"))  # register_adapters (post-import)
import vise_lock  # noqa: E402
try:
    from version import VERSION as _VISE_VERSION  # type: ignore
except Exception:
    _VISE_VERSION = "0.0.0"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Runtime noise excluded from export, mirroring baseline.is_ignored's
# directory rule: a bundle carries adapter source, not caches or runtime
# state that happens to sit inside adapters/ (e.g. a stale `adapters/.vise`).
EXPORT_IGNORE_DIRS = {"__pycache__", ".git", ".vise"}


def _exportable(path: Path) -> bool:
    return not any(part in EXPORT_IGNORE_DIRS for part in path.parts)


def _iter_included_files(include_audit: bool) -> list[Path]:
    files: list[Path] = []
    # adapters/ whole tree
    adapters = VISE_ROOT / "adapters"
    if adapters.is_dir():
        for p in adapters.rglob("*"):
            if p.is_file() and _exportable(p):
                files.append(p)
    # vise.release.json
    rel = VISE_ROOT / "vise.release.json"
    if rel.is_file():
        files.append(rel)
    # selected .vise subtrees
    for rel_dir in VISE_INCLUDE:
        d = VISE_ROOT / rel_dir
        if d.is_dir():
            for p in d.rglob("*"):
                if p.is_file() and "__pycache__" not in p.parts:
                    files.append(p)
    # audit (opt-in)
    if include_audit:
        ad = VISE_ROOT / ".vise" / "audit"
        if ad.is_dir():
            for p in ad.rglob("*"):
                if p.is_file():
                    files.append(p)
    return files


def _rel(path: Path) -> str:
    return path.relative_to(VISE_ROOT).as_posix()


def bundle_export(out_path: Path, *, include_audit: bool = False) -> dict:
    out_path = out_path.resolve()
    if out_path.exists():
        raise ValueError(f"output exists, refusing to overwrite: {out_path}")

    with vise_lock.write_lock("bundle export", timeout=30):
        files = _iter_included_files(include_audit)
        # First pass: read + hash every file so BUNDLE.json (written inside the
        # same tar) already knows the final hashes.
        blobs: dict[str, bytes] = {}
        modes: dict[str, int] = {}
        entries: list[dict] = []
        for f in files:
            rel = _rel(f)
            data = f.read_bytes()
            blobs[rel] = data
            # Executable bits are part of the source (thread scripts are
            # committed as 100755); losing them would make every import
            # show up as a mode drift in git.
            modes[rel] = stat.S_IMODE(f.stat().st_mode)
            entries.append({"path": rel,
                            "sha256": _sha256_bytes(data),
                            "size": len(data)})
        bundle = {
            "vise_version": _VISE_VERSION,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "include_audit": include_audit,
            "file_count": len(entries),
            "files": entries,
        }
        bjson = json.dumps(bundle, indent=2, ensure_ascii=False).encode("utf-8")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for rel, data in blobs.items():
                info = tarfile.TarInfo(name=rel)
                info.size = len(data)
                info.mtime = int(time.time())
                info.mode = modes[rel]
                tar.addfile(info, io.BytesIO(data))
            info = tarfile.TarInfo(name="BUNDLE.json")
            info.size = len(bjson)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(bjson))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(buf.getvalue())
    return {"out": str(out_path), "files": len(entries),
            "vise_version": _VISE_VERSION}


def bundle_import_check(bundle_path: Path) -> tuple[int, str]:
    """Validate bundle integrity; does NOT modify the tree (decision D5)."""
    if not bundle_path.is_file():
        return 2, f"bundle not found: {bundle_path}"
    try:
        with tarfile.open(bundle_path, mode="r:gz") as tar:
            names = tar.getnames()
            if "BUNDLE.json" not in names:
                return 2, "bundle is missing BUNDLE.json"
            bjson = json.loads(tar.extractfile("BUNDLE.json").read())
            declared = {e["path"]: e for e in bjson.get("files", [])}
            actual = [n for n in names if n != "BUNDLE.json"]
            if set(actual) != set(declared):
                return 2, ("bundle file list does not match BUNDLE.json "
                           f"({len(actual)} files, {len(declared)} declared)")
            bad = 0
            for name in actual:
                data = tar.extractfile(name).read()
                if _sha256_bytes(data) != declared[name]["sha256"]:
                    bad += 1
            if bad:
                return 2, f"{bad} file(s) failed sha256 verification"
    except (tarfile.TarError, ValueError, OSError) as exc:
        return 2, f"bundle is corrupt: {exc}"
    # D4: version mismatch warns but never blocks.
    bundle_ver = bjson.get("vise_version")
    if bundle_ver != _VISE_VERSION:
        print(f"bundle import: WARNING bundle built with vise "
              f"v{bundle_ver}, current vise v{_VISE_VERSION}",
              file=sys.stderr)
    return 0, (f"bundle ok: {len(declared)} file(s), "
               f"vise v{bundle_ver}, "
               f"created {bjson.get('created_at')}")


def cmd_export(args) -> int:
    try:
        info = bundle_export(Path(args.out),
                             include_audit=getattr(args, "include_audit", False))
    except (ValueError, vise_lock.LockTimeout) as exc:
        print(f"bundle: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, ValueError) else vise_lock.LOCK_EXIT_CODE
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        print(f"bundle: exported {info['files']} file(s) -> {info['out']} "
              f"(vise v{info['vise_version']})")
    return 0


def cmd_import(args) -> int:
    rc, rows, msg = _run_import(Path(args.file), args)
    _print_report(rows, msg, dry_run=getattr(args, "dry_run", False),
                  json_out=getattr(args, "json", False))
    return rc


def _run_import(bundle_path: Path, args) -> tuple[int, list[dict], str]:
    """Dispatch `bundle import` with the lock held (dry-run is lock-free)."""
    conflicts = getattr(args, "conflicts", "ours")
    if getattr(args, "dry_run", False):
        return bundle_import(bundle_path, conflicts=conflicts, dry_run=True)
    import vise_lock
    try:
        with vise_lock.write_lock("bundle import"):
            return bundle_import(bundle_path, conflicts=conflicts,
                                 dry_run=False)
    except vise_lock.LockTimeout as exc:
        return vise_lock.LOCK_EXIT_CODE, [], str(exc)


# --- import (D5): per-adapter reconciliation ---------------------------------

# The only trees a bundle may write into. Anything else in the tar is
# rejected outright - a bundle is generated by `bundle export`, so a
# different shape is corruption or tampering, not a new feature.
IMPORT_ROOT_PREFIXES = ("adapters/", ".vise/baseline/", ".vise/patches/",
                        ".vise/state/")
# Verified by BUNDLE.json but never written: vise.release.json is
# regenerated locally together with the registry after import.
IMPORT_IGNORED_FILES = {"vise.release.json"}


def _validate_bundle_names(names: list[str]) -> list[str]:
    bad = []
    for name in names:
        if name == "BUNDLE.json" or name in IMPORT_IGNORED_FILES:
            continue
        if name.startswith("/") or ".." in Path(name).parts \
                or not name.startswith(IMPORT_ROOT_PREFIXES):
            bad.append(name)
    return bad


def _bundle_layout(tar, bjson: dict) -> dict[str, dict]:
    """Group verified bundle members per adapter id.

    Returns {adapter_id: {"dir": <dir name>, "files": {rel: {sha256, size}},
    "baseline": [tar names], "patches": [tar names], "state": [tar names]}}.
    Raises ValueError on structural problems (missing manifest, duplicate id,
    unexpected paths) - those are input errors, not reconciliation outcomes.
    """
    file_names = [n for n in tar.getnames() if not n.endswith("/")]
    bad = _validate_bundle_names(file_names)
    if bad:
        raise ValueError("bundle contains paths outside the import surface, "
                         f"e.g. {bad[0]}")
    declared = {e["path"]: e for e in bjson.get("files", [])}

    adapters: dict[str, dict] = {}
    dir_to_id: dict[str, str] = {}
    for name in file_names:
        parts = name.split("/")
        if parts[0] != "adapters" or len(parts) < 3:
            continue
        d = parts[1]
        aid = dir_to_id.get(d)
        if aid is None:
            man_name = f"adapters/{d}/vise.adapter.json"
            if man_name not in declared:
                raise ValueError(f"bundle adapter dir 'adapters/{d}' has no "
                                 "vise.adapter.json - not a Vise export")
            try:
                man = json.loads(tar.extractfile(man_name).read())
            except (json.JSONDecodeError, OSError) as exc:
                raise ValueError(f"bundle adapter dir 'adapters/{d}': "
                                 f"manifest unreadable: {exc}")
            aid = man.get("adapter_id") if isinstance(man, dict) else None
            if not aid:
                raise ValueError(f"adapters/{d}/vise.adapter.json declares "
                                 "no adapter_id")
            if aid in adapters:
                raise ValueError(f"two bundle directories claim adapter_id "
                                 f"'{aid}' ('{adapters[aid]['dir']}' and "
                                 f"'{d}') - corrupt or hand-made bundle")
            dir_to_id[d] = aid
            adapters[aid] = {"dir": d, "files": {}, "baseline": [],
                             "patches": [], "state": []}
        rel = "/".join(parts[2:])
        adapters[aid]["files"][rel] = {"sha256": declared[name]["sha256"],
                                       "size": declared[name].get("size")}

    prefix_map = (".vise/baseline/", ".vise/patches/", ".vise/state/")
    key_map = {".vise/baseline/": "baseline", ".vise/patches/": "patches",
               ".vise/state/": "state"}
    for name in file_names:
        if name == "BUNDLE.json" or name in IMPORT_IGNORED_FILES:
            continue
        for prefix in prefix_map:
            if name.startswith(prefix):
                aid = name[len(prefix):].split("/", 1)[0]
                if aid in adapters:
                    adapters[aid][key_map[prefix]].append(name)
                break  # per-adapter only; other ids are not our business
    return adapters


def _write_member(tar, name: str, dest: Path) -> None:
    member = tar.getmember(name)
    data = tar.extractfile(member).read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    if member.mode & 0o777:
        dest.chmod(member.mode & 0o777)


def _review_reason(path: str, old: dict, new: dict) -> str:
    """Why this path is undecidable - attribution unknown, never an accusation."""
    if path not in new:
        return ("the bundle drops this file, but the on-disk copy differs "
                "from the old baseline - left in place; delete it yourself "
                "if unwanted")
    if path not in old:
        return ("the bundle adds this path, but a different file already "
                "exists there - left in place; the bundle copy was not "
                "written")
    return ("matches neither the old baseline nor the bundle bytes - Vise "
            "cannot attribute the change; left in place")


def bundle_import(bundle_path: Path, *, conflicts: str = "ours",
                  dry_run: bool = False) -> tuple[int, list[dict], str]:
    """Verify then reconcile a bundle. Returns (exit_code, rows, check_msg)."""
    rc, msg = bundle_import_check(bundle_path)
    if rc != 0:
        return rc, [], msg
    try:
        with tarfile.open(bundle_path, mode="r:gz") as tar:
            bjson = json.loads(tar.extractfile("BUNDLE.json").read())
            adapters = _bundle_layout(tar, bjson)
            rows = _apply_bundle(tar, adapters, conflicts=conflicts,
                                 dry_run=dry_run)
    except (tarfile.TarError, ValueError, OSError) as exc:
        return 2, [], f"bundle import failed: {exc}"
    pending = any(r.get("review") for r in rows)
    return (2 if pending else 0), rows, msg


def _apply_bundle(tar, adapters: dict[str, dict], *, conflicts: str,
                  dry_run: bool) -> list[dict]:
    import baseline as vise_baseline

    reg_paths = {a["adapter_id"]: a["path"]
                 for a in vise_baseline.registry_adapters()}
    rows: list[dict] = []
    post_establish: list[str] = []
    totals = {"patches_copied": 0, "patches_skipped": 0, "state_copied": 0}

    for aid in sorted(adapters):
        info = adapters[aid]
        d = info["dir"]
        row = {"adapter_id": aid, "dir": f"adapters/{d}", "mode": None,
               "updated": 0, "deleted": 0, "kept": 0, "user": 0,
               "review": [], "baseline": None, "patches": None,
               "state": None, "snapshot": None}
        rows.append(row)

        # --- mode resolution --------------------------------------------
        local_rel = reg_paths.get(aid)
        old_base = vise_baseline.load(aid)
        if local_rel is None and old_base is not None:
            local_rel = old_base.get("source_root")
        if local_rel is None and (VISE_ROOT / "adapters" / d).is_dir():
            local_rel = f"adapters/{d}"

        if local_rel is not None:
            if Path(local_rel).name != d:
                row["mode"] = "skipped"
                row["reason"] = (f"local directory '{local_rel}' holds this "
                                 f"adapter_id but does not match the bundle "
                                 f"directory 'adapters/{d}' - import skipped")
                continue
            mode = "merge"
            local = VISE_ROOT / local_rel
            old = (old_base or {}).get("files", {})
            disk = vise_baseline.scan(local) if local.is_dir() else {}
            plan = vise_baseline.classify(old, info["files"], disk)
        else:
            mode = "fresh"
            local = VISE_ROOT / "adapters" / d
            plan = {}
        row["mode"] = mode

        # --- patches: copy-if-absent by filename, never replayed ---------
        patch_copied, patch_skipped = [], []
        for name in info["patches"]:
            dest = VISE_ROOT / name
            if dest.exists():
                patch_skipped.append(name)
            else:
                if not dry_run:
                    _write_member(tar, name, dest)
                patch_copied.append(name)
        if info["patches"]:
            row["patches"] = (f"{len(patch_copied)} copied, "
                              f"{len(patch_skipped)} skipped")
            totals["patches_copied"] += len(patch_copied)
            totals["patches_skipped"] += len(patch_skipped)

        # --- merge: three-way apply with a pre-merge snapshot -------------
        if mode == "merge":
            if not dry_run and local.is_dir():
                try:
                    import adapter_guard
                    snap = adapter_guard.snapshot(
                        aid, reason="bundle import: pre-merge snapshot",
                        triggered_by="bundle_import")
                    if snap.get("status") == "unknown-adapter":
                        row["snapshot"] = "skipped (adapter not registered)"
                except OSError as exc:
                    row["snapshot"] = f"skipped ({exc})"
            for path, act in plan.items():
                meta = info["files"].get(path)
                src_name = f"adapters/{d}/{path}"
                if act == "update" or (act == "review" and conflicts == "theirs"
                                       and meta is not None):
                    if not dry_run:
                        _write_member(tar, src_name, local / path)
                    row["updated"] += 1
                elif act == "delete":
                    if not dry_run:
                        victim = local / path
                        if victim.is_file():
                            victim.unlink()
                    row["deleted"] += 1
                elif act == "keep":
                    row["kept"] += 1
                elif act == "user":
                    row["user"] += 1
                else:  # review, unresolved
                    row["review"].append(
                        {"path": path, "reason": _review_reason(path, old,
                                                                info["files"])})
            row["baseline"] = ("established from the applied tree"
                               if not dry_run else
                               "would be re-established from the applied tree")
            if not dry_run:
                post_establish.append(aid)
            continue

        # --- fresh: verbatim extraction (files, baseline, state) ----------
        if not dry_run:
            for rel in sorted(info["files"]):
                _write_member(tar, f"adapters/{d}/{rel}",
                              VISE_ROOT / "adapters" / d / rel)
            if info["baseline"]:
                for name in info["baseline"]:
                    _write_member(tar, name, VISE_ROOT / name)
                row["baseline"] = "from bundle (provenance preserved)"
            else:
                row["baseline"] = "established after registration"
        state_copied = 0
        for name in info["state"]:
            dest = VISE_ROOT / name
            if dest.exists():
                continue
            if not dry_run:
                _write_member(tar, name, dest)
            state_copied += 1
        if info["state"]:
            row["state"] = f"{state_copied} file(s) copied"
            totals["state_copied"] += state_copied
        if not info["baseline"] and not dry_run:
            post_establish.append(aid)
        row["kept"] = len(info["files"])

    # --- finalize: register, establish, regenerate the release manifest ---
    if dry_run:
        if post_establish:
            rows.append({"mode": "summary",
                         "note": f"would register {len(post_establish)} "
                                 "adapter(s) and establish/refresh their "
                                 "baselines"})
        return rows
    try:
        from register_adapters import register
        import release as vise_release
        rc, warnings = register(announce=False)
        if rc != 0:
            raise ValueError("post-import registration failed:\n"
                             + "\n".join(warnings))
        for aid in post_establish:
            vise_baseline.establish(aid, provider="install", refresh=True)
        vise_release.generate(VISE_ROOT)
    except (OSError, ValueError) as exc:
        raise ValueError(f"post-import refresh failed: {exc}")
    return rows


def _print_report(rows: list[dict], msg: str, *, dry_run: bool,
                  json_out: bool) -> None:
    tag = "[dry-run] " if dry_run else ""
    print(f"bundle import: {msg}")
    if json_out:
        print(json.dumps({"rows": [r for r in rows if r.get("mode") != "summary"],
                          "dry_run": dry_run}, indent=2, ensure_ascii=False))
        return
    for r in rows:
        mode = r.get("mode")
        if mode == "summary":
            print(f"{tag}bundle import: {r['note']}")
            continue
        aid = r["adapter_id"]
        if mode == "skipped":
            print(f"{tag}[SKIP]    {aid}: {r['reason']}")
        elif mode == "fresh":
            extra = f", {r['baseline']}" if r.get("baseline") else ""
            print(f"{tag}[ADDED]   {aid}: fresh from bundle "
                  f"({r['kept']} file(s){extra})")
        else:
            bits = [f"{r['updated']} updated", f"{r['deleted']} deleted",
                    f"{r['kept']} kept"]
            if r["user"]:
                bits.append(f"{r['user']} user file(s) untouched")
            print(f"{tag}[MERGE]   {aid}: " + ", ".join(bits))
        for rf in r.get("review", []):
            print(f"{tag}           ? {rf['path']}: {rf['reason']}")
        if r.get("snapshot"):
            print(f"{tag}           snapshot: {r['snapshot']}")
    pending = [r for r in rows if r.get("review")]
    if pending:
        n = sum(len(r["review"]) for r in pending)
        print(f"\n{len(pending)} adapter(s) need review ({n} file(s) "
              "undecidable). Review means Vise cannot attribute a change - "
              "not that you edited it.")
        print("  To take the bundle's bytes:   re-run with --conflicts=theirs")
        print("  To keep the on-disk state:   visectl adapter rebaseline "
              "<id> --force")


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="bundle")
    sub = parser.add_subparsers(dest="command", required=True)
    pe = sub.add_parser("export")
    pe.add_argument("--out", required=True)
    pe.add_argument("--include-audit", action="store_true")
    pe.add_argument("--json", action="store_true")
    pi = sub.add_parser("import")
    pi.add_argument("file")
    pi.add_argument("--conflicts", choices=["ours", "theirs"], default="ours",
                    help="review-path resolution: ours (default) leaves the "
                         "on-disk copy, theirs takes the bundle bytes")
    pi.add_argument("--dry-run", action="store_true",
                    help="print the per-file plan without touching disk, "
                         "snapshots or the registry")
    pi.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "export":
        return cmd_export(args)
    return cmd_import(args)


if __name__ == "__main__":
    sys.exit(main())
