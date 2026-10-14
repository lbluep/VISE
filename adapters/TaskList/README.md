# TaskList

Persistent to-do list for AI assistants — plan in one session, resume in the
next. Pure Python stdlib (sqlite3).

## Usage

```bash
visectl run task_list add "review PR #12" --priority high
visectl run task_list list                # open tasks, high priority first
visectl run task_list done 1
visectl read task_list pending            # session start: what's open
```
