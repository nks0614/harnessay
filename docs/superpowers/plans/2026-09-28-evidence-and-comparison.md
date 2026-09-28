# Evidence and comparison implementation plan

> **For agentic workers:** Use superpowers:executing-plans for implementation; independent data-export work is delegated with explicit file ownership.

**Goal:** Improve trustworthy measurement, export comparable snapshots, and trace report findings to source records.
**Architecture:** Keep the existing Python CLI and static HTML report. Extend transcript events with source locations, collect input diagnostics, deduplicate stable event identities, and reuse the same aggregate for HTML/JSON. A small report_data module owns JSON conversion and comparison only.
**Tech Stack:** Python 3.8+, standard library.
**Spec:** docs/REVIEW.ko.md priorities: measurement accuracy, before/after comparison, source tracing.

## Global constraints

- Preserve existing uncommitted work and Claude/Codex support.
- No new dependencies, servers, live model evaluations or automatic skill installation.
- Never deduplicate distinct calls merely because text matches.
- Date range is UTC [since, until); exclude unassignable timestamps in bounded reports and disclose counts.
- Export paths and line numbers, not raw tool output or prompts.

## Review focus

- Fork copied metadata must not overwrite the child's identity.
- Duplicate IDs in unrelated sessions must not erase legitimate calls.
- Damaged rows and unreadable files must be counted, not crash the run.
- Empty baselines must not imply a percentage improvement.
- Paths, tool names and timestamps must be escaped in generated HTML.

## Tasks

- [x] Parser tests first: nested invalid shapes, bad UTF-8/JSON, until boundary, owner metadata and fork replay.
- [x] Implement input diagnostics, date bounds, provenance and evidence-based duplicate exclusion in harnessay.py.
- [x] report_data.py: JSON-safe snapshot conversion and validated comparisons; add runnable test_report_data.py.
- [x] CLI: --until, --json-out and --compare; validate paths/date ranges before writing artifacts.
- [x] HTML: diagnostics, comparison metrics, largest outputs, repeated-read sources and candidate sources.
- [x] Update both READMEs, SKILL.md and review status.
- [x] Run offline checks, focused independent review and real local reports; record remaining limitations.

## Verification record

All three offline test scripts pass. Added regression coverage for Claude usage
metadata, damaged input, UTC end bounds, copied owner metadata, explicit ordinal
fork boundaries, stable-ID replay outside the selected period, independent
sessions, JSON round trips, comparison arithmetic and CLI overwrite protection.
A focused independent review identified the usage-metadata and two fork-boundary
issues; each was reproduced and corrected. Real local history was analyzed and
bounded before/after snapshots generated. No live model evaluation was run.

Ruling: Continue in the existing checkout to preserve the prior turn's authorized
uncommitted implementation. Scope is the recommended first three priorities.
No public release, push, new dependency or user-wide skill install is included.
