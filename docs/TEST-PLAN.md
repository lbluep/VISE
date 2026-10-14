# Vise Test Plan

Concrete, copy-pasteable verification points for a pull request. This is the
catalog referenced by [CONTRIBUTING.md](../CONTRIBUTING.md) §6 and by the PR
template. Run the sections that match your change surface; report what you ran
and what you observed.

Prerequisites: a checkout with `bash init/setup.sh` already run once (so
`.vise/` and `.venv/` exist). `visectl` and `viseinit` are shell functions
defined by `core/vise_functions.sh`; in a non-interactive shell use
`.venv/bin/python core/visectl.py ...` instead. The external tools adapters
declare in `required_cots` must also be installed — a clean environment with
no tooling makes `viseinit --all` exit `2` (e.g. `fast_search` needs ripgrep
`>= 13`). CI installs them; see `.github/workflows/ci.yml`.

Legend: **T#** = test point id. "expect 0" means the process exit code is `0`.

---

## T0 — Universal smoke (every PR)

**Automated:** `bash tests/smoke.sh` runs T0 + T1 in one command (same script
CI executes). The individual commands below are what it asserts.

| ID | Command | Expected |
| --- | --- | --- |
| T0.1 | `viseinit --all --json` | every enabled adapter `[OK]`; summary all-OK; exit `0` |
| T0.2 | `visectl doctor --json` | `n/n` OK; no `issues` on any adapter |
| T0.3 | `visectl list --json` | valid JSON array; one entry per enabled adapter |
| T0.4 | `git status --short` | no generated artifact listed (see CONTRIBUTING §5) |

---

## T1 — Bootstrap & idempotency (change surface: `init/setup.sh`, L1)

| ID | Command | Expected |
| --- | --- | --- |
| T1.1 | `bash init/setup.sh` on a clean checkout | completes; creates `.venv` + `.vise/`; auto-runs `viseinit --all`; exit `0` |
| T1.2 | `bash init/setup.sh` a **second time** | idempotent: rc block "already contains Vise block", no duplicate block, no error |
| T1.3 | `grep -c "# >>> Vise >>>" ~/.bashrc` | `1` (not 0, not 2) |
| T1.4 | `source ~/.bashrc && visectl list` | command resolves after a fresh shell |
| T1.5 | delete `.vise/registry.json`, then `bash init/setup.sh` | registry regenerated; T0.1 passes again |
| T1.6 | in a throwaway copy, corrupt a manifest's JSON; `bash init/setup.sh` | registry generator **warns and skips** it; that adapter is not registered (`viseinit <id>` → exit `5`) |

---

## T2 — Entry / CLI contracts (change surface: `core/visectl.py`, `core/viseinit.py`, L4)

| ID | Command | Expected |
| --- | --- | --- |
| T2.1 | `visectl describe <id> --json` | valid JSON manifest incl. `commands` with args |
| T2.2 | `visectl list --category tools` | filters by `provides.category`; unfiltered adapters still listable |
| T2.3 | `visectl list --tag <tag>` | filters by `provides.tags` |
| T2.4 | `visectl run <id> <cmd> [args]` | runs adapter CLI; **cwd is the adapter directory** |
| T2.5 | `visectl read <id> <topic>` | prints adapter knowledge content (resource contract) |
| T2.6 | `viseinit nonexistent_id; echo $?` | exit `5` (unknown adapter) |
| T2.7 | delete `adapters/<id>/vise.adapter.json` while the registry still lists the adapter; `viseinit <id>; echo $?` | exit `6` (manifest not found); message names the missing path |
| T2.8 | `viseinit --all --json` then parse with a JSON parser | stdout is JSON only (no human summary mixed in) |
| T2.9 | `visectl clean --dry-run --json` | lists would-be-removed items, deletes nothing |

**Exit-code map to re-verify when L4 changes:** `0` all OK · `1` generic ·
`2` missing dependency · `3` init step failed · `4` healthcheck failed ·
`5` unknown adapter · `6` manifest error.

**Destructive-action safety:** any new destructive command must offer a
`--dry-run`/`preview` and must not act without it being requested.

---

## T3 — Adapter contract (change surface: a new/changed adapter, L3)

| ID | Command | Expected |
| --- | --- | --- |
| T3.1 | `visectl describe <new_id> --json` | manifest complete; `commands` match the real CLI (compare against `<cmd> --help`) |
| T3.2 | `viseinit <new_id>` | dependency check → init steps → healthcheck all pass; exit `0` |
| T3.3 | run `thread` a second time (via `viseinit <new_id>`) | idempotent: no error, no duplicated side effects |
| T3.4 | break the adapter (e.g. remove a required file); `viseinit <new_id>; echo $?` | `4` (healthcheck failed) or `2` (missing dep) — a *non-zero*, meaningful code |
| T3.5 | `visectl run <new_id> <declared command>` | works exactly as declared (no hidden args) |
| T3.6 | if the adapter exposes knowledge: `visectl read <new_id> <topic>` | returns content |
| T3.7 | inspect `healthcheck` | uses `$PYTHON`, not a bare `python` |
| T3.8 | if it writes outside `VISE_ROOT`: `visectl manifest list` | the written path appears in the ledger (see T6) |
| T3.9 | `<new_id>` appears twice (same `adapter_id` in two manifests); `bash init/setup.sh` | registry generator warns and skips the duplicate; adapter count unchanged |

