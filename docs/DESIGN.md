# Vise Architecture Design

> This document records Vise's detailed architecture design, the responsibilities of each component, and the execution flows.
> See [IMPROVEMENTS.md](IMPROVEMENTS.md) for the improvement proposals.

---

## 1. System Overview

Vise is a toolchain adaptation framework for **AI Agents and developers**. Core idea:
wrap each development tool (build, test, database management, PR checks, etc.) as an
**Adapter**, declare dependencies and entry points through a unified manifest, and let the
`viseinit` command handle unified initialization, health checks, and reporting.

```
        AI
     Sub-Agents
       SKILLs
   ▔▔▔▔ Vise ▔▔▔▔   <- this project: resource & automation foundation
```

Resource library vision (8-grid):

| Tools | Work Instruction | Database | AI chat archive |
| --- | --- | --- | --- |
| **Plan** | **ToDo List & Reminder** | **Cloud Drive Resource** | **External Resource Index** |

---

## 2. Architecture Layers

The current implementation is split into 4 layers, from the bottom up:

```
┌─────────────────────────────────────────────────┐
│  Layer 4: User / AI Agent entry                  │
│  viseinit · visectl · vise_mcp                   │
│  vise_functions.sh (shell integration)           │
├─────────────────────────────────────────────────┤
│  Layer 3: Adapter Layer                          │
│  vise.adapter.json · thread · CLI                │
├─────────────────────────────────────────────────┤
│  Layer 2: Shared Config                          │
│  adapter_paths.json · tool_paths.json            │
│  vise_adapter_paths.py                           │
├─────────────────────────────────────────────────┤
│  Layer 1: Setup                                  │
│  init/setup.sh · init/register_adapters.py       │
│  init/uninstall.sh · init/pick_pip_index.py      │
└─────────────────────────────────────────────────┘
```

---

## 3. Layer-by-Layer Detailed Design

### 3.1 Layer 1 — Setup Layer

**File list:**

| File | Responsibility |
| --- | --- |
| `init/setup.sh` | One-shot install (Git Bash / Linux): detect tools → create `.venv` → write `.vise/tool_paths.json` → generate `.vise/registry.json` → capture adapter baselines → install deps → idempotently update shell rc → automatically run `viseinit --all` |
| `init/register_adapters.py` | Scan `adapters/*/vise.adapter.json` → generate `.vise/registry.json` + `.vise/requirements.txt` (`adapter_id` uniqueness check, warn-and-skip invalid manifests) |
| `init/pick_pip_index.py` | Probe official PyPI + public mirrors concurrently and print the fastest reachable index (used by `setup.sh` before `pip install`) |
| `init/uninstall.sh` | One-shot uninstall: apply the uninstall ledger (remove/revert everything Vise added or modified outside `VISE_ROOT`), plus fallback wrapper scan and idempotent rc-marker cleanup; optionally clean up all generated artifacts |
| `core/manifest.py` | Uninstall ledger writer: installers call `add` right after writing any file outside `VISE_ROOT` (upsert on kind+path, sha256 for created files, revert recipe for modified files); exposes `list`/`verify`/`uninstall-apply` |
| `core/baseline.py` | Install-time reference state (C13): fingerprints each adapter into `.vise/baseline/<id>/` (per-file sha256 + a full tree copy) so drift can be detected and the original restored **without git**; captured by `setup.sh` **only when absent**, so re-running setup never blesses an edited tree as "original". Providers are pluggable for future non-install sources |

**Design intent:**
- Install runs only once; afterwards, opening a new shell automatically provides the Vise commands
- Lenient tool detection: Python uses a candidate list + version check (>= 3.11), others use `command -v` and take the first match
- Idempotent shell rc injection: uses a `# >>> Vise >>>` marker block, skipped if detected on repeated runs
- Auto-detect `$SHELL`: bash → `~/.bashrc`, zsh → `~/.zshrc`
- Safe uninstall: `--dry-run` preview, `--keep` keeps artifacts and only removes the shell block

**Install flow:**

```
setup.sh
  │
  ├── 1. detect tools (python3/git/7z/code)
  ├── 2. create/refresh .venv (from the detected python)
  ├── 3. write .vise/tool_paths.json + .vise/python (python -> the .venv interpreter)
  ├── 4. call register_adapters.py → .vise/registry.json + .vise/requirements.txt
  ├── 5. capture adapter baselines (only when absent, C13)
  ├── 6. probe indexes (official + mirrors), pip install into .venv
  ├── 7. idempotently inject shell rc → source vise_functions.sh
  └── 8. automatically run viseinit --all
```

