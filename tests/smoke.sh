#!/usr/bin/env bash
# Vise smoke test — the machine-checkable subset of docs/TEST-PLAN.md (T0, T1, T10, T11, T13, T14).
#
# Runs against a checkout, installs the library, then asserts it is healthy, that
# the adapter guard (C13) protects and rolls back an edit, and that the repository
# is left unpolluted. Used by contributors locally (`bash tests/smoke.sh`) and by
# CI (.github/workflows/ci.yml) — one script, two entry points, so the logic is
# maintained in exactly one place.
#
# Non-interactive: it calls the Python entry points directly, not the
# `visectl` / `viseinit` shell functions (those are only loaded by an
# interactive rc and are not available in CI).
#
# Exit 0 = all checks passed; non-zero = the first failing check.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
LOG="$(mktemp)"
SANDBOX=""
trap 'rm -f "$LOG"; [[ -z "$SANDBOX" ]] || rm -rf "$SANDBOX"' EXIT

pass() { printf '  [OK]   %s\n' "$1"; }
fail() {
    printf 'SMOKE FAIL: %s\n' "$1" >&2
    if [[ -s "$LOG" ]]; then
        echo '--- tail of init/setup.sh output ---' >&2
        tail -n 20 "$LOG" >&2
    fi
    exit 1
}
# expect <exit-code> <label> <command...>: asserts a specific exit code. The
# guard domain uses distinct codes (0/2/4/7) and an agent-facing contract
# depends on them, so the code itself is part of the assertion.
expect() {
    local want="$1" label="$2"; shift 2
    local got=0
    "$@" >/dev/null 2>&1 || got=$?
    [[ "$got" == "$want" ]] || fail "$label: exit $got, expected $want"
    pass "$label (exit $want)"
}

echo "== Vise smoke test =="

echo "[1/9] install"
bash "$ROOT/init/setup.sh" >"$LOG" 2>&1 || fail "init/setup.sh exited non-zero"
pass "init/setup.sh completed"

echo "[2/9] discover"
"$PY" "$ROOT/core/visectl.py" list --json >/dev/null \
    || fail "visectl list --json failed"
pass "visectl list --json"

echo "[3/9] initialize + health"
"$PY" "$ROOT/core/viseinit.py" --all --json >/dev/null \
    || fail "viseinit --all --json exited non-zero"
pass "viseinit --all --json"

echo "[4/9] diagnose"
"$PY" "$ROOT/core/visectl.py" doctor >/dev/null \
    || fail "visectl doctor reported a failure"
pass "visectl doctor"

echo "[5/9] uninstall ledger integrity"
"$PY" "$ROOT/core/manifest.py" verify >/dev/null \
    || fail "manifest verify reported drift"
pass "manifest verify (no drift)"

echo "[6/9] generated artifacts stay out of git"
# Only meaningful in a git worktree; a zip/tarball install has no repository to
# pollute, and that install is exactly what the guard (step 7) is designed for.
if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    for p in .vise .venv; do
        git -C "$ROOT" check-ignore -q "$p" \
            || fail "$p is not gitignored (see CONTRIBUTING section 5)"
        if git -C "$ROOT" ls-files --error-unmatch "$p" >/dev/null 2>&1; then
            fail "$p is tracked in git (see CONTRIBUTING section 5)"
        fi
    done
    pass "generated artifacts ignored and untracked"
else
    echo "  [SKIP] not a git worktree - nothing to ignore"
fi

echo "[7/9] adapter guard (C13): protect, roll back, sediment"
# Driven from the reference adapter's own manifest so a rename or an entry-point
# move cannot silently weaken the assertions below.
MAN="$ROOT/adapters/ExampleAdapter_reference/vise.adapter.json"
ADIR="$ROOT/adapters/ExampleAdapter_reference"
AID="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["adapter_id"])' "$MAN")"
CLI_REL="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["entry_points"]["cli"])' "$MAN")"
CLI_FILE="$ADIR/$CLI_REL"
MARK="guard smoke marker"

