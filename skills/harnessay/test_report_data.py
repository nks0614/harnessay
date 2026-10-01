#!/usr/bin/env python3
"""Offline checks: python3 skills/harnessay/test_report_data.py."""
from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path

from report_data import export_stats, compare_reports


def coverage_checks():
    old = export_stats({})
    current = deepcopy(old)
    current['usage'] = {'records': 2, 'fields': {
        key: {'tokens': 0, 'records': 2} for key in (
            'input_tokens', 'uncached_input_tokens', 'fresh_input_tokens', 'cached_input_tokens',
            'cache_write_input_tokens', 'output_tokens', 'reasoning_output_tokens')}}
    metrics = {r['metric']: r for r in compare_reports(current, old)['metrics']}
    assert metrics['input_tokens']['before'] is None
    assert metrics['input_tokens']['after'] == 0
    assert metrics['input_tokens']['delta'] is None
    partial = deepcopy(current)
    partial['usage']['fields']['input_tokens'] = {'tokens': 100, 'records': 1}
    comparison = compare_reports(partial, current)
    row = next(r for r in comparison['metrics'] if r['metric'] == 'input_tokens')
    assert row['after'] is None and row['percent_change'] is None
    assert any('partial token coverage' in warning for warning in comparison['warnings'])
    for bad in (None, True, -1, 3, 1.5, '1'):
        invalid = deepcopy(current)
        invalid['usage']['fields']['input_tokens']['records'] = bad
        try:
            compare_reports(invalid, current)
        except ValueError:
            pass
        else:
            raise AssertionError('invalid field coverage accepted')
    for field in ({'tokens': 5, 'records': 0}, {'tokens': -1, 'records': 1},
                  {'tokens': True, 'records': 1}, {'tokens': None, 'records': 1}):
        invalid = deepcopy(current)
        invalid['usage']['fields']['input_tokens'] = field
        try:
            compare_reports(invalid, current)
        except ValueError:
            pass
        else:
            raise AssertionError('invalid observed tokens accepted')


def main():
    coverage_checks()
    evidence = {"large_results": [{"project": "한글", "tool": "Read", "bytes": 100,
                "source": {"path": "/기록/세션.jsonl", "line": 2, "timestamp": "2026-09-01T00:00:00Z"}}],
                "redundant_reads": [], "candidates": []}
    stats = {"source": "claude", "since": "2026-09-01", "until": "2026-09-03",
             "projects": defaultdict(Counter, {"한글": Counter(sessions=2, output=10)}),
             "tools": {"Read": Counter(calls=4, bytes=200), "Bash": Counter(bytes=600)},
             "totals": Counter(result_bytes=800, output=30, sidechain_output=10,
                               compactions=2, redundant_bytes=50),
             "reads": Counter({("한글", Path("/문서/파일.py")): 4}),
             "redundant": Counter({("한글", Path("/문서/파일.py")): 50}),
             "diagnostics": Counter(malformed_json=2), "evidence": evidence}
    baseline = export_stats(stats)
    assert baseline["schema_version"] == "2026-09-evidence-v1"
    assert baseline["totals"]["sessions"] == 2
    assert baseline["projects"] == [{"project": "한글", "sessions": 2, "output": 10}]
    assert baseline["reads"] == [{"project": "한글", "file": "/문서/파일.py", "calls": 4}]
    assert baseline["redundant_reads"] == [{"project": "한글", "file": "/문서/파일.py", "bytes": 50}]
    assert baseline["evidence"] is evidence
    assert json.loads(json.dumps(baseline, ensure_ascii=False))["diagnostics"] == {"malformed_json": 2}
    assert "sessions" not in stats["totals"]  # Export must not modify its input.

    current = deepcopy(baseline)
    current.update(since="2026-09-03", until="2026-09-05")
    current["totals"].update(sessions=4, result_bytes=1200, output=40,
                             sidechain_output=20, compactions=2, redundant_bytes=20)
    current["tools"][0]["bytes"] = 400
    comparison = compare_reports(current, baseline)
    metrics = {row["metric"]: row for row in comparison["metrics"]}
    for metric, expected in {
        "sessions": (2, 4, 2, 100), "result_bytes": (800, 1200, 400, 50),
        "output_tokens": (40, 60, 20, 50), "compactions": (2, 2, 0, 0),
        "bytes_per_session": (400, 300, -100, -25),
        "compactions_per_session": (1, 0.5, -0.5, -50),
        "redundant_read_percent": (25, 5, -20, -80),
    }.items():
        assert tuple(metrics[metric][key] for key in ("before", "after", "delta", "percent_change")) == expected
    assert comparison["warnings"] == []
    assert comparison["baseline"] == {"since": "2026-09-01", "until": "2026-09-03", "source": "claude"}

    empty = export_stats({})
    empty_comparison = compare_reports(empty, empty)
    zero = {row["metric"]: row for row in empty_comparison["metrics"]}
    assert zero["sessions"]["percent_change"] == 0
    assert zero["bytes_per_session"]["before"] is None
    assert zero["redundant_read_percent"]["percent_change"] is None
    assert empty_comparison["warnings"]
    positive = compare_reports(current, empty)
    assert positive["metrics"][0]["percent_change"] is None
    assert positive["metrics"][4]["delta"] is None
    unequal = deepcopy(current)
    unequal["until"] = "2026-09-06"
    assert any("length" in warning for warning in compare_reports(unequal, baseline)["warnings"])

    invalid_reports = []
    for key, bad in (("schema_version", "old"), ("source", "codex"),
                     ("source", []), ("totals", []), ("projects", {}),
                     ("tools", [{}]), ("reads", [None]), ("redundant_reads", {}),
                     ("diagnostics", []), ("evidence", []),
                     ("since", "2026-9-01"), ("until", "2026-02-30"),
                     ("until", "2026-09-01")):
        bad_report = deepcopy(baseline)
        bad_report[key] = bad
        invalid_reports.append(bad_report)
    for bad in (True, -1, float("inf"), float("nan"), "12", None):
        bad_report = deepcopy(baseline)
        bad_report["totals"]["output"] = bad
        invalid_reports.append(bad_report)
    missing = deepcopy(baseline)
    del missing["tools"]
    invalid_reports.append(missing)
    for bad_report in invalid_reports:
        try:
            compare_reports(bad_report, baseline)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid report accepted: %r" % bad_report)
    print("report_data offline checks passed")


if __name__ == "__main__":
    main()
