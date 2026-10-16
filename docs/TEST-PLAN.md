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

**Automated:** `bash tests/smoke.sh` runs T0 + T1 (plus the core of T10, step 7, and T11, step 8)
in one command — the same script CI executes. The individual commands below are
what it asserts.

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

## T7 — Shell integration & safety (change surface: `core/vise_functions.sh`, L4)

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

---

## T10 — Adapter guard: protect / roll back / promote (change surface: `core/baseline.py`, `core/adapter_guard.py`, L4 `visectl adapter`, `init/setup.sh`)

Change surface note: run this group **both** in a normal clone and in a
sandbox that has **no `.git`** — the guard must not depend on git. Build the
second one with:

```bash
mkdir /tmp/vise-nogit && git archive --format=tar HEAD | tar -x -C /tmp/vise-nogit
cd /tmp/vise-nogit && bash init/setup.sh
```

**Automated:** `bash tests/smoke.sh` step 7 exercises the gate, rollback and
sediment core (T10.1, T10.2, T10.4–T10.7, T10.9, T10.12, T10.14, T10.15, T10.20,
T10.21) — several of those only in part; the remaining rows are run manually
from a checkout.

| ID | Command | Expected |
| --- | --- | --- |
| T10.1 | after `bash init/setup.sh`, `visectl adapter status` | every adapter `clean`, exit `0` |
| T10.2 | re-run `bash init/setup.sh` | baselines **kept, not refreshed** (`[KEEP]`; `established_at` unchanged) — setup never blesses an edited tree |
| T10.3 | edit an adapter file, then `visectl adapter diff <id>` | the file listed as changed; exit `1` |
| T10.4 | with that edit present, `visectl run <id> list` | **refused**, exit `7`; stderr names snapshot/restore/rebaseline |
| T10.5 | `visectl adapter check <id>` and `visectl doctor` | check exits `7`; doctor reports the adapter `[FAIL]` with the guard note |
| T10.6 | `visectl run --allow-modified <id> list` | adapter executes normally (escape hatch) |
| T10.7 | `visectl adapter snapshot <id> -m "..."` then `visectl run <id> list` | snapshot created; run now permitted |
| T10.8 | `visectl adapter snapshot <id> -m "..."` twice (no `--force`) | second call reports `already-open`, no extra generation |
| T10.9 | `visectl adapter restore <id> --base` after an edit | tracked files byte-identical to baseline (`git diff` empty if git is present); untracked `*.bak`/`__pycache__` left alone |
| T10.10 | break the adapter (fail its `healthcheck`), then `visectl adapter restore <id>` | restore still succeeds — it never executes adapter code |
| T10.11 | `visectl adapter restore <id> --gen 1` with ≥2 snapshots | restores the second-newest generation (gen `0` = newest) |
| T10.12 | `visectl adapter promote <id> -m "..."` on a modified adapter | `.vise/patches/<id>/*.patch` written; prints "committing is a separate act"; **no git commit made** |
| T10.13 | `patch -p1 -d adapters/<Name> < .vise/patches/<id>/*.patch` on a restored tree | patch applies cleanly; result matches the promoted state |
| T10.14 | `visectl adapter promote <id> -m "..."` on a **clean** adapter | `[NOOP] nothing-to-promote`, exit `4` |
| T10.15 | `visectl adapter promote <id> -m "..."` with unprotected drift | **refused**, exit `7` (C13 gate) |
| T10.16 | 6+ snapshots of one adapter (mix of open/closed) | newest 5 kept, **plus** the newest open snapshot regardless of age; older snapshots — including older open ones — are pruned, so `.vise/` stays bounded |
| T10.17 | `visectl adapter rebaseline <id>` while modified | `[REFUSE]`, exit `7`; with `--force` it accepts the tree, closes open snapshots, and the adapter becomes `clean` |
| T10.18 | `.gitignore` check for `.vise/baseline`, `.vise/snapshots`, `.vise/patches` | all ignored and untracked (C7) |
| T10.19 | non-git sandbox: full lifecycle `snapshot → edit → run → restore → promote → replay` | identical outcomes to a clone; no `git` invocation anywhere in the guard |
| T10.20 | `visectl adapter status --json` while an adapter is drifted | exit `1`, identical to the human-output run — the exit code reflects the outcome, never the requested format |
| T10.21 | `visectl adapter snapshot <id> -m ""` (blank reason) | rejected, exit `2` — a blank reason carried no audit trail |
| T10.22 | tamper a file inside `.vise/baseline/<id>/tree/` | `visectl adapter verify <id>` reports the file, exit `1`; `doctor` fails with a rebuild hint; `establish <id> --refresh` restores integrity (verify exit `0`). Unbaselined adapters are `[SKIP]`, not corruption |
| T10.23 | bump `core/version.py` **without regenerating** `vise.release.json`, touch one adapter's source (vendor change), re-run `setup.sh` | upgrade detected (`vX -> vY`); the stale manifest triggers whole-adapter fallback with a warning — clean adapters `[REFRESH]` re-stamped, the touched one `[REVIEW]` with drift summary and guidance; `.vise/version` updated; setup still exits `0`. (With a regenerated manifest the same scenario reconciles per file instead — see T11.11–T11.13.) |
| T10.24 | after T10.23, `visectl adapter restore <id> --base` on the not-yet-rebaselined adapter | `[REFUSE]`, exit `7` — the baseline still holds the old release's bytes; `--force` restores them; a refreshed adapter's `restore --base` works normally. Baselines without `vise_version` (pre-stamp installs) restore without refusal |
| T10.25 | snapshot taken under vX, then bump version and re-run setup | `adapter list` flags the snapshot `!=cur`; `restore` (no `--base`) of that generation is refused, exit `7`; `--force` restores it |
| T10.26 | manually create `.vise/{baseline,snapshots,patches,state}/ghost_tool/`, remove `ghost_tool` from `adapters/` so it is absent from registry | `visectl doctor` prints "Orphan state" with `ghost_tool` (exit `0` — not a failure); `clean --state --dry-run` lists all four prefixed (`baseline/ghost_tool`, `snapshots/ghost_tool`, `patches/ghost_tool`, `state/ghost_tool`); actual `clean --state` removes them; subsequent `doctor` no longer reports orphan |

