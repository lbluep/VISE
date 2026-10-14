# RepoMap

Repository orientation for AI assistants — the "where am I, what changed,
what does this project look like" question answered in one shot.

## Usage

```bash
visectl run repo_map overview /path/to/repo     # branch + changes + commits + layout
visectl run repo_map status /path/to/repo
visectl run repo_map log 10
visectl run repo_map tree /path/to/repo --depth 3
```

Pass **absolute paths** — the adapter cwd is its own directory (git resolves
the enclosing repo from the path you give).
