# Toolhost Layers — Investigation Notes

This document records two linked investigations:

1. **Why the first CI run failed** on the contribution-governance PR, and how
   the root cause was located.
2. **What `rg` actually is and where it came from** — which turned out to
   reveal that the agent host manages its own tools with a layered
   architecture closely parallel to Vise's.

The two are connected: the CI failure was caused by the very nature of the
host's resource model (tools bundled with the host, invisible to a clean
environment). Sections 6–7 draw the architectural parallel; section 8 draws
the design conclusion — whether Vise is still needed at all.

---

## 1. Context: the first CI run failed

`tests/smoke.sh` was added together with `.github/workflows/ci.yml` (commit
`864f735`). The workflow runs on `pull_request` and on pushes to `main`.

- PR: `vise-check-in` → `main`
- Run: `38529272650` — job `smoke` → **failure**, at step *Smoke test*
  (`bash tests/smoke.sh`), reported as `Process completed with exit code 1`.
- The failure annotation gave only the exit code, and the log download endpoint
  requires authentication (`403 Must have admin rights`). Diagnosis therefore
  proceeded by **local reproduction** rather than by reading the log.

## 2. Method, and the hypotheses that were ruled out

Reproduction: a fresh clone with an isolated `HOME` (`/tmp/vise-ci`,
`HOME=/tmp/vise-ci-home`) — this strips the existing `.vise/`, `.venv/` and rc
block, approximating a clean checkout.

Two plausible hypotheses were tested and **ruled out** before the real cause
was found — this is why the investigation did not "fix the wrong thing":

| Hypothesis | Test | Result |
| --- | --- | --- |
| `githubToolkit`'s `/etc/hosts` dependency fails on a pristine runner | read `congit --self-check` (commit-level source) | **Ruled out** — it only requires `curl` on `PATH` and a readable `/etc/hosts`; it does *not* require GitHub entries in the file |
| Python detection depends on `$HOME/miniconda3` | `find_python()` candidate list | **Real but not the CI cause** — the list omits a bare `python` and relies on `$HOME/miniconda3/bin/python3`; this fails on *this* machine with an isolated HOME, but `ubuntu-latest` (24.04) ships `python3` 3.12, so CI is unaffected |

The actual cause surfaced by minimising `PATH` on the fresh clone:

```bash
env -i PATH=/usr/bin:/bin HOME=/tmp/vise-ci-home \
  /tmp/vise-ci/.venv/bin/python /tmp/vise-ci/core/viseinit.py --all --json
# => exit=2
```

## 3. Root cause

`viseinit --all` exited `2` because one adapter's external dependency was
missing:

```
adapter : fast_search
status  : failed
error   : missing COTS: rg. Install the tool(s) or set an explicit path in adapter_paths.json
deps    : {"rg": {"version": null, "required": "13.0"}, "python": {...}}
```

`fast_search` declares `required_cots: [rg (>= 13), python]`, and the smoke
test asserts that `viseinit --all` exits `0`. A clean runner has no ripgrep, so
the assertion failed and the whole smoke test went red. Contributors' machines
pass only because they already have an `rg` (see §4).

## 4. What `rg` is — and where it really came from

**`rg` is ripgrep**, a recursive text-search tool (a modern `grep`/`ack`/`ag`
replacement) written in Rust. It is fast (parallelism + SIMD), respects
`.gitignore` by default, skips binaries and hidden directories, filters by file
type (`--type py`), and supports full regex plus PCRE2. In short: *search a
code tree for a pattern, quickly*. VS Code uses ripgrep as the engine behind
its search box, which is why the binary ships as `@vscode/ripgrep`.

**A correction to the initial assumption ("rg comes from conda"):** it does not.
The evidence:

```bash
$ type -a rg
rg is a function
rg () { local threads; threads="$(__toolhost_rg_threads)";
        if [ -n "$threads" ]; then "$TOOLHOST_RG_PATH" --threads "$threads" "$@";
        else "$TOOLHOST_RG_PATH" "$@"; fi; }

$ echo "$TOOLHOST_RG_PATH"
/usr/share/trae-cn/resources/app/node_modules/@vscode/ripgrep/bin/rg

$ "$TOOLHOST_RG_PATH" --version
ripgrep 15.0.0 (rev 3a612f88b8)  features:+pcre2

$ conda list | grep -i ripgrep
(ripgrep NOT installed as a conda package)
```

So `rg` is **bundled with the editor / agent host** and exposed to the shell by
the toolhost's integration script (the function is defined by
`TOOLHOST_INTEGRATION_TOOLS_ENV_PATH` → `.../terminal/common/scripts/integration_tools.sh`;
the `hooks_env` directory is empty). It is **not** a system-level or
user-installed tool.

## 5. Fix

