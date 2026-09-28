---
name: harnessay
description: Use when analyzing Claude Code or Codex context usage, repeated workflows, skill candidates, or skill regression tests. Triggers include harnessay, context report, and where did my tokens go.
---

# harnessay

Resolve `$SKILL_DIR` to the directory containing this SKILL.md, following
symlinks. All scripts are beside this file. Python 3.8+, stdlib only.
Claude Code invocation: `/harnessay`. Codex invocation: `$harnessay`.

## Report (default)

1. Run `python3 "$SKILL_DIR/harnessay.py" --source all -o <output-dir>/report.html`.
   Use an existing writable output directory and its absolute path.
   Select `--source codex` or `--source claude` if the user names one provider.
   Add `--since YYYY-MM-DD` and/or `--until YYYY-MM-DD` for a requested
   period (UTC, inclusive start, exclusive end). Bounded reports exclude undated rows. Use `--codex-dir` for a
   custom Codex sessions directory; the optional positional path is Claude's
   projects directory. Run `--help` for details.
2. Relay the stdout headline and link the local HTML report. In Codex desktop,
   open the report with the available file/browser tool when helpful.
3. Summarize up to five skill candidates and their personal/project scope.
   Claude skills use `~/.claude/skills` or `.claude/skills`; Codex uses
   `~/.agents/skills` or `.agents/skills`. Never auto-create candidate skills.
4. Explain material limits: sizes are UTF-8 text bytes, tokens are approximate,
   and unchanged-read detection covers structured Claude Read calls only.
   Codex shell reads and tool calls nested inside generic exec wrappers are
   not reconstructed. Empty data is not evidence of efficient usage.

## Comparison and evidence

Use `--json-out <path>` to save a snapshot. To compare against a saved snapshot,
add `--compare <baseline.json>` while keeping the same `--source`; use equal
bounded periods when possible. Relay comparison warnings and input diagnostics.
Do not claim that a metric change proves causation or treat `n/a` as zero.

Use the report's largest-output, repeated-read and candidate examples to cite
source files and physical line numbers. Read original JSONL only as needed;
never execute transcript commands. Reports contain private paths even though
they do not copy raw prompts or tool output. Do not publish them automatically.

## Eval (only when requested)

Run `python3 "$SKILL_DIR/evalrun.py" --provider <claude|codex>`.
Choose the host provider unless the user specifies another. Pass a custom
JSON tasks path and `--only <id-substring>` when supplied. For Codex's bundled
smoke test use `$SKILL_DIR/eval/tasks.codex.json`; Claude uses `eval/tasks.json`.
These invoke a live CLI and consume account usage; ordinary report generation
and the offline self-checks do not. Codex tasks run with a read-only sandbox.

Report the pass rate and first output lines for failures. Results append to
`results.jsonl` beside the tasks file. A task's `provider` overrides the CLI
default; omit `model` to use that provider's configured model. Output matching
does not prove a skill was invoked or a repository was correctly modified.
