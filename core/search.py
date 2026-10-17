#!/usr/bin/env python3
"""search - full-text discovery across the adapter library.

Mirrors the role of an LLM platform's "tool search" capability: with a
large library, an agent should not need to pull every manifest into its
context to find the right capability. This command scans enabled adapters
at call time (no persistent index - the library is small and a live scan
avoids another state file that needs locking/invalidation) and ranks hits
across three surfaces:

  * adapter   - adapter_id, name, description, category, tags
  * command   - command names, descriptions and argument names
  * doc       - the adapter's README.md (knowledge text)

It never indexes adapter *source code* - that is the job of `fast_search`.
Design keeps the two tools' responsibilities non-overlapping.

Scoring (case-insensitive): exact substring > token hit; a command hit
outranks a README hit; ties are broken adapter-first.

Exit codes: 0 one or more matches; 1 no matches (informational, lets an
agent tell "nothing found" from "found but wrong word"); 2 usage error.
The command is read-only: no lock, no audit record.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

VISE_ROOT = Path(__file__).resolve().parent.parent
README_LIMIT = 64 * 1024
DEFAULT_LIMIT = 20

# Match kinds, ordered by score weight (lowest first).
KIND_WEIGHT = {"doc": 1, "command": 2, "adapter": 3}


def _registry() -> list[dict]:
    try:
        from baseline import registry_adapters
    except Exception:
        return []
    try:
        return [a for a in registry_adapters() if a.get("enabled")]
    except Exception:
        return []


def _manifest(rel_path: str) -> dict | None:
    mp = VISE_ROOT / rel_path / "vise.adapter.json"
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _readme_text(rel_path: str) -> str:
    rp = VISE_ROOT / rel_path / "README.md"
    try:
        return rp.read_text(encoding="utf-8", errors="replace")[:README_LIMIT]
    except OSError:
        return ""


def _haystacks_manifest(m: dict) -> tuple[str, list[str], list[str]]:
    """Return (text, command_texts, command_names) from a manifest."""
    provides = m.get("provides") or {}
    adapter_text = " ".join([
        str(m.get("adapter_id", "")),
        str(m.get("name", "")),
        str(m.get("description", "")),
        str(provides.get("category", "")),
        " ".join(str(t) for t in provides.get("tags") or []),
    ])
    cmd_texts, cmd_names = [], []
    for c in m.get("commands") or []:
        name = str(c.get("name", ""))
        desc = str(c.get("description", ""))
        args = c.get("args") or {}
        if isinstance(args, dict):
            arg_text = " ".join(
                f"{k} {v.get('description', '') if isinstance(v, dict) else v}"
                for k, v in args.items())
        elif isinstance(args, list):
            arg_text = " ".join(
                f"{a.get('name', '')} {a.get('description', '')}"
                if isinstance(a, dict) else str(a) for a in args)
        else:
            arg_text = str(args)
        cmd_names.append(name)
        cmd_texts.append(f"{name} {desc} {arg_text}")
    return adapter_text, cmd_texts, cmd_names


def _score(haystack: str, query: str) -> int:
    h = haystack.lower()
    q = query.lower()
    if not q:
        return 0
    score = 0
    if q in h:
        score += 100
    for tok in re.findall(r"[a-z0-9]+", q):
        if len(tok) < 2:
            continue
        if tok in h:
            score += max(1, 10 - len(tok))
    return score


def search(query: str, *, limit: int = DEFAULT_LIMIT,
           kind: str | None = None) -> list[dict]:
    rows: list[dict] = []
    for a in _registry():
        m = _manifest(a["path"])
        if not m:
            continue
        adapter_text, cmd_texts, cmd_names = _haystacks_manifest(m)
        aid = str(m.get("adapter_id", a["adapter_id"]))

        if kind in (None, "adapter"):
            s = _score(adapter_text, query)
            if s:
                rows.append({"adapter_id": aid, "kind": "adapter",
                             "score": s * KIND_WEIGHT["adapter"],
                             "snippet": _snippet(adapter_text, query)})
        if kind in (None, "command"):
            for name, text in zip(cmd_names, cmd_texts):
                s = _score(text, query)
                if s:
                    rows.append({"adapter_id": aid, "kind": "command",
                                 "command": name,
                                 "score": s * KIND_WEIGHT["command"],
                                 "snippet": _snippet(text, query)})
        if kind in (None, "doc"):
            text = _readme_text(a["path"])
            s = _score(text, query)
            if s:
                rows.append({"adapter_id": aid, "kind": "doc",
                             "score": s * KIND_WEIGHT["doc"],
                             "snippet": _snippet(text, query)})

    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:limit]


def _snippet(text: str, query: str, width: int = 120) -> str:
    q = query.lower()
    t = text.lower()
    i = t.find(q)
    if i < 0:
        for tok in re.findall(r"[a-z0-9]+", q):
            i = t.find(tok)
            if i >= 0:
                break
    if i < 0:
        return text[:width].replace("\n", " ").strip()
    start = max(0, i - 40)
    end = min(len(text), i + len(query) + 80)
    frag = text[start:end].replace("\n", " ").strip()
    return frag[:width]


def cmd_search(args) -> int:
    query = getattr(args, "query", "")
    if not query:
        print("visectl search: query is required", file=sys.stderr)  # noqa: F821
        return 2
    rows = search(query, limit=getattr(args, "limit", DEFAULT_LIMIT),
                  kind=getattr(args, "kind", None))
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    elif rows:
        for r in rows:
            where = f" ({r['command']})" if r["kind"] == "command" else ""
            print(f"[{r['kind']:>7}] {r['adapter_id']}{where}  "
                  f"{r['snippet']}")
    else:
        print("search: no matches")
    return 0 if rows else 1