# Snapshots and patches are generated state (C7); clearing the reference
# adapter's entries makes this step deterministic however often smoke has run.
rm -rf "$ROOT/.vise/snapshots/$AID" "$ROOT/.vise/patches/$AID"

expect 0 "guard: every adapter is protected" \
    "$PY" "$ROOT/core/visectl.py" adapter check
expect 0 "baseline: every adapter is clean" \
    "$PY" "$ROOT/core/visectl.py" adapter status
expect 4 "restore without a snapshot is unavailable" \
    "$PY" "$ROOT/core/visectl.py" adapter restore "$AID"
expect 2 "blank --message is rejected (the reason is auditable)" \
    "$PY" "$ROOT/core/visectl.py" adapter snapshot "$AID" -m ""
expect 2 "unknown adapter is a usage error" \
    "$PY" "$ROOT/core/visectl.py" adapter snapshot no_such_adapter -m "x"

printf '\n# %s\n' "$MARK" >>"$CLI_FILE"
expect 7 "run refuses an unprotected edit (C13 gate)" \
    "$PY" "$ROOT/core/visectl.py" run "$AID" list
expect 7 "promote refuses an unprotected edit" \
    "$PY" "$ROOT/core/visectl.py" adapter promote "$AID" -m "smoke: sediment"
expect 7 "check names the unprotected adapter" \
    "$PY" "$ROOT/core/visectl.py" adapter check
expect 1 "status reports the drift" \
    "$PY" "$ROOT/core/visectl.py" adapter status
expect 1 "status --json reports it with the same code (the code never depends on --json)" \
    "$PY" "$ROOT/core/visectl.py" adapter status --json
expect 0 "run --allow-modified is the escape hatch" \
    "$PY" "$ROOT/core/visectl.py" run --allow-modified "$AID" list

expect 0 "snapshot opens the rollback point" \
    "$PY" "$ROOT/core/visectl.py" adapter snapshot "$AID" -m "smoke: exercise the guard"
expect 0 "run is permitted once the edit is protected" \
    "$PY" "$ROOT/core/visectl.py" run "$AID" list
expect 0 "promote sediments the change as a patch" \
    "$PY" "$ROOT/core/visectl.py" adapter promote "$AID" -m "smoke: sediment"

