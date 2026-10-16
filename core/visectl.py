#!/usr/bin/env python3
"""visectl - unified Vise CLI entry point.

All adapter operations go through this single command, eliminating
per-adapter global function naming conflicts (P0-1 fix).

Usage:
  visectl list                           # list all enabled adapters
  visectl describe <adapter_id>          # show adapter manifest
  visectl thread <adapter_id>            # bootstrap a single adapter
  visectl thread --all                    # bootstrap all adapters
  visectl run <adapter_id> <command> [args...]  # run adapter CLI
  visectl read <adapter_id> [query...]          # read adapter knowledge content
  visectl mcp                                   # MCP stdio bridge (JSON-RPC)
  visectl doctor                                # diagnose without executing init_steps
  visectl clean --reports                 # remove report files >30 days old (P2-3)
  visectl clean --state                   # remove state for adapters not in registry (P2-3)
  visectl clean --reports --state         # do both
  visectl clean --dry-run                 # show what would be removed, don't delete
  visectl manifest list                   # files Vise added outside VISE_ROOT
  visectl manifest verify                 # check those files are intact
  visectl adapter status                  # clean / modified / unbaselined
  visectl adapter diff <id>               # drift against the baseline
  visectl adapter verify [id]             # baseline tree intact vs manifest
  visectl adapter snapshot <id> -m "why"  # snapshot BEFORE editing an adapter
  visectl adapter restore <id> [--base]   # roll back to the snapshot/baseline
  visectl adapter promote <id> -m "why"   # sediment the change as a patch
  visectl adapter rebaseline <id>         # accept the tree as the reference
  visectl adapter upgrade [--staged DIR]  # reconcile baselines with a release

All commands support --json for machine-readable output to stdout.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import vise_adapter_paths as lap

VISE_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = VISE_ROOT / ".vise" / "registry.json"
REPORTS_DIR = VISE_ROOT / ".vise" / "reports"
STATE_DIR = VISE_ROOT / ".vise" / "state"
REPORT_TTL_DAYS = 30  # P2-3: reports older than this are cleaned

# Guard domain exit code (C13): a modified adapter with no open snapshot is
# refused by `run` and `promote`. Kept out of the viseinit 0-6 map.
EXIT_UNPROTECTED = 7


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def get_registry() -> list[dict]:
    if not REGISTRY_PATH.is_file():
        # P2-1: registry is a generated artifact, not repo-managed
        print(f"visectl: {REGISTRY_PATH.relative_to(VISE_ROOT)} not found "
              "(generated artifact). Run: bash init/setup.sh", file=sys.stderr)
        sys.exit(1)
    registry = load_json(REGISTRY_PATH)
    return [a for a in registry.get("adapters", []) if a.get("enabled")]


def get_adapter_dir(adapter_id: str) -> Path | None:
    for a in get_registry():
        if a["adapter_id"] == adapter_id:
            return (VISE_ROOT / a["path"]).resolve()
    return None


def get_manifest(adapter_id: str) -> dict | None:
    adapter_dir = get_adapter_dir(adapter_id)
    if adapter_dir is None:
        return None
    manifest_path = adapter_dir / "vise.adapter.json"
    if not manifest_path.is_file():
        return None
    return load_json(manifest_path)


def _guard():
    """Lazy import: the guard module is only needed on the update path."""
    import adapter_guard
    return adapter_guard


def _baseline():
    import baseline
    return baseline


def cmd_list(args: argparse.Namespace) -> int:
    adapters = get_registry()

    # P3-1: filter by category / tag
    category = getattr(args, "category", None)
    tag = getattr(args, "tag", None)
    if category or tag:
        filtered = []
        for a in adapters:
            m = get_manifest(a["adapter_id"]) or {}
            prov = m.get("provides", {})
            if category and prov.get("category") != category:
                continue
            if tag and tag not in prov.get("tags", []):
                continue
            filtered.append(a)
        adapters = filtered

    if args.json:
        out = []
        for a in adapters:
            m = get_manifest(a["adapter_id"]) or {}
            # Parity with the human table: discovery needs identity
            # (name/version), not just the path — an agent matches a need
            # against these before deciding describe/run.
            entry = {"adapter_id": a["adapter_id"], "path": a["path"],
                     "name": m.get("name", a["adapter_id"]),
                     "version": m.get("version", "?")}
            if m.get("provides"):
                entry["provides"] = m["provides"]
            out.append(entry)
        print(json.dumps(out, indent=2))
        return 0
    if not adapters:
        print("No enabled adapters.")
        return 0
    print(f"{'adapter_id':<20} {'path'}")
    print("-" * 60)
    for a in adapters:
        m = get_manifest(a["adapter_id"]) or {}
        name = m.get("name", a["adapter_id"])
        ver = m.get("version", "?")
        print(f"{a['adapter_id']:<20} {a['path']}  ({name} v{ver})")
    return 0


def format_step(step: dict) -> str:
    """Human-readable form of an init_step/healthcheck (P1-1: both formats)."""
    if "command" in step:
        return step["command"]
    return " ".join([step.get("run", "?")]
                    + [str(a) for a in step.get("args", [])])


def cmd_describe(args: argparse.Namespace) -> int:
    manifest = get_manifest(args.adapter_id)
    if manifest is None:
        print(f"visectl: adapter '{args.adapter_id}' not found or has no manifest",
              file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(manifest, indent=2))
    else:
        print(f"Adapter: {manifest.get('name', args.adapter_id)}")
        print(f"  id:          {manifest.get('adapter_id')}")
        print(f"  version:     {manifest.get('version')}")
        print(f"  description: {manifest.get('description', '')}")
        print(f"  required_cots: {manifest.get('required_cots', [])}")
        prov = manifest.get("provides")
        if prov:
            print(f"  provides:")
            print(f"    category:    {prov.get('category', '?')}")
            print(f"    tags:        {prov.get('tags', [])}")
            if prov.get("description"):
                print(f"    description: {prov['description']}")
        print(f"  entry_points:")
        for k, v in manifest.get("entry_points", {}).items():
            print(f"    {k}: {v}")
        cmds = manifest.get("commands", [])
        if cmds:
            print(f"  commands:")
            for c in cmds:
                arg_desc = ", ".join(
                    a["name"] + ("" if a.get("required") else "?")
                    for a in c.get("args", []))
                line = f"    - {c.get('name', '?')}: {c.get('description', '')}"
                if arg_desc:
                    line += f"  (args: {arg_desc})"
                print(line)
        steps = manifest.get("init_steps", [])
        if steps:
            print(f"  init_steps:")
            for s in steps:
                print(f"    - {s.get('name', '?')}: {format_step(s)}")
        hc = manifest.get("healthcheck")
        if hc:
            print(f"  healthcheck: {format_step(hc)}")
    return 0


def cmd_thread(args: argparse.Namespace) -> int:
    if args.all:
        # Delegate to viseinit --all
        viseinit = VISE_ROOT / "core" / "viseinit.py"
        python = lap.resolve("python") or sys.executable
        proc = subprocess.run([python, str(viseinit), "--all"])
        return proc.returncode

    adapter_dir = get_adapter_dir(args.adapter_id)
    if adapter_dir is None:
        print(f"visectl: unknown adapter '{args.adapter_id}'", file=sys.stderr)
        return 1

    thread_script = adapter_dir / "thread"
    if not thread_script.is_file():
        print(f"visectl: adapter '{args.adapter_id}' has no thread script",
              file=sys.stderr)
        return 1

    env = dict(os.environ)
    env["VISE_ROOT"] = str(VISE_ROOT)
    env["ADAPTER_DIR"] = str(adapter_dir)
    env["PYTHON"] = lap.resolve("python") or sys.executable
    proc = subprocess.run(["bash", str(thread_script)], cwd=adapter_dir, env=env)
    return proc.returncode


def cmd_run(args: argparse.Namespace) -> int:
    manifest = get_manifest(args.adapter_id)
    if manifest is None:
        print(f"visectl: adapter '{args.adapter_id}' not found or has no manifest",
              file=sys.stderr)
        return 1

    # C13 gate: an adapter edited without a snapshot is unprotected - it can
    # neither be trusted nor rolled back, so refuse to run it. An *unbaselined*
    # adapter passes through (an install predating baselines must keep working);
    # `doctor` / `adapter status` are where that state is surfaced.
    if not getattr(args, "allow_modified", False):
        gate = _guard().check(args.adapter_id)
        if gate["state"] == "modified" and not gate["protected"]:
            print(f"visectl: refusing to run '{args.adapter_id}': {gate['note']}",
                  file=sys.stderr)
            return EXIT_UNPROTECTED

    cli_entry = manifest.get("entry_points", {}).get("cli")
    if not cli_entry:
        print(f"visectl: adapter '{args.adapter_id}' has no CLI entry point",
              file=sys.stderr)
        return 1

    adapter_dir = get_adapter_dir(args.adapter_id)
    cli_path = adapter_dir / cli_entry
    python = lap.resolve("python") or sys.executable
    proc = subprocess.run([python, str(cli_path)] + args.cli_args,
                          cwd=adapter_dir)
    return proc.returncode


def cmd_read(args: argparse.Namespace) -> int:
    """Resource access contract: read knowledge content from an adapter.

    Dispatch: explicit entry_points.read script if declared, otherwise the
    conventional `read` subcommand of the adapter CLI.
    """
    manifest = get_manifest(args.adapter_id)
    if manifest is None:
        print(f"visectl: adapter '{args.adapter_id}' not found or has no manifest",
              file=sys.stderr)
        return 1
    entry_points = manifest.get("entry_points", {})
    adapter_dir = get_adapter_dir(args.adapter_id)
    python = lap.resolve("python") or sys.executable
    if entry_points.get("read"):
        cmd = [python, str(adapter_dir / entry_points["read"])] + args.query
    else:
        cli_entry = entry_points.get("cli")
        if not cli_entry:
            print(f"visectl: adapter '{args.adapter_id}' has no read/cli entry",
                  file=sys.stderr)
            return 1
        cmd = [python, str(adapter_dir / cli_entry), "read"] + args.query
    return subprocess.run(cmd, cwd=adapter_dir).returncode


def cmd_mcp(_args: argparse.Namespace) -> int:
    """Run the MCP stdio bridge (JSON-RPC on stdin/stdout)."""
    server = VISE_ROOT / "core" / "vise_mcp.py"
    python = lap.resolve("python") or sys.executable
    return subprocess.run([python, str(server)]).returncode


def cmd_doctor(args: argparse.Namespace) -> int:
    """Diagnose adapters without executing init_steps."""
    adapters = get_registry()
    all_ok = True
    results = []

    for a in adapters:
        r = {"adapter_id": a["adapter_id"], "status": "ok", "issues": []}
        manifest = get_manifest(a["adapter_id"])
        if manifest is None:
            r["status"] = "failed"
            r["issues"].append("manifest not found")
            results.append(r)
            all_ok = False
            continue

        # Check dependencies — supports string and object format (P1-2)
        required = manifest.get("required_cots", [])
        deps = {}
        missing = []
        for item in required:
            if isinstance(item, str):
                tool, min_ver = item, None
            elif isinstance(item, dict):
                tool, min_ver = item["tool"], item.get("min_version")
            else:
                continue
            if min_ver:
                vr = lap.resolve_with_version(tool, min_ver)
                deps[tool] = {"path": vr["path"],
                              "version": vr.get("version"),
                              "required": min_ver}
                if not vr["ok"]:
                    if vr["path"] is None:
                        missing.append(tool)
                    elif vr.get("version"):
                        missing.append(
                            f"{tool} (v{vr['version']} < required {min_ver})")
                    else:
                        missing.append(f"{tool} (version check failed)")
            else:
                p = lap.resolve(tool)
                deps[tool] = p
                if p is None:
                    missing.append(tool)
        if missing:
            r["status"] = "failed"
            r["issues"].append(f"missing COTS: {', '.join(missing)}")
            all_ok = False

        r["dependencies"] = deps
        steps = manifest.get("init_steps", [])
        if steps:
            r["init_steps"] = [format_step(s) for s in steps]
        hc = manifest.get("healthcheck")
        if hc:
            r["healthcheck"] = format_step(hc)

        # C13: report adapter-code drift. An unprotected modified adapter is
        # refused by `run`, so it is a genuine usability failure, not a note.
        gate = _guard().check(a["adapter_id"])
        r["guard"] = {"state": gate["state"], "protected": gate["protected"],
                      "open_snapshot": gate["open_snapshot"]}
        if gate["state"] == "modified" and not gate["protected"]:
            r["status"] = "failed"
            r["issues"].append(f"unprotected drift: {gate['note']}")
            all_ok = False

        # A corrupted baseline tree makes `restore --base` silently produce
        # bytes that no longer match the manifest - a usability failure too.
        bv = _baseline().verify(a["adapter_id"])
        r["baseline"] = bv["status"]
        if bv["status"] == "corrupted":
            r["status"] = "failed"
            r["issues"].append(
                f"baseline corrupted: {len(bv['changed'])} changed, "
                f"{len(bv['missing'])} missing, {len(bv['added'])} added - "
                f"run: visectl adapter verify {a['adapter_id']}")
            all_ok = False
        results.append(r)

    # P2: residue from adapters removed from adapters/ - reported, never a
    # failure (it breaks nothing), and never auto-deleted (orphan baselines
    # and patches may be the last copy of unpublished work).
    orphans = _baseline().orphan_ids()

    if args.json:
        print(json.dumps({"adapters": results, "orphans": orphans},
                         indent=2))
    else:
        for r in results:
            mark = "[OK]" if r["status"] == "ok" else "[FAIL]"
            print(f"{mark} {r['adapter_id']}")
            for issue in r["issues"]:
                print(f"       -> {issue}")
            for step in r.get("init_steps", []):
                print(f"       init: {step}")
            if r.get("healthcheck"):
                print(f"       healthcheck: {r['healthcheck']}")
            guard = r.get("guard") or {}
            if guard.get("state") not in (None, "clean"):
                detail = (f"open snapshot {guard['open_snapshot']}"
                          if guard.get("open_snapshot") else guard["state"])
                print(f"       guard: {detail}")
        print(f"\nDoctor: {sum(r['status']=='ok' for r in results)}/{len(results)} OK")
        if orphans:
            flat = sorted({aid for ids in orphans.values() for aid in ids})
            print(f"Orphan state (adapter gone, generated files remain): "
                  f"{', '.join(flat)}")
            print("  -> inspect or remove with: visectl clean --state [--dry-run]")

    return 0 if all_ok else 1


def cmd_adapter(args: argparse.Namespace) -> int:
    """Dispatch `visectl adapter <verb>` to the baseline / guard modules.

    Verb-to-module mapping: `status` and `diff` are read-only baseline views;
    everything else is a guard action. Flag names are identical in both CLIs,
    so the parsed namespace is handed over unchanged.
    """
    verb = args.adapter_cmd
    # Mirrors the check in core/adapter_guard.py's own CLI: `-m ""` satisfies
    # argparse's `required=True` while carrying no information, and the reason
    # is what makes a later rollback decision auditable.
    if verb in ("snapshot", "promote") and not args.message.strip():
        print(f"visectl: {verb}: --message must not be blank - "
              "record WHY the adapter changes", file=sys.stderr)
        return 2
    if verb in ("status", "diff", "verify", "upgrade"):
        return getattr(_baseline(), f"cmd_{verb}")(args)
    return getattr(_guard(), f"cmd_{verb}")(args)


def cmd_manifest(args: argparse.Namespace) -> int:
    """Read-only view of the outside-VISE_ROOT artifact ledger.

    Writes (add/remove/uninstall-apply) stay in core/manifest.py, called by
    installers (setup.sh / thread scripts) and init/uninstall.sh — not
    exposed here to keep the agent-facing CLI read-only for the ledger.
    """
    import manifest as vise_manifest
    if args.manifest_cmd == "list":
        return vise_manifest.cmd_list(args)
    return vise_manifest.cmd_verify(args)


def cmd_clean(args: argparse.Namespace) -> int:
    """Clean up stale reports and orphaned adapter state (P2-3)."""
    dry_run = getattr(args, "dry_run", False)
    do_reports = getattr(args, "reports", False)
    do_state = getattr(args, "state", False)
    # If neither flag given, do both (sensible default)
    if not do_reports and not do_state:
        do_reports = do_state = True

    result = {"dry_run": dry_run, "removed": {}, "kept": {}}

    # --- Clean stale reports (>30 days, keep latest.json) ---
    if do_reports and REPORTS_DIR.is_dir():
        cutoff = time.time() - REPORT_TTL_DAYS * 86400
        removed_reports = []
        kept_reports = []
        for f in sorted(REPORTS_DIR.iterdir()):
            if not f.is_file() or not f.name.endswith(".json"):
                continue
            if f.name == "latest.json":
                kept_reports.append(f.name)
                continue
            if f.stat().st_mtime < cutoff:
                removed_reports.append(f.name)
                if not dry_run:
                    f.unlink()
            else:
                kept_reports.append(f.name)
        result["removed"]["reports"] = removed_reports
        result["kept"]["reports"] = kept_reports

    # --- Clean orphaned state (adapter dirs not in registry) ---
    if do_state:
        try:
            registered_ids = {a["adapter_id"] for a in get_registry()}
        except SystemExit:
            # Registry missing — skip state cleanup
            registered_ids = set()
            result["state_cleanup_skipped"] = "registry not found"
        removed_state = []
        kept_state = []
        # STATE_DIR holds runtime state; the guard trio (baseline/snapshots/
        # patches) holds an adapter's protection history. Same orphan rule:
        # gone from the registry -> residue to report and remove. Orphan
        # baselines/patches may be the last copy of unpublished work, so they
        # are listed by kind in the output and only removed here, explicitly.
        orphan_dirs = [STATE_DIR, VISE_ROOT / ".vise" / "baseline",
                       VISE_ROOT / ".vise" / "snapshots",
                       VISE_ROOT / ".vise" / "patches"]
        for root in orphan_dirs:
            if not root.is_dir():
                continue
            for d in sorted(root.iterdir()):
                if not d.is_dir():
                    continue
                entry = f"{root.name}/{d.name}"
                if d.name not in registered_ids:
                    removed_state.append(entry)
                    if not dry_run:
                        shutil.rmtree(d)
                else:
                    kept_state.append(entry)
        result["removed"]["state"] = removed_state
        result["kept"]["state"] = kept_state

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        prefix = "[DRY RUN] " if dry_run else ""
        if do_reports:
            removed = result["removed"].get("reports", [])
            kept = result["kept"].get("reports", [])
            print(f"{prefix}Reports: {len(removed)} removed, "
                  f"{len(kept)} kept (>{REPORT_TTL_DAYS}d threshold)")
            for name in removed:
                print(f"  {prefix}removed: {name}")
        if do_state:
            if "state_cleanup_skipped" in result:
                print(f"{prefix}State: skipped ({result['state_cleanup_skipped']})")
            else:
                removed = result["removed"].get("state", [])
                kept = result["kept"].get("state", [])
                print(f"{prefix}State: {len(removed)} removed, {len(kept)} kept")
                for name in removed:
                    print(f"  {prefix}removed: {name}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="visectl",
        description="Vise unified CLI — list, describe, thread, run, doctor.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list all enabled adapters")
    p_list.add_argument("--json", action="store_true")
    p_list.add_argument("--category", help="filter by provides.category (P3-1)")
    p_list.add_argument("--tag", help="filter by provides.tags (P3-1)")

    p_desc = sub.add_parser("describe", help="show adapter manifest")
    p_desc.add_argument("adapter_id")
    p_desc.add_argument("--json", action="store_true")

    p_thread = sub.add_parser("thread", help="bootstrap an adapter")
    p_thread.add_argument("adapter_id", nargs="?")
    p_thread.add_argument("--all", action="store_true")

    p_run = sub.add_parser("run", help="run adapter CLI")
    p_run.add_argument("--allow-modified", action="store_true",
                       help="run even if the adapter drifted without a "
                            "snapshot (C13 escape hatch)")
    p_run.add_argument("adapter_id")
    p_run.add_argument("cli_args", nargs=argparse.REMAINDER)

    p_read = sub.add_parser("read", help="read adapter knowledge content")
    p_read.add_argument("adapter_id")
    p_read.add_argument("query", nargs=argparse.REMAINDER)

    sub.add_parser("mcp", help="run MCP stdio bridge (JSON-RPC on stdio)")

    p_doctor = sub.add_parser("doctor", help="diagnose without executing init_steps")
    p_doctor.add_argument("--json", action="store_true")

    # P2-3: clean stale reports and orphaned state
    p_clean = sub.add_parser("clean",
                             help="remove stale reports (>30d) and orphaned state")
    p_clean.add_argument("--reports", action="store_true",
                         help="clean report files older than 30 days")
    p_clean.add_argument("--state", action="store_true",
                         help="clean state for adapters not in registry")
    p_clean.add_argument("--dry-run", action="store_true",
                         help="show what would be removed without deleting")
    p_clean.add_argument("--json", action="store_true")

    # Outside-VISE_ROOT artifact ledger (read-only view; see core/manifest.py)
    p_manifest = sub.add_parser(
        "manifest", help="query the ledger of files Vise added outside VISE_ROOT")
    p_manifest.add_argument("manifest_cmd", choices=["list", "verify"])
    p_manifest.add_argument("--json", action="store_true")

    # C13: adapter protection / rollback / sedimentation. Verbs and flag names
    # mirror core/baseline.py and core/adapter_guard.py so their namespaces pass
    # straight through to the module handlers.
    p_adapter = sub.add_parser(
        "adapter", help="protect, roll back and sediment adapter-code changes")
    a_sub = p_adapter.add_subparsers(dest="adapter_cmd", required=True)

    a_st = a_sub.add_parser("status", help="clean / modified / unbaselined")
    a_st.add_argument("adapter_id", nargs="?")
    a_st.add_argument("--json", action="store_true")

    a_df = a_sub.add_parser("diff", help="file-level drift against the baseline")
    a_df.add_argument("adapter_id")
    a_df.add_argument("--json", action="store_true")

    a_ve = a_sub.add_parser("verify",
                            help="check the baseline tree matches its manifest")
    a_ve.add_argument("adapter_id", nargs="?")
    a_ve.add_argument("--json", action="store_true")

    a_sn = a_sub.add_parser("snapshot",
                            help="snapshot an adapter BEFORE editing it")
    a_sn.add_argument("adapter_id")
    a_sn.add_argument("-m", "--message", required=True,
                      help="why the adapter is about to be modified")
    a_sn.add_argument("--triggered-by", default=None)
    a_sn.add_argument("--force", action="store_true",
                      help="add a snapshot even if one is already open")
    a_sn.add_argument("--json", action="store_true")

    a_ls = a_sub.add_parser("list", help="list snapshot generations")
    a_ls.add_argument("adapter_id")
    a_ls.add_argument("--json", action="store_true")

    a_rs = a_sub.add_parser("restore",
                            help="reinstate a snapshot or the pristine baseline")
    a_rs.add_argument("adapter_id")
    a_rs.add_argument("--gen", type=int, default=None,
                      help="generation to restore (0 = newest, default)")
    a_rs.add_argument("--base", action="store_true",
                      help="restore the pristine baseline instead")
    a_rs.add_argument("--force", action="store_true",
                      help="allow restoring a baseline from an older Vise "
                           "version")
    a_rs.add_argument("--dry-run", action="store_true")
    a_rs.add_argument("--json", action="store_true")

    a_pr = a_sub.add_parser("promote",
                            help="write a patch for the improvement (no commit)")
    a_pr.add_argument("adapter_id")
    a_pr.add_argument("-m", "--message", required=True)
    a_pr.add_argument("--dry-run", action="store_true")
    a_pr.add_argument("--json", action="store_true")

    a_ck = a_sub.add_parser("check", help="gate: is the adapter protected?")
    a_ck.add_argument("adapter_id", nargs="?")
    a_ck.add_argument("--json", action="store_true")

    a_rb = a_sub.add_parser("rebaseline",
                            help="accept the current tree as the new reference")
    a_rb.add_argument("adapter_id", nargs="?")
    a_rb.add_argument("--all", action="store_true")
    a_rb.add_argument("--force", action="store_true")
    a_rb.add_argument("--json", action="store_true")

    a_up = a_sub.add_parser(
        "upgrade",
        help="reconcile baselines against the release manifest (per file)")
    a_up.add_argument("--staged", default=None, metavar="DIR",
                      help="staged release tree (compare before overwrite)")
    a_up.add_argument("--dry-run", action="store_true")
    a_up.add_argument("--json", action="store_true")

    args = parser.parse_args()

    # --json is per-subcommand
    if args.command == "list":
        return cmd_list(args)
    elif args.command == "describe":
        return cmd_describe(args)
    elif args.command == "thread":
        if not args.all and not args.adapter_id:
            parser.error("thread requires an adapter_id or --all")
        return cmd_thread(args)
    elif args.command == "run":
        return cmd_run(args)
    elif args.command == "read":
        return cmd_read(args)
    elif args.command == "mcp":
        return cmd_mcp(args)
    elif args.command == "doctor":
        return cmd_doctor(args)
    elif args.command == "clean":
        return cmd_clean(args)
    elif args.command == "manifest":
        return cmd_manifest(args)
    elif args.command == "adapter":
        return cmd_adapter(args)
    else:
        parser.print_help()
        return 2


if __name__ == "__main__":
    sys.exit(main())
