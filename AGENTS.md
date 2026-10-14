# AGENTS.md — How AI assistants should work in this repo

Vise is a local resource-management layer. **Before writing ad-hoc scripts or
reaching for raw shell commands, check whether a Vise adapter already does the
job** — these tools are health-checked, documented in their manifests, and
shared across sessions.

## Entry points

```bash
visectl list                      # discover available adapters
visectl describe <id>             # full manifest incl. declared commands+args
viseinit --all                    # verify/init the whole library (exit codes 0-6)
visectl run <id> <cmd> [args]     # invoke an adapter capability
visectl read <id> <topic>         # read adapter knowledge content
visectl doctor                    # diagnose without executing anything
```

All commands support `--json`. Stable exit codes are designed for agents.

## Adapter capabilities (prefer these over ad-hoc generation)

| Need | Use | Example |
| --- | --- | --- |
| Search code/text | `fast_search` | `visectl run fast_search search "def main" "$PWD" --type py` |
| Repo orientation | `repo_map` | `visectl run repo_map overview "$PWD"` |
| JSON query/format | `json_tool` | `visectl run json_tool get data.json "a.b.0"` |
| Persistent memory | `note_keeper` | `visectl run note_keeper add "title" "body" --tags x` |
| Persistent todos | `task_list` | `visectl read task_list pending` (session start) |
| Review-before-write edits | `file_patch` | `cat new | visectl run file_patch apply /abs/file` |
| Fetch web content | `http_fetch` | `visectl run http_fetch get https://...` |
| GitHub connectivity fix | `github_toolkit` | `visectl run github_toolkit --dry-run` |

## Conventions

- **Absolute paths**: `visectl run` executes with the *adapter directory* as
  cwd — always pass absolute paths for files outside it.
- **Discovery flow**: `list` → `describe` → `run`. Never guess arguments; read
  the manifest's declared `commands` first.
- **Safety**: `viseinit --dry-run` shows what init would execute; `file_patch
  preview` shows a diff before any write.
- **Persistence**: notes and tasks survive sessions — write durable knowledge
  to `note_keeper` instead of chat-only summaries.

Full architecture: [docs/DESIGN.md](docs/DESIGN.md)
