#!/usr/bin/env bash
# Vise smoke test — the machine-checkable subset of docs/TEST-PLAN.md (T0, T1, T10).
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
trap 'rm -f "$LOG"' EXIT

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

echo "[1/7] install"
bash "$ROOT/init/setup.sh" >"$LOG" 2>&1 || fail "init/setup.sh exited non-zero"
pass "init/setup.sh completed"

echo "[2/7] discover"
"$PY" "$ROOT/core/visectl.py" list --json >/dev/null \
    || fail "visectl list --json failed"
pass "visectl list --json"

echo "[3/7] initialize + health"
"$PY" "$ROOT/core/viseinit.py" --all --json >/dev/null \
    || fail "viseinit --all --json exited non-zero"
pass "viseinit --all --json"

echo "[4/7] diagnose"
"$PY" "$ROOT/core/visectl.py" doctor >/dev/null \
    || fail "visectl doctor reported a failure"
pass "visectl doctor"

echo "[5/7] uninstall ledger integrity"
"$PY" "$ROOT/core/manifest.py" verify >/dev/null \
    || fail "manifest verify reported drift"
pass "manifest verify (no drift)"

echo "[6/7] generated artifacts stay out of git"
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

echo "[7/7] adapter guard (C13): protect, roll back, sediment"
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

echo
echo "SMOKE OK"
