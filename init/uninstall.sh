#!/usr/bin/env bash
# Vise uninstall: remove shell integration + optionally clean up artifacts.
#
# Usage:
#   bash init/uninstall.sh             # remove everything (shell + artifacts)
#   bash init/uninstall.sh --keep      # only remove shell integration, keep artifacts
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

# --- 1. Remove Vise block from shell rc files ---
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

remove_vise_block "$RC_FILE"

# Also check the other common rc file (in case shell changed)
OTHER_RC="$HOME/.bashrc"
[[ "$RC_FILE" == "$OTHER_RC" ]] && OTHER_RC="$HOME/.zshrc"
if [[ -f "$OTHER_RC" ]]; then
    remove_vise_block "$OTHER_RC"
fi

# --- 2. Remove generated artifacts ---
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
if $DRY_RUN; then
    echo "This was a dry run. Re-run without --dry-run to actually remove."
fi