`.github/workflows/ci.yml` now installs the external tools that adapters
declare, before running the smoke test:

```yaml
- name: Install external tools required by adapters
  run: |
    sudo apt-get install -y ripgrep
    rg --version
```

The prerequisite is also recorded in `docs/TEST-PLAN.md`.

Verified green — commit `98b1c92`, run `38530241105`:

```
JOB smoke -> success
  1 Set up job -> success
  2 Run actions/checkout@v4 -> success
  3 Run actions/setup-python@v5 -> success
  4 Install external tools required by adapters -> success
  5 Smoke test -> success
```

## 6. The deeper finding: the host has a layered resource architecture

Inspecting the host environment shows the tool naming, resolution, injection and
state are managed in distinct layers — the same separation of concerns Vise
implements:

```
# path resolution (logical name -> real absolute path)
TOOLHOST_RG_PATH=/usr/share/trae-cn/.../@vscode/ripgrep/bin/rg
TOOLHOST_PATH_SUFFIX=/usr/share/trae-cn/.../@vscode/ripgrep/bin
TOOLHOST_FFMPEG_DIR=/usr/share/trae-cn/resources/app/bin

# shell integration (a script injected into the shell)
TOOLHOST_INTEGRATION_TOOLS_ENV_PATH=.../terminal/common/scripts/integration_tools.sh
TOOLHOST_SHELL_INIT=snapshot

# runtime state / transport
TOOLHOST_IPC_NAME=/tmp/agent-code-toolhost-325255-*.sock
TOOLHOST_LOG_DIR=.../toolhost-host
TOOLHOST_ROLE=host   TOOLHOST_TRANSPORT=ipc   TOOLHOST_RUNTIME_MODE=direct_spawn
```

### Side by side with Vise

| Responsibility | agent toolhost | Vise |
| --- | --- | --- |
| Resource store | vendored binaries under the app dir (`@vscode/ripgrep`, `resources/app/bin`) | `adapters/` |
| Path resolution | `TOOLHOST_*_PATH` / `PATH_SUFFIX` env vars | L2 `adapter_paths.json` + `.vise/tool_paths.json` (logical → real, PATH fallback) |
| Shell integration | `integration_tools.sh` injected into the shell, defining `rg()` | L4 `core/vise_functions.sh` injected into the rc (marker-managed) |
| Discovery / registry | MCP / skills / plugins registries, `ToolSearch`, `database.db` | `.vise/registry.json`, `visectl list` / `describe` |
| Agent-facing contract | `AGENTS.md` injected as rules | `AGENTS.md` (same idea) |
| Runtime state | `TOOLHOST_LOG_DIR`, `/tmp/.../jobs/`, `database.db` | `.vise/` (`state/`, `reports/`, `registry.json`) |
| Dependency declaration | **implicit** (bundled with the app; no manifest for the user) | **explicit** `required_cots` + `min_version` |
| Health check | not exposed | per-adapter `healthcheck` |
| Removal / cleanup | n/a (part of the app) | ledger + `init/uninstall.sh` |
| Machine-readable output | IPC sockets / JSON | `--json` + stable exit codes |

## 7. Takeaway

The host's resource model and Vise's converge on the same four concerns, but
they differ in **where the knowledge lives**:

- The host does it **implicitly and privately** — `rg` exists only inside the
  host's process environment, with no manifest, no declared dependency, no
  health check, no uninstall ledger. Leave the host and it silently vanishes.
- Vise does it **explicitly and auditably** — dependencies are *declared*
  (`required_cots`), availability is *verified* (`healthcheck`), side effects
  are *recorded* (the uninstall ledger), and status is *machine-readable*
  (`--json`, stable exit codes).

The CI incident is therefore an external validation of Vise's design: the
host's implicit bundling fails precisely in a headless environment, while the
declarative contract makes the same dependency visible, checkable and fixable.
Vise can be read as *the host's resource abstraction, promoted from a private
host property into a project-owned, portable one*.

The generalisable lesson: **a tool that is a by-product of an editor or host
(e.g. `@vscode/ripgrep`) is inherently absent in headless environments and must
be declared and installed explicitly, not assumed.**

## 8. Is Vise redundant now that the host manages tools?

**Short answer: no — the two are not substitutes.** They sit in different
layers and answer different questions:

- The host layer answers **"which binaries are available?"** (availability).
- Vise answers **"how should *this project* do X — repeatably, portably and
  auditably?"** (a contract and a usage policy).

### Three orthogonal axes