shopt -s nullglob
patches=("$ROOT/.vise/patches/$AID"/*.patch)
shopt -u nullglob
[[ ${#patches[@]} -gt 0 ]] || fail "promote reported success but wrote no patch"
grep -qF "$MARK" "${patches[0]}" || fail "the patch does not carry the promoted change"
pass "patch written and carries the change"

expect 0 "restore --base rolls the edit back" \
    "$PY" "$ROOT/core/visectl.py" adapter restore "$AID" --base
expect 0 "the tree matches the baseline byte-for-byte after restore" \
    "$PY" "$ROOT/core/visectl.py" adapter diff "$AID"
if grep -qF "$MARK" "$CLI_FILE"; then
    fail "restore left the edited line in place"
fi
pass "the edit is gone"

expect 0 "status is clean again" \
    "$PY" "$ROOT/core/visectl.py" adapter status
expect 4 "promote on a clean adapter is a no-op" \
    "$PY" "$ROOT/core/visectl.py" adapter promote "$AID" -m "smoke: nothing to add"

out="$("$PY" "$ROOT/core/baseline.py" establish --all 2>/dev/null)"
grep -qF '[KEEP]' <<<"$out" \
    || fail "re-running establish refreshed an existing baseline (setup must never bless an edit)"
pass "a re-run keeps the baseline, never refreshes it"

if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    [[ -z "$(git -C "$ROOT" status --short -- adapters)" ]] \
        || fail "the guard left adapters/ modified in git"
    pass "adapters/ is unmodified in git"
fi

rm -rf "$ROOT/.vise/snapshots/$AID" "$ROOT/.vise/patches/$AID"

echo "[8/9] release manifest + staged upgrade (C13: per-file reconcile)"
# The committed manifest must always describe the tree it sits in.
expect 0 "release manifest matches the tree" \
    "$PY" "$ROOT/core/release.py" check

# An isolated install (OLD, current version) and a staged release tree (NEW,
# next version): no git, no venv - the copied entry points are driven by this
# .venv's interpreter, and each tree's modules resolve VISE_ROOT to itself.
SANDBOX="$(mktemp -d)"
OLD="$SANDBOX/old" NEW="$SANDBOX/new"
mkdir -p "$OLD" "$NEW"
for dest in "$OLD" "$NEW"; do
    tar -C "$ROOT" \
        --exclude='./.git' --exclude='./.venv' --exclude='./.vise' \
        --exclude='./adapters/.vise' \
        -cf - . | tar -C "$dest" -xf -
done
"$PY" "$OLD/init/register_adapters.py" >/dev/null
"$PY" "$OLD/core/baseline.py" establish --all >/dev/null \
    || fail "sandbox: baseline establish failed"
"$PY" "$OLD/core/baseline.py" version > "$OLD/.vise/version"
pass "sandbox install prepared (git-free, venv-free)"

CUR="$("$PY" "$OLD/core/baseline.py" version)"
NEXT="$("$PY" -c "v='$CUR'.split('.'); v[-1]=str(int(v[-1])+1); print('.'.join(v))")"
SAD="$OLD/adapters/ExampleAdapter_reference"
SND="$NEW/adapters/ExampleAdapter_reference"

# NEW: the vendor moves - update one file, add one file, drop one file.
VISE_SMOKE_NEW="$NEW" VISE_SMOKE_CUR="$CUR" VISE_SMOKE_NEXT="$NEXT" \
    "$PY" - <<'PY'
import os
from pathlib import Path
p = Path(os.environ["VISE_SMOKE_NEW"]) / "core" / "version.py"
s = p.read_text()
old = f'VERSION = "{os.environ["VISE_SMOKE_CUR"]}"'
assert old in s, "core/version.py shape changed"
p.write_text(s.replace(old, f'VERSION = "{os.environ["VISE_SMOKE_NEXT"]}"'))
PY
printf '\n# vendor update\n' >> "$SND/src/example_tool/__init__.py"
printf '\n# vendor conflict-side edit\n' >> "$SND/src/example_tool/main.py"
mkdir -p "$SND/docs" && printf 'new in this release\n' > "$SND/docs/NEW.md"
rm "$SND/vise_extensions.sh" "$SND/src/example_tool/thread_cli.py"
"$PY" "$NEW/core/release.py" generate --root "$NEW" >/dev/null \
    || fail "sandbox: release manifest generation failed"

# OLD: a local edit where the vendor also moved (conflict), a local edit the
# vendor left alone (drift to keep), a file that is nobody's (user file), and
# a vendor file the user deleted on their own.
printf '\n# user conflict edit\n' >> "$SAD/src/example_tool/main.py"
printf '\n# user local edit\n' >> "$SAD/README.md"
printf 'mine\n' > "$SAD/notes.txt"
rm "$SAD/vise_extensions.sh"

# Non-staged reconcile against the install's own manifest (same version): a
# pure re-stamp must keep every baseline self-consistent even where the disk
# has drifted on a vendor-unchanged file - the baseline tree must hold vendor
# bytes, never the drift (C13: the old reference stays vendor-sourced). A
# vendor file the user deleted comes back from the baseline tree: the vendor
# has not changed it, so those bytes are still the reference.
up_out="$("$PY" "$OLD/core/baseline.py" upgrade 2>&1)" || {
    printf '%s\n' "$up_out" >&2
    fail "non-staged reconcile on the current release: see rows above"
}
pass "non-staged reconcile on the current release applies cleanly"
expect 0 "every rewritten baseline is self-consistent" \
    "$PY" "$OLD/core/baseline.py" verify
[[ -f "$SAD/vise_extensions.sh" ]] \
    || fail "a vendor-unchanged deleted file was not restored from the baseline"
pass "missing vendor file restored from the baseline tree"

# A vendor-dropped file the user also deleted: neither side has bytes, the
# staged upgrade must simply record the deletion (no crash, no baseline entry).
rm "$SAD/src/example_tool/thread_cli.py"

expect 1 "dry-run flags the conflict, applies nothing" \
    "$PY" "$OLD/core/baseline.py" upgrade --staged "$NEW" --dry-run
[[ ! -e "$SAD/docs/NEW.md" ]] || fail "dry-run wrote the addition"
grep -qF '# user conflict edit' "$SAD/src/example_tool/main.py" \
    || fail "dry-run touched the conflicted file"
pass "dry-run left disk and baselines untouched"

expect 1 "staged upgrade: vendor moves applied, conflict reviewed" \
    "$PY" "$OLD/core/baseline.py" upgrade --staged "$NEW"
grep -qF '# vendor update' "$SAD/src/example_tool/__init__.py" \
    || fail "vendor update was not applied"
grep -qF '# user conflict edit' "$SAD/src/example_tool/main.py" \
    || fail "the conflicted edit was overwritten (must stay in place)"
! grep -qF '# vendor conflict-side edit' "$SAD/src/example_tool/main.py" \
    || fail "the release's side of the conflict was force-applied"
[[ -f "$SAD/docs/NEW.md" ]] || fail "vendor addition missing"
[[ ! -e "$SAD/vise_extensions.sh" ]] || fail "vendor deletion not applied"
grep -qF '# user local edit' "$SAD/README.md" \
    || fail "local drift on a vendor-untouched file was lost"
[[ -f "$SAD/notes.txt" ]] || fail "a user file was touched"
pass "per-file matrix: apply / add / delete / keep / user / review"

"$PY" - "$OLD" "$AID" "$NEXT" <<'PY' || fail "baseline provenance missing"
import json, sys
m = json.load(open(f"{sys.argv[1]}/.vise/baseline/{sys.argv[2]}/manifest.json"))
assert m["release"] == sys.argv[3] and m["vendor_sourced"] is True, m
PY
pass "baseline records release + vendor_sourced provenance"

expect 0 "baselines stay self-consistent after the staged upgrade" \
    "$PY" "$OLD/core/baseline.py" verify

expect 2 "hash-only provider without --tree-from is a usage error" \
    "$PY" "$OLD/core/baseline.py" establish "$AID" --provider release --refresh
expect 2 "bytes failing manifest verification never become a baseline" \
    "$PY" "$OLD/core/baseline.py" establish "$AID" --provider release \
        --refresh --tree-from "$SND"
expect 0 "the refused establishes left the baseline intact" \
    "$PY" "$OLD/core/baseline.py" verify "$AID"

echo "$NEXT" > "$OLD/.vise/version"
"$PY" "$NEW/core/release.py" generate --root "$NEW" --release 0.0.0 >/dev/null
expect 2 "a downgrade is refused" \
    "$PY" "$OLD/core/baseline.py" upgrade --staged "$NEW"

"$PY" "$NEW/core/release.py" generate --root "$NEW" >/dev/null
printf '\n# tampered after manifest generation\n' >> "$SND/README.md"
expect 2 "a staged tree disagreeing with its manifest is refused" \
    "$PY" "$OLD/core/baseline.py" upgrade --staged "$NEW"

rm "$NEW/vise.release.json"
expect 1 "no release manifest falls back to whole-adapter verdicts" \
    "$PY" "$OLD/core/baseline.py" upgrade --staged "$NEW"

rm -rf "$SANDBOX"
SANDBOX=""

echo "[9/9] wave1 governance: write lock, audit log, MCP surface, scaffold refresh"

# --- Single-writer lock (Linux fcntl; other platforms degrade to no-op) ---
if [[ "$(uname -s)" == Linux* ]]; then
    "$PY" -c 'import sys,time; sys.path.insert(0,"'"$ROOT"'/core"); import vise_lock; l=vise_lock.write_lock("smoke holder"); l.__enter__(); time.sleep(3)' &
    HOLDER=$!
    sleep 0.5
    expect 75 "lock contention exits 75 (EX_TEMPFAIL, retryable)" \
        env VISE_LOCK_TIMEOUT=0 "$PY" "$ROOT/init/register_adapters.py"
    wait "$HOLDER"
    expect 0 "the lock is available again immediately after the holder exits" \
        "$PY" "$ROOT/init/register_adapters.py"
else
    echo "  [SKIP] write-lock contention is Linux (fcntl) only"
fi

# --- Execution audit: every run (incl. refusals) is recorded, values are not ---
expect 0 "an audited adapter run succeeds" \
    "$PY" "$ROOT/core/visectl.py" run "$AID" list
"$PY" - "$ROOT" <<'PY' || fail "audit query did not return the expected record"
import json, subprocess, sys
root = sys.argv[1]
p = subprocess.run([sys.executable, f"{root}/core/visectl.py", "audit",
                    "--event", "run", "--adapter", "example_tool",
                    "--limit", "1", "--json"],
                   capture_output=True, text=True, cwd=root)
assert p.returncode == 0, p.stderr
rows = json.loads(p.stdout)
assert rows and rows[0]["event"] == "run" and rows[0]["rc"] == 0, rows
assert "detail" in rows[0] and "duration_ms" in rows[0], rows
PY
pass "audit records event/adapter/capability (never argument values)"

# --- MCP bridge: 8 tools, annotations, destructive gate on restore ---
"$PY" - "$ROOT" <<'PY' || fail "MCP bridge assertions failed"
import json, subprocess, sys
root = sys.argv[1]
msgs = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "vise_restore",
                "arguments": {"adapter_id": "example_tool",
                              "target": "base"}}},
]
p = subprocess.run([sys.executable, f"{root}/core/vise_mcp.py"],
                   input="\n".join(json.dumps(m) for m in msgs) + "\n",
                   capture_output=True, text=True, cwd=root, timeout=60)
assert p.returncode == 0, p.stderr
replies = [json.loads(line) for line in p.stdout.splitlines()]
assert replies[0]["result"]["serverInfo"]["name"] == "vise"
tools = {t["name"]: t for t in replies[1]["result"]["tools"]}
expected = {"vise_list", "vise_describe", "vise_doctor", "vise_read",
            "vise_status", "vise_run", "vise_search",
            "vise_snapshot", "vise_restore"}
assert set(tools) == expected, set(tools)
assert tools["vise_read"]["annotations"]["readOnlyHint"] is True
assert tools["vise_search"]["annotations"]["readOnlyHint"] is True
assert tools["vise_restore"]["annotations"]["destructiveHint"] is True
assert replies[2]["result"]["isError"] is True, "restore must demand confirm"
PY
pass "MCP: 9 tools, readOnly/destructive annotations, restore confirm gate"

# --- Scaffold refreshes the release manifest in the same locked operation ---
expect 2 "scaffold still rejects a bad name" \
    "$PY" "$ROOT/core/scaffold.py" lower_case
# The committed manifest gets rewritten by scaffold; back it up so cleanup
# restores byte-identical content (regenerate would only churn generated_at).
REL_BAK="$(mktemp)"
cp "$ROOT/vise.release.json" "$REL_BAK"
expect 0 "scaffold creates, registers AND refreshes the release manifest" \
    "$PY" "$ROOT/core/visectl.py" adapter scaffold DemoScratch --json
expect 0 "release check passes with no manual generate after scaffold" \
    "$PY" "$ROOT/core/release.py" check
rm -rf "$ROOT/adapters/DemoScratch"
"$PY" "$ROOT/init/register_adapters.py" >/dev/null
cp "$REL_BAK" "$ROOT/vise.release.json"
rm -f "$REL_BAK"
expect 0 "tree restored: release manifest matches again" \
    "$PY" "$ROOT/core/release.py" check

echo "[10/10] wave2: search, skill bridge, bundle export + import (D5)"
# --- Wave-2: search, skill bridge, bundle export/import ---
expect 0 "search finds a known command" \
    "$PY" "$ROOT/core/visectl.py" search encode --json
expect 1 "search no-match exits 1" \
    "$PY" "$ROOT/core/visectl.py" search zzz_nomatch
expect 0 "search --kind doc filters to doc hits" \
    sh -c "'$PY' '$ROOT/core/visectl.py' search --kind doc adapter --json | '$PY' -c 'import json,sys; rows=json.load(sys.stdin); assert rows and all(r[\"kind\"]==\"doc\" for r in rows)'"

SK_DIR="$(mktemp -d)"
SK_DIR2="$(mktemp -d)"
expect 0 "skill export example_tool" \
    "$PY" "$ROOT/core/visectl.py" skill export example_tool --out "$SK_DIR"
[[ -f "$SK_DIR/SKILL.md" ]] || fail "skill export did not write SKILL.md"
# knowledge-type skill
KNOW_DIR="$(mktemp -d)"
cat >"$KNOW_DIR/SKILL.md" <<'EOF'
---
name: SmokeKnow
description: smoke knowledge skill
category: tools
---
body content served by read skill
EOF
expect 0 "skill import (knowledge)" \
    "$PY" "$ROOT/core/visectl.py" skill import "$KNOW_DIR" --name SmokeKnow
expect 0 "knowledge skill self-test" \
    "$PY" "$ROOT/core/visectl.py" run smoke_know --self-test
expect 0 "knowledge skill read serves the body" \
    sh -c "'$PY' '$ROOT/core/visectl.py' run smoke_know read skill | grep -q 'body content served by read skill'"
expect 0 "imported knowledge adapter is clean" \
    sh -c "'$PY' '$ROOT/core/visectl.py' adapter status smoke_know | grep -q clean"
# scripted-type skill (re-import the exported example_tool)
expect 0 "skill import (scripted)" \
    "$PY" "$ROOT/core/visectl.py" skill import "$SK_DIR" --name DemoSkill
expect 0 "scripted skill self-test" \
    "$PY" "$ROOT/core/visectl.py" run demo_skill --self-test
expect 0 "scripted skill --help lists command(s)" \
    sh -c "'$PY' '$ROOT/core/visectl.py' run demo_skill --help | grep -q main"
# cleanup the two imported adapters and restore registry/release
rm -rf "$ROOT/adapters/SmokeKnow" "$ROOT/adapters/DemoSkill"
"$PY" "$ROOT/init/register_adapters.py" >/dev/null
"$PY" "$ROOT/core/release.py" generate >/dev/null
rm -rf "$SK_DIR" "$SK_DIR2" "$KNOW_DIR"

BUNDLE="/tmp/vise-smoke.bundle.tar.gz"
BUNDLE2="/tmp/vise-smoke.bundle2.tar.gz"
rm -f "$BUNDLE" "$BUNDLE2"
expect 0 "bundle export" \
    "$PY" "$ROOT/core/visectl.py" bundle export --out "$BUNDLE" --json
# D5 reconcile: importing the same tree back keeps every file, rc 0.
expect 0 "bundle import (same tree) reconciles to all-keep" \
    "$PY" "$ROOT/core/visectl.py" bundle import "$BUNDLE"
expect 0 "release manifest matches after import" \
    "$PY" "$ROOT/core/release.py" check

# Three-way mismatch -> review. Build a bundle that carries one variant of a
# new file while the disk holds another: neither side can attribute the change.
VARIANT="$ROOT/adapters/NoteKeeper/notes_extra.md"
printf 'bundle variant\n' > "$VARIANT"
expect 0 "bundle export (carries one variant)" \
    "$PY" "$ROOT/core/visectl.py" bundle export --out "$BUNDLE2"
printf 'local variant\n' > "$VARIANT"
SNAP_COUNT="$(find "$ROOT/.vise/snapshots" -type f 2>/dev/null | wc -l)"
expect 2 "bundle import --dry-run reports review without writes" \
    "$PY" "$ROOT/core/visectl.py" bundle import "$BUNDLE2" --dry-run
grep -q 'local variant' "$VARIANT" || fail "--dry-run modified the tree"
[[ "$(find "$ROOT/.vise/snapshots" -type f 2>/dev/null | wc -l)" == "$SNAP_COUNT" ]] \
    || fail "--dry-run created snapshots"
expect 2 "bundle import (ours) leaves undecidable paths for review" \
    "$PY" "$ROOT/core/visectl.py" bundle import "$BUNDLE2"
grep -q 'local variant' "$VARIANT" || fail "review import overwrote on-disk bytes"
expect 0 "bundle import --conflicts=theirs resolves review paths" \
    "$PY" "$ROOT/core/visectl.py" bundle import "$BUNDLE2" --conflicts=theirs
grep -q 'bundle variant' "$VARIANT" || fail "theirs did not take the bundle bytes"
grep -rlq '"triggered_by": "bundle_import"' "$ROOT/.vise/snapshots/note_keeper" 2>/dev/null \
    || fail "merge import did not auto-snapshot the adapter"
# The first bundle predates the variant file: importing it removes it again.
expect 0 "bundle import applies vendor deletions" \
    "$PY" "$ROOT/core/visectl.py" bundle import "$BUNDLE"
[[ ! -e "$VARIANT" ]] || fail "vendor-deleted file was not removed"

# Delete an adapter outright (dir + baseline), then restore from the bundle.
rm -rf "$ROOT/adapters/JsonTool" "$ROOT/.vise/baseline/json_tool"
expect 0 "bundle import restores a deleted adapter" \
    "$PY" "$ROOT/core/visectl.py" bundle import "$BUNDLE"
[[ -f "$ROOT/adapters/JsonTool/vise.adapter.json" ]] || fail "adapter not restored"
[[ -f "$ROOT/.vise/baseline/json_tool/manifest.json" ]] || fail "restored adapter has no baseline"
[[ -x "$ROOT/adapters/JsonTool/thread" ]] || fail "import lost the adapter's executable bit"
expect 0 "restored adapter is clean" \
    "$PY" "$ROOT/core/visectl.py" adapter status json_tool
expect 0 "release check passes after restore" \
    "$PY" "$ROOT/core/release.py" check

# The review/theirs dance re-baselined note_keeper from edited bytes
# (vendor_sourced=False); reset it so the tree keeps its pristine provenance.
rm -rf "$ROOT/.vise/baseline/note_keeper"
"$PY" "$ROOT/core/baseline.py" establish note_keeper >/dev/null
expect 0 "note_keeper clean after baseline provenance reset" \
    "$PY" "$ROOT/core/visectl.py" adapter status note_keeper

# tamper detection
"$PY" - <<PY
import tarfile, io
with tarfile.open("$BUNDLE", "r:gz") as t:
    data = {m.name: t.extractfile(m).read() for m in t.getmembers() if m.isfile()}
k = "vise.release.json"
data[k] = bytes([data[k][0] ^ 0xFF]) + data[k][1:]
with tarfile.open("/tmp/vise-smoke.tampered.tar.gz", "w:gz") as o:
    for n, b in data.items():
        ti = tarfile.TarInfo(n); ti.size = len(b); o.addfile(ti, io.BytesIO(b))
PY
rc=0
"$PY" "$ROOT/core/visectl.py" bundle import /tmp/vise-smoke.tampered.tar.gz >/dev/null 2>&1 || rc=$?
[[ $rc -eq 2 ]] || fail "tampered bundle must fail verification (rc 2)"
expect 0 "audit records bundle_import events" \
    "$PY" "$ROOT/core/visectl.py" audit --event bundle_import --json
rm -f "$BUNDLE" "$BUNDLE2" /tmp/vise-smoke.tampered.tar.gz
pass "search / skill bridge / bundle export + import (D5)"

echo
echo "SMOKE OK"
