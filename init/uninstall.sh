#!/usr/bin/env bash
# Vise uninstall: remove everything Vise added or modified outside VISE_ROOT,
# then optionally clean up artifacts.
#
# Removal is ledger-driven: installers (setup.sh, adapter thread scripts)
# record every outside-VISE_ROOT file they create or modify in
# .vise/installed.json (core/manifest.py). Uninstall applies that ledger:
#   - created  files  -> deleted (only if the recorded checksum still matches;
#                        a changed file is SKIPPED and reported for manual
#                        inspection, never silently deleted)
#   - modified files  -> revert recipe applied (rc marker block removed)
# Two safety nets stay on top of the ledger:
#   - fallback scan of ~/.local/bin for Vise-generated wrappers
#     (Auto-generated header + VISE_ROOT reference)
#   - marker-based rc block removal (idempotent, works without any ledger)
#
# Usage:
#   bash init/uninstall.sh             # remove everything (outside files + artifacts)
#   bash init/uninstall.sh --keep      # same, but keep .vise/ and .venv/ artifacts
#   bash init/uninstall.sh --dry-run   # preview what would be removed
set -euo pipefail

VISE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKER="# >>> Vise >>>"
MARKER_END="# <<< Vise <<<"

DRY_RUN=false
KEEP_ARTIFACTS=false

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --keep)    KEEP_ARTIFACTS=true ;;
        *) echo "unknown option: $arg" >&2; exit 1 ;;
    esac
done

# --- Helpers ---
detect_rc() {
    local shell_name
    shell_name="$(basename "${SHELL:-bash}")"
    case "$shell_name" in
        zsh) echo "$HOME/.zshrc" ;;
        *)   echo "$HOME/.bashrc" ;;
    esac
}

RC_FILE="$(detect_rc)"
prefix=""
$DRY_RUN && prefix="[DRY RUN] "

# Reuse the Python recorded by setup.sh, or fall back
PYTHON_BIN="$(cat "$VISE_ROOT/.vise/python" 2>/dev/null || command -v python3 || echo python3)"

# --- 1. Ledger-driven cleanup: files Vise added/modified outside VISE_ROOT ---
# Exit 3 means drift-skipped files remain (needs manual action).
MANIFEST_PY="$VISE_ROOT/core/manifest.py"
LEDGER_RC=0
if [[ -f "$MANIFEST_PY" ]]; then
    if $DRY_RUN; then
        "$PYTHON_BIN" "$MANIFEST_PY" uninstall-apply --dry-run || LEDGER_RC=$?
    else
        "$PYTHON_BIN" "$MANIFEST_PY" uninstall-apply || LEDGER_RC=$?
    fi
else
    echo "${prefix}core/manifest.py not found - ledger cleanup skipped"
fi

remove_vise_block() {
    local file="$1"
    if [[ ! -f "$file" ]]; then
        echo "${prefix}$file not found (already clean?)"
        return 0
    fi
    local found=0
    local markers=("# >>> Vise >>>")
    local m
    for m in "${markers[@]}"; do
        grep -qF "$m" "$file" || continue
        found=1
        echo "${prefix}Removing ${m#*>>> } block from $file"
        if ! $DRY_RUN; then
            # Use Python to avoid sed temp-file permission issues
            "$PYTHON_BIN" -c "
import sys, re
path, start = sys.argv[1], sys.argv[2]
end = start.replace('>>>', '<<<')
text = open(path).read()
text = re.sub(r'(?m)^' + re.escape(start) + r'.*?' + re.escape(end) + r'\n?', '', text, flags=re.DOTALL)
open(path, 'w').write(text)
" "$file" "$m"
        fi
    done
    (( found )) || echo "${prefix}Vise block not found in $file (already clean?)"
}

# --- 2. Remove Vise block from shell rc files (marker defense, idempotent) ---
remove_vise_block "$RC_FILE"

# Also check the other common rc file (in case shell changed)
OTHER_RC="$HOME/.bashrc"
[[ "$RC_FILE" == "$OTHER_RC" ]] && OTHER_RC="$HOME/.zshrc"
if [[ -f "$OTHER_RC" ]]; then
    remove_vise_block "$OTHER_RC"
fi

# --- 3. Remove generated artifacts ---
if $KEEP_ARTIFACTS; then
    echo
    echo "${prefix}Keeping artifacts (--keep specified)"
else
    echo
    artifacts=(
        "$VISE_ROOT/.vise"
        "$VISE_ROOT/.venv"
    )
    for f in "${artifacts[@]}"; do
        if [[ -e "$f" ]]; then
            echo "${prefix}Removing: $f"
            if ! $DRY_RUN; then
                rm -rf "$f"
            fi
        fi
    done
fi

echo
echo "${prefix}Uninstall complete!"
if (( LEDGER_RC == 3 )); then
    echo
    echo "Manual action needed: some Vise-installed files changed since"
    echo "install and were NOT removed (SKIP! lines above). Inspect and"
    echo "delete them yourself if they are no longer wanted."
fi
if $DRY_RUN; then
    echo "This was a dry run. Re-run without --dry-run to actually remove."
fi
# Propagate the ledger result: 3 = drift-skipped files need manual action.
exit "$LEDGER_RC"
