# harnessay

Tools built on the Claude Code session transcripts already sitting in
`~/.claude/projects/*/*.jsonl` — the data nobody reads. Local-only, Python
stdlib only, no API key needed (the eval harness runs on your existing
`claude` subscription via `claude -p`).

1. **Context budget profiler** — where your tokens actually go: per-tool
   context consumption, per-project totals, duplicate file reads, compactions.
   Output is a one-sentence headline ("52% of tool-result context is Bash.
   31% of Read bytes are same-file re-reads.") plus a static HTML report.
2. **Skill-promotion detector** — repeated tool-call sequences across your
   projects, suggested as skill candidates. Shared across 3+ projects →
   personal skill; single project → project skill. Evidence only, promotion
   is always manual.
3. **Skill regression harness** — golden tasks per skill, batch-run with
   `claude -p`, pass rate tracked over time. CI for your skills.

## Install

As a Claude Code plugin:

```
/plugin marketplace add nks0614/harnessay
/plugin install harnessay@harnessay
```

Or as a standalone personal skill:

```bash
git clone https://github.com/nks0614/harnessay.git
ln -s "$(pwd)/harnessay/skills/harnessay" ~/.claude/skills/harnessay
```

## Use

In any Claude Code session:

- `/harnessay` — generate the report, get the headline + top skill candidates
- `/harnessay eval` — run golden tasks, report pass rate

Or directly:

```bash
python3 skills/harnessay/harnessay.py            # → report.html
python3 skills/harnessay/evalrun.py              # runs eval/tasks.json
```

Golden task format (`eval/tasks.json`):

```json
{"id": "...", "skill": "skill-name", "prompt": "...",
 "check": {"type": "contains|regex", "value": "..."}, "model": "optional"}
```

Draft your golden tasks from the report's skill-candidate sequences.

## Development

```bash
python3 skills/harnessay/test_harnessay.py       # self-check
```

The transcript format is unofficial (see `SCHEMA_VERSION` in `harnessay.py`).
If a Claude Code update breaks parsing, only `parse_session` should need
changes.
