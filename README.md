# harnessay

**Profiling and regression testing for your Claude Code workflow, built on the
session transcripts you already have.**

Claude Code writes a full transcript of every session to
`~/.claude/projects/*/*.jsonl` — token usage, every tool call, every tool
result. Almost nobody reads them. harnessay turns that idle data into three
tools:

| Tool | Question it answers |
|---|---|
| Context budget profiler | Where do my tokens actually go? |
| Skill-promotion detector | Which repeated workflows deserve to become skills? |
| Skill regression harness | Do my skills still work after I change them? |

Everything runs locally with the Python standard library. No API key: the
regression harness runs on your existing Claude subscription via `claude -p`.

## Installation

**As a Claude Code plugin** (recommended):

```
/plugin marketplace add nks0614/harnessay
/plugin install harnessay@harnessay
```

**As a standalone personal skill:**

```bash
git clone https://github.com/nks0614/harnessay.git
ln -s "$(pwd)/harnessay/skills/harnessay" ~/.claude/skills/harnessay
```

Requirements: Claude Code, Python 3.8+. No third-party packages.

## Usage

In any Claude Code session:

```
/harnessay          # context budget report + skill candidates
/harnessay eval     # run golden tasks, report pass rate
```

Or run the scripts directly:

```bash
python3 skills/harnessay/harnessay.py -o report.html
python3 skills/harnessay/evalrun.py [tasks.json] [--only id-substring]
```

## Context budget profiler

Parses every transcript and aggregates:

- **Context consumption by tool** — bytes each tool's results put into your
  context window (Read, Bash, Grep, MCP tools, …)
- **Duplicate reads** — files read repeatedly and the bytes wasted re-reading
  them
- **Per-project totals** — output tokens, sidechain (subagent) tokens, cache
  writes, compactions

The report opens with a single actionable headline, e.g.:

> 52% of tool-result context is Bash. 31% of Read bytes are same-file
> re-reads.

That sentence is the point: it tells you what to fix (put frequently re-read
file summaries in `CLAUDE.md`, trim noisy Bash output) without reading a
dashboard.

## Skill-promotion detector

The same pass extracts each session's tool-call sequence (Bash calls keyed by
their leading command, e.g. `Bash:git`) and surfaces n-grams repeated three or
more times:

- Shared across **3+ projects** → suggested as a **personal** skill
  (`~/.claude/skills`)
- Confined to **one project** → suggested as a **project** skill
  (`.claude/skills`)

The detector only presents evidence. It never generates skills — one-off
workflows promoted automatically become noise, so promotion stays a human
decision.

## Skill regression harness

CI for your skills. Define golden tasks, batch-run them with `claude -p`,
track the pass rate over time in `results.jsonl`.

```json
[
  {
    "id": "my-skill-smoke",
    "skill": "my-skill",
    "prompt": "/my-skill do the usual thing",
    "check": { "type": "regex", "value": "expected output pattern" },
    "model": "claude-haiku-4-5"
  }
]
```

- `check.type` is `contains` or `regex`, applied to the session's final
  output.
- `model` is optional; use a small model for cheap sanity tasks.
- Each run appends `{ts, id, skill, pass, duration}` to `results.jsonl` next
  to your tasks file, so regressions show up as a pass-rate drop.
- Draft golden tasks from the profiler's skill-candidate sequences — they are,
  by construction, the workflows you repeat most.

Note: each task consumes your Claude subscription quota. Keep golden suites
small (1–2 tasks per skill).

## Privacy

Transcripts can contain source code, file paths, and anything else from your
sessions. harnessay never uploads them: parsing and reporting are entirely
local, and the generated `report.html` is a plain static file that stays on
your machine. The only network activity is the regression harness invoking
your own `claude` CLI.

## Limitations

- **Unofficial format.** The transcript schema is not a public API and may
  change between Claude Code releases. Parsing is isolated in
  `parse_session()` and stamped with `SCHEMA_VERSION` so breakage is contained
  to one function.
- **Estimated tokens.** Tool-result sizes are measured in bytes; the `~tokens`
  column uses a bytes/4 approximation, not a tokenizer.
- **Heavy-user tool.** The insights scale with usage. If you run a handful of
  sessions a week, the report will be thin.

## Development

```bash
python3 skills/harnessay/test_harnessay.py   # self-check, no fixtures
```

Layout: `skills/harnessay/` contains everything — `SKILL.md` (Claude Code
entry point), `harnessay.py` (parser + aggregation + report), `evalrun.py`
(regression runner), `eval/tasks.json` (golden tasks). The parsing layer and
aggregation layer are deliberately separate; format changes should only ever
touch `parse_session()`.

## License

[MIT](LICENSE)
