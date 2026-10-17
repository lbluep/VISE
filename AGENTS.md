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
visectl manifest list|verify      # what Vise added outside VISE_ROOT (+ integrity)
visectl adapter status|diff|verify  # drifted from baseline? baseline tree intact?
visectl adapter upgrade [--staged DIR]  # reconcile baselines with a release manifest
visectl audit [--adapter <id>] [--event <e>]  # execution audit (run + state changes)
visectl search <query>               # find an adapter by keyword (identity, commands, README)
visectl skill export <id> --out DIR  # package an adapter as a SKILL.md bundle
visectl skill import <dir>           # turn a SKILL.md package into a managed adapter
visectl bundle export --out FILE     # export a portable, hash-verified tree
visectl bundle import FILE           # verify + reconcile (fresh/merge per adapter)
visectl doctor                    # diagnose without executing anything
```

All commands support `--json`. Stable exit codes are designed for agents.
State-changing commands serialize through one Linux write lock (`.vise/.lock`,
`fcntl`): contention past `VISE_LOCK_TIMEOUT` (default 10 s) exits **75** — a
transient failure to retry, never an adapter failure. Every `run` and every
state-changing visectl command appends one audit record (event/adapter/
capability only — never argument values). An MCP host reaches the
agent-facing surface (list/describe/doctor/read/run/status/search/
snapshot/restore) through `core/vise_mcp.py`; restore requires an explicit
`confirm: true`.

**Physical entry (non-interactive shells):** `visectl` / `viseinit` are shell
functions loaded only by an interactive rc. In agent tool calls, scripts and
CI, invoke the entry points directly:

```bash
.venv/bin/python core/visectl.py <cmd> ...
.venv/bin/python core/viseinit.py <cmd> ...
```

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
| Protect an adapter edit | `visectl adapter` | `visectl adapter snapshot <id> -m "why"` (see below) |

## Modifying an adapter (when a tool almost solves the task)

If an adapter solves *most* of the problem but not all of it, the fix is a
protected edit — not an ad-hoc script beside it. Follow this order:

```bash
visectl adapter snapshot <id> -m "why the adapter is about to change"   # 1. mandatory
# 2. edit adapters/<Name>/ source — use file_patch for the write
viseinit <id>                                       # 3. validate
visectl adapter restore <id> [--gen N|--base]       # 4a. failed -> roll back
visectl adapter promote <id> -m "what changed"      # 4b. worked -> write a patch
```

- The snapshot is **mandatory**: a modified adapter with no open snapshot is
  refused by `visectl run` and `promote` (exit `7`). Override only for a
  deliberate manual customization: `visectl run --allow-modified <id> ...`.
- `promote` writes `.vise/patches/<id>/*.patch` (a reviewable unified diff) and
  **never commits** — committing is a separate, deliberate act.
- `restore` never depends on the adapter's own code, so it still works after an
  edit broke the adapter.
- No git required: a zip/tarball install gets the same protection. The baseline
  is captured at install time and never overwritten by re-running setup (use
  `visectl adapter rebaseline <id> [--force]` to accept new state explicitly).
- Read the adapter's own `read` topic before changing it, and re-check
  `visectl adapter status` (or `doctor`) afterwards.

## Onboarding a new adapter (when nothing in the library fits)

Fast path: `visectl adapter scaffold <Name> [--category C]` copies the
reference template, renames the identity references, re-registers **and
refreshes `vise.release.json` automatically** (one locked operation; failure
to refresh is exit 1). Manual equivalent, in this order:

1. Copy `adapters/ExampleAdapter_reference/` → `adapters/<NewName>/` (it is
   the template for the full contract: manifest + `thread` + `src/` layout)
2. Edit `vise.adapter.json`: unique `adapter_id`; `required_cots` with
   `min_version` where needed; `entry_points.cli`; **declare `commands`**
   (required — registration skips a manifest without it); `provides.category`
   from the closed set (`tools | work_instruction | database | ai_chat | plan
   | todo | onedrive | external`) so `visectl list --json` can match it
3. Implement the CLI (argv in, exit code out) and the `thread` bootstrap
4. Re-register: `bash init/setup.sh` (idempotent) or `viseinit --all`
5. Gate before calling it done: `viseinit <id>` exit `0` (init + healthcheck),
   `visectl describe <id>` shows the declared surface, and one real
   `visectl run <id> <cmd>` works end-to-end
6. Refresh the release manifest: `.venv/bin/python core/release.py generate`
   (any change under `adapters/` or a version bump needs it; CI/smoke runs
   `core/release.py check`)

Full manifest contract: [docs/DESIGN.md](docs/DESIGN.md) §3.3 (field list, the
two `init_steps` forms, the `read` resource convention).

## Conventions

- **Absolute paths**: `visectl run` executes with the *adapter directory* as
  cwd — always pass absolute paths for files outside it.
- **Discovery flow**: `list` → `describe` → `run`. Never guess arguments; read
  the manifest's declared `commands` first.
- **Nothing fits?** Build the capability, then **onboard it as an adapter**
  (one `vise.adapter.json` + `thread`, template: `adapters/ExampleAdapter_reference/`)
  instead of leaving an ad-hoc script — one-off tools become reusable entries
  in the library instead of disappearing with the session.
- **Safety**: `viseinit --dry-run` shows what init would execute; `file_patch
  preview` shows a diff before any write.
- **Persistence**: notes and tasks survive sessions — write durable knowledge
  to `note_keeper` instead of chat-only summaries.
- **Generated state is off-limits**: never hand-edit anything under `.vise/`
  (baselines, snapshots, patches, registry, reports). It is machine-written;
  the sanctioned way to change a baseline is
  `visectl adapter rebaseline <id> [--force]`, never editing its files.
- **Ledger contract**: anything a script installs *outside* `VISE_ROOT`
  (e.g. `~/.local/bin` wrappers, rc-file blocks) must be recorded in the
  uninstall ledger at write time via
  `python core/manifest.py add created|modified ...` — see the module
  docstring. `visectl manifest verify` checks integrity; `init/uninstall.sh`
  removes exactly what was recorded. Wrapper files must keep the
  `# Auto-generated by <source> - do not edit.` header (fallback-scan marker).

Full architecture: [docs/DESIGN.md](docs/DESIGN.md)
