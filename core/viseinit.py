#!/usr/bin/env python3
"""viseinit - initialize Vise adapters.

Flow (see README "Interface Adapter Layer"):
  load registry.json -> select enabled adapters -> load each
  vise.adapter.json manifest -> resolve required COTS via adapter_paths.json
  (+ PATH fallback) -> run init_steps -> run health check -> collect status
  -> console summary + JSON report.

Exit codes (P1-3, stable for AI Agent consumption):
  0 = all passed
  1 = generic failure (fallback)
  2 = dependency missing
  3 = init step failed
  4 = healthcheck failed
  5 = unknown adapter id
  6 = manifest format error

Usage:
  viseinit --all                      initialize every enabled adapter
  viseinit example_tool ...           initialize only the listed adapters
  viseinit --all --json               machine-readable JSON to stdout (no files)
  viseinit --all --jobs 4             parallel init (P2-2)
  viseinit --all --fail-fast          abort on first failure (P2-2)
  viseinit --all --jobs 4 --keep-going  4-way parallel, run all even if some fail
  viseinit --all --dry-run            review planned steps without executing
  viseinit --all --ndjson             stream newline-delimited progress events

Adapter dependencies (P2-2+):
  Manifest may declare "depends_on": ["other_adapter_id"].
  Adapters are topologically sorted into layers; parallel execution
  only happens within a layer. Layers run sequentially (layer N+1
  waits for layer N to complete). Circular dependencies are detected
  and reported.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import vise_adapter_paths as lap

VISE_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = VISE_ROOT / ".vise" / "registry.json"
REPORTS_DIR = VISE_ROOT / ".vise" / "reports"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_step(command: str, cwd: Path, env: dict, timeout: int) -> dict:
    """Execute a shell command string (legacy format)."""
    start = time.monotonic()
    try:
        proc = subprocess.run(
            command, shell=True, cwd=cwd, env=env,
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "status": "ok" if proc.returncode == 0 else "failed",
            "exit_code": proc.returncode,
            "duration_sec": round(time.monotonic() - start, 2),
            "output": (proc.stdout + proc.stderr).strip()[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "failed",
            "exit_code": None,
            "duration_sec": round(time.monotonic() - start, 2),
            "output": f"timeout after {timeout}s",
        }


def run_step_structured(program: str, args: list[str], cwd: Path,
                        env: dict, timeout: int) -> dict:
    """Execute a structured command: subprocess.run([program] + args, ...).

    Does NOT use shell — avoids quoting/escaping issues (P1-1).
    """
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [program] + list(args), cwd=cwd, env=env,
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "status": "ok" if proc.returncode == 0 else "failed",
            "exit_code": proc.returncode,
            "duration_sec": round(time.monotonic() - start, 2),
            "output": (proc.stdout + proc.stderr).strip()[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "failed",
            "exit_code": None,
            "duration_sec": round(time.monotonic() - start, 2),
            "output": f"timeout after {timeout}s",
        }
    except FileNotFoundError as exc:
        return {
            "status": "failed",
            "exit_code": None,
            "duration_sec": round(time.monotonic() - start, 2),
            "output": f"program not found: {program} ({exc})",
        }


def execute_step(step: dict, cwd: Path, env: dict, default_timeout: int = 120) -> dict:
    """Execute an init_step or healthcheck, supporting both formats.

    Structured (P1-1): {"run": "bash", "args": ["./thread"], "timeout_sec": 60}
    Shell (legacy):    {"command": "bash ./thread", "timeout_sec": 60}

    For structured format, if "run" starts with $, it is expanded from env.
    e.g. {"run": "$PYTHON", "args": ["main.py", "--self-test"]}
    """
    timeout = step.get("timeout_sec", default_timeout)
    if "run" in step:
        program = step["run"]
        # Expand env var references ($VAR) in program path
        if program.startswith("$"):
            var_name = program[1:]
            program = env.get(var_name, program)
        r = run_step_structured(program, step.get("args", []),
                                cwd, env, timeout)
        r["mode"] = "structured"
    else:
        r = run_step(step["command"], cwd, env, timeout)
        r["mode"] = "shell"
    r["name"] = step.get("name", step.get("command", step.get("run", "?")))
    return r


def _fmt_step(step: dict) -> str:
    """Human-readable form of an init_step/healthcheck (both formats)."""
    if "command" in step:
        return step["command"]
    return " ".join([step.get("run", "?")]
                    + [str(a) for a in step.get("args", [])])


def init_adapter(entry: dict, dry_run: bool = False) -> dict:
    """Run the full init contract for one adapter and collect its status."""
    adapter_dir = (VISE_ROOT / entry["path"]).resolve()
    result = {
        "adapter_id": entry["adapter_id"],
        "path": entry["path"],
        "status": "ok",
        "error": None,
    }

    # Load per-adapter manifest (vise.adapter.json)
    manifest_path = adapter_dir / "vise.adapter.json"
    if not manifest_path.is_file():
        result.update(status="failed",
                      error=f"manifest not found: {manifest_path}",
                      failure_type="manifest_error")
        return result
    manifest = load_json(manifest_path)
    result["name"] = manifest.get("name", entry["adapter_id"])
    result["version"] = manifest.get("version")

    # Resolve required COTS — supports both string and object format (P1-2)
    #   string:  "python"
    #   object:  {"tool": "python", "min_version": "3.11"}
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
            deps[tool] = {"path": vr["path"], "version": vr.get("version"),
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

    result["dependencies"] = deps
    if missing:
        result.update(
            status="failed",
            error=(
                f"missing COTS: {', '.join(missing)}. Install the tool(s) or "
                f"set an explicit path in adapter_paths.json"
            ),
            failure_type="dependency_missing",
        )
        return result

    # Resolve PYTHON env var from deps (may be dict or str)
    python_dep = deps.get("python")
    if isinstance(python_dep, dict):
        python_path = python_dep["path"]
    else:
        python_path = python_dep
    env = dict(os.environ)
    env["VISE_ROOT"] = str(VISE_ROOT)
    env["ADAPTER_DIR"] = str(adapter_dir)
    env["PYTHON"] = python_path or sys.executable

    # Dry run: report what WOULD execute, run nothing
    if dry_run:
        result["dry_run"] = True
        result["status"] = "skipped"
        result["planned_steps"] = [
            {"name": s.get("name", "?"), "command": _fmt_step(s),
             "timeout_sec": s.get("timeout_sec", 120)}
            for s in manifest.get("init_steps", [])
        ]
        hc = manifest.get("healthcheck")
        if hc:
            result["planned_healthcheck"] = {
                "command": _fmt_step(hc),
                "timeout_sec": hc.get("timeout_sec", 60),
            }
        result["entry_points"] = manifest.get("entry_points", {})
        return result

    # Run init steps — supports structured {"run", "args"} and shell {"command"} (P1-1)
    result["init_steps"] = []
    for step in manifest.get("init_steps", []):
        r = execute_step(step, adapter_dir, env, 120)
        result["init_steps"].append(r)
        if r["status"] != "ok":
            result.update(status="failed",
                          error=f"init step failed: {r['name']}",
                          failure_type="init_step_failed")
            return result

    # Run adapter health check (supports both formats via execute_step)
    hc = manifest.get("healthcheck")
    if hc:
        result["healthcheck"] = execute_step(hc, adapter_dir, env, 60)
        if result["healthcheck"]["status"] != "ok":
            result.update(status="failed", error="health check failed",
                          failure_type="healthcheck_failed")
            return result

    # Export info for the unified report
    result["entry_points"] = manifest.get("entry_points", {})
    return result


def select_adapters(registry: dict, ids: list[str], all_: bool) -> list[dict]:
    enabled = [a for a in registry.get("adapters", []) if a.get("enabled")]
    if all_:
        return enabled
    by_id = {a["adapter_id"]: a for a in enabled}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        known = ", ".join(by_id) or "(none)"
        msg = (f"viseinit: unknown adapter(s): {', '.join(unknown)}. "
               f"Enabled adapters: {known}")
        print(msg, file=sys.stderr)
        sys.exit(5)  # P1-3: unknown adapter id
    return [by_id[i] for i in ids]


# Exit code mapping (P1-3)
_FAILURE_EXIT_CODES = {
    "manifest_error": 6,
    "dependency_missing": 2,
    "init_step_failed": 3,
    "healthcheck_failed": 4,
}
# Priority order (most fundamental first)
_FAILURE_PRIORITY = ["manifest_error", "dependency_missing",
                      "init_step_failed", "healthcheck_failed"]


def compute_exit_code(results: list[dict]) -> int:
    """Determine exit code from results. Returns 0 if all pass."""
    if all(r["status"] == "ok" or r.get("dry_run") for r in results):
        return 0
    for ft in _FAILURE_PRIORITY:
        if any(r.get("failure_type") == ft for r in results):
            return _FAILURE_EXIT_CODES[ft]
    return 1  # generic fallback


def _load_depends_on(entry: dict) -> list[str]:
    """Read depends_on from the adapter's manifest (if present)."""
    adapter_dir = (VISE_ROOT / entry["path"]).resolve()
    manifest_path = adapter_dir / "vise.adapter.json"
    if not manifest_path.is_file():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return list(manifest.get("depends_on", []))


