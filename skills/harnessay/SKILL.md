---
name: harnessay
description: Profile Claude Code transcripts. /harnessay = context budget report + skill candidates, /harnessay eval = skill regression tests. Triggers - "harnessay", "context report", "where did my tokens go", "skill candidates", "skill regression"
---

# harnessay

All scripts live in this skill's base directory (shown above as "Base directory
for this skill" — referred to as `$SKILL_DIR` below). Python 3.8+, stdlib only.

## Default (report)

1. Run:
   ```bash
   python3 "$SKILL_DIR/harnessay.py" -o <scratchpad>/report.html
   ```
2. Relay the stdout headline sentence to the user.
3. Send report.html to the user (if headless, just give the path).
4. Summarize the top 5 rows of the report's "Skill candidates" section with
   their personal/project suggestion. Never auto-create skills — the user
   decides what to promote.

## eval (skill regression tests)

If the arguments contain "eval":

```bash
python3 "$SKILL_DIR/evalrun.py"
```

- Pass through `--only <id-substring>` and a custom tasks.json path if given.
  Golden tasks live in `$SKILL_DIR/eval/tasks.json` by default; users can point
  to their own file.
- Report the pass rate and the first lines of output for any FAIL.
- Results accumulate in results.jsonl next to the tasks file; no extra saving
  needed.
