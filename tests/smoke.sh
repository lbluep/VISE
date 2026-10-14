#!/usr/bin/env bash
# Vise smoke test — the machine-checkable subset of docs/TEST-PLAN.md (T0, T1).
#
# Runs against a checkout, installs the library, then asserts it is healthy and
# that the repository is unpolluted. Used by contributors locally
# (`bash tests/smoke.sh`) and by CI (.github/workflows/ci.yml) — one script,
# two entry points, so the logic is maintained in exactly one place.
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

echo "== Vise smoke test =="

echo "[1/6] install"
bash "$ROOT/init/setup.sh" >"$LOG" 2>&1 || fail "init/setup.sh exited non-zero"
pass "init/setup.sh completed"

echo "[2/6] discover"
"$PY" "$ROOT/core/visectl.py" list --json >/dev/null \
    || fail "visectl list --json failed"
pass "visectl list --json"

echo "[3/6] initialize + health"
"$PY" "$ROOT/core/viseinit.py" --all --json >/dev/null \
    || fail "viseinit --all --json exited non-zero"
pass "viseinit --all --json"

echo "[4/6] diagnose"
"$PY" "$ROOT/core/visectl.py" doctor >/dev/null \
    || fail "visectl doctor reported a failure"
pass "visectl doctor"

echo "[5/6] uninstall ledger integrity"
"$PY" "$ROOT/core/manifest.py" verify >/dev/null \
    || fail "manifest verify reported drift"
pass "manifest verify (no drift)"

echo "[6/6] generated artifacts stay out of git"
for p in .vise .venv; do
    git -C "$ROOT" check-ignore -q "$p" \
        || fail "$p is not gitignored (see CONTRIBUTING section 5)"
    if git -C "$ROOT" ls-files --error-unmatch "$p" >/dev/null 2>&1; then
        fail "$p is tracked in git (see CONTRIBUTING section 5)"
    fi
done
pass "generated artifacts ignored and untracked"

echo
echo "SMOKE OK"
