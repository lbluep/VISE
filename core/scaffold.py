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

Exit codes: 0 scaffolded, registered and release-refreshed; 2 usage error
(bad name, existing directory, duplicate adapter_id, unknown category);
1 internal failure (files exist, re-run `core/release.py generate` to
repair); 75 write-lock timeout (another process is changing .vise state).
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

VISE_ROOT = Path(__file__).resolve().parent.parent
ADAPTERS_DIR = VISE_ROOT / "adapters"
TEMPLATE_DIR = ADAPTERS_DIR / "ExampleAdapter_reference"

# Same set as init/register_adapters.py (B2) — imported when available,
# duplicated as a literal fallback so scaffold also works standalone.
sys.path.insert(0, str(VISE_ROOT / "init"))
try:
    from register_adapters import CATEGORIES as _CATEGORIES  # noqa: E402
    from register_adapters import register as _register_now  # noqa: E402
except Exception:  # pragma: no cover
    _CATEGORIES = {
        "tools", "work_instruction", "database", "ai_chat",
        "plan", "todo", "onedrive", "external",
    }
    _register_now = None
sys.path.insert(0, str(VISE_ROOT / "core"))
import vise_lock  # noqa: E402
import release as vise_release  # noqa: E402

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


def cmd_scaffold(args) -> int:
    # The existence/duplicate checks and the whole copy -> register ->
    # release-refresh sequence are one serialized state change: a concurrent
    # scaffold/register/upgrade must not see the new directory half-written
    # or an unrefreshed release manifest.
    try:
        with vise_lock.write_lock(f"scaffold {args.name}"):
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
            ok, output = finalize(result)
    except vise_lock.LockTimeout as exc:
        print(f"visectl: {exc}", file=sys.stderr)
        return vise_lock.LOCK_EXIT_CODE

    result["registered"] = ok
    if args.json:
        print(json.dumps(result, indent=2))
        if output:
            print(output, file=sys.stderr)
    else:
        print(f"scaffold: created {result['path']} "
              f"(adapter_id={result['adapter_id']}, "
              f"category={result['category']})")
        if output:
            print(output)
        if ok:
            print("scaffold: release manifest refreshed automatically; "
                  "next — edit commands + src/<id>/main.py, then run "
                  "`viseinit <id>` and one real `visectl run`.")
    return 0 if ok else 1


def finalize(result: dict) -> tuple[bool, str]:
    """In-process register + release refresh. Returns (ok, human output).

    Public so skill_bridge can reuse it for imported skills under its own
    write lock. A failure here is exit 1: the adapter files exist, but the
    generated state is inconsistent and the operator (or CI `release check`)
    must see that immediately.
    """
    import io
    import contextlib

    if _register_now is None:  # pragma: no cover
        return False, ("scaffold: init/register_adapters.py unavailable; "
                       "registry and release manifest not refreshed")

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc, _warnings = _register_now(announce=False)
        if rc != 0:
            return False, "scaffold: registration failed\n" + buf.getvalue()
        out = vise_release.generate(VISE_ROOT)
    except (OSError, ValueError) as exc:
        return False, (f"scaffold: post-scaffold refresh failed: {exc}\n"
                       "fix with: .venv/bin/python core/release.py generate\n"
                       + buf.getvalue())
    buf.write(f"scaffold: registry refreshed; release manifest now covers "
              f"{out['adapters']} adapter(s), {out['files']} files\n")
    return True, buf.getvalue()


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