**Uninstall flow:**

```
uninstall.sh
  │
  ├── 1. apply the uninstall ledger (.vise/installed.json):
  │     ├── created  -> delete (only if sha256 still matches; a changed file
  │     │                is SKIPPED and reported for manual action, exit 3)
  │     └── modified -> apply revert recipe (remove rc marker block)
  ├── 1b. fallback scan ~/.local/bin for Vise-generated wrappers
  │       (Auto-generated header + VISE_ROOT reference) — covers a lost ledger
  ├── 2. remove the # >>> Vise >>> block from ~/.bashrc / ~/.zshrc
  │      (idempotent marker defense, works even without a ledger)
  ├── 3. (default) delete generated artifacts:
  │     ├── .vise/  (tool_paths, registry, requirements, state, reports, ledger)
  │     └── .venv/
  └── (--keep) keep artifacts, still clean everything outside VISE_ROOT
```

**Ledger contract:** every write outside `VISE_ROOT` must be followed by
`core/manifest.py add created|modified --path ... --source ...` (idempotent
upsert). Query with `visectl manifest list|verify`; drift (created file
changed since install) surfaces in `verify` (exit 1) and makes uninstall
skip + report instead of delete. Wrapper scripts must keep the
`# Auto-generated by <source>` header so the fallback scan can identify them.

### 3.2 Layer 2 — Shared Config Layer

**File list:**

| File | Responsibility |
| --- | --- |
| `adapter_paths.json` | Explicit override layer: hand-written portable paths, highest priority (nested schema, repo-managed) |
| `.vise/tool_paths.json` | Machine-local detection layer: automatically generated by the install script, do not edit by hand (flat schema, not repo-managed) |
| `vise_adapter_paths.py` | Resolver: three-level fallback — `adapter_paths.json` → `.vise/tool_paths.json` → PATH |

**Three-level path resolution flow:**

```
resolve("python")
  │
  ├── adapter_paths.json["cots"]["python"]["path"] exists?
  │     └── yes → return that path
  │
  ├── tool_paths.json["python"] exists?
  │     └── yes → return that path
  │
  └── shutil.which("python3") / shutil.which("python")
        └── found → return it
        └── none found → return None
```

**Alias mapping** (`_ALIASES` dict):

| Logical name | Search aliases |
| --- | --- |
| `python` | `python3`, `python` |
| `git` | `git` |
| `7zip` | `7z`, `7zz` |
| `vscode` | `code` |

**Version check** (`resolve_with_version`):

A `required_cots` entry may specify `min_version`; the resolver runs the tool's
`--version` command, parses the version number, and compares whether it meets the requirement.

### 3.3 Layer 3 — Adapter Layer

#### 3.3.1 Adapter Standard (Unified Structure)

Every adapter follows a unified directory structure:

```
adapters/<AdapterName>/
├── vise.adapter.json        # contract manifest
├── vise_extensions.sh       # optional shell extensions (aliases/completions)
├── thread                   # bootstrap script
├── README.md
└── src/<tool_name>/
    ├── __init__.py
    ├── main.py              # CLI entry
    └── thread_cli.py        # bootstrap logic implementation
```

#### 3.3.2 Manifest Contract (`vise.adapter.json`)

```jsonc
{
  "schema_version": 1,               // manifest format version (forward compat)
  "adapter_id": "example_tool",     // globally unique identifier
  "name": "ExampleAdapter",
  "version": "1.0.0",
  "description": "...",
  "required_cots": [                 // list of required tools
    { "tool": "python", "min_version": "3.11" },  // optional version requirement
    { "tool": "git" }
  ],
  "python_deps": ["requests>=2.31"],  // third-party Python packages -> .vise/requirements.txt -> .venv
  "depends_on": [],                  // other adapter_ids this depends on (optional, P2-2)
  "init_steps": [                    // ordered initialization steps
    {
      "name": "thread bootstrap",
      "run": "bash",                 // structured form: program + argument list
      "args": ["./thread"],
      "timeout_sec": 60
    }
  ],
  "healthcheck": {                   // health check contract
    "run": "$PYTHON",               // $VAR expanded from env
    "args": ["src/example_tool/main.py", "--self-test"],
    "timeout_sec": 30
  },
  "entry_points": {                 // exposed entries
    "cli": "src/example_tool/main.py",
    "thread_cli": "src/example_tool/thread_cli.py",
    "extensions": "vise_extensions.sh"
  },
  "commands": [                     // optional: declared CLI surface (AI-agent friendly)
    {
      "name": "decode",
      "description": "Decode an encoded string",
      "args": [
        { "name": "text", "required": true, "description": "..." },
        { "name": "--hex", "required": false, "description": "..." }
      ]
    }
  ],
  "provides": {                     // resource library classification (optional, P3-1)
    "category": "tools",
    "tags": ["example", "reference"],
    "description": "Reference adapter"
  }
}
```