**Exit-code note:** the guard uses `7` for *refused* (unprotected drift,
`rebaseline` needing `--force`, cross-version `restore --base`), `4` for
*action impossible* (no such generation / nothing to promote) and `2` for
usage/input errors. These are distinct from the `viseinit` `0`–`6` map and
from the ledger's `1`/`3`.

---

## T11 — Release manifest and staged upgrade (change surface: `core/release.py`, `core/baseline.py`, `init/setup.sh`, `vise.release.json`, `visectl adapter upgrade`)

Run this group in both a normal clone and a sandbox built with `git archive`:

```bash
mkdir /tmp/vise-nogit && git archive --format=tar HEAD | tar -x -C /tmp/vise-nogit
cd /tmp/vise-nogit && bash init/setup.sh
```

**Automated:** `bash tests/smoke.sh` step 8 exercises the manifest check,
staged per-file reconcile, downgrade refusal, staged-tree tamper detection,
hash-only establish verification, provenance, and legacy fallback (T11.1,
T11.2, T11.4, T11.6, T11.10–T11.15, T11.19–T11.21).

| ID | Command | Expected |
| --- | --- | --- |
| T11.1 | `.venv/bin/python core/release.py check` | exit `0` — the committed manifest describes the tree it sits in |
| T11.2 | `core/release.py check` after editing a file under `adapters/` | mismatch reported (changed path listed with `M`); exit `1` |
| T11.3 | `core/release.py check` after bumping `core/version.py` without regenerating | `release_mismatch` reported: manifest vs tree disagree; exit `1` |
| T11.4 | `core/release.py generate` after adapter changes | atomic write (no partially-written file); a subsequent `check` passes |
| T11.5 | `.gitignore` check for `vise.release.json` | **tracked** in git (it is an ordinary committed file) |
| T11.6 | `setup.sh --upgrade-from /tmp/staged-tree` with a clean tree | staged reconciliation exit `0`; overlay applied; `.vise/version` stamped with the NEW version |
| T11.7 | `setup.sh --upgrade-from /tmp/staged-tree` with a conflicting edit | staged reconciliation exit `1`; conflict path stays in place; review guidance printed; overlay still applied (setup continues) |
| T11.8 | `setup.sh --upgrade-from` pointing at a directory with no `vise.release.json` | setup aborts, exit `1` |
| T11.9 | `baseline.py upgrade --staged /tmp/new` where the staged `vise.release.json` describes a version lower than the install stamp | **refused**, exit `2` (downgrade) |
| T11.10 | `baseline.py upgrade --staged /tmp/new` where a staged file disagrees with its own manifest (e.g. manifest generated, then file edited without re-gen) | **refused**, exit `2` — staged tree is treated as corrupt input, not a baseline source |
| T11.11 | `baseline.py upgrade` (non-staged) after a file change but `vise.release.json` already matches the current code version | per-file matrix runs: unchanged clean adapters `[KEEP]` or `[REFRESH]` (stamp-only), changed files `[REVIEW]`; exit `1` if any review |
| T11.12 | `baseline.py upgrade --staged` dry-run then real | dry-run prints the same review list and exit `1` but **touches nothing** (disk, baselines, and the staged tree all unchanged) |
| T11.13 | `baseline.py upgrade --staged` with: vendor-update file, vendor-added file, vendor-deleted file, untouched-by-vendor drift, three-way-conflict, user-only file | update applied, addition installed, deletion removed, drift kept, conflict left in place with review reason, user file untouched |
| T11.14 | after T11.13, `baseline.py verify` on the conflict adapter | the baseline tree matches its manifest (the manifest records the new release's bytes for the conflict path; the on-disk drift is NOT in the baseline) |
| T11.15 | `baseline.py establish <id> --provider release --refresh --tree-from <dir>` where `<dir>` bytes mismatch the manifest | **refused**, exit `2`; the existing baseline is untouched |
| T11.16 | after a staged upgrade, `baseline.py adapter status` | adapters that had review paths show `modified` (disk drift vs baseline); cleanly refreshed adapters show `clean`; `release` and `vendor_sourced` appear in `--json` manifest row |
| T11.17 | `baseline.py upgrade --staged` when an adapter's baseline is `vendor_sourced: false` | the adapter is reported as a whole-adapter `[REVIEW]` with reason about non-vendor-sourced baseline; per-file classify is skipped for that adapter |
| T11.18 | `baseline.py upgrade --staged` for an adapter absent from the new manifest | `[REVIEW]` with reason "not covered by the release manifest"; files left in place; orphan machinery handles residue after manual removal |
| T11.19 | `upgrade --staged` where the vendor drops a file the user already deleted | no crash; counted as deleted; path absent from the new baseline (there are no bytes to record) |
| T11.20 | non-staged `upgrade` on the current release with local drift on vendor-unchanged files | every rewritten baseline passes `verify` — the baseline tree holds vendor bytes (from the old baseline tree), never the drift; `restore --base` cannot hand back user bytes |
| T11.21 | non-staged `upgrade` where a vendor file is missing on disk but unchanged since the baseline | restored from the baseline tree (no staged bytes needed); a missing file the vendor *did* change becomes review — neither disk nor baseline can supply the new bytes |
