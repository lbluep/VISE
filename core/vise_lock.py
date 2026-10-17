#!/usr/bin/env python3
"""vise_lock - single-writer lock for generated state under ``.vise/``.

Two AI agents (or an agent and `setup.sh`) can otherwise interleave writes
to the registry, snapshots, baselines and the audit log: two concurrent
`snapshot` calls pruning generations, a `scaffold` racing a register
rewrite, etc. Every command that *writes* ``.vise/`` state therefore takes
this lock; read-only commands never do.

Mechanism (Linux): ``fcntl.flock`` on ``.vise/.lock``.

* The lock is released by the kernel when the holding process exits or is
  killed - there is no stale-lock cleanup problem.
* Acquisition is non-blocking + polled so a ``VISE_LOCK_TIMEOUT`` (seconds,
  default 10; 0 = fail immediately) bounds how long a caller waits.
* Acquisition is re-entrant *within one process* (a module-level depth
  counter): visectl dispatches guard/baseline/scaffold code in-process, and
  nested helper calls must not deadlock against the outer command.
* Independent processes (the standalone ``core/baseline.py`` entry point,
  ``init/register_adapters.py``, ``init/setup.sh``) open their own file
  descriptions and block correctly.

On platforms without ``fcntl`` the lock degrades to a no-op: Vise targets
Linux terminals; the Windows Git Bash surface keeps working without
cross-process mutual exclusion.

Exit code on timeout is ``75`` (EX_TEMPFAIL convention: a transient
condition the caller may retry), kept outside the viseinit/guard/ledger
code domains.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Linux
    fcntl = None

VISE_ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = VISE_ROOT / ".vise" / ".lock"
LOCK_EXIT_CODE = 75
_POLL_SECONDS = 0.1

# In-process reentrancy state (one CLI invocation = one process).
_state = {"fd": None, "depth": 0, "label": None}


class LockTimeout(Exception):
    """Raised when the write lock cannot be acquired within the timeout."""


def _timeout() -> float:
    try:
        return float(os.environ.get("VISE_LOCK_TIMEOUT", "10"))
    except ValueError:
        return 10.0


def _holder_info() -> dict | None:
    """Best-effort read of the current holder's label (for diagnostics)."""
    try:
        text = LOCK_PATH.read_text(encoding="utf-8").strip()
        return json.loads(text) if text else None
    except (OSError, ValueError):
        return None


@contextlib.contextmanager
def write_lock(label: str, timeout: float | None = None):
    """Serialize a ``.vise/``-writing operation.

    `label` identifies the operation (e.g. "snapshot fast_search") and is
    written into the lock file so a waiting process can report who holds
    the lock. `timeout` overrides VISE_LOCK_TIMEOUT (0 = one non-blocking
    attempt). Raises LockTimeout on timeout.
    """
    if fcntl is None:
        # Documented best-effort fallback, not a silent claim of safety.
        if os.environ.get("VISE_DEBUG"):
            print("vise_lock: fcntl unavailable; running without cross-"
                  "process locking", file=sys.stderr)
        yield
        return

    if _state["depth"] > 0:
        # Same process already holds it: just extend the depth.
        _state["depth"] += 1
        try:
            yield
        finally:
            _state["depth"] -= 1
        return

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_PATH), os.O_RDWR | os.O_CREAT, 0o600)
    wait = _timeout() if timeout is None else timeout
    deadline = time.monotonic() + wait
    acquired = False
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(_POLL_SECONDS)
        if not acquired:
            holder = _holder_info()
            who = (f" (held by pid {holder['pid']}: {holder.get('label')})"
                   if holder and holder.get("pid") else "")
            raise LockTimeout(
                f"timed out waiting for the Vise write lock{who}; "
                "another process is changing .vise state - retry shortly "
                f"(or set VISE_LOCK_TIMEOUT)")
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps(
            {"pid": os.getpid(), "label": label,
             "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}).encode("utf-8"))
        _state.update(fd=fd, depth=1, label=label)
        yield
    finally:
        if acquired:
            _state.update(fd=None, depth=0, label=None)
            try:
                os.ftruncate(fd, 0)
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        else:
            os.close(fd)