def dependency_layers(selected: list[dict]) -> tuple[list[list[dict]], list[str]]:
    """Group adapters into dependency layers (topological sort).

    Layer 0: adapters with no dependencies (or whose dependencies are
    not in the selected set — treated as external/satisfied).
    Layer N: adapters whose dependencies are all in layers 0..N-1.

    Returns (layers, cyclic_nodes):
      layers — list of layers (each is a list of entries that can run in parallel)
      cyclic_nodes — adapter_ids involved in a circular dependency (empty if none)
    """
    selected_ids = {e["adapter_id"] for e in selected}
    # Build dependency map: only count deps WITHIN the selected set
    deps_map: dict[str, set[str]] = {}
    for entry in selected:
        aid = entry["adapter_id"]
        all_deps = set(_load_depends_on(entry))
        deps_map[aid] = all_deps & selected_ids  # ignore external deps

    # Kahn's algorithm (layered)
    layers: list[list[dict]] = []
    entry_by_id = {e["adapter_id"]: e for e in selected}
    remaining = dict(deps_map)
    placed: set[str] = set()

    while remaining:
        # Find all nodes whose deps are fully placed
        ready = [aid for aid, deps in remaining.items()
                 if deps <= placed]
        if not ready:
            # Remaining nodes are in cycles — stop here
            cyclic_nodes = sorted(remaining)
            break
        layer = [entry_by_id[aid] for aid in sorted(ready)]
        layers.append(layer)
        for aid in ready:
            placed.add(aid)
            del remaining[aid]
    else:
        cyclic_nodes = []  # all resolved, no cycle

    return layers, cyclic_nodes


