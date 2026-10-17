#!/usr/bin/env python3
"""release.py - generate and check `vise.release.json`, the release manifest.

The manifest is an ordinary in-tree file, so it rides along with any
distribution channel (zip, tarball, rsync, clone). It records, per adapter,
the sha256+size of every vendor file, plus the release version it describes.
`core/baseline.py upgrade` uses it as the *new* leg of the three-way compare;
`restore --base` surfaces it as provenance.

Discipline (enforced by `check` in CI / tests/smoke.sh): the manifest always
describes the tree it sits in. Regenerate it whenever adapters/ changes and
whenever `core/version.py` is bumped - both land in the same commit:

    .venv/bin/python core/release.py generate

Exit codes (baseline domain, see core/baseline.py):
  generate   0 written, 2 usage or input error
  check      0 manifest matches the tree, 1 mismatch, 2 no manifest / usage
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import baseline  # core/ is on sys.path when run as a script

VISE_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_VERSION = 1


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def tree_version(root: Path) -> str:
    """The VERSION constant of a tree's core/version.py (any root)."""
    path = root / "core" / "version.py"
    if not path.is_file():
        raise ValueError(f"{path} not found - not a Vise tree")
    spec = importlib.util.spec_from_file_location("vise_tree_version", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return str(mod.VERSION)


def build_manifest(root: Path, release: str) -> dict:
    """Fingerprint every adapter under <root>/adapters/."""
    adapters: dict[str, dict] = {}
    adir_root = root / "adapters"
    skipped: list[str] = []
    if adir_root.is_dir():
        for adir in sorted(adir_root.iterdir()):
            if not adir.is_dir() or adir.name.startswith("."):
                continue
            aid = baseline._adapter_id_for(adir)
            if not aid:
                skipped.append(adir.name)
                continue
            if aid in adapters:
                raise ValueError(f"duplicate adapter_id '{aid}' "
                                 f"({adir.name})")
            adapters[aid] = {"path": f"adapters/{adir.name}",
                             "files": baseline.scan(adir)}
    for name in skipped:
        print(f"release: WARNING: adapters/{name} has no readable "
              "vise.adapter.json - skipped", file=sys.stderr)
    return {"version": MANIFEST_VERSION,
            "release": release,
            "generated_at": _utcnow(),
            "adapters": adapters}


def check(root: Path) -> dict:
    """Compare the manifest against the tree it sits in.

    Returns {"ok", "release", "tree_version", "adapters": {aid: report}}.
    A mismatch is three kinds: changed (hash differs), missing (manifest
    only), unlisted (tree only) - plus adapters present without a manifest
    entry and vice versa.
    """
    data = baseline.load_release_manifest(root)  # None or raises ValueError
    if data is None:
        raise ValueError(f"no {baseline.RELEASE_MANIFEST} in {root}")
    report: dict = {"ok": True, "release": data.get("release"),
                    "tree_version": tree_version(root), "adapters": {}}
    if data.get("release") != report["tree_version"]:
        report["ok"] = False
        report["release_mismatch"] = (
            f"manifest describes v{data.get('release')} but "
            f"core/version.py is v{report['tree_version']} - regenerate: "
            "core/release.py generate")
    man = data["adapters"]
    on_disk = {p.name: p for p in (root / "adapters").iterdir()
               if p.is_dir() and not p.name.startswith(".")} \
        if (root / "adapters").is_dir() else {}
    listed_paths = {e.get("path", "") for e in man.values()}
    for aid, entry in sorted(man.items()):
        adir = root / entry.get("path", "")
        want = {rel: meta["sha256"]
                for rel, meta in entry.get("files", {}).items()}
        got = ({rel: meta["sha256"]
                for rel, meta in baseline.scan(adir).items()}
               if adir.is_dir() else {})
        changed = sorted(p for p in want.keys() & got.keys()
                         if want[p] != got[p])
        missing = sorted(want.keys() - got.keys())
        unlisted = sorted(got.keys() - want.keys())
        ok = not (changed or missing or unlisted)
        report["adapters"][aid] = {"ok": ok, "changed": changed,
                                   "missing": missing, "unlisted": unlisted}
        report["ok"] = report["ok"] and ok
    for name, adir in sorted(on_disk.items()):
        if f"adapters/{name}" not in listed_paths:
            aid = baseline._adapter_id_for(adir)
            if aid:  # an unlisted adapter dir, not a stray folder
                report["ok"] = False
                report["adapters"][aid or name] = {
                    "ok": False, "changed": [], "missing": [],
                    "unlisted": ["<whole adapter>"]}
    return report


def generate(root: Path, release: str | None = None) -> dict:
    """(Re)write the release manifest for `root`; reusable, no argparse.

    Raises ValueError on input errors (e.g. not a Vise tree). Returns a small
    summary dict. The write is atomic (tmp file + os.replace).
    """
    root = Path(root).resolve()
    release = release or tree_version(root)
    manifest = build_manifest(root, release)
    path = baseline.release_manifest_path(root)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    n_files = sum(len(e["files"]) for e in manifest["adapters"].values())
    return {"path": str(path), "release": release,
            "adapters": len(manifest["adapters"]), "files": n_files}


def cmd_generate(args: argparse.Namespace) -> int:
    try:
        out = generate(Path(args.root), release=args.release)
    except ValueError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"[RELEASE] wrote {out['path']}: v{out['release']}, "
              f"{out['adapters']} adapters, {out['files']} files")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    try:
        report = check(root)
    except ValueError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    if report.get("release_mismatch"):
        print(f"[BAD]  {report['release_mismatch']}")
    for aid, r in sorted(report["adapters"].items()):
        if r["ok"]:
            continue
        print(f"[BAD]  {aid}:")
        for kind, mark in (("changed", "M"), ("missing", "D"),
                           ("unlisted", "A")):
            for path in r[kind]:
                print(f"         {mark} {path}")
    if report["ok"]:
        print(f"[OK]   {baseline.RELEASE_MANIFEST} matches the tree "
              f"(v{report['release']})")
    else:
        print("Regenerate with: .venv/bin/python core/release.py generate")
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release.py",
        description="Generate and check vise.release.json (release manifest).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="(re)write the release manifest")
    p_gen.add_argument("--root", default=str(VISE_ROOT),
                       help="tree to fingerprint (default: this repo)")
    p_gen.add_argument("--release", default=None,
                       help="release stamp (default: the tree's "
                            "core/version.py VERSION)")
    p_gen.add_argument("--json", action="store_true")

    p_chk = sub.add_parser("check",
                           help="verify the manifest matches the tree")
    p_chk.add_argument("--root", default=str(VISE_ROOT),
                       help="tree to check (default: this repo)")
    p_chk.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    return {"generate": cmd_generate, "check": cmd_check}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
