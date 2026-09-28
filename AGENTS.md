# Working on harnessay

This is a local, Python-standard-library-only analyzer for Claude Code and
Codex transcripts. Keep it usable with Python 3.8+ and without API keys.

- `skills/harnessay/harnessay.py`: transcript adapters, aggregation, HTML.
- `skills/harnessay/report_data.py`: portable snapshots and validated comparisons.
- `skills/harnessay/evalrun.py`: explicit live CLI regression runs.
- `skills/harnessay/SKILL.md`: shared Claude/Codex instructions;
  `.agents/skills/harnessay` points here for Codex discovery.
- `README.md` and `README.ko.md`: keep user-facing changes in sync.

Run offline checks after relevant changes:

```sh
python3 skills/harnessay/test_harnessay.py
python3 skills/harnessay/test_evalrun.py
python3 skills/harnessay/test_report_data.py
```

Do not use live evaluations as an automatic test: they consume account usage.
Do not commit generated reports or private transcript content. Keep provider
parsing separate from aggregation, match tool outputs by call ID, and avoid
counting duplicate usage events. Document unsupported transcript shapes
instead of presenting incomplete measurements as exact costs.