**`commands` declaration:** lets an AI agent construct valid `visectl run <id>`
calls without trial-and-error (`--help` probing), surfaced via
`visectl describe <id>` (both human and `--json` output). Declarative metadata
only — the CLI itself remains the source of truth for parsing.

**Resource read convention:** knowledge-type adapters (e.g. category
`database`, `work instruction`) expose content through a `read` interface:
either a `read` subcommand of the CLI (convention), or an explicit
`entry_points.read` script (override). Access it uniformly via
`visectl read <id> [query...]`.

**Two forms for init_steps:**
- **Structured (recommended)**: `{"run": "bash", "args": ["./thread"]}`, executed with `subprocess.run([program] + args)`, not through the shell
- **Shell string (escape hatch)**: `{"command": "bash ./thread && echo done"}`, backward compatible

**healthcheck supports the same two forms.**

#### 3.3.3 Bootstrap Script (`thread`)

Shell script, responsibilities:
1. Create the `.vise/state/<adapter_id>/` state directory
2. Invoke `thread_cli.py` (validate manifest completeness + validate Python version + validate entry file existence + write `state.json`)

#### 3.3.4 CLI (`main.py`)

The reference implementation includes the subcommands:
- `list` — output adapter information
- `decode <text> [--hex]` — base64/hex decoding
- `--self-test` — health check entry

#### 3.3.5 Extensions File (`vise_extensions.sh`)

Optional file that defines adapter-specific shell aliases/completions. Function names are prefixed with
- `vise_<adapter_id>_` to avoid conflicts. **It is not loop-sourced by `core/vise_functions.sh`**;
the adapter CLI is dispatched uniformly through `visectl run <id>`.

### 3.4 Layer 4 — Entry Layer

**`viseinit.py`** core execution flow:

```
viseinit [--all | <adapter_id> ...] [--json | --ndjson] [--jobs N]
         [--keep-going | --fail-fast] [--dry-run]
  │
  ├── 1. load .vise/registry.json (if missing, prompt "Run: bash init/setup.sh")
  ├── 2. filter enabled adapters (all or the specified ids)
  ├── 3. dependency_layers() topological sort (depends_on, Kahn's algorithm)
  │
  └── layered execution:
      │
      ├── Layer 0 (no dependencies): parallel within the layer with --jobs N
      ├── Layer 1 (depends on Layer 0): parallel after Layer 0 completes
      └── ...
      │
      └── for each adapter:
          ├── 4. load vise.adapter.json manifest
          ├── 5. resolve required_cots (three-level path resolution + version check)
          │     └── missing → record failed + actionable error (exit 2)
          ├── 6. run init_steps (structured or shell; on failure abort this adapter)
          │     └── failure → exit 3
          ├── 7. run healthcheck
          │     └── failure → exit 4
          └── 8. collect status + entry_points + dependency info
  │
  ├── 9. Console summary ([OK]/[FAIL]/[SKIP] + error reason)
  └── 10. JSON report → .vise/reports/viseinit_report_<timestamp>.json + latest.json
         (--json mode: pure JSON to stdout, no file written)
```

**Exit codes (P1-3, stable for AI Agents):**

| Exit code | Meaning |
| --- | --- |
| 0 | All passed |
| 1 | Generic failure (fallback) |
| 2 | Missing dependency |
| 3 | init step failure |
| 4 | healthcheck failure |
| 5 | Unknown adapter id |
| 6 | Manifest format error |

**`visectl.py`** unified CLI entry:

