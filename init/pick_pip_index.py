#!/usr/bin/env python3
"""pick_pip_index - choose the fastest reachable Python package index.

setup.sh runs this before `pip install` so pip talks to whichever index
(official PyPI or a public mirror) answers fastest from this machine.

Candidates (override via argv or the space-separated VISE_PIP_INDEXES env var):
  official PyPI plus well-known public mirrors (Tsinghua, USTC,
  Tencent Cloud).

All candidates are probed concurrently with a small request; the index with
the lowest response time wins.

Output:
  stdout - the fastest index URL (nothing if none are reachable)
  stderr - a diagnostic table (each index + latency, or the failure reason)

Exit code: 0 if a working index was found, 1 otherwise.

Standalone usage:
    python3 init/pick_pip_index.py
    python3 init/pick_pip_index.py --timeout=3
"""
from __future__ import annotations

import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_INDEXES = [
    "https://pypi.org/simple/",                        # official PyPI
    "https://pypi.tuna.tsinghua.edu.cn/simple/",       # Tsinghua TUNA
    "https://mirrors.ustc.edu.cn/pypi/simple/",        # USTC
    "https://mirrors.cloud.tencent.com/pypi/simple/",  # Tencent Cloud
]

PROBE_PATH = "pip/"   # a small, always-present package page under /simple/
PROBE_BYTES = 65536   # read at most this much: latency proxy, not a full download
DEFAULT_TIMEOUT = 5.0


def probe(index: str, timeout: float) -> tuple[str, float | None, str]:
    """Measure response time for one index. Returns (index, seconds|None, note)."""
    url = (index if index.endswith("/") else index + "/") + PROBE_PATH
    start = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "vise-setup"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(PROBE_BYTES)
        return index, time.monotonic() - start, "ok"
    except Exception as exc:  # noqa: BLE001 - any failure just skips this mirror
        return index, None, f"{type(exc).__name__}: {exc}"


def main() -> int:
    args = sys.argv[1:]
    positional = [a for a in args if not a.startswith("-")]

    env_indexes = os.environ.get("VISE_PIP_INDEXES", "").split()
    indexes = positional or env_indexes or DEFAULT_INDEXES

    timeout = DEFAULT_TIMEOUT
    for a in args:
        if a.startswith("--timeout="):
            try:
                timeout = float(a.split("=", 1)[1])
            except ValueError:
                pass

    results: list[tuple[str, float | None, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, len(indexes))) as pool:
        futures = [pool.submit(probe, idx, timeout) for idx in indexes]
        for fut in as_completed(futures):
            results.append(fut.result())

    # Reachable first (ascending latency), unreachable last
    results.sort(key=lambda r: (r[1] is None, r[1] if r[1] is not None else 0.0))
    for idx, secs, note in results:
        timing = f"{secs * 1000:7.1f} ms" if secs is not None else "  FAILED"
        print(f"  {timing}  {idx}  ({note})", file=sys.stderr)

    fastest = next((idx for idx, secs, _ in results if secs is not None), None)
    if fastest is None:
        print("pick_pip_index: no reachable package index", file=sys.stderr)
        return 1
    print(fastest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
