# ExampleAdapter (reference)

Reference implementation of the Vise adapter contract. Copy this directory,
edit `vise.adapter.json`, then run `bash init/setup.sh` to auto-generate
the registry and onboard the new adapter.

## Files

| File | Role |
| --- | --- |
| `vise.adapter.json` | Adapter manifest: adapter_id, version, required_cots, init_steps, healthcheck, entry_points |
| `thread` | Bootstrap script: sets up config, validates paths, registers state with Vise |
| `vise_extensions.sh` | Optional shell extensions (aliases/completions). Functions prefixed with `vise_<adapter_id>_` |
| `src/example_tool/__init__.py` | Package initializer |
| `src/example_tool/main.py` | Main CLI entry point: argument parsing and command dispatch |
| `src/example_tool/thread_cli.py` | thread CLI implementation: runs when `thread` is invoked |

## Usage

```bash
# Via visectl (recommended)
visectl thread example_tool              # initialize adapter state
visectl run example_tool list            # show adapter info
visectl run example_tool decode SGVsbG8=  # base64-decode a string
visectl run example_tool decode --hex 48656c6c6f

# Or directly
bash thread                               # initialize adapter state
python src/example_tool/main.py list
python src/example_tool/main.py decode SGVsbG8=
```
