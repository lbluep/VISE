# githubToolkit optional shell extensions.
# The primary entry is the global `congit` command installed by `thread`
# into ~/.local/bin. This file provides a namespaced alias for shells that
# prefer to source adapter extensions explicitly (prefix avoids P0-1 clashes).
# shellcheck shell=bash

vise_github_toolkit_congit() {
  bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bin/congit" "$@"
}
