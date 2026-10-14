# ExampleAdapter extensions - optional, sourced by user if needed.
# Functions must be prefixed with vise_<adapter_id>_ to avoid conflicts.
# shellcheck shell=bash

# This file is OPTIONAL. Adapter CLI is accessed via:
#   visectl run example_tool <command>
# Only define shell functions here if you need aliases or completions.

vise_example_tool() {
    "$VISE_PYTHON" "$VISE_ROOT/adapters/ExampleAdapter_reference/src/example_tool/main.py" "$@"
}
