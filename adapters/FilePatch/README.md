# FilePatch

Controlled file editing: preview changes as a unified diff before writing,
automatic `.bak` backup, one-command restore. New content arrives via stdin,
so it composes with pipes and agents.

## Usage

```bash
cat new_version.py | visectl run file_patch preview /abs/path/file.py   # diff only
cat new_version.py | visectl run file_patch apply /abs/path/file.py     # diff + backup + write
visectl run file_patch restore /abs/path/file.py                        # undo from .bak
```
