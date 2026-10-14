# NoteKeeper

Persistent note store for AI assistants — knowledge captured in one session
stays searchable in the next. Pure Python stdlib (sqlite3), zero external
dependencies.

## Usage

```bash
visectl run note_keeper add "deploy-trick" "use --fail-fast in CI" --tags ci,deploy
visectl run note_keeper search deploy
visectl run note_keeper list --tag ci
visectl run note_keeper get deploy-trick
visectl read note_keeper recent     # dump latest 5 notes (knowledge contract)
```

## Storage

Notes live in `$VISE_ROOT/.vise/state/note_keeper/notes.db` — machine-local,
shared by every shell and agent session.
