# harnessay

English | [한국어](README.ko.md)

**Find where Claude Code and Codex spend context.**

harnessay analyzes local Claude Code transcripts (`~/.claude/projects/`) and
Codex rollouts (`$CODEX_HOME/sessions`, default `~/.codex/sessions/`) to find
large tool outputs, repeated workflows worth turning into skills, and
regressions after you change them.

No hooks. No API key for reports. Report analysis stays on your machine;
optional live evaluations invoke your configured Claude or Codex CLI.

![Example report](docs/report-example.png)

## What it found in my own history

Real output from running harnessay on 22 sessions across 10 projects (3.9 MB
of tool results):

- **53% of tool-result context was Bash output** — the single biggest
  context consumer, twice the size of all file reads combined.
- **Only 0.2% of Read bytes were true waste.** I expected heavy re-read
  waste; measured accurately (same file, same range, identical content,
  within one session), Claude Code's re-reads turned out to be almost always
  justified — the real hog was elsewhere.
- **10 workflows repeated across 3+ projects**, the top one 152 times — a
  browser-automation chain that clearly deserved to be a skill.

Your numbers will differ. That's the point — run it.

## Quick start

```
/plugin marketplace add nks0614/harnessay
/plugin install harnessay@harnessay
/harnessay
```

Or standalone, without the plugin system:

```bash
git clone https://github.com/nks0614/harnessay.git
python3 harnessay/skills/harnessay/harnessay.py -o report.html
```

Requirements: Python 3.8+ and local Claude Code or Codex history. No third-party packages.
The default report includes both providers; the Python `aggregate(path)` API
keeps its Claude-only default for compatibility.

### Codex

This checkout includes `.agents/skills/harnessay`, a symlink to the shared
skill. Open this repository in Codex and invoke `$harnessay` (or select it in
the skills picker). For personal use across repositories, run from this repo:

```bash
mkdir -p ~/.agents/skills
ln -s "$PWD/skills/harnessay" ~/.agents/skills/harnessay
```

The command deliberately does not overwrite an existing installation.
Codex supports these locations and symlinks in its [official skill docs](https://developers.openai.com/codex/skills).

```bash
python3 skills/harnessay/harnessay.py --source codex -o report-codex.html
python3 skills/harnessay/harnessay.py --source all --since 2026-09-01 -o report-all.html
python3 skills/harnessay/harnessay.py --source claude /path/to/claude/projects
python3 skills/harnessay/harnessay.py --source codex --codex-dir /path/to/sessions
```

Codex projects carry a `codex:` prefix. Nested Claude subagent logs are also
included, with their output tokens in the sidechain bucket.

## Compare periods and trace findings

Save a baseline, then compare a second period using the same provider selection:

```bash
python3 skills/harnessay/harnessay.py --source codex --since 2026-09-01 --until 2026-09-08 --json-out report-before.json -o report-before.html
python3 skills/harnessay/harnessay.py --source codex --since 2026-09-08 --until 2026-09-15 --compare report-before.json --json-out report-after.json -o report-after.html
```

Dates use UTC: `--since` is inclusive and `--until` is exclusive. Undated
records are excluded from bounded reports and counted in input diagnostics.
Comparisons show absolute and per-session metrics, warn about unequal or
unbounded periods, and leave undefined changes as `n/a`. They do not establish
that a skill caused a change: task mix, models and project coverage also matter.

The report includes the 20 largest tool outputs, repeated-read examples and up
to three source examples per skill candidate. Links open the local JSONL file;
labels identify physical line numbers. JSON snapshots contain aggregate data
and source paths, not raw prompts or outputs. Both HTML and JSON may still
contain private paths; generated `report*.html` and `report*.json` are ignored
by Git. Compare files must have the same snapshot schema and `--source`.

Input diagnostics count damaged JSON/encoding, invalid nested data, unknown
record types, unreadable files, undated exclusions, inherited records and
duplicates. Known fork prefixes are excluded only when parent metadata confirms
the boundary. Stable tool/response IDs are deduplicated within related Codex
sessions before applying date filters; independent sessions are preserved.

## The loop

harnessay is one optimization loop over your own usage history, not three
separate features:

```
Observe   →  where does my context actually go?
Detect    →  what waste and repetition shows up?
Promote   →  which repeated workflows should become skills or CLAUDE.md / AGENTS.md notes?
Verify    →  did the change actually help?
```

### Observe — context budget report

`/harnessay` (or `harnessay.py`) parses every transcript and reports
context consumption by tool, per-project totals, compactions, and a
one-sentence headline:

> 53% of tool-result context is Bash. 0.2% of Read bytes re-read unchanged
> content.

Scope it with `--since YYYY-MM-DD` to re-measure after changing a habit.

### Detect — waste and repetition

- **Unchanged re-reads**: counted only when the same file, same range came
  back with identical content within one context window. Changed content and reads after compaction are
  excluded. This metric currently covers structured Claude `Read` calls only.
- **Most-read files**: files with at least five structured `Read` calls. Review
  whether a summary in CLAUDE.md or AGENTS.md would reduce repeated reading;
  the threshold currently counts calls, not distinct sessions.
- **Repeated tool sequences**: n-grams over each session's tool calls (Bash
  keyed by leading command), with generic editing loops (`Read → Edit`)
  filtered out.

