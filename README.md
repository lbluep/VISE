# VISE

**V**endor-neutral **I**nterface for **S**etup & **E**xecution

*A vise grips the workpiece so your hands are free — VISE grips your tools
so you (and your AI agents) can build.

Vise is a local resource-management layer for AI-assisted development. Every
tool or knowledge base is onboarded once as an *adapter* — a small declarative
manifest — behind a single, machine-readable entry point. AI agents discover
and call your local tools without guessing, accumulating reusable capability
instead of resetting every session.

---

## Why Vise

**What it does** — Vise turns a pile of local tools into a managed resource
library: onboard each tool once, and every shell and every AI assistant gets
the same discoverable, health-checked entry point (`visectl` / `viseinit`).

**Value for human users**

- No more "works on my machine" — tool paths, versions and init steps differ
  per machine; Vise resolves them declaratively instead of hardcoding
- Health checks surface a broken tool *before* you depend on it, not after
- Onboarding without friction — new machine? one setup script; new tool? one
  small manifest

**Value for AI agents & assistants** — an agent is only as reliable as the
tools it drives. Vise gives a local AI assistant a *verified* tool environment,
cheap capability discovery without blind probing, review-before-execute
safety, and machine-typed results it can act on. Tools the agent builds while
solving problems can be onboarded as adapters too — one-off scripts become
managed, reusable capabilities instead of disappearing with the session.
([details](#value-for-locally-deployed-ai-assistants))

**How it complements MCP** — MCP standardizes how an agent *talks to* a
capability; it assumes that capability is already installed and healthy. Vise
standardizes the layer MCP leaves out: installation, dependency resolution,
initialization, health and cleanup — and can bridge into any MCP host.
([details](#vise--mcp-complementary-layers))

---

## Quick Start Guide

### Quick Setup (New Users)

Before running Vise, make sure these tools are installed:

- Python (3.11+)
- Git
- 7-Zip (optional)
- VS Code (optional)

Just run one command and you're ready to go. **This only needs to be executed once.**

```bash
./init/setup.sh
```

That's it! The script will:

1. Detect your installed tools (Python, Git, 7-Zip, VS Code)
2. Create the project virtual environment `.venv`
3. Create the configuration file `.vise/tool_paths.json` (pointing `python` at `.venv`)
4. Generate `.vise/registry.json` and `.vise/requirements.txt`, then install adapter
   Python dependencies into `.venv` — probing the official PyPI index plus
   public mirrors and using the fastest one
5. Update your shell startup to auto-load Vise commands
6. Automatically run `viseinit` to initialize all Vise configurations

One-line setup complete!

---

## Vise Design

Build up the resource library that can truly help the work:

| Tools | Work Instruction | Database | AI chat archive |
| --- | --- | --- | --- |
| **Plan** | **ToDo List & Reminder** | **Cloud Drive Resource** | **External Resource Index** |

The adapter layer is the machinery behind the library: each entry (a tool, a
database manager, a WI manager, ...) is onboarded once as an adapter and is
then initialized, checked and reported uniformly.

---

## Architecture

Vise is built in four layers, from the bottom up:

```
┌─────────────────────────────────────────────────┐
│  Layer 4: User / AI Agent entry                  │
│  viseinit (CLI)  ·  visectl (CLI)  · functions   │
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
│  init/uninstall.sh · vise_functions.sh           │
└─────────────────────────────────────────────────┘
```

**Layer 4 is the single entry point for both users and AI agents — all
resources in the library are managed through it.** Every resource (a tool, a
work instruction, a database, ...) is onboarded once as an adapter; from then
on, an AI agent can discover, initialize, diagnose and operate it uniformly:

- **Discover** — `visectl list --json` / `visectl describe <id> --json` expose
  each adapter's manifest, its declared `commands` (with arguments, so agents
  can construct calls without `--help` probing) and resource category
  (`provides`)
- **Initialize & verify** — `viseinit --all --json` runs dependency checks,
  init steps and health checks, with stable exit codes (`0`-`6`) designed for
  agent consumption; `--ndjson` streams progress events in real time, and
  `--dry-run` shows exactly what would execute (`[PLAN]`) before anything runs
- **Operate & maintain** — `visectl run <id> <cmd>` dispatches to any adapter
  CLI; `visectl read <id>` reads adapter knowledge content; `visectl doctor`
  and `visectl clean` cover diagnostics and cleanup
- **Bridge** — `visectl mcp` exposes the same surface as an MCP server for
  MCP-based agent hosts (see next section)

See [docs/DESIGN.md](docs/DESIGN.md) for the full architecture design.

---

## Vise + MCP: complementary layers

[MCP](https://modelcontextprotocol.io) (Model Context Protocol) standardizes
how an agent **talks to** a capability — JSON-RPC, tool schemas, sessions. It
deliberately does not cover how that capability got installed, whether its
dependencies are resolved, or whether it is healthy. Vise standardizes exactly
that missing layer:

| Question | MCP | Vise |
| --- | --- | --- |
| How does an agent call a tool? | JSON-RPC protocol, typed schemas | bridged via `visectl mcp` |
| Is the tool installed and its env ready? | out of scope (assumes ready) | `required_cots` + version checks + `viseinit` |
| How to initialize / health-check / clean up? | out of scope | manifest `init_steps` / `healthcheck` / `visectl clean` |
| Where does it run? | any host, local or remote | local machine, zero daemon |

Agents can reach Vise through two paths:

- **Direct CLI (default)** — coding agents that can run shell commands call
  `visectl` / `viseinit` directly: `--json` output, stable exit codes, no
  protocol overhead
- **MCP bridge (optional)** — `visectl mcp` serves the same surface over MCP
  stdio: tools `vise_list` / `vise_describe` / `vise_run` / `vise_doctor`, and
  resources `vise://adapters`, `vise://adapter/<id>/manifest`

### Value for locally-deployed AI assistants

1. **Deterministic environment readiness** — an agent's first move is
   `viseinit --all` or `visectl doctor`: failures surface with actionable
   errors and typed exit codes, instead of mid-task trial-and-error debugging
2. **Zero-guessing calls** — declared `commands` in the manifest tell the agent
   what each tool accepts before it runs anything
3. **Token-efficient discovery** — progressive disclosure: `list` (cheap
   summary) → `describe` (detail) → `run` (execute), nothing preloaded into
   context
4. **Auditable automation** — `--dry-run` reviews planned init steps before
   execution; JSON reports land in `.vise/reports/` for replay and cleanup
5. **Near-zero integration cost** — no daemon, no server to keep alive; plain
   CLI + JSON, onboard a new resource by dropping in one manifest
6. **Agent-built tools become reusable assets** — when a local AI assistant
   writes a script to solve a problem, packaging it as a Vise adapter
   (manifest + CLI + healthcheck) turns it into a managed resource: the next
   session — or the next agent — discovers it via `visectl list`, learns its
   usage from its declared `commands`, and trusts it because it health-checks.
   The assistant's capabilities compound over time instead of resetting every
   session

---

## Interface Adapter Layer

### Architecture diagram

```
 User / AI Agent
       |
       v
 visectl / viseinit Command
       |
       v
 Load .vise/registry.json
       |
       v
 Selected adapters
       |
       v
 Load per-adapter manifest (vise.adapter.json)
       |
       v
 Resolve required COTS via adapter_paths.json + PATH fallback
       |
       v
   Dependencies OK? ---- No ----> Fail adapter init with actionable error
       | Yes
       v
 Run init steps (e.g. thread)
       |
       v
 Run adapter health check
       |
       v
 Collect status + export info
       |
       v
   Init Report -> Console summary + JSON report (.vise/reports/)
```

### Shared config layer

- `core/adapter_paths.json` — explicit, portable tool paths (developer-maintained,
  nested schema `{"cots": {"python": {"path": "..."}}}`)
- `.vise/tool_paths.json` — detected by `init/setup.sh` at setup
  time (auto-generated, flat schema `{"python": "/path"}`, do not edit)
- `core/vise_adapter_paths.py` — resolver: `core/adapter_paths.json` →
  `.vise/tool_paths.json` → PATH fallback

### Adapter layer

`example_tool` ships as the reference adapter. Domain adapters — e.g.
prChecker, BuildCollection, cvtChecker, Database Manager, WI Manager, or any
future Adapter X — are onboarded with exactly the same contract.

### Execution flow

1. User runs `viseinit` with an adapter list or `--all`.
2. Adapter-local usage runs `thread` first to initialize required config
   before other commands.
3. Registry selects enabled adapters.
4. Adapter manifest drives dependency resolution and init actions.
5. Each adapter runs the health-check contract.
6. `viseinit` writes a unified report and exits with a stable code
   (`0` = all OK, `2` = dependency missing, `3` = init step failed,
   `4` = healthcheck failed, `5` = unknown adapter, `6` = manifest error,
   `1` = generic fallback).
7. All configuration is portable via `core/adapter_paths.json` (with PATH
   fallback).
8. New adapters are onboarded by adding a manifest under `adapters/` and
   running `bash init/setup.sh` (registry auto-generated).

---

## Unified Structure

Every adapter follows the same layout (shown: the reference adapter):

```
adapters/ExampleAdapter_reference/
├── vise.adapter.json        # Adapter manifest: adapter_id, version,
│                            #   required_cots, init_steps, healthcheck,
│                            #   entry_points
├── vise_extensions.sh       # Optional shell extensions (aliases/completions).
│                            #   Functions prefixed with vise_<adapter_id>_
├── thread                   # Bootstrap script: sets up adapter config,
│                            #   validates paths, registers with Vise
├── README.md                # Documentation: usage, configuration
└── src/
    └── example_tool/        # Python package for the adapter
        ├── __init__.py      # Package initializer
        ├── main.py          # Main CLI entry point: argument parsing
        │                    #   and command dispatch
        └── thread_cli.py   # thread CLI implementation: runs when
                             #   thread is invoked for this adapter
```

### Key components

- **`vise.adapter.json`** — the contract that registers this adapter with Vise
- **`thread`** — the bootstrap script that initializes the adapter's state
- **`src/example_tool/`** — the actual Python implementation
- **`main.py`** — handles CLI commands (like `visectl run example_tool list`,
  `visectl run example_tool decode`)
- **`thread_cli.py`** — specific logic for the thread initialization step
- **`__init__.py`** — makes it a proper Python package

### How it works together

```
1. Register          2. Initialize        3. Use CLI              4. Execute
vise.adapter.json -> thread ----------> visectl run <id> -----> src/example_tool/
Registers adapter    Sets up config,      Unified CLI dispatch    Python package
with Vise            validates paths,     via visectl             executes commands
                     registers state
\--------------------------- Vise Workspace Environment ---------------------------/
```

---

## Detailed Setup Guide

### What the installer does (per file)

| File | Produced by | Purpose |
| --- | --- | --- |
| `.venv/` | `init/setup.sh` | Project virtual environment; the interpreter all Vise commands and adapters use |
| `.vise/tool_paths.json` | `init/setup.sh` | Detected tool paths; `python` points at the `.venv` interpreter (machine-local, auto-generated) |
| `.vise/requirements.txt` | `init/register_adapters.py` | Aggregated adapter `python_deps`; installed into `.venv` at setup |
| `.vise/python` | `init/setup.sh` | Interpreter chosen for Vise shell functions (the `.venv` interpreter) |
| `core/vise_functions.sh` | repo | Loader sourced by your shell startup; defines `visectl` and `viseinit` |
| `.vise/state/<id>/state.json` | `thread` | Per-adapter registered state |
| `.vise/reports/viseinit_report_*.json` | `viseinit` | Unified init reports (+ `latest.json`) |

### Everyday commands

```bash
viseinit --all                          # initialize every enabled adapter
viseinit --all --json                   # machine-readable JSON to stdout
viseinit --all --jobs 4                 # parallel init (4 workers)
viseinit --all --fail-fast              # stop on first failure
viseinit example_tool                   # initialize selected adapters only
visectl list                            # list all adapters
visectl list --category tools          # filter by resource category
visectl describe example_tool          # show adapter manifest
visectl thread example_tool            # bootstrap a single adapter
visectl run example_tool list          # run adapter CLI
visectl run example_tool decode SGVsbG8=  # base64 decode; add --hex for hex
visectl read example_tool readme       # read adapter knowledge content
visectl mcp                            # MCP stdio bridge for AI-agent hosts
visectl doctor                          # diagnose without executing init_steps
visectl clean --reports                 # remove >30-day reports
visectl clean --state                   # remove orphaned adapter state
viseinit --all --dry-run                # review planned steps, execute nothing
viseinit --all --ndjson                 # stream NDJSON progress events
visereload                              # re-source shell integration (auto on next vise cmd)
```

Note: `visectl run` executes adapter CLIs with the **adapter directory** as
working directory — pass absolute paths when targeting files elsewhere.

### Onboarding a new adapter

1. Copy `adapters/ExampleAdapter_reference/` to `adapters/<YourAdapter>/`.
2. Edit `vise.adapter.json` (new `adapter_id`, `required_cots`, `init_steps`,
   `healthcheck`) and implement your package under `src/`.
3. Declare any third-party Python packages in `python_deps` (e.g.
   `["requests>=2.31"]`). They are aggregated into `.vise/requirements.txt` and
   installed into `.venv` by setup — no need to list them in `required_cots`.
4. Run `bash init/setup.sh` — this regenerates `.vise/registry.json` and
   `.vise/requirements.txt` by scanning `adapters/*/vise.adapter.json`, then
   installs the declared packages into `.venv`. No manual registry editing
   needed.
5. Run `viseinit --all`. If a non-Python dependency is missing, either install
   it or pin an explicit path in `core/adapter_paths.json`.

### Uninstall

```bash
bash init/uninstall.sh             # remove shell integration + all artifacts
bash init/uninstall.sh --keep      # remove only shell integration, keep artifacts
bash init/uninstall.sh --dry-run   # preview what would be removed
```

Artifacts are the `.vise/` directory (tool paths, registry, requirements,
state, reports) and `.venv/`.

### Requirements

Python >= 3.11, Git, and bash. Vise itself uses only the standard library; setup
creates a project virtual environment (`.venv`) that all Vise commands use, so
adapters may declare third-party Python packages in their manifest `python_deps`
field — those are aggregated into `.vise/requirements.txt` and installed into
`.venv`. Runs on Linux, macOS, and Git Bash on Windows.
