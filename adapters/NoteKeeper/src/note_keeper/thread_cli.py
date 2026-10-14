"""thread_cli - bootstrap logic for NoteKeeper."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(prog="thread_cli")
    parser.add_argument("--adapter-dir", required=True)
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args()

    adapter_dir = Path(args.adapter_dir)
    state_dir = Path(args.state_dir)

    manifest = json.loads((adapter_dir / "vise.adapter.json").read_text(encoding="utf-8"))
    for key in ("adapter_id", "version", "entry_points"):
        if key not in manifest:
            print(f"thread_cli: manifest missing key: {key}", file=sys.stderr)
            return 1

    if sys.version_info < (3, 11):
        print("thread_cli: Python >= 3.11 required", file=sys.stderr)
        return 1

    for name, rel in manifest["entry_points"].items():
        if name != "extensions" and not (adapter_dir / rel).exists():
            print(f"thread_cli: entry_point missing: {rel}", file=sys.stderr)
            return 1

    state_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "adapter_id": manifest["adapter_id"],
        "version": manifest["version"],
        "adapter_dir": str(adapter_dir),
        "initialized_at": datetime.now(timezone.utc).isoformat(),
        "status": "initialized",
    }
    state_file = state_dir / "state.json"
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"[thread_cli] {manifest['adapter_id']} state written to {state_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
