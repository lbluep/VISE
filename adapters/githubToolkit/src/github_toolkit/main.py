#!/usr/bin/env python3
"""github_toolkit CLI shim.

The real implementation is the self-contained bash script `bin/congit`
(needs bash + curl, no Python). This shim lets `visectl run github_toolkit`
drive it the same way; arguments are forwarded verbatim.

Usage:
  visectl run github_toolkit                 # same as: congit (fix hosts)
  visectl run github_toolkit --dry-run       # preview changes
  visectl run github_toolkit --restore       # remove managed block
  visectl run github_toolkit --self-check    # instant sanity check
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ADAPTER_DIR = Path(__file__).resolve().parents[2]
CONGIT = ADAPTER_DIR / "bin" / "congit"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not CONGIT.is_file():
        print(f"github_toolkit: congit script not found: {CONGIT}",
              file=sys.stderr)
        return 1
    proc = subprocess.run(["bash", str(CONGIT), *args])
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