---

## T4 — Shared config / path resolution (change surface: L2)

| ID | Command | Expected |
| --- | --- | --- |
| T4.1 | `viseinit --all` after removing `core/adapter_paths.json` entry for a tool | resolver falls back to `PATH`; no crash |
| T4.2 | pin a wrong `min_version`; `viseinit <id>; echo $?` | `2` (missing dependency), message states the version shortfall |
| T4.3 | `.vise/tool_paths.json` shape | flat schema `{"python": "..."}` — not nested; `core/adapter_paths.json` stays nested |

---

## T5 — Parallelism & dependency topology (change surface: `viseinit` scheduling)

| ID | Command | Expected |
| --- | --- | --- |
| T5.1 | `viseinit --all --jobs 4` | all adapters OK; exit `0` |
| T5.2 | `viseinit --all --fail-fast` (after breaking one adapter) | first failure aborts; remaining marked `[SKIP]`; JSON summary has `skipped > 0` |
| T5.3 | declare `depends_on` between two adapters | dependency layer runs after its dependency; order respected |
| T5.4 | introduce a `depends_on` cycle | cyclic adapters marked `skipped`; non-cyclic adapters still run; no infinite loop |
| T5.5 | `viseinit --all --ndjson` | line-delimited progress events stream; last line is a terminal status |

---

## T6 — Uninstall completeness & ledger (change surface: anywhere writing outside `VISE_ROOT`)

Test uninstall **non-destructively first** — never run a real uninstall
against your working install. Use a sandbox: copy `core/manifest.py` and
`init/uninstall.sh` into a temp dir, point `HOME` at a fake home, and give the
sandbox its own `.vise/python`.

| ID | Command | Expected |
| --- | --- | --- |
| T6.1 | `visectl manifest list` | every outside-`VISE_ROOT` artifact Vise added is listed with its `source` |
| T6.2 | `visectl manifest verify; echo $?` | all `[OK]`, exit `0` |
| T6.3 | edit a recorded `created` file, then `visectl manifest verify; echo $?` | that entry `[DRIFT]`, exit `1` |
| T6.4 | sandbox `uninstall.sh --dry-run` | lists each `delete` / `revert` / `SKIP!`; **changes nothing** |
| T6.5 | sandbox real `uninstall.sh` (no drift) | recorded files removed, rc marker block gone, exit `0` |
| T6.6 | sandbox real `uninstall.sh` **with** a drifted `created` file | that file is **kept** and reported `SKIP!`; exit `3`; ledger retains its entry |
| T6.7 | after uninstall, `cat ~/.bashrc` (sandbox) | user content before/after the marker block intact; only the block removed |
| T6.8 | delete the ledger, re-run sandbox uninstall | fallback scan still removes Vise-generated wrappers (header + `VISE_ROOT` reference) |
| T6.9 | inspect any outside wrapper | keeps the `# Auto-generated by <source> - do not edit.` header (C9) |

**Exit-code note:** the ledger uses `1` for *drift detected* (`verify`) and `3`
for *drift skipped during removal* (`uninstall-apply`). These are distinct from
the `viseinit` `0`–`6` map — don't conflate them.

---

## T7 — Shell integration & safety (change surface: `core/vise_functions.sh`, L1/L4)

| ID | Command | Expected |
| --- | --- | --- |
| T7.1 | `grep -R "PROMPT_COMMAND" core init` | **no match** — implicit execution is prohibited (C8) |
| T7.2 | `grep -R "Auto-generated by" adapters` where wrappers exist | header present (fallback-scan marker) |
| T7.3 | rc injection markers | exactly `# >>> Vise >>>` / `# <<< Vise <<<`; matched exactly, other content untouched |
| T7.4 | shell reload behavior | reload happens only in response to a `vise*` command (trigger-based), never on shell open |
| T7.5 | `bash init/uninstall.sh --keep` | removes outside-`VISE_ROOT` artifacts, keeps `.vise/` + `.venv/` |

---

## T8 — Cross-platform / portability (change surface: shell scripts, file edits)

| ID | Command | Expected |
| --- | --- | --- |
| T8.1 | `grep -REn "sed -i" core init adapters` | no match — file edits use Python, not `sed -i` (C11) |
| T8.2 | `grep -REn "powershell\|\.ps1" core init adapters` | no match |
| T8.3 | re-read changed scripts for bash-only constructs | runs under Linux bash **and** Git Bash (e.g. `sha256sum`/`shasum` fallback where used) |
| T8.4 | if a core entry file was renamed/moved | every hardcoded reference updated: `init/setup.sh` template, README, AGENTS.md, DESIGN.md, and previously injected rc files (C12) |

---

## T9 — Docs-only (change surface: `*.md` only)

| ID | Command | Expected |
| --- | --- | --- |
| T9.1 | every command shown in the docs actually exists | `visectl`/`viseinit` accept it (spot-check against `--help`) |
| T9.2 | relative links resolve | e.g. `docs/DESIGN.md`, `docs/TEST-PLAN.md`, adapter `README.md` |
| T9.3 | no code or generated artifact changed | `git status --short` shows only `*.md` |