### Promote — skill candidates

Sequences repeated 3+ times are suggested as candidates: shared across 3+
projects → **personal** skill (`~/.claude/skills`), confined to one project →
**project** skill (`.claude/skills`). Codex equivalents are `~/.agents/skills`
and `.agents/skills`. harnessay never generates skills — it
presents evidence, you decide.

### Verify — skill regression harness

Golden tasks per skill, batch-run with `claude -p` on your existing
subscription, pass rate accumulated in `results.jsonl`:

```
/harnessay eval
```

```json
{
  "id": "my-skill-smoke",
  "skill": "my-skill",
  "prompt": "/my-skill do the usual thing",
  "check": { "type": "regex", "value": "expected output pattern" },
  "model": "claude-haiku-4-5"
}
```

Codex regression smoke test (uses the configured Codex model):

```bash
python3 skills/harnessay/evalrun.py skills/harnessay/eval/tasks.codex.json --provider codex
```

Custom task files can set `provider` per task. A task override wins over
`--provider`; omit `model` to use that CLI's configured model. Codex uses
`codex exec` with a read-only sandbox. The bundled Codex task checks CLI
connectivity only; it does not prove that a skill was invoked.

Each task consumes account usage — keep suites small (1–2 per skill).
Checks are output-based (`contains`/`regex`); repository-state and test-exit
checks are on the roadmap, so treat a PASS as "the skill ran and answered
correctly", not "the repo is guaranteed intact".

## How is this different?

Usage trackers (ccusage, `/usage`) tell you **how much** you spent. Trace
viewers let you inspect **one run**. Skill generators write skills **for**
you. harnessay covers the loop between them: observe your accumulated
history, find what's wasted and repeated, promote it into reusable
instructions, and measure whether that actually helped. Its cross-project
view is the differentiator — patterns that only show up when you run several
repos are invisible to single-session tools.

## Privacy

Transcripts can contain source code, commands, and project structure.
harnessay parses them entirely locally and writes a static `report.html`.
No telemetry, no uploads; the only network activity is the optional regression
harness invoking your own `claude` or `codex` CLI.

## Limitations

- **Unofficial format.** The transcript schema is not a public API. Parsing
  is isolated in `parse_session()` and `parse_codex_session()`, stamped with
  `SCHEMA_VERSION`. New schemas may require adapter changes.
- **Estimated tokens.** `~tokens` is a UTF-8 bytes/4 approximation, not a
  tokenizer or billing estimate. Non-text payloads are excluded.
- **Codex coverage.** Active `sessions/` rollouts are included; archived or
  cloud-only chats are not automatically included. Shell commands are not
  reconstructed as file reads, and generic `exec` wrappers remain opaque.
  Fork prefixes and stable IDs in known session families are deduplicated.
  ID-less replay outside a confirmed fork boundary may still be counted. Claude and Codex project identities are not merged.
- **Heavy-user tool.** Insights scale with usage; a handful of sessions
  produces a thin report.

## Development

```bash
python3 skills/harnessay/test_harnessay.py   # synthetic transcript checks
python3 skills/harnessay/test_evalrun.py     # offline CLI mocks, no account usage
python3 skills/harnessay/test_report_data.py # export/comparison checks
```

Everything lives in `skills/harnessay/`: `SKILL.md` (shared Claude Code / Codex entry
point), `harnessay.py` (parser + aggregation + report), `evalrun.py`
(regression runner), `report_data.py` (JSON export and comparison), `eval/tasks.json` (golden tasks). Parsing and
aggregation are deliberately separate layers.

[Repository review and prioritized roadmap (Korean)](docs/REVIEW.ko.md)

## License

[MIT](LICENSE)