```
visectl list [--json] [--category <cat>] [--tag <tag>]   # list adapters
visectl describe <id> [--json]                           # view manifest
visectl thread <id> | --all                              # bootstrap adapter
visectl run <id> <command> [args...]                     # run adapter CLI
visectl read <id> [query...]                             # read adapter knowledge content
visectl mcp                                              # MCP stdio bridge (JSON-RPC)
visectl doctor [--json]                                  # diagnose without running init_steps
visectl clean [--reports] [--state] [--dry-run] [--json] # clean artifacts
visectl manifest list|verify [--json]                    # ledger of files Vise wrote outside VISE_ROOT
visectl adapter status [<id>] [--json]                   # clean / modified / unbaselined
visectl adapter diff <id> [--json]                       # file-level drift against the baseline
visectl adapter check [<id>] [--json]                    # gate: is the adapter protected?
visectl adapter snapshot <id> -m "why" [--force]         # snapshot BEFORE editing an adapter
visectl adapter list <id> [--json]                       # snapshot generations
visectl adapter restore <id> [--gen N | --base] [--dry-run]  # roll back byte-for-byte
visectl adapter promote <id> -m "why" [--dry-run]        # sediment the change as a patch (no commit)
visectl adapter rebaseline <id> [--all] [--force]        # accept the current tree as the reference
```

All commands support `--json` to output machine-readable content to stdout.

**`visectl run` working directory:** adapter CLIs are dispatched with the
*adapter directory* as cwd, so adapter-relative paths in manifests and scripts
behave predictably. Callers (humans and agents) must therefore pass **absolute
paths** when referring to files elsewhere — e.g.
`visectl run fast_search search "def main" "$PWD/adapters" --type py`.

**`adapter_guard.py`** — the adapter update path (C13):

The scenario: an agent runs an adapter, finds it solves *most* of the task but
not all of it, and edits the adapter's source. The workflow that protects the
original and sediments the improvement:

```
snapshot <id> -m "why"   → open a snapshot (.vise/snapshots/<id>/<ts>/)
edit adapters/<Name>/    → file_patch is the sanctioned writer
viseinit <id>            → validate (init_steps + healthcheck)
  ├── failed → restore <id> [--gen N|--base]  → roll back byte-for-byte
  └── worked → promote <id> -m "what changed" → .vise/patches/<id>/*.patch
rebaseline <id> [--force] → accept the tree as the reference (closes snapshots)
```

- **Gate:** a modified adapter with **no open snapshot** is *unprotected*;
  `visectl run` and `promote` refuse it (**exit 7**) with an actionable message.
  `--allow-modified` is the escape hatch for a deliberate manual customization.
  An *unbaselined* adapter passes through — an install predating baselines
  keeps working; `doctor` / `adapter status` surface that state.
- **git-free:** the baseline (L1, `core/baseline.py`) is the reference; restore
  and promote never invoke git, so a zip/tarball install is protected
  identically. With a repository present, `promote` still only writes the
  patch — committing stays a separate, deliberate act.
- **Never depends on the adapter:** restore/promote read the registry and
  manifests, never the adapter's own code, so they work after an edit broke it.
- **Retention:** the newest 5 snapshot generations per adapter; an open
  snapshot is never pruned.
- **Exit codes (guard domain):** `0` ok, `2` usage/input, `4` action impossible
  (no such generation / nothing to promote), `7` refused (unprotected drift or
  `rebaseline` needing `--force`). Distinct from the `viseinit` `0`–`6` map.

**`vise_mcp.py`** MCP stdio bridge (optional Layer 4 front for MCP hosts):

A thin newline-delimited JSON-RPC 2.0 server (stdlib only) that translates MCP
requests into `visectl` calls — Vise itself stays a local CLI:

| MCP | Vise |
| --- | --- |
| `tools/call vise_list / vise_describe / vise_doctor / vise_run` | `visectl list/describe/doctor/run` (`--json`) |
| `resources/list`, `resources/read` | `vise://adapters`, `vise://adapter/<id>/manifest` |

Launch via `visectl mcp`, or register `core/vise_mcp.py` in an MCP host config.

**`viseinit` review/streaming flags:**
- `--dry-run` — resolves dependencies and prints the planned init_steps +
  healthcheck (`[PLAN]`) without executing anything (review before run)
- `--ndjson` — streams one JSON event per line (`adapter_started` /
  `adapter_finished` / `summary`) instead of the console summary; no report
  files are written

**`core/vise_functions.sh`** load flow:

```bash
# block injected into ~/.bashrc or ~/.zshrc
# >>> Vise >>>
[[ -f "/path/to/Vise/core/vise_functions.sh" ]] && source "/path/to/Vise/core/vise_functions.sh"
# <<< Vise <<<
```

