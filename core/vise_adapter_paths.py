"""Vise shared config layer: resolve COTS (commercial off-the-shelf) tool paths.

Resolution order for each tool:
1. adapter_paths.json    -> explicit override (nested schema, developer-maintained)
2. tool_paths.json  -> detected by init/setup at setup time (flat schema)
3. PATH fallback via shutil.which
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

VISE_ROOT = Path(__file__).resolve().parent.parent

_ALIASES = {
    "python": ["python3", "python"],
    "git": ["git"],
    "7zip": ["7z", "7zz"],
    "vscode": ["code"],
    "ripgrep": ["rg", "ripgrep"],
}


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def resolve(tool: str) -> str | None:
    """Return the executable path for a COTS tool, or None if not found."""
    # 1. adapter_paths.json — explicit override (nested schema)
    override = _load_json(VISE_ROOT / "core" / "adapter_paths.json")
    entry = override.get("cots", {}).get(tool)
    if isinstance(entry, dict):
        p = entry.get("path")
    elif isinstance(entry, str):
        p = entry
    else:
        p = None
    if p and Path(p).exists():
        return p

    # 2. tool_paths.json — detected (flat schema)
    detected = _load_json(VISE_ROOT / ".vise" / "tool_paths.json")
    p = detected.get(tool)
    if isinstance(p, str) and Path(p).exists():
        return p

    # 3. PATH fallback
    for name in _ALIASES.get(tool, [tool]):
        found = shutil.which(name)
        if found:
            return found
    return None


def resolve_all(tools: list[str]) -> dict[str, str | None]:
    return {t: resolve(t) for t in tools}


# --- Version checking (P1-2) ---

_VERSION_CMDS = {
    "python": ["--version"],
    "git": ["--version"],
    "vscode": ["--version"],
    "ripgrep": ["--version"],
    "7zip": [],
}


def _extract_version(output: str) -> str | None:
    """Extract version string from tool --version output."""
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", output)
    return m.group(1) if m else None


def _parse_version(s: str) -> tuple[int, ...]:
    """Parse '3.13.1' -> (3, 13, 1). Stops at first non-numeric part."""
    parts = []
    for part in s.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            break
    return tuple(parts)


def _version_probe(tool: str, path: str) -> str | None:
    """Run the tool's version command and extract the version string.

    Most tools print it on `--version`; `7z` prints a banner on bare
    invocation (exit code 0); `code` needs no version today.
    """
    args = _VERSION_CMDS.get(tool)
    if args is None:
        return None
    try:
        proc = subprocess.run(
            [path] + args,
            capture_output=True, text=True, timeout=10,
        )
        return _extract_version(proc.stdout + proc.stderr)
    except (OSError, subprocess.SubprocessError):
        return None


def resolve_with_version(tool: str, min_version: str | None = None) -> dict:
    """Resolve a tool path and optionally check its version.

    Returns {"path": str|None, "version": str|None, "ok": bool, ...}.
    If min_version is None, only checks path existence.
    """
    path = resolve(tool)
    if path is None:
        return {"path": None, "version": None, "ok": False}

    if min_version is None:
        return {"path": path, "version": None, "ok": True}

    version_str = _version_probe(tool, path)
    if version_str is None and _VERSION_CMDS.get(tool) is None:
        # No version checking available for this tool, pass
        return {"path": path, "version": None, "ok": True}

    if version_str is None:
        return {"path": path, "version": None, "ok": False,
                "error": f"cannot determine version of {tool}"}

    ok = _parse_version(version_str) >= _parse_version(min_version)
    return {"path": path, "version": version_str, "ok": ok,
            "required": min_version}


if __name__ == "__main__":
    tools = sys.argv[1:] or ["python", "git", "7zip", "vscode"]
    print(json.dumps(resolve_all(tools), indent=2))
