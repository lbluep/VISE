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
