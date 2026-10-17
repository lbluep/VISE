#!/usr/bin/env python3
"""skill_bridge - two-way bridge between Vise adapters and SKILL.md packages.

Goal: let VISE act as a multi-host *local* skill registry. The skill
ecosystem (and its private registries) handles distribution; VISE supplies
what skills lack: health checks, the adapter guard (snapshot/restore), and
baseline reconciliation.

skill export <adapter_id> --out DIR [--no-scripts]
    Render an adapter as a standard SKILL.md package. The package carries
    the capability surface and (by default) the adapter's Python scripts,
    but NOT the Vise governance (baseline/guard/healthcheck) - that is
    available only when the package is re-imported back into VISE.

skill import <dir> [--category C] [--id <id>]
    Turn a SKILL.md package into a *managed* Vise adapter: write the
    directory, register it, refresh the release manifest, and establish a
    baseline (so it enters the guard-protected state immediately). Two
    shapes:
      - knowledge (no scripts/): a list+read knowledge adapter whose
        `read skill` topic serves the SKILL.md body.
      - scripted: each scripts/*.py becomes a command; a dispatch main.py
        runs the matching script with the remaining argv. This is a
        *starting point* - command semantics and python deps still need
        human refinement.

Exit codes: 0 ok; 2 usage/input (missing SKILL.md, bad name, id clash);
1 registration or release-refresh failure; 75 write-lock contention.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

VISE_ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = {
    "tools", "work_instruction", "database", "ai_chat",
    "plan", "todo", "onedrive", "external",
}
NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
DESCRIPTION_CAP = 1024

sys.path.insert(0, str(VISE_ROOT / "core"))
import vise_lock  # noqa: E402
import release as vise_release  # noqa: E402
sys.path.insert(0, str(VISE_ROOT / "init"))
from register_adapters import register as _register_now  # noqa: E402
from baseline import registry_adapters  # noqa: E402


# ----------------------------- skill export --------------------------------

def _adapter_dir(adapter_id: str) -> Path | None:
    for a in registry_adapters():
        if a["adapter_id"] == adapter_id and a.get("enabled"):
            return (VISE_ROOT / a["path"]).resolve()
    return None


def _snake(name: str) -> str:
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


def _read_manifest(adapter_dir: Path) -> dict:
    return json.loads((adapter_dir / "vise.adapter.json").read_text(
        encoding="utf-8"))


def _cap(text: str, n: int = DESCRIPTION_CAP) -> str:
    return text if len(text) <= n else text[: n - 3] + "..."


def skill_export(adapter_id: str, out_dir: Path,
                 include_scripts: bool = True) -> dict:
    adapter_dir = _adapter_dir(adapter_id)
    if adapter_dir is None:
        raise ValueError(f"unknown or disabled adapter: {adapter_id}")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError(f"--out exists and is non-empty: {out_dir}")
    m = _read_manifest(adapter_dir)
    provides = m.get("provides") or {}

    lines = ["---", f"name: {adapter_id}",
             f"description: {_cap(str(m.get('description', '')))}",
             f"category: {provides.get('category', 'tools')}",
             f"managed-by: vise", "---", ""]
    lines.append(f"# {m.get('name', adapter_id)}\n")
    desc = m.get("description", "")
    if desc:
        lines.append(str(desc) + "\n")
    cmds = m.get("commands") or []
    lines.append("## Capabilities\n")
    lines.append("| command | description |")
    lines.append("| --- | --- |")
    for c in cmds:
        lines.append(f"| {c.get('name','')} | {c.get('description','')} |")
    lines.append("")
    lines.append("## Notes")
    lines.append("- Distributed without Vise governance (baseline/guard/"
                 "healthcheck). Re-import into VISE to get those.")
    lines.append("- Exit codes from the host CLI apply; in VISE, guard "
                 "refusals use 7 and lock contention uses 75.")
    lines.append("")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")

    scripts_copied = 0
    if include_scripts:
        src = adapter_dir / "src" / adapter_id
        if src.is_dir():
            scripts_dir = out_dir / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            for f in src.rglob("*.py"):
                if f.name == "__init__.py":
                    continue
                rel = f.relative_to(src)
                dst = scripts_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
                scripts_copied += 1
    return {"adapter_id": adapter_id, "out": str(out_dir),
            "commands": len(cmds), "scripts": scripts_copied}


# ----------------------------- skill import --------------------------------

def _parse_frontmatter(skill_md: Path) -> tuple[dict, str]:
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError("SKILL.md has no YAML frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError("SKILL.md frontmatter is not closed")
    fm_block = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    fm: dict = {}
    for line in fm_block.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, body


def _validate_dir_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise ValueError(
            f"adapter directory name must be PascalCase: {name!r}")


def _existing_dir(dir_name: str) -> bool:
    return (VISE_ROOT / "adapters" / dir_name).exists()


def _existing_id(adapter_id: str) -> bool:
    return any(a["adapter_id"] == adapter_id for a in registry_adapters())


def _pascal_to_snake(name: str) -> str:
    return _snake(name) or name.lower()


def _write_tree(base: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _manifest_for(name: str, adapter_id: str, category: str,
                  description: str, commands: list[dict]) -> dict:
    return {
        "adapter_id": adapter_id,
        "name": name,
        "version": "0.1.0",
        "description": description,
        "enabled": True,
        "entry_points": {
            "cli": f"src/{adapter_id}/main.py",
            "thread_cli": f"src/{adapter_id}/thread_cli.py",
        },
        "required_cots": [{"tool": "python", "min_version": "3.10"}],
        "commands": commands,
        "provides": {"category": category, "tags": ["imported-skill"]},
    }


def _thread_for(adapter_id: str) -> str:
    return f"""#!/usr/bin/env python3