The loader defines `visectl`, `viseinit`, and `visereload` (manual re-source).
Reload is **trigger-based**: the staleness check (registry.json / loader mtime)
runs only when a Vise command is invoked, and re-sources the loader before the
command executes. Non-interactive shells skip the check.

---

## 4. Registry Design

`.vise/registry.json` — **a machine-local artifact generated by the init script (not repo-managed)**:

```json
{
  "_doc": "AUTO-GENERATED by init/register_adapters.py - do not edit, not repo-managed.",
  "version": 1,
  "generated_at": "2026-10-13T05:33:26Z",
  "adapters": [
    { "adapter_id": "example_tool", "path": "adapters/ExampleAdapter_reference", "enabled": true }
  ]
}
```

Design intent:
- Single source of truth = the `adapters/` directory + each adapter's `vise.adapter.json`;
  the registry is just a locally generated projection of a scan. `init/register_adapters.py` generates it,
  and `setup.sh` calls it automatically on every setup (idempotent)
- `"enabled": false` in a manifest disables an adapter (controls whether it participates in initialization)
- When the file is missing, `viseinit` / `visectl` prompts to run `bash init/setup.sh` first

---

## 5. Runtime Artifacts

| Path | Produced by | Content | Repo-managed |
| --- | --- | --- | --- |
| `.venv/` | `init/setup.sh` | Project virtual environment (the interpreter all Vise commands and adapters use) | No |
| `.vise/tool_paths.json` | `init/setup.sh` | Detected tool paths; `python` points at the `.venv` interpreter (re-detected and refreshed on every setup) | No |
| `.vise/requirements.txt` | `init/register_adapters.py` | Aggregated adapter `python_deps`, installed into `.venv` at setup | No |
| `.vise/registry.json` | `init/register_adapters.py` | Registry generated by scanning `adapters/` | No |
| `.vise/python` | `init/setup.sh` | The selected Python interpreter path (the `.venv` interpreter) | No |
| `.vise/state/<id>/state.json` | `thread` | Initialization state for each adapter | No |
| `.vise/installed.json` | `core/manifest.py` (via setup.sh / thread) | Uninstall ledger: files Vise created/modified outside `VISE_ROOT`, with sha256 (created) or revert recipe (modified) | No |
| `.vise/reports/viseinit_report_*.json` | `viseinit` | Initialization report (timestamp + summary + details) | No |
| `.vise/reports/latest.json` | `viseinit` | Soft copy of the latest report | No |

Cleanup methods: `visectl clean --reports` (reports >30 days old), `visectl clean --state` (orphan state),
`bash init/uninstall.sh` (full cleanup).

---

## 6. Design Decisions

The load-bearing decisions behind the layers above; the full proposals are in
[IMPROVEMENTS.md](IMPROVEMENTS.md).

### P0-1 · One dispatch path for adapter CLIs

Adapter shortcut files are not loop-sourced; every adapter CLI is reached
through `visectl run <id>`, so no global shell function name can collide.

### P0-2 · Two path files, two schemas

`core/adapter_paths.json` holds hand-written overrides (nested schema);
`.vise/tool_paths.json` holds detected paths (flat schema). Each carries a
`_doc` field, and the resolver accepts only its own schema.

### P1-1 · Structured `init_steps`

`init_steps` use `{"run": "bash", "args": ["./thread"]}` and run without a
shell, so there is no quote-escaping fragility; the `command` string form
remains an escape hatch.

### P1-2 · Versioned dependencies

`required_cots` accepts `{"tool": "python", "min_version": "3.11"}`, and the
resolver's `resolve_with_version()` verifies the version.

### P1-3 · Agent-facing interface

`visectl` (list / describe / doctor) + `viseinit --json` + stable exit codes
`0`–`6`.

### P2-1 · Generated registry

`registry.json` and `tool_paths.json` are generated by the init script and are
not repo-managed.

### P2-2 · Parallel init and failure semantics

`--jobs N` parallel execution + `--keep-going` / `--fail-fast` + `depends_on`
topological layering.

### P2-3 · State and report lifecycle

`visectl clean --reports` + `visectl clean --state` + `--dry-run`.

### P3-1 · Resource classification

Manifest `provides` field + `visectl list --category` / `--tag` filtering.

### IA-1 · Multi-shell install and uninstall

`init/setup.sh` detects `$SHELL` and injects the matching rc file
(`~/.bashrc` / `~/.zshrc`); `init/uninstall.sh` reverses the install.