| Axis | agent toolhost | Vise |
| --- | --- | --- |
| Resource domain | only the host's own bundled binaries (`rg`, `ffmpeg`) — a **closed** registry | any project-defined capability — an **open**, developer-extensible registry |
| Lifetime & ownership | bound to the **host process/session**; gone outside it | bound to the **repository**; survives sessions, editors, CI and other agents |
| Contract quality | implicit, private — no manifest, health check, ledger or audit path | explicit — `required_cots`, `healthcheck`, ledger + `uninstall`, `--json`, stable exit codes, `doctor` |

All three differ at once, so this is not "the same job done twice".

### The argument, point by point

1. **The resource domains do not overlap.** The host will never onboard *your*
   project's resources (`note_keeper`, `task_list`, `repo_map`, a project wiki).
   Its registry ships with the app and is closed; Vise's is opened by whoever
   writes a manifest.
2. **The lifetimes differ — and the CI incident is the proof.** The host's `rg`
   is process-scoped: leave the host (a clean runner, another editor, CI, a
   script) and it silently vanishes — exactly the failure in §1–3. Vise exists
   to make the project self-sufficient in *any* environment rather than
   dependent on one host.
3. **The contract quality differs — the host is un-auditable, Vise is
   governable.** You cannot ask the host layer "what did it write to my
   machine, can it be removed, is it healthy, which version does it need?" —
   there is no manifest, ledger or health check. Vise makes exactly those four
   things a contract. *Vise's value is not owning tools; it is owning a tool
   contract that can be inspected and reversed.*
4. **The roles differ — availability vs policy.** Which binaries exist is the
   host's question. Which path to take for X in *this project*, with which
   arguments, and how to hand the result to an agent is Vise's. The original
   motivation — "stop regenerating ad-hoc scripts each time" — is a
   **behavioural consistency** problem, and the host layer does not address it
   at all: it provides tools, not usage.
5. **Vendor neutrality.** `TOOLHOST_*` is proprietary to one host; switch agent
   or editor and that layer is gone. Vise is plain files in the repository
   (manifests + a Python CLI + `AGENTS.md`); every agent, editor, CI job and
   human reads the same contract.

### They compose; they do not compete

A design detail settles the relationship: Vise's `required_cots` with a `PATH`
fallback accepts *either* the host's `rg` *or* a system-installed one. That is:

> **Vise declares the dependency; the environment (host, system or CI) provides it.**

Vise is a declaration/contract layer, not a binary store, so it **sits above**
the host layer rather than occupying the same slot. The fix in §5 demonstrates
it: instead of leaning on the host's `rg`, it *declares* ripgrep and lets the
environment satisfy the dependency.

### Honest limits

Vise's value is conditional and grows with:

- **resource heterogeneity** — many and varied tools/knowledge → more value;
  two or three tools in a single editor → the host layer may suffice;
- **portability requirements** — cross-editor / cross-agent / CI
  reproducibility → Vise is irreplaceable;
- **auditability requirements** — "what did it install, can it be fully
  removed?" → Vise is the only answer.

And Vise has a **structural ceiling**: it cannot conjure a binary. `rg` still
has to be installed. It promises **visibility, not provisioning**; automatic
provisioning would be a new capability, not a current one.

### Counterfactual

Delete Vise and what is lost? Agents revert to ad-hoc scripts (not
reproducible), cross-session memory and todos disappear, nothing is portable
across environments, whatever was written outside the repository cannot be
audited, and behaviour varies per session. The host layer replaces **none** of
this, because it does not cover those responsibilities.

### Bottom line

**Vise is still needed.** The host layer is a session-scoped, private,
availability-only layer over its *own* binaries; Vise is a repository-scoped,
vendor-neutral, auditable contract-and-policy layer over *the project's*
resources. The host cannot replace Vise because it (1) will not onboard
project-specific resources, (2) disappears outside the host — proven by the CI
failure, (3) exposes no dependency/health/reversibility contract, and (4)
offers no cross-agent usage policy. Equally, Vise should not duplicate the
host's job — it **declares**, and the environment **provides**.

In one line: the host tells you **what you can use**; Vise tells you **how this
project should use it, identically wherever you are, and in a way you can
inspect and undo**. That is a layer of **governance**, not a layer of **tools**.

---

## Appendix — evidence trail

- CI runs (public API): `38529272650` (failure) and `38530241105` (success,
  after the fix).
- Root-cause repro: `env -i PATH=/usr/bin:/bin … viseinit.py --all --json`
  → `exit=2`; failing adapter `fast_search`, `missing COTS: rg`.
- `rg` provenance: `type -a rg`, `$TOOLHOST_RG_PATH` (ripgrep 15.0.0),
  `conda list | grep -i ripgrep` (absent).
- Host layers: `env | grep '^TOOLHOST'`,
  `TOOLHOST_INTEGRATION_TOOLS_ENV_PATH`, `TOOLHOST_LOG_DIR`,
  `/tmp/agent-code-toolhost-*/`.