import os, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.argv[0] = str(HERE / "src" / "{adapter_id}" / "main.py")
os.execvp(sys.executable, [sys.executable] + sys.argv)
"""


def _thread_cli_for(adapter_id: str) -> str:
    return f'''#!/usr/bin/env python3
"""Thread entry: delegates to the adapter CLI."""
import os, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.argv[0] = str(HERE / "main.py")
os.execvp(sys.executable, [sys.executable] + sys.argv)
'''


def _vise_extensions_for(adapter_id: str) -> str:
    return "# No shell extensions needed for this adapter.\n"


def _knowledge_files(name: str, adapter_id: str, category: str,
                     description: str, body: str) -> dict[str, str]:
    manifest = _manifest_for(name, adapter_id, category, description, [
        {"name": "list", "description": "List available read topics.",
         "args": {}},
        {"name": "read", "description": "Read a knowledge topic.",
         "args": {"topic": {"required": True,
                            "description": "Topic: info | skill"}}},
    ])
    main = f'''#!/usr/bin/env python3
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ADAPTER = HERE.parent.parent
SKILL_BODY = {json.dumps(body)}

def cmd_list():
    print(json.dumps([{{"topic": "info",
                        "description": "Adapter identity and capabilities"}},
                       {{"topic": "skill",
                        "description": "Imported SKILL.md knowledge"}}],
                     indent=2))

def cmd_read(topic):
    if topic == "info":
        m = json.loads((ADAPTER / "vise.adapter.json").read_text())
        print(m.get("name", "{adapter_id}"))
        print(m.get("description", ""))
    elif topic == "skill":
        print(SKILL_BODY, end="")
    else:
        print(f"unknown topic: {{topic}}", file=sys.stderr)
        return 1
    return 0

def main(argv):
    if not argv or argv[0] == "--self-test":
        return 0
    cmd = argv[0]
    if cmd == "list":
        return cmd_list()
    if cmd == "read":
        if len(argv) < 2:
            print("usage: read <topic>", file=sys.stderr)
            return 2
        return cmd_read(argv[1])
    if cmd == "--help":
        print("commands: list | read <info|skill>")
        return 0
    print(f"unknown command: {{cmd}}", file=sys.stderr)
    return 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''
    readme = f"# {name}\n\n{description}\n\nImported from a SKILL.md package.\n"
    return {
        "vise.adapter.json": json.dumps(manifest, indent=2) + "\n",
        "README.md": readme,
        "thread": _thread_for(adapter_id),
        "vise_extensions.sh": _vise_extensions_for(adapter_id),
        f"src/{adapter_id}/__init__.py": "",
        f"src/{adapter_id}/main.py": main,
        f"src/{adapter_id}/thread_cli.py": _thread_cli_for(adapter_id),
    }


def _scripted_files(name: str, adapter_id: str, category: str,
                    description: str, scripts: list[Path]) -> dict[str, str]:
    cmd_names = [s.stem for s in scripts]
    commands = [
        {"name": cn,
         "description": f"Script-backed command (passthrough args).",
         "args": {"passthrough": {"required": False,
                                  "description": "arguments forwarded to the script"}}}
        for cn in cmd_names
    ]
    manifest = _manifest_for(name, adapter_id, category, description, commands)
    main = f'''#!/usr/bin/env python3
import runpy, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
SCRIPTS = HERE / "scripts"
COMMANDS = {cmd_names!r}
sys.path.insert(0, str(SCRIPTS))

def main(argv):
    if not argv or argv[0] == "--self-test":
        return 0
    cmd = argv[0]
    if cmd == "--help":
        print("commands: " + " | ".join(COMMANDS))
        return 0
    if cmd not in COMMANDS:
        print(f"unknown command: {{cmd}}", file=sys.stderr)
        return 2
    script = SCRIPTS / f"{{cmd}}.py"
    if not script.is_file():
        print(f"script missing: {{script}}", file=sys.stderr)
        return 1
    sys.argv = [str(script)] + argv[1:]
    runpy.run_path(str(script), run_name="__main__")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''
    readme = (f"# {name}\n\n{description}\n\nScript-backed adapter imported "
              f"from a SKILL.md package. Commands forward to scripts/.\n")
    files = {
        "vise.adapter.json": json.dumps(manifest, indent=2) + "\n",
        "README.md": readme,
        "thread": _thread_for(adapter_id),
        "vise_extensions.sh": _vise_extensions_for(adapter_id),
        f"src/{adapter_id}/__init__.py": "",
        f"src/{adapter_id}/main.py": main,
        f"src/{adapter_id}/thread_cli.py": _thread_cli_for(adapter_id),
    }
    for s in scripts:
        rel = f"src/{adapter_id}/scripts/{s.name}"
        files[rel] = s.read_text(encoding="utf-8")
    return files


def skill_import(skill_dir: Path, *, name: str | None = None,
                 adapter_id: str | None = None,
                 category: str = "tools") -> dict:
    skill_dir = skill_dir.resolve()
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise ValueError(f"no SKILL.md in {skill_dir}")
    fm, body = _parse_frontmatter(skill_md)
    skill_name = (fm.get("name") or "").strip()
    if not skill_name:
        raise ValueError("SKILL.md frontmatter is missing 'name'")
    if category not in CATEGORIES:
        raise ValueError(f"unknown category: {category}")

    dir_name = name or (skill_name if NAME_RE.match(skill_name)
                        else skill_name.title().replace(" ", ""))
    _validate_dir_name(dir_name)
    aid = adapter_id or _pascal_to_snake(dir_name)
    if _existing_dir(dir_name):
        raise ValueError(f"adapter directory already exists: adapters/{dir_name}")
    if _existing_id(aid):
        raise ValueError(f"adapter_id already registered: {aid}")

    description = fm.get("description") or skill_name
    scripts_dir = skill_dir / "scripts"
    scripts = sorted([p for p in scripts_dir.glob("*.py")
                      if p.is_file()]) if scripts_dir.is_dir() else []

    if scripts:
        files = _scripted_files(dir_name, aid, category, description, scripts)
    else:
        files = _knowledge_files(dir_name, aid, category, description, body)

    base = VISE_ROOT / "adapters" / dir_name
    _write_tree(base, files)
    return {"name": dir_name, "adapter_id": aid, "path": f"adapters/{dir_name}",
            "category": category, "kind": "scripted" if scripts else "knowledge"}


# ------------------------------- CLI ---------------------------------------

def _finalize_import(result: dict) -> tuple[bool, str]:
    import io, contextlib
    if _register_now is None:
        return False, "skill_bridge: register helper unavailable"
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc, _ = _register_now(announce=False)
        if rc != 0:
            return False, "skill import: registration failed\n" + buf.getvalue()
        import baseline
        # The on-disk tree is authoritative for an import (even when
        # re-importing an adapter that was previously deleted and whose stale
        # baseline may still be present), so force a refresh.
        baseline.establish(result["adapter_id"], provider="install",
                           refresh=True)
        out = vise_release.generate(VISE_ROOT)
    except (OSError, ValueError) as exc:
        return False, (f"skill import: post-import refresh failed: {exc}\n"
                       + buf.getvalue())
    buf.write(f"skill import: registered, baselined, release covers "
              f"{out['adapters']} adapters\n")
    return True, buf.getvalue()


def cmd_export(args) -> int:
    out = Path(args.out).resolve()
    try:
        info = skill_export(args.adapter_id, out,
                            include_scripts=not args.no_scripts)
    except ValueError as exc:
        print(f"skill: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        print(f"skill: exported {info['adapter_id']} -> {info['out']} "
              f"({info['commands']} command(s), {info['scripts']} script(s))")
    return 0


def cmd_import(args) -> int:
    skill_dir = Path(args.dir)
    try:
        result = skill_import(skill_dir, name=args.name,
                              adapter_id=args.id, category=args.category)
    except ValueError as exc:
        print(f"skill: {exc}", file=sys.stderr)
        return 2
    try:
        with vise_lock.write_lock(f"skill import {result['name']}"):
            ok, output = _finalize_import(result)
    except vise_lock.LockTimeout as exc:
        print(f"skill: {exc}", file=sys.stderr)
        return vise_lock.LOCK_EXIT_CODE
    result["registered"] = ok
    if args.json:
        print(json.dumps(result, indent=2))
        if output:
            print(output, file=sys.stderr)
    else:
        print(f"skill: imported -> adapters/{result['name']} "
              f"(adapter_id={result['adapter_id']}, kind={result['kind']})")
        if output:
            print(output)
        if not ok:
            print("skill import: files exist but registration/refresh "
                  "failed; re-run `core/release.py generate` if needed")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="skill_bridge")
    sub = parser.add_subparsers(dest="command", required=True)
    pe = sub.add_parser("export")
    pe.add_argument("adapter_id")
    pe.add_argument("--out", required=True)
    pe.add_argument("--no-scripts", action="store_true")
    pe.add_argument("--json", action="store_true")
    pi = sub.add_parser("import")
    pi.add_argument("dir")
    pi.add_argument("--id", default=None)
    pi.add_argument("--name", default=None)
    pi.add_argument("--category", default="tools")
    pi.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "export":
        return cmd_export(args)
    return cmd_import(args)


if __name__ == "__main__":
    sys.exit(main())
