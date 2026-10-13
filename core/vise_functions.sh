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

visectl()  { "$VISE_PYTHON" "$CORE_DIR/visectl.py"  "$@"; }
viseinit() { "$VISE_PYTHON" "$CORE_DIR/viseinit.py" "$@"; }

# visereload - manually re-source this loader (picks up new interpreters,
# renamed functions, etc. without opening a new shell).
visereload() {
    # shellcheck disable=SC1090
    source "$CORE_DIR/vise_functions.sh"
    echo "[vise] reloaded ($VISE_ROOT)"
}

# --- Auto-reload watcher (interactive shells only) ---------------------------
# After a new adapter is onboarded (registry.json rewritten by setup) or the
# loader itself changes, the NEXT prompt in any open shell re-sources this
# file automatically. Child processes cannot mutate the parent shell, so a
# prompt hook is the only honest way to "reload bashrc" for existing sessions.
if [[ $- == *i* ]] && [[ -n "${BASH_VERSION:-}" ]]; then
    _VISE_WATCH_FILES=("$VISE_ROOT/.vise/registry.json" "$CORE_DIR/vise_functions.sh")

    _vise_stamp() {
        # GNU stat (Linux, Git Bash) first, BSD stat (macOS) as fallback
        stat -c '%Y' "${_VISE_WATCH_FILES[@]}" 2>/dev/null \
            || stat -f '%m' "${_VISE_WATCH_FILES[@]}" 2>/dev/null
    }

    _vise_auto_reload() {
        local stamp
        stamp="$(_vise_stamp | tr '\n' ':')"
        if [[ -z "${_vise_last_stamp:-}" ]]; then
            _vise_last_stamp="$stamp"
            return 0
        fi
        if [[ "$stamp" != "$_vise_last_stamp" ]]; then
            _vise_last_stamp="$stamp"
            # shellcheck disable=SC1090
            source "$CORE_DIR/vise_functions.sh"
            echo "[vise] adapter set or shell integration changed - reloaded"
        fi
    }

    case "${PROMPT_COMMAND:-}" in
        *_vise_auto_reload*) ;;  # already hooked
        *) PROMPT_COMMAND="_vise_auto_reload${PROMPT_COMMAND:+;$PROMPT_COMMAND}" ;;
    esac
fi