def _run_layer(layer: list[dict], jobs: int, dry_run: bool = False,
               emit=None) -> list[dict]:
    """Run a single dependency layer (serial or parallel)."""
    if jobs <= 1 or len(layer) == 1:
        out = []
        for e in layer:
            if emit:
                emit({"event": "adapter_started",
                      "adapter_id": e["adapter_id"]})
            r = init_adapter(e, dry_run=dry_run)
            if emit:
                emit({"event": "adapter_finished",
                      "adapter_id": e["adapter_id"], "status": r["status"]})
            out.append(r)
        return out

    results: list[dict | None] = [None] * len(layer)
    index_map = {id(e): i for i, e in enumerate(layer)}

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        future_to_entry = {}
        for e in layer:
            if emit:
                emit({"event": "adapter_started",
                      "adapter_id": e["adapter_id"]})
            future_to_entry[pool.submit(init_adapter, e, dry_run)] = e
        for fut in as_completed(future_to_entry):
            entry = future_to_entry[fut]
            idx = index_map[id(entry)]
            try:
                results[idx] = fut.result()
            except Exception as exc:
                results[idx] = {
                    "adapter_id": entry["adapter_id"],
                    "path": entry["path"],
                    "status": "failed",
                    "error": f"unhandled exception: {exc}",
                    "failure_type": "init_step_failed",
                }
            if emit:
                emit({"event": "adapter_finished",
                      "adapter_id": entry["adapter_id"],
                      "status": results[idx]["status"]})
    # Safety: fill any None
    for i, r in enumerate(results):
        if r is None:
            results[i] = {
                "adapter_id": layer[i]["adapter_id"],
                "path": layer[i]["path"],
                "status": "failed",
                "error": "result not collected (internal error)",
                "failure_type": "init_step_failed",
            }
    return results  # type: ignore[return-value]


def _make_skipped(entry: dict, reason: str) -> dict:
    return {
        "adapter_id": entry["adapter_id"],
        "path": entry["path"],
        "status": "skipped",
        "error": reason,
        "failure_type": "skipped",
    }


