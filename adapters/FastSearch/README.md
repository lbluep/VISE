# FastSearch

ripgrep-backed search adapter: fast regex search and file listing for agents
and humans.

## Usage

```bash
visectl run fast_search search "def main" . --type py --max 20
visectl run fast_search files src --glob '*.py'
visectl run fast_search read tips
visectl read fast_search usage      # same, via the resource access contract
```

## Dependency

Requires ripgrep (`rg`, >= 13.0). Resolution order: PATH → known bundled
locations (Trae CN / VS Code ship their own `rg`). If missing, install ripgrep
or pin a path in `core/adapter_paths.json`.
