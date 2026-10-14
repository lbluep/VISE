# Vise shell functions loader - sourced from shell startup (~/.bashrc).
# Defines visectl and viseinit as shell functions.
# Does NOT loop-source adapter shortcuts — adapter CLI is accessed via
# `visectl run <id>` (P0-1 fix: eliminates per-adapter naming conflicts).
# shellcheck shell=bash

CORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VISE_ROOT="$(cd "$CORE_DIR/.." && pwd)"

# Interpreter chosen by init/setup (falls back to python3)
VISE_PYTHON="$(cat "$VISE_ROOT/.vise/python" 2>/dev/null || command -v python3)"
export VISE_PYTHON

visectl()  { _vise_maybe_reload; "$VISE_PYTHON" "$CORE_DIR/visectl.py"  "$@"; }
viseinit() { _vise_maybe_reload; "$VISE_PYTHON" "$CORE_DIR/viseinit.py" "$@"; }

# visereload - manually re-source this loader (picks up new interpreters,
# renamed functions, etc. without opening a new shell).
visereload() {
    # shellcheck disable=SC1090
    source "$CORE_DIR/vise_functions.sh"
    echo "[vise] reloaded ($VISE_ROOT)"
}

# --- Trigger-based reload ----------------------------------------------------
# Design decision (supersedes the old PROMPT_COMMAND watcher):
# a prompt hook executes code on EVERY prompt, which shell-hardening audits
# flag as a persistence / surprising-execution vector. Instead, the staleness
# check runs only when the user deliberately invokes a Vise command — reload
# happens exactly when it is needed, framed by an explicit user action.
# Non-interactive shells skip the check entirely.
_VISE_WATCH_FILES=("$VISE_ROOT/.vise/registry.json" "$CORE_DIR/vise_functions.sh")

_vise_stamp() {
    # GNU stat (Linux, Git Bash) first, BSD stat (macOS) as fallback
    stat -c '%Y' "${_VISE_WATCH_FILES[@]}" 2>/dev/null \
        || stat -f '%m' "${_VISE_WATCH_FILES[@]}" 2>/dev/null
}

_vise_maybe_reload() {
    [[ $- == *i* ]] || return 0
    local stamp
    stamp="$(_vise_stamp | tr '\n' ':')"
    if [[ -n "${_vise_last_stamp:-}" && "$stamp" != "$_vise_last_stamp" ]]; then
        # shellcheck disable=SC1090
        source "$CORE_DIR/vise_functions.sh"
        echo "[vise] adapter set or shell integration changed - reloaded"
    fi
    _vise_last_stamp="$stamp"
}
