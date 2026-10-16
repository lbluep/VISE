#!/usr/bin/env python3
"""scaffold - turn the reference template into a new adapter, in one command.

    python core/scaffold.py <AdapterName> [--id <adapter_id>]
                            [--category tools] [--description "..."] [--json]

or via visectl:  visectl adapter scaffold <AdapterName> ...

Copies adapters/ExampleAdapter_reference/ (the full contract: manifest +
thread + src/ layout) to adapters/<AdapterName>/, renames the adapter_id /
package / name references, validates the result against the same contract
rules as init/register_adapters.py, and re-registers the library.

What it does NOT do: invent your commands. The scaffolded manifest keeps the
example command skeleton (list / decode / read) — edit `commands`, the CLI
(src/<id>/main.py) and the healthcheck before running `bash init/setup.sh`.

Exit codes: 0 scaffolded and registered; 2 usage error (bad name, existing
directory, duplicate adapter_id, unknown category); 1 internal failure.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

VISE_ROOT = Path(__file__).resolve().parent.parent
ADAPTERS_DIR = VISE_ROOT / "adapters"
TEMPLATE_DIR = ADAPTERS_DIR / "ExampleAdapter_reference"

# Same set as init/register_adapters.py (B2) — imported when available,
# duplicated as a literal fallback so scaffold also works standalone.
try:
    sys.path.insert(0, str(VISE_ROOT / "init"))
    from register_adapters import CATEGORIES as _CATEGORIES  # noqa: E402
except Exception:  # pragma: no cover
    _CATEGORIES = {
        "tools", "work_instruction", "database", "ai_chat",
        "plan", "todo", "onedrive", "external",
    }

# Ordered longest-first so "ExampleAdapter_reference" is replaced before its
# substrings "ExampleAdapter" / "example_tool" could match.
_RENAMES = [
    ("ExampleAdapter_reference", None),   # directory name -> <Name>
    ("example_tool", None),               # adapter_id / package -> <id>
    ("ExampleAdapter", None),             # display name -> <Name>
]

# "" covers the extensionless `thread` bootstrap script.
_TEXT_SUFFIXES = {".py", ".json", ".sh", ".md", ""}
_SKIP_DIRS = {"__pycache__"}


def _to_snake(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _existing_ids() -> set[str]:
    ids = set()
    for mf in ADAPTERS_DIR.glob("*/vise.adapter.json"):
        try:
            ids.add(json.loads(mf.read_text(encoding="utf-8"))
                    .get("adapter_id", ""))
        except (OSError, json.JSONDecodeError):
            continue
    return ids - {""}


def scaffold(name: str, *, adapter_id: str | None = None,
             category: str = "tools", description: str | None = None,
             ) -> dict:
    """Copy the reference template and rewrite its identity. Raises ValueError
    on usage errors (message prefixed 'usage:')."""
    if not re.fullmatch(r"[A-Z][A-Za-z0-9]+", name):
        raise ValueError(
            f"usage: adapter name must be PascalCase (got {name!r}); "
            "e.g. FastSearch")
    aid = adapter_id or _to_snake(name)
    if not re.fullmatch(r"[a-z][a-z0-9_]*", aid):
        raise ValueError(
            f"usage: adapter_id must be snake_case (got {aid!r})")
    if category not in _CATEGORIES:
        raise ValueError(
            "usage: unknown category "
            f"'{category}' (expected one of: {', '.join(sorted(_CATEGORIES))})")
    dest = ADAPTERS_DIR / name
    if dest.exists():
        raise ValueError(f"usage: {dest.relative_to(VISE_ROOT)} already exists")
    if aid in _existing_ids():
        raise ValueError(f"usage: adapter_id '{aid}' is already registered")
    if not TEMPLATE_DIR.is_dir():
        raise RuntimeError("reference template missing: "
                           f"{TEMPLATE_DIR.relative_to(VISE_ROOT)}")

    renames = [(old, new) for old, new in (
        ("ExampleAdapter_reference", name),
        ("example_tool", aid),
        ("ExampleAdapter", name),
    )]
    shutil.copytree(TEMPLATE_DIR, dest,
                    ignore=shutil.ignore_patterns(*_SKIP_DIRS))

    # Rewrite identity in text files, then rename the package directory.
    for p in sorted(dest.rglob("*")):
        if not p.is_file() or p.suffix not in _TEXT_SUFFIXES:
            continue
        text = p.read_text(encoding="utf-8")
        for old, new in renames:
            text = text.replace(old, new)
        p.write_text(text, encoding="utf-8")
    src_pkg = dest / "src" / "example_tool"
    if src_pkg.is_dir():
        src_pkg.rename(dest / "src" / aid)

    # Manifest identity fields (version starts at 0.1.0: it is a scaffold).
    mf_path = dest / "vise.adapter.json"
    manifest = json.loads(mf_path.read_text(encoding="utf-8"))
    manifest["adapter_id"] = aid
    manifest["name"] = name
    manifest["version"] = "0.1.0"
    if description:
        manifest["description"] = description
    manifest.setdefault("provides", {})["category"] = category
    manifest["provides"]["tags"] = [aid, category]
    mf_path.write_text(json.dumps(manifest, indent=2) + "\n",
                       encoding="utf-8")
    return {"name": name, "adapter_id": aid, "path": str(
        dest.relative_to(VISE_ROOT)), "category": category}


def _register() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(VISE_ROOT / "init" / "register_adapters.py")],
        capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def cmd_scaffold(args) -> int:
    try:
        result = scaffold(args.name, adapter_id=args.id,
                          category=args.category,
                          description=args.description)
    except ValueError as exc:
        print(f"visectl: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"visectl: {exc}", file=sys.stderr)
        return 1

    rc, output = _register()
    result["registered"] = rc == 0
    if args.json:
        print(json.dumps(result, indent=2))
        if output:
            print(output, file=sys.stderr)
    else:
        print(f"scaffold: created {result['path']} "
              f"(adapter_id={result['adapter_id']}, "
              f"category={result['category']})")
        print(output)
        if rc == 0:
            print("scaffold: next — edit commands + src/<id>/main.py, then "
                  "run `viseinit <id>` and one real `visectl run`.")
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        prog="scaffold",
        description="Scaffold a new adapter from ExampleAdapter_reference.")
    parser.add_argument("name", help="adapter directory name (PascalCase)")
    parser.add_argument("--id", default=None,
                        help="adapter_id (default: snake_case of NAME)")
    parser.add_argument("--category", default="tools")
    parser.add_argument("--description", default=None)
    parser.add_argument("--json", action="store_true")
    sys.exit(cmd_scaffold(parser.parse_args()))