def run_adapters(selected: list[dict], jobs: int,
                 fail_fast: bool, dry_run: bool = False,
                 emit=None) -> list[dict]:
    """Run init_adapter for all selected entries.

    P2-2: supports parallel execution (--jobs N) and fail-fast semantics.
    P2-2+: respects adapter dependencies (depends_on in manifest) via
    topological layering — parallel only within a layer, layers run
    sequentially. Returns results in the SAME ORDER as `selected`.
    """
    layers, cyclic_nodes = dependency_layers(selected)
    if cyclic_nodes:
        print(f"viseinit: circular dependency detected among: "
              f"{', '.join(cyclic_nodes)}", file=sys.stderr)

    results: list[dict] = []
    aborted = False
    cyclic_set = set(cyclic_nodes)

    for layer in layers:
        if aborted:
            results.extend(_make_skipped(e, "skipped due to --fail-fast")
                           for e in layer)
            continue

        layer_results = _run_layer(layer, jobs, dry_run=dry_run, emit=emit)
        results.extend(layer_results)

        if fail_fast and not dry_run and \
                any(r["status"] != "ok" for r in layer_results):
            aborted = True

    # Mark cyclic adapters as skipped
    for entry in selected:
        if entry["adapter_id"] in cyclic_set:
            results.append(_make_skipped(entry, "circular dependency"))

    # Reorder results to match `selected` order
    by_id = {r["adapter_id"]: r for r in results}
    return [by_id[e["adapter_id"]] for e in selected]


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="viseinit",
        description="Initialize Vise adapters (see registry.json).")
    parser.add_argument("adapters", nargs="*",
                        help="adapter ids to initialize")
    parser.add_argument("--all", action="store_true",
                        help="initialize all enabled adapters")
    out_group = parser.add_mutually_exclusive_group()
    out_group.add_argument("--json", action="store_true",
                           help="output machine-readable JSON to stdout "
                                "(no console summary, no report files)")
    out_group.add_argument("--ndjson", action="store_true",
                           help="stream newline-delimited JSON events "
                                "(adapter_started/finished + summary; "
                                "no console summary, no report files)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would execute without running "
                             "init_steps/healthcheck (review before run)")
    # P2-2: parallel execution + failure semantics
    parser.add_argument("--jobs", type=int, default=1,
                        help="number of adapters to initialize in parallel "
                             "(default: 1, serial)")
    fail_group = parser.add_mutually_exclusive_group()
    fail_group.add_argument("--keep-going", action="store_true", default=True,
                            help="continue after failures (default)")
    fail_group.add_argument("--fail-fast", action="store_true",
                            help="abort remaining adapters on first failure")
    args = parser.parse_args()
    if not args.all and not args.adapters:
        parser.error("provide adapter id(s) or --all")

    if not REGISTRY_PATH.is_file():
        # P2-1: registry is a generated artifact, not repo-managed
        print(f"viseinit: {REGISTRY_PATH.relative_to(VISE_ROOT)} not found "
              "(generated artifact). Run: bash init/setup.sh", file=sys.stderr)
        return 1
    registry = load_json(REGISTRY_PATH)
    selected = select_adapters(registry, args.adapters, args.all)

    def emit(event: dict) -> None:
        print(json.dumps(event), flush=True)

    results = run_adapters(selected, args.jobs, args.fail_fast,
                           dry_run=args.dry_run,
                           emit=emit if args.ndjson else None)
    ok = sum(r["status"] == "ok" for r in results)
    exit_code = compute_exit_code(results)

    skipped = sum(r["status"] == "skipped" for r in results)
    failed = sum(r["status"] == "failed" for r in results)

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "vise_root": str(VISE_ROOT),
        "results": results,
        "summary": {"total": len(results), "ok": ok,
                    "failed": failed, "skipped": skipped},
    }

    if args.ndjson:
        # P2-2++: streaming mode — events already emitted; close with summary
        print(json.dumps({"event": "summary", **report["summary"],
                          "exit_code": exit_code}), flush=True)
        return exit_code

    if args.json:
        # P1-3: machine-readable output to stdout only
        print(json.dumps(report, indent=2))
        return exit_code

    # Human-readable console summary
    jobs_info = f" (jobs={args.jobs}" if args.jobs > 1 else ""
    mode = ", fail-fast" if args.fail_fast else ""
    suffix = jobs_info + mode + ")" if (jobs_info or mode) else ""
    print(f"Vise init: {len(selected)} adapter(s) selected{suffix}")
    if args.dry_run:
        print("(dry run — nothing will be executed)")
    print()
    for r in results:
        if r.get("dry_run"):
            mark = "[PLAN]"
        elif r["status"] == "ok":
            mark = "[OK]"
        elif r["status"] == "skipped":
            mark = "[SKIP]"
        else:
            mark = "[FAIL]"
        line = f"{mark} {r['adapter_id']}"
        if r.get("version"):
            line += f" v{r['version']}"
        print(line)
        for ps in r.get("planned_steps", []):
            print(f"       plan: {ps['command']}")
        if r.get("planned_healthcheck"):
            print(f"       plan-healthcheck: "
                  f"{r['planned_healthcheck']['command']}")
        if r["error"]:
            print(f"       -> {r['error']}")

    parts = [f"{ok}/{len(results)} adapter(s) initialized"]
    if skipped:
        parts.append(f"{skipped} skipped")
    print(f"\nSummary: {', '.join(parts)}")

    # JSON report to file
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"viseinit_report_{stamp}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    (REPORTS_DIR / "latest.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report: {report_path}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
